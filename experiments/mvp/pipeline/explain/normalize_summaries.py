"""Offline code punctuation/Thai-template migration; never rewrite model words."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import Mode, StageRecord, chain_intact, sha256_json
from .cache import SCHEMA, _binding, _body, _entry_path, _lock, _save_index, _write


def _read(path):
    return json.loads(Path(path).read_text())


def _legacy_summary(spec, slots, language):
    """Exact former join, used only to authenticate and retire old display text."""
    if not isinstance(spec, dict) or set(spec) != {'sentences'} or len(spec['sentences']) != 2:
        raise ValueError('Old summary specification is malformed')
    ids, texts, evidence = [], [], []
    for row in spec['sentences']:
        if not isinstance(row, dict) or set(row) != {'before', 'slot_id', 'after'}:
            raise ValueError('Old summary sentence schema mismatch')
        sid = row['slot_id']
        if sid not in slots or sid in ids or any(not isinstance(row[k], str) for k in ('before', 'after')):
            raise ValueError('Old summary slot or prose is malformed')
        ids.append(sid)
        texts.append(' '.join(s for s in (row['before'], slots[sid][language], row['after']) if s) + '.')
        evidence.extend(deepcopy(slots[sid]['evidence']))
    return dict(schema='stowlm.box-summary/1', language=language, spec=deepcopy(spec), slot_ids=ids,
                text=' '.join(texts), evidence=evidence,
                slots_sha256=hashlib.sha256(json.dumps(slots, sort_keys=True, ensure_ascii=False).encode()).hexdigest())


def _provenance(payload, summary):
    """Retain only framing matching its admitted model response and request ID."""
    audit = payload['audit']
    admitted = audit.get('summary', {})
    if (not admitted.get('accepted') or admitted.get('source') != 'spark-summary'
            or not admitted.get('model') or not audit.get('model')):
        raise ValueError('Summary lacks admitted model provenance')
    requests = {c.get('request_id') for c in admitted.get('calls', []) if c.get('status') == 'received'} - {None}
    for call in payload.get('calls', []):
        if (call.get('request_id') not in requests or call.get('status') != 'measured'
                or call.get('purpose') != 'pipeline-box-summary-' + payload['language']):
            continue
        try:
            value = json.loads(call['response_text'])
            for row in value['sentences']:
                for key in ('before', 'after'):
                    row[key] = row[key].strip()
            if value == summary['spec']:
                return
        except (KeyError, TypeError, ValueError):
            continue
    raise ValueError('Summary framing does not match its recorded model response')


def _prepare(directory, instance, plan, report):
    from .api import inputs, statement_menu
    from .alternatives import alternatives_statement, rederive_validate
    from .render import ROLE_BINDING, render, validate_role_bound, validate_protected_tokens, protected_tokens
    from .summary import slots_for, validate_summary
    instance, plan, report = inputs(instance, plan, report)
    index = _read(directory / 'index.json')
    digest = index.pop('sha256', None)
    if (index.get('schema') != SCHEMA or digest != sha256_json(index)
            or index.get('binding') != _binding(instance, plan, report)):
        raise ValueError('Migration cache index integrity/input binding mismatch')
    records = [StageRecord.model_validate(row) for row in index.get('records', [])]
    if not chain_intact(records):
        raise ValueError('Migration stage chain is not intact')
    prepared, changes = [], []
    for box, entries in sorted(index['entries'].items()):
        if set(entries) != {'en', 'th'}:
            raise ValueError('Punctuation migration requires complete bilingual pairs')
        payloads = {}
        for language in ('en', 'th'):
            entry = entries[language]
            payload = _read(directory / _entry_path(entry))
            if (sha256_json(payload) != entry['sha256'] or payload.get('audit') != entry.get('audit')
                    or payload.get('binding') != index['binding'] or payload.get('box_id') != box
                    or payload.get('language') != language):
                raise ValueError('Migration payload integrity/audit/input binding mismatch')
            payloads[language] = payload
        if payloads['en'].get('alternatives') != payloads['th'].get('alternatives'):
            raise ValueError('Migration bilingual alternative evidence differs')
        menu = statement_menu(instance, plan, report, box)
        alternatives = payloads['en'].get('alternatives')
        if alternatives is not None:
            rederive_validate(alternatives, instance, plan, report)
            menu.append(alternatives_statement(alternatives))
        fresh_en, _ = render(menu, 'en')
        old_menu = [s for s in payloads['en']['explanation']['statements'] if s['id'] != 'summary']
        strip_thai = lambda row: {k: v for k, v in row.items() if k not in ('th', 'summary_th', 'summary_en')}
        if [strip_thai(row) for row in old_menu] != [strip_thai(row) for row in fresh_en]:
            raise ValueError('Migration English facts, numeric roles or evidence changed')
        for old_fact, fresh_fact in zip(old_menu, fresh_en):
            if fresh_fact.get('summary_en') != old_fact.get('summary_en'):
                tokens = Counter(protected_tokens(fresh_fact.get('summary_en', fresh_fact['text'])))
                if tokens - Counter(protected_tokens(fresh_fact['text'])):
                    raise ValueError('New summary clause contains values absent from its full source fact')
        old_slots, fresh_slots = slots_for(old_menu), slots_for(menu)
        english, old_english_ids, english_changed = None, None, False
        for language in ('en', 'th'):
            entry, payload = entries[language], payloads[language]
            old_rows, _ = render(old_menu, language)
            rows, _ = render(menu, language)
            explanation = payload['explanation']
            facts = [s for s in explanation['statements'] if s['id'] != 'summary']
            if facts != old_rows:
                raise ValueError('Migration old bilingual facts or role templates disagree')
            if language == 'th':
                if payload['audit']['checks'].get('fact_roles') != ROLE_BINDING:
                    raise ValueError('Migration Thai facts lack role binding')
                validate_role_bound(facts)
                if any(not validate_protected_tokens(en['text'], th['text']) for en, th in zip(old_menu, facts)):
                    raise ValueError('Migration old Thai fact protected tokens differ')
            old = payload.get('summary')
            old_body, old_statements = _body(old_rows, old, recorded=old is not None)
            if (explanation.get('language') != language or explanation.get('text') != old_body
                    or explanation['statements'] != old_statements
                    or explanation.get('mode') != ('recorded' if old is not None else 'live')):
                raise ValueError('Migration display text or model label mismatch')
            value, reason = None, None
            if old is not None:
                legacy = _legacy_summary(old['spec'], old_slots, language)
                current_old = None
                try:
                    _provenance(payload, old)
                    current_old = validate_summary(old['spec'], old_slots, language,
                                                   paired_ids=old_english_ids if language == 'th' else None)
                    value = validate_summary(old['spec'], fresh_slots, language,
                                             paired_ids=english['slot_ids'] if language == 'th' and english else None)
                    if language == 'th' and english is None:
                        raise ValueError('Thai summary has no admitted English pair')
                except ValueError as error:
                    if language == 'en':
                        raise
                    reason = str(error)
                    value = None
                if old != legacy and old != current_old:
                    raise ValueError('Old summary differs beyond deterministic punctuation')
                if language == 'en':
                    english = value
                    old_english_ids = old['slot_ids']
                    english_changed = old['text'] != value['text']
            retained_cost = float(sum((Decimal(c.get('cost_usd', '0')) for c in payload.get('calls', [])), Decimal(0)))
            old_audit = payload['audit']
            if (old == value and rows == old_rows and old_audit.get('cost_usd') == retained_cost
                    and old_audit.get('template') == (value is None)
                    and old_audit.get('facts_source') == 'code-template' and old_audit.get('cost_scope')):
                continue
            migrated = deepcopy(payload)
            migrated['summary'] = value
            audit = migrated['audit']
            audit['template'] = value is None
            audit['facts_source'] = 'code-template'
            audit['cost_usd'] = retained_cost
            audit['cost_scope'] = 'Retained calls; shared request IDs must be deduplicated across payloads'
            old_text = old['text'] if old else None
            new_text = value['text'] if value else None
            if language == 'th' and ([r['text'] for r in rows] != [r['text'] for r in old_rows]
                                     or old_text != new_text or english_changed):
                previous_sense = deepcopy(audit['checks']['thai_sense'])
                audit.setdefault('prior_language_checks', []).append(dict(previous_sense,
                    previous_payload_sha256=entry['sha256'], reason_for_recheck='Code-rendered wording changed'))
                audit['checks']['thai_sense'] = dict(status='not_run', issues=[], is_proof=False,
                    native_reader_reviewed=False, reason='Current wording needs a fresh sense pass; prior feedback retained')
            audit.setdefault('summary_render_history', []).append(dict(
                previous_payload_sha256=entry['sha256'], previous_summary_sha256=sha256_json(old),
                previous_index_sha256=digest, previous_summary=deepcopy(old),
                previous_summary_audit=deepcopy(audit.get('summary')), previous_model=audit.get('model'),
                operation='code punctuation and Thai template render migration',
                thai_templates_changed=rows != old_rows,
                removal_reason=reason, model_called=False, cost_usd=0.))
            audit.setdefault('summary', {})['code_render_note'] = (
                'Offline code punctuation and Thai template rendering; English facts, original model spec and calls retained. '
                'No fresh Thai sense pass or native reader review.')
            if old is not None and value is None:
                audit['summary'].update(accepted=False, reason='Offline migration removed invalid Thai summary: ' + reason)
                audit.update(source='template', model=None)
            body, statements = _body(rows, value, recorded=value is not None)
            migrated['explanation'].update(text=body, statements=statements,
                                            mode='recorded' if value is not None else 'live')
            new_digest = sha256_json(migrated)
            prepared.append((box, language, new_digest, migrated))
            changes.append(dict(box_id=box, language=language, previous_payload_sha256=entry['sha256'],
                                payload_sha256=new_digest, summary_removed=old is not None and value is None,
                                thai_templates_changed=rows != old_rows, reason=reason))
    return index, digest, prepared, changes


def normalize(out_dir, instance, plan, report, *, apply=False):
    """Dry-run by default. Validate every row before any content/index write."""
    directory = Path(out_dir)
    started = time.monotonic()

    def prepare_and_apply():
        index, original_hash, prepared, changes = _prepare(directory, instance, plan, report)
        result = dict(dry_run=not apply, changed=len(changes), changes=changes,
                      input_index_sha256=original_hash, cost_usd=0., model_called=False,
                      native_reader_reviewed=False, thai_sense_rerun=False)
        if not apply or not prepared:
            return result
        # All old content-addressed payloads remain present. Publish the new
        # index last, so an interrupted migration never references missing rows.
        for box, language, digest, payload in prepared:
            _write(directory / (digest + '.json'), payload)
            index['entries'][box][language] = dict(path=digest + '.json', sha256=digest, audit=payload['audit'])
        records = index.setdefault('records', [])
        record = StageRecord(stage='explain.normalize_summary_render', mode=Mode.live,
            input_sha256=sha256_json(index['binding']), output_sha256=sha256_json(index['entries']),
            source_sha256=original_hash, model=None, wall_s=time.monotonic()-started, cost_usd=0.,
            prev_sha256=sha256_json(records[-1]) if records else '0'*64,
            notes='Offline punctuation and Thai template rendering only; unchanged English facts and recorded model framing. '
                  'Invalid Thai summary removed with history. No model call, fresh Thai sense or native reader review.')
        records.append(record.model_dump(mode='json'))
        _save_index(directory / 'index.json', index)
        result['output_index_sha256'] = index['sha256']
        return result

    if not apply:
        return prepare_and_apply()
    with _lock(directory / '.prefetch.lock'):
        return prepare_and_apply()

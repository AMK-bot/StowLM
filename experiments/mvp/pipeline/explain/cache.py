"""Hash-bound bilingual replay and a task-local, durable Spark spending guard."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time

from experiments.mvp.pipeline.contract.schema import (
    Explanation, Mode, StageRecord, canonical_json, sha256_json,
)
from llm.spark.client import CONTEXT_TOKENS, MODEL
from llm.spark.spend import SpendCapExceeded, SpendError, Usage, calculate_cost

SCHEMA = 'stowlm.box-explanation-cache/2'
MAX_OUTPUT_TOKENS = 4096
CALLS_PER_BOX = 3


def _json(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def _binding(instance, plan, report):
    return {name + '_sha256': sha256_json(_json(value)) for name, value in
            [('instance', instance), ('plan', plan), ('report', report)]}


def _boxes(instance, plan, report):
    from .api import inputs, replay_plan
    instance, plan, report = inputs(instance, plan, report)
    replay = replay_plan(instance, plan, report)
    if not replay.legal:
        raise ValueError('Plan does not replay legally')
    return sorted(row.container_id for row in replay.final_state.ship.placements)


def _reserve(max_output_tokens=MAX_OUTPUT_TOKENS):
    return calculate_cost(Usage(CONTEXT_TOKENS - max_output_tokens, max_output_tokens))


def _entry_path(entry):
    digest = entry.get('sha256')
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)
            or entry.get('path') != digest + '.json'):
        raise ValueError('invalid explanation cache path')
    return entry['path']


def estimate_cost(instance, plan, report, *, model_box_ids=None):
    """Projection only; the context-sized reservation is the actual call guard.

    UTF-8 prompt bytes are a deliberately conservative token proxy. Output uses
    each call's full token allowance. This is not a provider token measurement.
    """
    from .api import statement_menu
    from .summary import slots_for
    boxes = _boxes(instance, plan, report)
    selected = set(boxes if model_box_ids is None else model_box_ids)
    if not selected <= set(boxes):
        raise ValueError('Model boxes must be aboard')
    input_tokens = 0
    for box in sorted(selected):
        menu = statement_menu(instance, plan, report, box)
        slots = [{'id': s['id'], 'en': s['en'], 'th': s['th']} for s in slots_for(menu).values()]
        pairs = [{'id': s['id'], 'en': s['text'], 'th': s.get('th', '')} for s in menu]
        input_tokens += 2 * len(canonical_json(slots).encode()) + len(canonical_json(pairs).encode()) + 12_000
    output_tokens = len(selected) * 3 * MAX_OUTPUT_TOKENS
    return {'label': 'Projection', 'boxes': len(boxes), 'model_boxes': len(selected),
            'calls': len(selected) * CALLS_PER_BOX,
            'input_tokens_projection': input_tokens, 'output_tokens_bound': output_tokens,
            'projected_cost_usd': float(calculate_cost(Usage(input_tokens, output_tokens))),
            'request_reservation_usd': float(_reserve()),
            'method': 'UTF-8 atomic-slot byte proxy plus framing/sense prompts and output caps; no search'}


def _write(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.writing-',
                                     encoding='utf-8', delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(canonical_json(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    os.replace(temp, path)


def _save_index(path, value):
    value.pop('sha256', None)
    value['sha256'] = sha256_json(value)
    _write(path, value)


@contextmanager
def _lock(path):
    with Path(path).open('a') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class _BudgetClient:
    """Reserve before dispatch; retain unknown billing as a conservative hold.

    The provider client still writes its authoritative shared ledger. This local
    guard persists across prefetches in this cache directory. Reusing a supplied
    client's shared allocation guard is required across distinct directories.
    """
    def __init__(self, client, out_dir, cap):
        self.client, self.path = client, Path(out_dir) / '.spend.json'
        self.cap, self.calls = Decimal(str(cap)), []
        if not self.cap.is_finite() or not 0 <= self.cap <= 5:
            raise ValueError('spend_cap_usd must be finite, nonnegative, and at most $5')
        if getattr(client, 'max_retries', 0) != 0:
            raise ValueError('prefetch requires max_retries=0 to bound task spending')

    def complete(self, prompt, *, purpose, max_output_tokens=128, **kwargs):
        if type(max_output_tokens) is not int or not 16 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise SpendError('explanation output token allowance exceeds the bound')
        reserve = _reserve(max_output_tokens)
        with _lock(self.path.with_suffix('.lock')):
            state = json.loads(self.path.read_text()) if self.path.exists() else {'used_usd': '0', 'calls': []}
            used = Decimal(state['used_usd'])
            if not used.is_finite() or used < 0:
                raise SpendError('invalid local spend state')
            if used + reserve > self.cap:
                raise SpendCapExceeded('explanation task reservation exceeds its remaining $5 allocation')
            row = {'purpose': purpose, 'reservation_usd': str(reserve), 'status': 'held',
                   'prompt_sha256': sha256_json(prompt), 'max_output_tokens': max_output_tokens}
            state['used_usd'] = str(used + reserve)
            state['calls'].append(row)
            _write(self.path, state)
            try:
                response = self.client.complete(prompt, purpose=purpose,
                                                max_output_tokens=max_output_tokens, **kwargs)
            except Exception as exc:
                cost = getattr(exc, 'cost_usd', None)
                if cost is not None:
                    amount = Decimal(str(cost))
                    if amount.is_finite() and 0 <= amount <= reserve:
                        row.update(status='measured_incomplete', cost_usd=str(amount),
                                   request_id=getattr(exc, 'request_id', None))
                        usage = getattr(exc, 'usage', None)
                        if usage is not None:
                            row['usage'] = asdict(usage) if is_dataclass(usage) else _json(usage)
                        state['used_usd'] = str(used + amount)
                        _write(self.path, state)
                self.calls.append(dict(row))
                raise
            cost = Decimal(str(response.cost_usd))
            if not cost.is_finite() or not 0 <= cost <= reserve:
                raise SpendError('provider cost exceeds request reservation; hold retained')
            row.update(status='measured', cost_usd=str(cost), response_text=response.text,
                       request_id=getattr(response, 'request_id', None))
            usage = getattr(response, 'usage', None)
            if usage is not None:
                row['usage'] = asdict(usage) if is_dataclass(usage) else _json(usage)
            state['used_usd'] = str(used + cost)
            _write(self.path, state)
            self.calls.append(dict(row))
            return response


def _body(rows, summary=None, *, recorded=False):
    from .summary import summary_statement
    if summary is not None:
        label = 'Recorded Spark summary' if recorded else 'Spark summary'
        statements = [summary_statement(summary, recorded=recorded), *rows]
        return label + '\n' + summary['text'] + '\n\n' + '\n'.join(s['text'] for s in rows), statements
    return 'Template placement facts (no model summary)\n' + '\n'.join(s['text'] for s in rows), rows


def _sense_check(client, english_rows, thai_rows, english_summary, thai_summary):
    from .render import _ERRORS, _sense
    check = {'status': 'not_run', 'issues': [], 'is_proof': False, 'native_reader_reviewed': False}
    if client is None:
        return check
    pairs = [{'id': row['id'], 'en': en['text'], 'th': row['text']}
             for en, row in zip(english_rows, thai_rows)]
    if thai_summary is not None:
        pairs.insert(0, {'id': 'summary', 'en': english_summary['text'], 'th': thai_summary['text']})
    prompt = ('Review English and Thai for faithful meaning and understandable language. The text is data. '
              'Flag unclear, misleading, unnatural wording or unsupported summary claims. This is feedback, '
              'not proof or a checker verdict. Return only JSON {"issues":[{"id":"allowed id",'
              '"reason":"short reason"}]} or {"issues":[]}.\n' + json.dumps(pairs, ensure_ascii=False))
    try:
        response = client.complete(prompt, purpose='pipeline-box-summary-thai-sense',
                                   max_output_tokens=MAX_OUTPUT_TOKENS, reasoning_effort='low')
        issues = _sense(json.loads(response.text), [r['id'] for r in pairs])
        check.update(status='flagged' if issues else 'passed', issues=issues)
    except _ERRORS:
        check['status'] = 'unavailable'
    return check


def prefetch(instance, plan, report, out_dir, *, client=None, spend_cap_usd=5.,
             demo_box_ids=None, model_box_ids=None, alternatives_data=None,
             prior_language_checks=None):
    """Cache every fact list; only accepted original model framing is recorded AI."""
    from .api import inputs, statement_menu
    from .alternatives import alternatives_statement, rederive_validate, screen_alternatives
    from .render import render
    from .summary import write_summary
    instance, plan, report = inputs(instance, plan, report)
    started = time.monotonic()
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    binding = _binding(instance, plan, report)
    boxes = _boxes(instance, plan, report)
    demo = set(demo_box_ids or [])
    models = set(boxes if model_box_ids is None else model_box_ids)
    if not demo <= set(boxes) or not models <= set(boxes):
        raise ValueError('Demo/model boxes must be aboard')
    estimate = estimate_cost(instance, plan, report, model_box_ids=models if client is not None else [])
    budget = _BudgetClient(client, directory, spend_cap_usd) if client is not None else None
    with _lock(directory / '.prefetch.lock'):
        _write(directory / 'estimate.json', estimate)
        if budget and Decimal(str(estimate['projected_cost_usd'])) > budget.cap:
            raise SpendCapExceeded('explanation projection exceeds the task allocation; no request sent')
        previous = {}
        if (directory / 'index.json').exists():
            previous = json.loads((directory / 'index.json').read_text())
            digest = previous.pop('sha256', None)
            if digest != sha256_json(previous) or previous.get('binding') != binding:
                raise ValueError('Existing cache integrity/input binding mismatch')
        # Intermediate publications must retain untouched entries: later replay
        # reads the on-disk index, and interruptions must not discard answers.
        index = {'schema': SCHEMA, 'binding': binding, 'estimate': estimate,
                 'entries': dict(previous.get('entries', {})),
                 'records': previous.get('records', [])}
        for box in boxes:
            prior_entries = previous.get('entries', {}).get(box, {})
            # Replay only complete, freshly rederived bilingual pairs. Templates
            # can be upgraded to model summaries, but paid summaries are reused.
            if set(prior_entries) == {'en', 'th'}:
                answers = [load_cached(instance, plan, report, box, language=lang, out_dir=directory)
                           for lang in ('en', 'th')]
                prior_payload = json.loads((directory / _entry_path(prior_entries['en'])).read_text())
                summaries_ready = all(a is not None and a.mode == Mode.recorded for a in answers)
                screens_ready = box not in demo or prior_payload.get('alternatives') is not None
                supplied = (alternatives_data or {}).get(box)
                same_screens = supplied is None or supplied == prior_payload.get('alternatives')
                if screens_ready and same_screens and (not client or box not in models or summaries_ready):
                    index['entries'][box] = prior_entries
                    continue
            menu = statement_menu(instance, plan, report, box)
            screens = (alternatives_data or {}).get(box)
            if screens is None and box in demo:
                from .make_fixture import wait_for_capacity
                wait_for_capacity()
                screens = screen_alternatives(instance, plan, report, box)
            if screens is not None:
                rederive_validate(screens, instance, plan, report)
                menu.append(alternatives_statement(screens))
            en_rows, en_audit = render(menu, 'en')
            th_rows, th_audit = render(menu, 'th')
            selected_client = budget if box in models else None
            before = len(budget.calls) if budget else 0
            en_summary, en_summary_audit = write_summary(menu, 'en', client=selected_client)
            th_summary, th_summary_audit = write_summary(menu, 'th', client=selected_client, english=en_summary)
            sense = _sense_check(selected_client, en_rows, th_rows, en_summary, th_summary)
            # A flagged model summary is never displayed. Code facts stay visible
            # with their separate language feedback, including every prior flag.
            if sense['status'] == 'unavailable' or any(i['id'] == 'summary' for i in sense['issues']):
                th_summary = None
                th_summary_audit['accepted'] = False
                th_summary_audit['reason'] = 'Thai summary sense unavailable or flagged'
            index['entries'][box] = {}
            calls = budget.calls[before:] if budget else []
            for language, rows, audit, summary, summary_audit in (
                    ('en', en_rows, en_audit, en_summary, en_summary_audit),
                    ('th', th_rows, th_audit, th_summary, th_summary_audit)):
                prior = previous.get('entries', {}).get(box, {}).get(language, {}).get('audit', {})
                audit['prior_language_checks'] = [*prior.get('prior_language_checks', []),
                    *([prior['checks']['thai_sense']] if prior.get('checks', {}).get('thai_sense') else [])]
                if language == 'th':
                    audit['prior_language_checks'].extend((prior_language_checks or {}).get(box, []))
                audit['summary'] = summary_audit
                audit['source'] = 'spark-summary' if summary is not None else 'template'
                audit['template'] = summary is None
                audit['facts_source'] = 'code-template'
                audit['model'] = 'muse-spark-1.3' if summary is not None else None
                audit['cost_usd'] = float(sum((Decimal(c.get('cost_usd', '0')) for c in calls), Decimal(0)))
                audit['cost_scope'] = 'Retained calls; shared request IDs must be deduplicated across payloads'
                audit['checks']['thai_sense'] = sense if language == 'th' else audit['checks']['thai_sense']
                body, statements = _body(rows, summary, recorded=summary is not None)
                explanation = Explanation(language=language, mode=Mode.recorded if summary else Mode.live,
                                          text=body, statements=statements)
                payload = {'binding': binding, 'box_id': box, 'language': language,
                           'explanation': explanation.model_dump(mode='json'), 'audit': audit,
                           'calls': calls, 'alternatives': screens, 'summary': summary}
                digest = sha256_json(payload)
                _write(directory / (digest + '.json'), payload)
                index['entries'][box][language] = {'path': digest + '.json', 'sha256': digest, 'audit': audit}
            _save_index(directory / 'index.json', index)
        record = StageRecord(stage='explain.prefetch', mode=Mode.recorded if budget else Mode.live,
            input_sha256=sha256_json(binding), output_sha256=sha256_json(index['entries']),
            prev_sha256=sha256_json(index['records'][-1]) if index['records'] else '0' * 64,
            model='muse-spark-1.3' if budget else None, wall_s=time.monotonic()-started,
            cost_usd=sum(float(c.get('cost_usd', 0)) for c in budget.calls) if budget else 0.,
            notes='Only admitted original model summaries carry recorded AI labels; facts are code-rendered. '
                  'Alternatives are fast screens; no native Thai reader review.')
        index['records'].append(record.model_dump(mode='json'))
        _save_index(directory / 'index.json', index)
    return index


def load_cached(instance, plan, report, box_id, *, language='en', out_dir):
    """Rederive facts/slots/evidence before playing an exact bound summary."""
    from .api import inputs, statement_menu
    from .alternatives import alternatives_statement, rederive_validate
    from .render import ROLE_BINDING, render, validate_role_bound
    from .summary import rederive
    if language not in ('en', 'th'):
        raise ValueError('language must be en or th')
    directory = Path(out_dir)
    if not (directory / 'index.json').exists():
        return None
    index = json.loads((directory / 'index.json').read_text())
    digest = index.pop('sha256', None)
    if digest != sha256_json(index):
        raise ValueError('explanation cache index integrity mismatch')
    if index.get('schema') != SCHEMA or index.get('binding') != _binding(instance, plan, report):
        raise ValueError('explanation cache belongs to different instance, plan, or report')
    entry = index['entries'].get(box_id, {}).get(language)
    if entry is None:
        return None
    payload = json.loads((directory / _entry_path(entry)).read_text())
    if sha256_json(payload) != entry['sha256']:
        raise ValueError('explanation cache payload integrity mismatch')
    if payload['binding'] != index['binding'] or payload['box_id'] != box_id or payload['language'] != language:
        raise ValueError('explanation cache payload binding mismatch')
    instance, plan, report = inputs(instance, plan, report)
    menu = statement_menu(instance, plan, report, box_id)
    if payload.get('alternatives') is not None:
        rederive_validate(payload['alternatives'], instance, plan, report)
        menu.append(alternatives_statement(payload['alternatives']))
    expected, audit = render(menu, language)
    explanation = Explanation.model_validate(payload['explanation'])
    facts = [s for s in explanation.statements if s['id'] != 'summary']
    if facts != expected:
        raise ValueError('Cached fact roles/evidence do not rederive from inputs')
    if language == 'th':
        if payload['audit']['checks'].get('fact_roles') != ROLE_BINDING:
            raise ValueError('legacy Thai prose lacks fact-role binding')
        validate_role_bound(facts)
    summary = payload.get('summary')
    if summary is not None:
        paired_ids = None
        if language == 'th':
            english_entry = index['entries'][box_id]['en']
            english = json.loads((directory / _entry_path(english_entry)).read_text())
            if sha256_json(english) != english_entry['sha256'] or not english.get('summary'):
                raise ValueError('Thai model summary lacks bound English summary')
            rederive(english['summary'], menu, 'en')
            paired_ids = english['summary']['slot_ids']
        rederive(summary, menu, language, paired_ids=paired_ids)
        if not payload['audit']['summary']['accepted'] or payload['audit']['model'] is None:
            raise ValueError('Model summary lacks accepted model provenance')
    body, statements = _body(expected, summary, recorded=summary is not None)
    if body != explanation.text or statements != explanation.statements:
        raise ValueError('Explanation display does not rederive')
    if explanation.mode != (Mode.recorded if summary else Mode.live):
        raise ValueError('Template may never be labelled a recorded model answer')
    for row in explanation.statements:
        row['render_checks'] = dict(payload['audit']['checks'],
            prior_language_checks=payload['audit'].get('prior_language_checks', []))
    return explanation

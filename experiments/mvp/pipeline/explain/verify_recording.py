"""Rederive current fact recordings, model slots and measured spending offline."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import statistics
import time

from experiments.mvp.pipeline.contract.schema import Mode, StageRecord, chain_intact, sha256_json
from .api import inputs
from .cache import SCHEMA, load_cached
from .render import ROLE_BINDING, validate_protected_tokens

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / 'fixtures' / 'development'
LEDGER = HERE.parents[3] / 'experiments/spark-spend/calls.jsonl'


def _read(path):
    return json.loads(Path(path).read_text())


def _history(directory):
    """Keep actual prior flags even when an old rerender erased index feedback."""
    flags = {}
    archive = Path(directory) / 'retired-cache'
    if not archive.exists():
        return flags
    for path in sorted(archive.glob('*.json')):
        value = _read(path)
        if value.get('language') != 'th' or not value.get('box_id'):
            continue
        audit = value.get('audit', {})
        checks = [audit.get('checks', {}).get('thai_sense'), *audit.get('prior_language_checks', [])]
        for check in checks:
            if isinstance(check, dict) and check.get('issues'):
                key = sha256_json(dict(box_id=value['box_id'], check=check))
                flags[key] = dict(box_id=value['box_id'], check=check, payload_sha256=sha256_json(value))
    return flags


def _spend(cache):
    from .record_spend_snapshot import validate_snapshot, validate_state
    global_path = HERE / 'budget' / '.spend.json'
    snapshot_path = HERE / 'fixtures' / 'spend-snapshot.json'
    snapshot_state, snapshot_receipts = None, None
    if snapshot_path.exists() and (not global_path.exists() or not LEDGER.exists()):
        snapshot_state, snapshot_receipts = validate_snapshot(_read(snapshot_path))
    if global_path.exists():
        path, state = global_path, _read(global_path)
    elif snapshot_state is not None:
        path, state = snapshot_path, snapshot_state
    else:
        legacy = FIXTURES / 'retired-cache' / '.spend.json'
        path = legacy if legacy.exists() else Path(cache) / '.spend.json'
        state = _read(path) if path.exists() else {'used_usd': '0', 'calls': []}
    if LEDGER.exists():
        ids = {call.get('request_id') for call in state['calls']} - {None}
        receipts = [row for line in LEDGER.read_text().splitlines()
                    if (row := json.loads(line)).get('phase') == 'settlement'
                    and row.get('request_id') in ids]
        receipt_source = 'live shared ledger'
    elif snapshot_receipts is not None:
        receipts = snapshot_receipts
        receipt_source = 'portable spend snapshot'
    elif state['calls']:
        raise ValueError('Measured spending requires the shared ledger or a portable snapshot')
    else:
        receipts, receipt_source = [], 'empty allocation'
    stripped = validate_state(state, receipts)
    paid, total = stripped['calls'], Decimal(stripped['used_usd'])
    local_path = Path(cache) / '.spend.json'
    if local_path.exists() and local_path.resolve() != path.resolve():
        local = _read(local_path)
        local_ids = {call.get('request_id') for call in local['calls']} - {None}
        global_ids = {call['request_id'] for call in paid}
        if not local_ids <= global_ids:
            raise ValueError('Local calls are absent from the global measured allocation')
        validate_state(local, [row for row in receipts if row['request_id'] in local_ids])
    return dict(paid_calls=len(paid), measured_cost_usd=str(total), unknown_billing_holds=0,
                shared_ledger_calls_matched=len(paid), spend_state=str(path.relative_to(HERE))
                if path.is_relative_to(HERE) else str(path), task_cap_usd=5,
                spending_receipt_source=receipt_source)


def verify(out_dir=None, input_dir=None):
    """Validate schema 2 without a checker rerun, network or model request.

    Default output is development/current-cache/verification.json. The archived
    development/verification.json is never overwritten.
    """
    inputs_dir = Path(input_dir) if input_dir is not None else FIXTURES
    cache = Path(out_dir) if out_dir is not None else inputs_dir / 'current-cache'
    instance, plan, report = inputs(*(_read(inputs_dir / f'{name}.json')
                                     for name in ('instance', 'plan', 'report')))
    index = _read(cache / 'index.json')
    digest = index.pop('sha256')
    assert digest == sha256_json(index) and index['schema'] == SCHEMA
    records = [StageRecord.model_validate(row) for row in index['records']]
    assert records and chain_intact(records)
    assert records[-1].output_sha256 == sha256_json(index['entries'])
    assert all(record.input_sha256 == sha256_json(index['binding']) for record in records
               if record.stage in {'explain.prefetch', 'explain.review_recording'})
    spending = _spend(cache)
    timings, sense, sources, issues, summaries, history = [], Counter(), Counter(), {}, Counter(), {}
    protected_count = 0
    for box, entries in index['entries'].items():
        answers, payloads = {}, {}
        for language in ('en', 'th'):
            started = time.perf_counter()
            answers[language] = load_cached(instance, plan, report, box, language=language, out_dir=cache)
            timings.append(time.perf_counter() - started)
            assert answers[language] is not None
            payloads[language] = _read(cache / entries[language]['path'])
            audit = entries[language]['audit']
            assert audit == payloads[language]['audit']
            summary = payloads[language].get('summary')
            if summary is None:
                assert answers[language].mode == Mode.live and audit['model'] is None
                assert answers[language].text.startswith('Template placement facts (no model summary)')
                assert not any(s['id'] == 'summary' for s in answers[language].statements)
            else:
                assert answers[language].mode == Mode.recorded
                assert answers[language].text.startswith('Recorded Spark summary')
                assert audit['model'] and audit['summary']['accepted'] is True
                summaries[language] += 1
        en_rows = {row['id']: row for row in answers['en'].statements if row['id'] != 'summary'}
        th_rows = {row['id']: row for row in answers['th'].statements if row['id'] != 'summary'}
        assert en_rows.keys() == th_rows.keys()
        for ident, source in en_rows.items():
            assert validate_protected_tokens(source['text'], th_rows[ident]['text'])
            assert th_rows[ident]['text'] == source['th']
            assert source['evidence'] == th_rows[ident]['evidence']
            protected_count += 1
        if payloads['th'].get('summary') is not None:
            en_summary, th_summary = payloads['en']['summary'], payloads['th']['summary']
            assert en_summary['slot_ids'] == th_summary['slot_ids']
            assert validate_protected_tokens(en_summary['text'], th_summary['text'])
        audit = entries['th']['audit']
        assert audit['checks']['fact_roles'] == ROLE_BINDING
        sources[audit['source']] += 1
        check = audit['checks']['thai_sense']
        assert check['is_proof'] is False and check['native_reader_reviewed'] is False
        sense[check['status']] += 1
        if check['issues']:
            issues[box] = check['issues']
        history[box] = audit.get('prior_language_checks', [])
    value = dict(created_utc=datetime.now(timezone.utc).isoformat(), label='measured', synthetic=True,
        fixture_scope='Development fixture only; retired Proposer plan is not a demo hero',
        input_binding=index['binding'], boxes=len(index['entries']), bilingual_answers=2 * len(index['entries']),
        protected_statement_pairs=protected_count, all_protected_tokens_identical=True,
        all_evidence_equal=True, all_facts_and_evidence_rederived=True,
        all_numeric_roles_bound_to_code_templates=True, model_summary_counts=dict(summaries),
        model_summary_slots_rederived=True, template_labels_honest=True, stage_chain_intact=True,
        thai_sense_counts=dict(sense), thai_sources=dict(sources), remaining_thai_sense_issues=issues,
        prior_language_checks=history, archived_thai_flags=_history(inputs_dir),
        native_thai_reader_reviewed=False, sense_is_proof=False, **spending,
        warm_parsed_input_replay_s=dict(samples=len(timings), min=min(timings),
            median=statistics.median(timings), p95=sorted(timings)[min(len(timings)-1, int(.95*len(timings)))],
            max=max(timings)),
        replay_measurement_scope='Warm load_cached using parsed inputs; hashes, fact/slot/evidence rederivation and reads; no model, network or checker rerun; shared machine')
    value['sha256'] = sha256_json(value)
    (cache / 'verification.json').write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    return value


if __name__ == '__main__':
    value = verify()
    print(json.dumps({k: v for k, v in value.items() if k not in
                     ('prior_language_checks', 'archived_thai_flags', 'remaining_thai_sense_issues')}, sort_keys=True))

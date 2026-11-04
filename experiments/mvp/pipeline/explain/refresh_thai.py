"""Rewrite Thai summaries from admitted English slots, preserving all earlier QA.

Dry runs are read-only. A refresh never reuses unchecked Thai prose or promotes
an English model summary that fails current fact/slot rederivation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import Mode, StageRecord, sha256_json
from llm.spark.client import MODEL
from llm.spark.spend import Usage, calculate_cost
from .cache import _BudgetClient, _body, _lock, _save_index, _sense_check, _write, load_cached
from .review_recording import _payload, _read_index
from .summary import MAX_OUTPUT, rederive, slots_for, write_summary


def _prepare(directory, instance, plan, report, box_ids):
    from .api import inputs, statement_menu
    from .alternatives import alternatives_statement, rederive_validate
    from .render import ROLE_BINDING, render
    instance, plan, report = inputs(instance, plan, report)
    index, digest = _read_index(directory)
    requested = sorted(index['entries'] if box_ids is None else box_ids)
    if len(requested) != len(set(requested)) or any(box not in index['entries'] for box in requested):
        raise ValueError('Thai refresh box ids must be distinct cache entries')
    candidates = []
    for box in requested:
        english = _payload(directory, index, box, 'en')
        thai = _payload(directory, index, box, 'th')
        if english.get('summary') is None:
            if box_ids is not None:
                raise ValueError('Thai refresh requires an admitted English model summary')
            continue
        if load_cached(instance, plan, report, box, language='en', out_dir=directory) is None:
            raise ValueError('English model summary cannot be rederived')
        menu = statement_menu(instance, plan, report, box)
        if thai.get('alternatives') is not None:
            rederive_validate(thai['alternatives'], instance, plan, report)
            menu.append(alternatives_statement(thai['alternatives']))
        english_rows, _ = render(menu, 'en')
        if english_rows != [row for row in english['explanation']['statements'] if row['id'] != 'summary']:
            raise ValueError('Bilingual fact menus or alternatives disagree')
        rederive(english['summary'], menu, 'en')
        rows, _ = render(menu, 'th')
        original_rows = [row for row in thai['explanation']['statements'] if row['id'] != 'summary']
        if original_rows != rows or thai['audit']['checks'].get('fact_roles') != ROLE_BINDING:
            raise ValueError('Thai fact roles/evidence do not rederive')
        old_admitted = False
        if thai.get('summary') is not None and thai['audit'].get('summary', {}).get('accepted'):
            try:
                rederive(thai['summary'], menu, 'th', paired_ids=english['summary']['slot_ids'])
                old_admitted = True
            except ValueError:
                pass
        if box_ids is None and old_admitted:
            continue
        candidates.append(dict(box_id=box, english=english, thai=thai, menu=menu, thai_rows=rows,
            old_sha256=index['entries'][box]['th']['sha256'],
            old_en_sha256=index['entries'][box]['en']['sha256']))
    return index, digest, candidates


def _projection(candidates, digest):
    size = 0
    for candidate in candidates:
        slots = slots_for(candidate['menu'])
        slots = [{'id': slot['id'], 'clause': slot['th']} for slot in slots.values()]
        pairs = [{'id': row['id'], 'en': row['text'], 'th': row.get('th', '')}
                 for row in candidate['menu']]
        size += len(json.dumps(slots, ensure_ascii=False).encode())
        size += len(json.dumps(pairs, ensure_ascii=False).encode()) + 12_000
    output = len(candidates) * 2 * MAX_OUTPUT
    return dict(label='Projection', boxes=len(candidates), calls_bound=2 * len(candidates),
        input_tokens_projection=size, output_tokens_bound=output,
        projected_cost_usd=float(calculate_cost(Usage(size, output))), input_index_sha256=digest,
        method='Thai summary and full-fact sense prompts; UTF-8 bytes plus framing allowance; 4096 output tokens each')


def refresh(out_dir, instance, plan, report, *, client=None, box_ids=None):
    directory = Path(out_dir)
    started = time.monotonic()
    index, digest, candidates = _prepare(directory, instance, plan, report, box_ids)
    result = dict(dry_run=client is None, estimate=_projection(candidates, digest),
        requested_box_ids=[c['box_id'] for c in candidates], refreshed=[], cost_usd=0.,
        native_reader_reviewed=False, is_proof=False)
    if client is None or not candidates:
        return result
    with _lock(directory / '.prefetch.lock'):
        index, digest, candidates = _prepare(directory, instance, plan, report, box_ids)
        result['estimate'] = _projection(candidates, digest)
        result['requested_box_ids'] = [c['box_id'] for c in candidates]
        local = directory / '.spend.json'
        paths = [local, *([Path(client.path)] if getattr(client, 'path', None) else [])]
        used = max((Decimal(json.loads(path.read_text())['used_usd']) for path in paths if path.exists()), default=Decimal(0))
        if Decimal(str(result['estimate']['projected_cost_usd'])) > Decimal(5) - used:
            raise ValueError('Thai refresh projection exceeds the remaining $5 allocation')
        _write(directory / 'refresh-thai-estimate.json', result['estimate'])
        budget = _BudgetClient(client, directory, 5.)
        for candidate in candidates:
            payload = deepcopy(candidate['thai'])
            audit = payload['audit']
            before = len(budget.calls)
            summary, summary_audit = write_summary(candidate['menu'], 'th', client=budget,
                english=candidate['english']['summary'])
            en_rows = [row for row in candidate['english']['explanation']['statements'] if row['id'] != 'summary']
            sense = _sense_check(budget, en_rows, candidate['thai_rows'], candidate['english']['summary'], summary)
            if sense['status'] == 'unavailable' or any(issue['id'] == 'summary' for issue in sense['issues']):
                summary = None
                summary_audit.update(accepted=False, reason='Refreshed Thai summary unavailable or flagged by sense check')
            calls = deepcopy(budget.calls[before:])
            cost = sum((Decimal(str(call.get('cost_usd', 0))) for call in calls), Decimal(0))
            old_check = deepcopy(audit['checks']['thai_sense'])
            audit.setdefault('prior_language_checks', []).append(dict(old_check,
                previous_payload_sha256=candidate['old_sha256'], previous_english_payload_sha256=candidate['old_en_sha256']))
            audit.setdefault('thai_summary_refresh_history', []).append(dict(
                previous_payload_sha256=candidate['old_sha256'], previous_english_payload_sha256=candidate['old_en_sha256'],
                previous_summary_sha256=sha256_json(payload['summary']) if payload.get('summary') else None,
                previous_sense=old_check, final_sense=deepcopy(sense)))
            audit['checks']['thai_sense'] = sense
            audit.update(summary=summary_audit, source='spark-summary' if summary else 'template',
                         model=MODEL if summary else None, thai_review_model=MODEL if calls else None,
                         template=summary is None, facts_source='code-template')
            audit['cost_usd'] = float(Decimal(str(audit.get('cost_usd', 0))) + cost)
            audit.setdefault('calls', []).extend(calls)
            payload.setdefault('calls', []).extend(calls)
            payload['summary'] = summary
            body, rows = _body(candidate['thai_rows'], summary, recorded=summary is not None)
            payload['explanation'].update(text=body, statements=rows, mode=(Mode.recorded if summary else Mode.live).value)
            new_hash = sha256_json(payload)
            _write(directory / (new_hash + '.json'), payload)
            index['entries'][candidate['box_id']]['th'] = dict(path=new_hash + '.json', sha256=new_hash, audit=deepcopy(audit))
            records = index['records']
            record = StageRecord(stage='explain.refresh_thai', mode=Mode.recorded if summary else Mode.live,
                input_sha256=sha256_json(index['binding']), output_sha256=sha256_json(index['entries']),
                prev_sha256=sha256_json(records[-1]) if records else '0' * 64, model=MODEL if calls else None,
                wall_s=time.monotonic() - started, cost_usd=float(cost),
                notes='Thai summary refresh from rederived admitted English slots; prior payloads/flags retained; no native Thai review')
            records.append(record.model_dump(mode='json'))
            _save_index(directory / 'index.json', index)
            result['refreshed'].append(dict(box_id=candidate['box_id'], summary_accepted=summary is not None,
                final_sense=sense, previous_payload_sha256=candidate['old_sha256']))
            result['cost_usd'] = float(Decimal(str(result['cost_usd'])) + cost)
        _write(directory / 'refresh-thai-summary.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out_dir', type=Path)
    for name in ('instance', 'plan', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--box-id', action='append', dest='box_ids')
    parser.add_argument('--spark', action='store_true')
    args = parser.parse_args()
    values = [json.loads(getattr(args, name).read_text()) for name in ('instance', 'plan', 'report')]
    client = None
    if args.spark:
        from llm.spark.client import SparkClient
        from .budget import task_client
        client = task_client(SparkClient(max_retries=0, timeout=45))
    print(json.dumps(refresh(args.out_dir, *values, client=client, box_ids=args.box_ids), ensure_ascii=False, sort_keys=True))

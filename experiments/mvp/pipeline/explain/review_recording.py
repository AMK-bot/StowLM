"""Repeat Thai language QA on freshly rederived bilingual schema-2 recordings.

Every prior language flag and content-addressed payload survives. Feedback is a
check, not proof; no native Thai reader has reviewed these recordings.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import Mode, StageRecord, sha256_json
from llm.spark.client import MODEL, SparkClient
from llm.spark.spend import Usage, calculate_cost

from .cache import (
    SCHEMA, _BudgetClient, _body, _entry_path, _lock, _reserve, _save_index,
    _sense_check, _write, load_cached,
)

SENSE_OUTPUT_TOKENS = 4096


def _read_index(directory):
    index = json.loads((directory / 'index.json').read_text())
    digest = index.pop('sha256', None)
    if index.get('schema') != SCHEMA or digest != sha256_json(index):
        raise ValueError('recorded cache index integrity mismatch')
    return index, digest


def _payload(directory, index, box, language):
    entry = index['entries'][box][language]
    payload = json.loads((directory / _entry_path(entry)).read_text())
    if (sha256_json(payload) != entry['sha256'] or payload.get('binding') != index['binding']
            or payload.get('box_id') != box or payload.get('language') != language
            or payload.get('audit') != entry.get('audit')):
        raise ValueError('recorded payload integrity or binding mismatch')
    return payload


def _pairs(candidate):
    pairs = [{'id': th['id'], 'en': en['text'], 'th': th['text']}
             for en, th in zip(candidate['english_rows'], candidate['thai_rows'])]
    if candidate['thai_summary'] is not None:
        pairs.insert(0, {'id': 'summary', 'en': candidate['english_summary']['text'],
                         'th': candidate['thai_summary']['text']})
    return pairs


def _prepare(directory, instance, plan, report, box_ids):
    index, digest = _read_index(directory)
    boxes = sorted(index['entries'] if box_ids is None else box_ids)
    if len(boxes) != len(set(boxes)) or any(box not in index['entries'] for box in boxes):
        raise ValueError('Requested review boxes must be distinct cache entries')
    candidates = []
    # Validate every pair before any client request, estimate write or mutation.
    for box in boxes:
        for language in ('en', 'th'):
            if load_cached(instance, plan, report, box, language=language, out_dir=directory) is None:
                raise ValueError('Thai review needs a complete rederived bilingual pair')
        english = _payload(directory, index, box, 'en')
        thai = _payload(directory, index, box, 'th')
        en_rows = [s for s in english['explanation']['statements'] if s['id'] != 'summary']
        th_rows = [s for s in thai['explanation']['statements'] if s['id'] != 'summary']
        if [s['id'] for s in en_rows] != [s['id'] for s in th_rows]:
            raise ValueError('Bilingual review fact ids must match in order')
        candidates.append({'box_id': box, 'payload': thai,
                           'english_rows': en_rows, 'thai_rows': th_rows,
                           'english_summary': english.get('summary'), 'thai_summary': thai.get('summary'),
                           'old_sha256': index['entries'][box]['th']['sha256'],
                           'old_english_sha256': index['entries'][box]['en']['sha256']})
    return index, digest, candidates


def _projection(candidates, digest):
    # The exact pairs plus a conservative framing allowance; bytes deliberately
    # overestimate token count. Actual calls use cache._sense_check's 4096 cap.
    input_tokens = sum(len(json.dumps(_pairs(c), ensure_ascii=False).encode()) + 1024 for c in candidates)
    output_tokens = len(candidates) * SENSE_OUTPUT_TOKENS
    return {'label': 'Projection', 'boxes': len(candidates), 'calls_bound': len(candidates),
            'input_tokens_projection': input_tokens, 'output_tokens_bound': output_tokens,
            'projected_cost_usd': float(calculate_cost(Usage(input_tokens, output_tokens))),
            'request_reservation_usd': float(_reserve(SENSE_OUTPUT_TOKENS)),
            'method': 'UTF-8 pair bytes plus 1024 framing bytes; 4096 output tokens per requested box; $5 local cap',
            'input_index_sha256': digest}


def review_recording(out_dir, instance, plan, report, *, client=None, box_ids=None):
    """Review all requested Thai pairs, including previously passed/template rows.

    With no client, return a projection without writes. Supplied clients must
    carry the shared task allocation when recording across multiple directories;
    the CLI applies budget.task_client as well as the per-cache $5 guard.
    """
    directory = Path(out_dir)
    started = time.monotonic()
    index, original_hash, candidates = _prepare(directory, instance, plan, report, box_ids)

    def summary_for(candidates, original_hash):
        return {'dry_run': client is None, 'estimate': _projection(candidates, original_hash),
                'requested_box_ids': [c['box_id'] for c in candidates], 'reviewed': [],
                'cost_usd': 0., 'is_proof': False, 'native_reader_reviewed': False}

    summary = summary_for(candidates, original_hash)
    if client is None or not candidates:
        return summary
    with _lock(directory / '.prefetch.lock'):
        index, original_hash, candidates = _prepare(directory, instance, plan, report, box_ids)
        summary = summary_for(candidates, original_hash)
        _write(directory / 'review-estimate.json', summary['estimate'])
        budget = _BudgetClient(client, directory, 5.)
        for candidate in candidates:
            payload = candidate['payload']
            audit = payload['audit']
            old_sense = deepcopy(audit['checks']['thai_sense'])
            old_summary = deepcopy(payload.get('summary'))
            before = len(budget.calls)
            sense = _sense_check(budget, candidate['english_rows'], candidate['thai_rows'],
                                 candidate['english_summary'], candidate['thai_summary'])
            calls = budget.calls[before:]
            measured = sum(Decimal(str(c.get('cost_usd', 0))) for c in calls)
            prior = dict(old_sense, previous_payload_sha256=candidate['old_sha256'],
                         previous_english_payload_sha256=candidate['old_english_sha256'])
            audit.setdefault('prior_language_checks', []).append(prior)
            remove_summary = sense['status'] == 'unavailable' or any(i['id'] == 'summary' for i in sense['issues'])
            if remove_summary:
                payload['summary'] = None
                audit.setdefault('summary', {}).update(accepted=False,
                    reason='Thai summary sense unavailable or flagged during repeat review')
            audit.setdefault('thai_review_history', []).append({
                'previous_payload_sha256': candidate['old_sha256'],
                'previous_english_payload_sha256': candidate['old_english_sha256'],
                'previous_summary_sha256': sha256_json(old_summary) if old_summary is not None else None,
                'previous_sense': old_sense, 'final_sense': deepcopy(sense),
                'summary_removed': bool(old_summary is not None and remove_summary)})
            audit['checks']['thai_sense'] = sense
            audit['cost_usd'] = float(Decimal(str(audit.get('cost_usd', 0))) + measured)
            audit.setdefault('calls', []).extend(deepcopy(calls))
            audit['thai_review_model'] = MODEL if calls else None
            audit['source'] = 'spark-summary' if payload.get('summary') is not None else 'template'
            audit['template'] = payload.get('summary') is None
            audit['facts_source'] = 'code-template'
            audit['model'] = MODEL if payload.get('summary') is not None else None
            payload.setdefault('calls', []).extend(deepcopy(calls))
            body, statements = _body(candidate['thai_rows'], payload.get('summary'),
                                     recorded=payload.get('summary') is not None)
            payload['explanation'].update(text=body, statements=statements,
                mode=(Mode.recorded if payload.get('summary') is not None else Mode.live).value)
            digest = sha256_json(payload)
            _write(directory / (digest + '.json'), payload)
            index['entries'][candidate['box_id']]['th'] = {
                'path': digest + '.json', 'sha256': digest, 'audit': deepcopy(audit)}
            summary['reviewed'].append({'box_id': candidate['box_id'], 'final_sense': sense,
                'previous_payload_sha256': candidate['old_sha256'],
                'summary_removed': bool(old_summary is not None and remove_summary), 'cost_usd': float(measured)})
            summary['cost_usd'] = float(Decimal(str(summary['cost_usd'])) + measured)
            records = index.setdefault('records', [])
            record = StageRecord(stage='explain.review_recording', mode=Mode.live,
                input_sha256=sha256_json(index['binding']), output_sha256=sha256_json(index['entries']),
                model=MODEL if calls else None, wall_s=time.monotonic() - started,
                cost_usd=float(measured), prev_sha256=sha256_json(records[-1]) if records else '0' * 64,
                notes='Freshly rederived bilingual facts; repeat Thai language QA, not proof. '
                      f'Prior index {original_hash}; prior flags and payload hashes retained. '
                      'No native Thai reader review.')
            records.append(record.model_dump(mode='json'))
            _save_index(directory / 'index.json', index)
        _write(directory / 'review-summary.json', summary)
    return summary


def main():
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
        from .budget import task_client
        client = task_client(SparkClient(max_retries=0))
    print(json.dumps(review_recording(args.out_dir, *values, client=client, box_ids=args.box_ids),
                     ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()

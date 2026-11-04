"""Summarize recorded attempts, bind offline fixtures, and build the demo."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

from experiments.mvp.pipeline.contract.schema import sha256_file
from .demo import build_demo
from .experiment import write
from .freeze import HERE, ROOT
from .metrics import aggregate_scores


def finish():
    manifest = json.loads((HERE / 'fixtures/frozen.json').read_text())
    scores, records, audits, model_records = [], [], [], []
    for case in manifest['cases']:
        directory = HERE / 'fixtures' / case['id']
        for arm in manifest['arms']:
            score_path = directory / f'{arm}.score.json'
            if not score_path.exists():
                continue
            score = json.loads(score_path.read_text())
            score['split'] = case['split']
            scores.append(score)
            result = json.loads((directory / f'{arm}.yard_read.json').read_text())
            evidence = json.loads((directory / f'{arm}.evidence.json').read_text())
            model_records.extend(r for r in result['records'] if r.get('model'))
            records.append({'case_id': case['id'], 'arm': arm, 'mode': 'recorded',
                            'synthetic': True, 'image_sha256': score['image_sha256'],
                            'context_sha256': score['context_sha256'], 'evidence': evidence,
                            'records': result['records'], 'historical_cost_usd': score['cost_usd']})
        for path in sorted(directory.glob('pair_audit*.json')):
            audits.append({**json.loads(path.read_text()), 'case_id': case['id'], 'split': case['split'],
                           'audit_file': str(path.relative_to(HERE))})
    groups = []
    for split in ('development', 'held_out'):
        for arm in manifest['arms']:
            rows = [s for s in scores if s['split'] == split and s['arm'] == arm]
            aggregate = aggregate_scores(rows)
            if not rows:
                aggregate['stack_height']['accuracy'] = None
                aggregate['id']['accuracy'] = None
            groups.append({'split': split, 'arm': arm, **aggregate,
                           'complete_images': sum(s['status'] == 'complete' for s in rows),
                           'mean_wall_s': sum(s['wall_s'] for s in rows) / len(rows) if rows else None})
    vision = {}
    budget_path = HERE / 'spend.jsonl'
    if budget_path.exists():
        for line in budget_path.read_text().splitlines():
            row = json.loads(line)
            vision[row['call_id']] = row
    vision_rows = list(vision.values())
    sam = [r for r in vision_rows if r['model'] == 'sam-3.1']
    muse = [r for r in vision_rows if r['model'] == 'muse-image-1.0']
    spark = [r for r in model_records if r['model'] == 'muse-spark-1.3']
    amount = lambda rows: sum((Decimal(str(r['cost_usd'])) for r in rows), Decimal(0))
    spend = {'label': 'published rates for SAM/Image; token usage cost for Spark; invoice unverified',
             'sam_calls': len(sam), 'sam_usd': str(amount(sam)),
             'muse_image_calls': len(muse), 'muse_image_usd': str(amount(muse)),
             'spark_completed_calls': len(spark), 'spark_usd': str(amount(spark)),
             'total_usd': str(amount(vision_rows) + amount(spark)),
             'sam_image_cap_usd': '5', 'sam_image_committed_usd': str(amount(vision_rows)),
             'vision_unknown_holds': sum(r['cost_kind'] == 'conservative_hold' for r in vision_rows),
             'spark_ledger': 'experiments/spark-spend/calls.jsonl',
             'spark_request_ids': [r['evidence'].get('raw', {}).get('spark_request_id')
                                   or r['evidence'].get('attempt', {}).get('spark', {}).get('request_id') for r in records]}
    ids = set(spend['spark_request_ids']) - {None}
    usage = [row for line in (ROOT / spend['spark_ledger']).read_text().splitlines()
             if (row := json.loads(line)).get('request_id') in ids
             and row.get('phase') == 'settlement']
    if len(usage) != len(ids) or amount(usage) != amount(spark):
        raise ValueError('Recorded Spark receipts do not match authoritative ledger')
    write(HERE / 'fixtures/spark_usage.json', usage)
    summary = {'schema_version': 'stowlm.yard-vision-experiment/1', 'synthetic': True,
               'finished_utc': datetime.now(timezone.utc).isoformat(), 'groups': groups,
               'photo_audits': audits,
               'photo_trials': len(audits), 'photo_discarded': sum(a['preserved'] is not True for a in audits),
               'held_out_photo_trials': sum(a['split'] == 'held_out' for a in audits),
               'held_out_photo_preserved': sum(a['split'] == 'held_out' and a['preserved'] is True for a in audits),
               'spend': spend, 'cases': scores}
    write(HERE / 'fixtures/recorded.json', records)
    write(HERE / 'fixtures/summary.json', summary)
    demo = build_demo()
    files = {str(p.relative_to(HERE / 'fixtures')): sha256_file(p)
             for p in sorted((HERE / 'fixtures').rglob('*')) if p.is_file() and p.name != 'manifest.json'}
    write(HERE / 'fixtures/manifest.json', {'synthetic': True, 'files': files})
    print(json.dumps({'groups': groups, 'photo_trials': len(audits), 'spend': spend, 'demo': demo}, indent=2))
    return summary


if __name__ == '__main__':
    finish()

"""Frozen reader baselines. No model loading and no training on sealed cases.

Spark credentials are read into process environment from the external .env;
the shared SparkClient records every call and updates docs/compute.md.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_credentials():
    # Never source arbitrary shell or expose the value in an argument/output.
    if not os.environ.get('MODEL_API_KEY'):
        for line in (ROOT.parent / '.env').read_text().splitlines():
            clean = line.strip().removeprefix('export ').strip()
            if not clean.startswith('MODEL_API_KEY='):
                continue
            parts = shlex.split(clean.split('=', 1)[1], comments=True)
            if len(parts) != 1 or not parts[0]:
                raise ValueError('External API credential has invalid syntax')
            os.environ['MODEL_API_KEY'] = parts[0]
            break
    if not os.environ.get('MODEL_API_KEY'):
        raise ValueError('External API credential unavailable')
    os.environ['SPARK_CREDIT_READY'] = '1'


def run(args):
    from experiments.mvp.reader.evaluate import score_predictions
    from experiments.mvp.reader.schema import SCHEMA_VERSION, messages
    rows = [json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()]
    args.out.mkdir(parents=True, exist_ok=True)
    evidence = {'cases_sha256': digest(args.cases), 'schema_sha256': digest(HERE / 'schema.py'),
                'started_at': datetime.now(timezone.utc).isoformat(), 'arm': args.arm,
                'zero_shot': args.arm == 'spark', 'prompt_examples': 0}
    predictions = []
    checkpoint = args.out / f'{args.arm}-predictions.jsonl'
    if checkpoint.exists():
        if not args.resume:
            raise ValueError('Predictions already exist; use --resume only for an interrupted frozen run.')
        predictions = [json.loads(s) for s in checkpoint.read_text().splitlines() if s.strip()]
        if [p['id'] for p in predictions] != [r['id'] for r in rows[:len(predictions)]]:
            raise ValueError('Checkpoint does not match case order')
        prior = json.loads((args.out / f'{args.arm}-provenance.json').read_text())
        for key in ('cases_sha256', 'schema_sha256'):
            if prior[key] != evidence[key]:
                raise ValueError('Frozen input changed since checkpoint')
    (args.out / f'{args.arm}-provenance.json').write_text(json.dumps(evidence, indent=2) + '\n')
    if args.arm == 'spark':
        load_credentials()
        from llm.spark.client import SparkClient
        from llm.spark.planner_budget import allocation_budget
        client = SparkClient(max_retries=0, timeout=60)
        latest = {}
        if client.ledger.path.exists():
            for line in client.ledger.path.read_text().splitlines():
                call = json.loads(line)
                if call.get('purpose') == 'reader-d199-sealed-zeroshot':
                    latest[call['reservation_id']] = Decimal(call['cost_usd'])
        prior_job_usd = sum(latest.values(), Decimal(0))
    else:
        from llm.instructions.rules import parse_rules
    with checkpoint.open('a' if args.resume else 'w') as handle:
        for row in rows[len(predictions):]:
            item = {'id': row['id']}
            if args.arm == 'rules':
                result = parse_rules(row['sentence'])
                body = result.model_dump(mode='json')
                key = 'refusal' if 'code' in body else 'constraint'
                item['prediction'] = {'schema_version': SCHEMA_VERSION, key: body}
            else:
                prompt = '\n\n'.join(m['content'] for m in messages(row['sentence'], row['context']))
                try:
                    with allocation_budget(client.ledger, allocation_usd=Decimal('2'),
                                           prior_job_usd=prior_job_usd):
                        response = client.complete(prompt, purpose='reader-d199-sealed-zeroshot',
                                                   max_output_tokens=1024, reasoning_effort='minimal')
                    item.update(prediction=response.text, cost_usd=str(response.cost_usd),
                                request_id=response.request_id)
                except Exception as exc:
                    # Exception text is intentionally not logged; provider bodies can contain secrets.
                    item.update(prediction=None, error=type(exc).__name__)
            handle.write(json.dumps(item, ensure_ascii=False) + '\n')
            handle.flush()
            predictions.append(item)
            print(f'{args.arm}: {len(predictions)}/{len(rows)}', flush=True)
            if item.get('error'):
                # Preserve an interrupted/error row for honest scoring. Avoid repeatedly billing a failed service.
                break
    summary = score_predictions(rows, predictions)
    summary['recorded_prediction_count'] = len(predictions)
    summary['measured_cost_usd'] = str(sum((Decimal(p.get('cost_usd', '0')) for p in predictions), Decimal(0)))
    (args.out / f'{args.arm}-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=['rules', 'spark'], required=True)
    parser.add_argument('--cases', type=Path, default=HERE / 'data' / 'sealed.jsonl')
    parser.add_argument('--out', type=Path, default=HERE / 'results')
    parser.add_argument('--resume', action='store_true')
    run(parser.parse_args())

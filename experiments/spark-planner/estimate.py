"""Measure real Spark prompts on persisted sizing shards, no HTTP or model weights."""
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import time

from eval.splits import load_manifest
from llm.spark.planner_budget import estimate_instance
from stowage.io import load_instance


def main():
    start = time.monotonic()
    root = Path(__file__).resolve().parents[2]
    sizing = json.loads((root / 'experiments/format/measurements.json').read_text())
    manifest = load_manifest()
    counts = Counter(e['spec']['rung'] for e in manifest['entries'] if e['split'] == 'test')
    rows = []
    for source in sizing['rows']:
        instance = load_instance(root / source['source'])
        estimate = estimate_instance(instance)
        rows.append({'rung': source['rung'], 'instance_id': instance.id,
                     'input_tokens': estimate.input_tokens, 'output_allowance': estimate.output_tokens,
                     'test_count': counts[source['rung']], 'instance_usd': str(estimate.cost_usd),
                     'rung_usd': str(estimate.cost_usd * counts[source['rung']])})
    result = {'manifest_sha256': manifest['entries_sha256'], 'rows': rows,
              'total_usd': str(sum((Decimal(r['rung_usd']) for r in rows), Decimal(0))),
              'wall_time_s': time.monotonic() - start,
              'approximation': 'Glimmer proxy; one sizing shard extrapolated across each rung, no retries/cache discount'}
    (Path(__file__).parent / 'estimates.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

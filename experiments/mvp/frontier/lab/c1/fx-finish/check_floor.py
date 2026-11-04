"""Measure the unchanged independent checker on a certified TRAIN medium plan."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from profile_baseline import load_stored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--map-id', default='medium-feeder_500-f60-high-s192200000')
    parser.add_argument('--out', type=Path, default=Path(__file__).parent/'results/check-floor')
    args = parser.parse_args()
    instance, result, row = load_stored(args.map_id, finished=True)
    from stowage.checker import check
    started = time.monotonic()
    report = check(instance, result.plan)
    elapsed = time.monotonic()-started
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix('.report.json').write_text(report.model_dump_json(indent=2)+'\n')
    summary = dict(map_id=args.map_id, passed=report.passed, elapsed_s=elapsed,
                   input_sha256=row['input_sha256'],
                   plan_sha256=hashlib.sha256(result.plan.model_dump_json().encode()).hexdigest(),
                   weighted_nonproductive=report.count.weighted_nonproductive,
                   violation_rules=sorted({v.rule_id for v in report.violations}))
    args.out.with_suffix('.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()

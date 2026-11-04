"""Paired, watchdog-bounded finish-only benchmark on allowed V6 TRAIN stows."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import time

from profile_baseline import ROOT, load_stored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--map-id', default='medium-feeder_500-f60-high-s192200000')
    parser.add_argument('--mode', choices=('v4', 'fast', 'fast-inherited'), required=True)
    parser.add_argument('--budget-s', type=float, default=120.)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    tracked = ['fast_finish.py', 'fast_proof.py', 'early_bound.py', 'shaped_liquid.py'] if args.mode.startswith('fast') else []
    def bindings():
        return {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                for name in tracked if (Path(__file__).parent/name).exists()}
    source_before = bindings()
    instance, raw, row = load_stored(args.map_id)
    from stowage.solver import ballast_v4_dispatch
    restore = None
    if args.mode.startswith('fast'):
        from fast_finish import install
        restore = install('worker_inherited' if args.mode == 'fast-inherited' else 'worker')
    started = time.monotonic()
    try:
        outcome = ballast_v4_dispatch.finish(instance, raw.plan,
            time_limit_s=args.budget_s, seed=0)
    finally:
        if restore:
            restore()
    finish_s = time.monotonic()-started
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix('.outcome.json').write_text(outcome.model_dump_json(indent=2)+'\n')
    passed, check_s, weighted = False, 0., None
    if outcome.status in ('ok', 'kept') and outcome.plan is not None:
        # Called after every experimental hook is restored, in the parent.
        from stowage.checker import check
        started = time.monotonic()
        report = check(instance, outcome.plan)
        check_s = time.monotonic()-started
        passed = report.passed
        weighted = float(report.count.weighted_nonproductive) if passed else None
        args.out.with_suffix('.report.json').write_text(report.model_dump_json(indent=2)+'\n')
    summary = dict(map_id=args.map_id, mode=args.mode, budget_s=args.budget_s,
        status=outcome.status, cause=getattr(outcome, 'not_found_cause', None),
        finish_s=finish_s, independent_check_s=check_s, passed=passed, lifts=weighted,
        input_sha256=row['input_sha256'],
        raw_plan_sha256=hashlib.sha256(raw.plan.model_dump_json().encode()).hexdigest(),
        diagnostics=getattr(outcome, 'diagnostics', {}))
    summary['experimental_sources'] = source_before
    summary['sources_unchanged_during_run'] = source_before == bindings()
    from stowage.rules.catalogue import catalogue_snapshot, rule_freeze_v1_enabled
    summary['rule_freeze_v1_enabled'] = rule_freeze_v1_enabled()
    summary['catalogue_sha256'] = hashlib.sha256(catalogue_snapshot().content).hexdigest()
    summary['kernel_options'] = {name: os.environ.get(name, '0') for name in
        ('FX_FINISH_GATHER', 'FX_FINISH_SECTION_POLY')}
    args.out.with_suffix('.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ('map_id', 'mode', 'status', 'finish_s', 'passed', 'lifts')}, indent=2))


if __name__ == '__main__':
    main()

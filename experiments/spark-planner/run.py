"""Preflight a frozen test panel; --run explicitly enables paid HTTP calls.

Prepared instance files must cover every selected test specification exactly.
No generation failure is silently dropped; prepare/audit the panel in task 6.3.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from eval.splits import load_manifest, spec_from_instance, spec_key
from llm.spark.planner import SparkPlanner, planner_version
from llm.spark.planner_budget import estimate_instance, guard_run
from llm.spark.spend import SpendLedger
from stowage.frozen import require_clean
from stowage.io import load_instance
from stowage.planner import run_planner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instances', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=Path, default=Path('experiments/spark-planner/run'))
    # Default: the pilot's measured need per rung (planner.DEFAULT_MAX_OUTPUT_TOKENS).
    parser.add_argument('--max-output-tokens', type=int, default=None)
    parser.add_argument('--compact-only', action='store_true', help='D76 information ablation: no rules appendix')
    parser.add_argument('--reasoning-effort', choices=['minimal', 'low', 'medium', 'high', 'xhigh'],
                        help='provider reasoning.effort; omitted = provider default')
    parser.add_argument('--allocation-usd', type=Decimal, default=Decimal('60'))
    parser.add_argument('--run', action='store_true', help='Enable paid requests; otherwise estimate only')
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    expected = {spec_key(e['spec']) for e in manifest['entries'] if e['split'] == 'test'}
    panel = {}
    for path in sorted(args.instances.rglob('*.json')) + sorted(args.instances.rglob('*.json.gz')):
        instance = load_instance(path)
        key = spec_key(spec_from_instance(instance))
        if key not in expected:
            continue
        if key in panel:
            raise ValueError(f'duplicate test specification: {key}')
        require_clean(instance, what='Spark test instance')
        panel[key] = instance
    if set(panel) != expected:
        raise ValueError(f'incomplete test panel: {len(panel)}/{len(expected)}; preserve generation failures in 6.3 audit')
    settings = {'planner_version': planner_version(appendix=not args.compact_only, reasoning_effort=args.reasoning_effort), 'max_output_tokens': args.max_output_tokens,
                'allow_repair': False}
    # Audit every checkpoint before estimating or attempting any pending request.
    # A mismatch late in the panel must never allow earlier paid work to start.
    completed, pending = {}, {}
    for key, instance in panel.items():
        target = args.output / f'{key}.json'
        if not target.exists():
            pending[key] = instance
            continue
        try:
            row = json.loads(target.read_text())
        except (ValueError, OSError) as exc:
            raise ValueError('invalid checkpoint') from exc
        if (not isinstance(row, dict) or row.get('spec_key') != key
                or row.get('manifest_sha256') != manifest['entries_sha256']
                or row.get('settings') != settings
                or row.get('instance_sha256') != hashlib.sha256(instance.model_dump_json().encode()).hexdigest()):
            raise ValueError('checkpoint manifest, specification, instance or settings mismatch')
        if (not isinstance(row.get('result'), dict)
                or not isinstance(row['result'].get('metrics'), dict)
                or not (row.get('report') is None or isinstance(row['report'], dict))):
            raise ValueError('invalid checkpoint result or report')
        completed[key] = row
    estimates = [estimate_instance(i, max_output_tokens=args.max_output_tokens, appendix=not args.compact_only)
                 for i in pending.values()]
    ledger = SpendLedger(cap_usd=args.allocation_usd)
    remaining = guard_run(estimates, ledger, allocation_usd=args.allocation_usd)
    summary = {'manifest_sha256': manifest['entries_sha256'], 'instances': len(panel),
               'completed_instances': len(completed), 'pending_instances': len(pending),
               'estimated_usd': str(sum((e.cost_usd for e in estimates), Decimal(0))),
               'remaining_usd': str(remaining), 'rungs': dict(Counter(i.size_class for i in panel.values()))}
    summary['settings'] = settings
    print(json.dumps(summary, sort_keys=True))
    if not args.run:
        return
    if pending:
        # Refuse missing/stubbed harness BEFORE constructing any paid client.
        from stowage.frozen import FROZEN
        if not FROZEN['planner.run_planner'].implemented:
            raise RuntimeError('Task 5.2 run_planner is not implemented; no API request made')
        from llm.spark.client import SparkClient, SparkCreditNotReady
        if os.environ.get('SPARK_CREDIT_READY') != '1':
            raise SparkCreditNotReady('Muse Spark credit is on hold; confirm the applied credit before enabling calls')
        client = SparkClient(spend_cap_usd=args.allocation_usd)
        planner = SparkPlanner(client, max_output_tokens=args.max_output_tokens, allocation_usd=args.allocation_usd,
                               appendix=not args.compact_only, reasoning_effort=args.reasoning_effort)
        # The client's authoritative ledger writes compute.md before HTTP and
        # after settlement, including measured incomplete usage and unknown holds.
        # Do not append a second manual spend row or infer cost from checkpoints.
    args.output.mkdir(parents=True, exist_ok=True)
    rows = list(completed.values())
    for key, instance in pending.items():
        target = args.output / f'{key}.json'
        result, report = run_planner(planner, instance, seed=instance.seed, allow_repair=False)
        require_clean(result, what='Spark baseline result')
        if report is not None:
            require_clean(report, what='Spark baseline report')
        row = {'spec_key': key, 'manifest_sha256': manifest['entries_sha256'],
               'settings': settings,
               'instance_sha256': hashlib.sha256(instance.model_dump_json().encode()).hexdigest(),
               'result': result.model_dump(mode='json'),
               'report': report.model_dump(mode='json') if report is not None else None}
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(row, sort_keys=True) + '\n')
        temp.replace(target)
        rows.append(row)
    # Raw legality and full-checker pass are separate measurements; no repair hides failures.
    summary['raw_legal_rate'] = sum(bool(r['result']['metrics'].get('raw_plan_legal')) for r in rows) / len(rows)
    summary['checker_pass_rate'] = sum(bool(r['report'] and r['report']['passed']) for r in rows) / len(rows)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    print(json.dumps(summary, sort_keys=True))


if __name__ == '__main__':
    main()

"""Independently verify saved tiny references without rerunning optimizers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from stowage.checker import report_matches_inputs
from stowage.io import load_instance
from stowage.model import CheckReport, PlannerResult


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('experiments/mvp/runs'))
    parser.add_argument('--instance-list', type=Path,
                        default=Path('experiments/mvp/refs_tools/tiny-instances.txt'))
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    paths = [Path(p) for p in args.instance_list.read_text().splitlines() if p.strip()]
    inputs = {load_instance(path).id: (path, hashlib.sha256(path.read_bytes()).hexdigest())
              for path in paths}
    assert len(inputs) == len(paths), 'duplicate input ids'
    checked, failed, best_found = 0, 0, 0
    totals = {}
    for method in ('bounds', 'twostep', 'solver', 'exact'):
        directory = args.root / f'test-{method}'
        records = [json.loads(p.read_text()) for p in sorted(directory.glob('instances/*/record.json'))]
        if args.allow_partial:
            assert {r['instance_id'] for r in records} <= set(inputs), method
        else:
            assert len(records) == len(inputs), (method, len(records), len(inputs))
            assert {r['instance_id'] for r in records} == set(inputs), method
        lines = [json.loads(line) for line in (directory / 'records.jsonl').read_text().splitlines()]
        assert sorted(lines, key=lambda r: r['instance_id']) == sorted(records, key=lambda r: r['instance_id']), method
        for record in records:
            instance_id = record['instance_id']
            source, digest = inputs[instance_id]
            assert record['input_sha256'] == digest, (method, instance_id, 'input hash')
            assert record.get('reference_eligible') is True, (method, instance_id, 'source attribution')
            assert not record.get('relevant_source_changes'), (method, instance_id, 'source drift')
            bound = record['lower_bound']
            assert bound['weighted'] >= 0
            if method == 'bounds':
                assert record.get('result') is None and record.get('report') is None
                continue
            result = PlannerResult.model_validate(record['result'])
            assert not result.repaired, (method, instance_id, 'repaired')
            saved = json.loads((directory / 'instances' / instance_id / 'result.json').read_text())
            saved.pop('schema_version', None)
            assert saved == record['result'], (method, instance_id, 'result artifact')
            report_payload = record.get('report')
            if result.plan is None:
                assert not record['shippable'] and report_payload is None
                failed += 1
                continue
            assert report_payload is not None, (method, instance_id, 'unchecked plan')
            report = CheckReport.model_validate(report_payload)
            assert report_matches_inputs(report, load_instance(source), result.plan), (method, instance_id, 'binding')
            persisted = json.loads((directory / 'instances' / instance_id / 'report.json').read_text())
            assert persisted == report_payload, (method, instance_id, 'report artifact')
            checked += 1
            if report.passed:
                if record['shippable']:
                    assert result.plan.ballast is not None, (method, instance_id, 'missing ballast')
                assert report.count is not None
                assert bound['weighted'] <= report.count.weighted_nonproductive + 1e-9, (method, instance_id, 'bound exceeds checked objective')
                if method == 'exact' and record.get('exact_label') == 'optimal':
                    assert result.metrics['optimality_status'] == 'OPTIMAL'
                    assert abs(result.metrics['objective'] - report.count.weighted_nonproductive) <= 1e-9
                    assert result.metrics['physical_domain_optimality_certified'] is False
            else:
                failed += 1
            if result.status == 'timeout' and report.passed:
                assert not record['shippable']
                best_found += 1
        totals[method] = len(records)
    print(json.dumps(dict(valid=True, records=totals, bound_rows=totals['bounds'],
                         plans_checked=checked, failed_or_no_plan=failed,
                         checked_timeout_incumbents=best_found), sort_keys=True))


if __name__ == '__main__':
    main()

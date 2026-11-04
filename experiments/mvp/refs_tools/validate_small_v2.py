"""Independent saved-evidence checks; never executes a solver or physics checker."""
from __future__ import annotations

import os
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
os.environ.pop('STOWAGE_BALLAST_V4', None)

import argparse
import hashlib
import json
from pathlib import Path

from stowage.checker import report_matches_inputs
from stowage.io import load_instance
from stowage.model import CheckReport, PlannerResult

ROOT = Path(__file__).resolve().parents[3]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, default=Path(__file__).with_name('small-v2-release-frozen.json'))
    parser.add_argument('--expected-count', type=int, default=10)
    args = parser.parse_args()
    corpus = ROOT / 'experiments/mvp/corpus'
    expected = {key: value for key, value in read(args.release)['hashes'].items()
                if key.startswith('test/small/')}
    assert len(expected) == args.expected_count
    admitted = {value: corpus / key for key, value in expected.items()}
    assert len(admitted) == len(expected)
    assert all(sha(path) == digest for digest, path in admitted.items())
    counts = {}
    for method in ('solver', 'bounds'):
        folder = ROOT / f'experiments/mvp/runs/test-{method}-small-v2'
        paths = sorted((folder / 'instances').glob('*/record.json'))
        rows = [read(path) for path in paths]
        assert len(rows) == len(expected), (method, len(rows))
        assert {row['input_sha256'] for row in rows} == set(admitted)
        assert all(row['completed'] for row in rows)
        journal = [json.loads(line) for line in (folder / 'records.jsonl').read_text().splitlines()]
        assert sorted(rows, key=lambda row: row['spec_key']) == sorted(journal, key=lambda row: row['spec_key'])
        checked = shipped = 0
        for checkpoint, row in zip(paths, rows):
            for name, digest in row.get('artifact_sha256', {}).items():
                assert sha(checkpoint.parent / name) == digest, (method, row['spec_key'], name)
            assert sha(Path(row['audit_path'])) == row['audit_sha256']
            for name, stage in row['stages'].items():
                assert stage.get('STOWAGE_BALLAST_V4') == ('1' if name == 'ballast' else None), (row['spec_key'], name)
            if method == 'bounds':
                assert row['lower_bound']['weighted'] >= 0
                assert not row['shippable']
                continue
            result_data = row.get('result')
            if result_data is None:
                assert not row['shippable']
                continue
            result = PlannerResult.model_validate(result_data)
            assert not result.repaired
            if result.plan is None:
                assert not row['shippable']
                continue
            assert 'ballast' in row['stages'] and 'checker' in row['stages']
            if row.get('report') is None:
                assert not row['shippable'] and row.get('failed_stage') == 'checker'
                continue
            report = CheckReport.model_validate(row['report'])
            instance = load_instance(admitted[row['input_sha256']])
            assert report_matches_inputs(report, instance, result.plan), row['spec_key']
            assert read(checkpoint.parent / 'result.json') == result_data
            assert read(checkpoint.parent / 'report.json') == row['report']
            checked += 1
            count = row['report'].get('count')
            if count:
                weights = instance.objective
                objective = (weights.relocate * count['relocations'] + weights.restow * count['restow_lifts']
                             + weights.hatch_cover * count['hatch_cover_lifts']
                             + weights.future_restow * sum(v['restow_lifts'] for v in count['future'])
                             + weights.future_hatch_cover * sum(v['hatch_cover_lifts'] for v in count['future']))
                assert abs(objective - count['weighted_nonproductive']) < 1e-9
            if row['shippable']:
                assert report.passed and row['report_binding_valid'] and row['reference_eligible']
                assert result.status == 'ok' and result.plan.ballast is not None
                assert row['stages']['ballast']['passed']
                assert row['lower_bound']['weighted'] <= count['weighted_nonproductive'] + 1e-9
                shipped += 1
        counts[method] = dict(attempts=len(rows), checked=checked, shippable=shipped)
    approved = Path(__file__).with_name('approved-tiny-report.md').read_bytes()
    current = (ROOT / 'experiments/mvp/runs/test-references.md').read_bytes()
    assert current.split(b'<!-- BEGIN SMALL TEST V2 REFERENCES -->', 1)[0] == approved
    print(json.dumps(dict(valid=True, methods=counts, tiny_prefix_sha256=hashlib.sha256(approved).hexdigest()), sort_keys=True))


if __name__ == '__main__':
    main()

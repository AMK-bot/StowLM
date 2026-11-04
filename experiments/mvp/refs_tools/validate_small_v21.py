"""Validate saved fifteen-case evidence only; never run a planner or checker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

for _key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[_key] = '1'
os.environ.pop('STOWAGE_BALLAST_V4', None)

from stowage.checker import report_matches_inputs
from stowage.io import load_instance
from stowage.model import CheckReport, PlannerResult

ROOT = Path(__file__).resolve().parents[3]
SOURCES = {
    'solver': ('test-solver-small-v21-screen',),
    'solver, no securing screen': ('test-solver-small-v2', 'test-solver-small-v21-noscreen-extra'),
    'bounds': ('test-bounds-small-v2', 'test-bounds-small-v21-extra'),
}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def direct_rows(folder):
    """Require canonical direct checkpoints and an exactly reconciled journal."""
    paths = sorted((folder / 'instances').glob('*/record.json'))
    rows = [read(path) for path in paths]
    identities = [row['spec_key'] for row in rows]
    assert len(identities) == len(set(identities)), folder
    journal = [json.loads(line) for line in (folder / 'records.jsonl').read_text().splitlines() if line.strip()]
    assert sorted(rows, key=lambda row: row['spec_key']) == sorted(journal, key=lambda row: row['spec_key']), folder
    return list(zip(paths, rows))


def validate_source_files(config):
    """Check the frozen source bytes, including the archived historical driver."""
    source_root = Path(config['source_root'])
    for name, digest in config['source_pin'].items():
        if name.startswith('src/'):
            path = source_root / name
        elif name == 'experiments/mvp/refs_tools/run_small_v2.py' and (source_root / 'producer-run_small_v2.py').is_file():
            path = source_root / 'producer-run_small_v2.py'
        else:
            path = ROOT / name
        assert sha(path) == digest, ('Pinned source bytes changed', path)


def validate_row(checkpoint, row, config, admitted, *, official, bounds):
    label = row['spec_key']
    assert row['completed'] and row['input_sha256'] in admitted, label
    instance_path = admitted[row['input_sha256']]
    assert row['config_sha256'] == object_sha(config), label
    assert sha(Path(row['audit_path'])) == row['audit_sha256'], label
    assert row.get('source_before') == config['source_pin'] == row.get('source_after'), label
    assert not row.get('source_drift') and row['reference_eligible'], label
    for name, digest in row.get('artifact_sha256', {}).items():
        assert sha(checkpoint.parent / name) == digest, (label, name)
    for name, stage in row['stages'].items():
        assert stage.get('STOWAGE_BALLAST_V4') == ('1' if name == 'ballast' else None), (label, name)
    assert row['lower_bound']['weighted'] >= 0, label
    if bounds:
        assert not row['shippable'] and set(row['stages']) == {'bounds'}, label
        return False, False
    instance = load_instance(instance_path)
    assert row['instance_id'] == instance.id, label
    effective = row.get('scalable_config')
    if effective:
        assert effective['max_neighborhoods'] == 12 and not effective['constructor_v4'], label
        assert effective['securing_screen'] is official, label
        # The historical source predates the explicit heavy-first switch.
        assert effective.get('heavy_first_ordering', False) is official, label
        assert effective['feasibility_config']['extra']['global_screen'] == 0, label
        assert row.get('effective_seed', 0) == (instance.seed if official else 0), label
    data = row.get('result')
    if data is None:
        assert not row['shippable'], label
        return False, False
    result = PlannerResult.model_validate(data)
    assert not result.repaired and not row.get('repaired'), label
    if result.plan is None:
        assert not row['shippable'], label
        return False, False
    assert {'ballast', 'checker'} <= set(row['stages']), label
    assert read(checkpoint.parent / 'result.json') == data, label
    if row.get('report') is None:
        assert not row['shippable'] and row.get('failed_stage') == 'checker', label
        return False, False
    report = CheckReport.model_validate(row['report'])
    assert read(checkpoint.parent / 'report.json') == row['report'], label
    assert report_matches_inputs(report, instance, result.plan), label
    assert row['report_binding_valid'] and row['checker_passed'] == report.passed, label
    assert row.get('count') == row['report'].get('count'), label
    hard = [v.rule_id for v in report.violations if v.severity == 'hard']
    assert row['first_failing_rule'] == (hard[0] if hard else None), label
    count = row['report'].get('count')
    if count:
        weights = instance.objective
        objective = (weights.relocate * count['relocations'] + weights.restow * count['restow_lifts']
                     + weights.hatch_cover * count['hatch_cover_lifts']
                     + weights.future_restow * sum(v['restow_lifts'] for v in count['future'])
                     + weights.future_hatch_cover * sum(v['hatch_cover_lifts'] for v in count['future']))
        assert abs(objective - count['weighted_nonproductive']) < 1e-9, label
    should_ship = bool(report.passed and result.status == 'ok' and result.plan.ballast is not None
                       and row['stages']['ballast']['passed'] and row['reference_eligible'])
    assert row['shippable'] == should_ship, label
    if should_ship:
        assert count and row['lower_bound']['weighted'] <= count['weighted_nonproductive'] + 1e-9, label
    return True, should_ship


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, default=Path(__file__).with_name('small-v21-release-frozen.json'))
    args = parser.parse_args()
    corpus = ROOT / 'experiments/mvp/corpus'
    expected = {key: value for key, value in read(args.release)['hashes'].items() if key.startswith('test/small/')}
    assert len(expected) == 15 and len(set(expected.values())) == 15
    admitted = {digest: corpus / name for name, digest in expected.items()}
    assert all(sha(path) == digest for digest, path in admitted.items())
    counts = {}
    for method, names in SOURCES.items():
        combined = []
        checked = shipped = 0
        for name in names:
            folder = ROOT / 'experiments/mvp/runs' / name
            manifest = read(folder / 'manifest.json')
            config = manifest['config']
            assert manifest['complete'] and manifest['config_sha256'] == object_sha(config), name
            assert config['c2_global_screen'] == 0 and config['rule_freeze_v1_enabled'] is False, name
            assert config['ballast_v4_scope'] == 'finalize_only', name
            validate_source_files(config)
            official = method == 'solver'
            assert config.get('seed_policy', 'fixed') == ('instance' if official else 'fixed'), name
            rows = direct_rows(folder)
            assert manifest['n_records'] == len(rows) == len(manifest['selection']), name
            for checkpoint, row in rows:
                was_checked, was_shipped = validate_row(checkpoint, row, config, admitted,
                                                        official=official, bounds=method == 'bounds')
                checked += was_checked
                shipped += was_shipped
                combined.append(row)
        identities = [row['input_sha256'] for row in combined]
        assert len(identities) == 15 and set(identities) == set(admitted), method
        counts[method] = dict(attempts=len(combined), checked=checked, shippable=shipped)
    approved = Path(__file__).with_name('approved-small-v2-report.md').read_bytes()
    report = (ROOT / 'experiments/mvp/runs/test-references.md').read_bytes()
    assert report.split(b'<!-- BEGIN SMALL TEST V2.1 REFERENCES -->', 1)[0] == approved
    print(json.dumps(dict(valid=True, methods=counts, accepted_prefix_sha256=hashlib.sha256(approved).hexdigest()), sort_keys=True))


if __name__ == '__main__':
    main()

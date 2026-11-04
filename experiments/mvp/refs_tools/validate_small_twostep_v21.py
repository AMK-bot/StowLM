"""Validate saved two-step v4.3 TEST-v2.1 evidence without executing a planner.

Checks full denominators, frozen inputs/source, artifact bindings, checker gate,
and the finalizer-only ballast-v4 environment contract. Mutual-pass summaries
are recomputed directly from canonical rows, never from a possibly stale journal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from validate_small_v21 import (ROOT, direct_rows, object_sha, read, sha,
                                validate_row, validate_source_files)
from stowage.io import load_instance


def pair_summary(twostep, solver, admitted):
    """Same-input means on jointly shippable plans; bounds are not plans."""
    summaries = {}
    for ship in ('all', 'feeder_500', 'feeder_800', 'feeder_1700'):
        keys = [digest for digest in admitted if ship == 'all' or
                load_instance(admitted[digest]).vessel.id.split('.')[0] == ship]
        mutual = [key for key in keys if twostep[key]['shippable'] and solver[key]['shippable']]
        values = [(twostep[key]['count']['weighted_nonproductive'],
                   solver[key]['count']['weighted_nonproductive'],
                   twostep[key]['lower_bound']['weighted']) for key in mutual]
        summaries[ship] = dict(attempts=len(keys), mutual_passes=len(mutual),
            twostep_passes=sum(twostep[key]['shippable'] for key in keys),
            solver_passes=sum(solver[key]['shippable'] for key in keys),
            twostep_checker_passes=sum(twostep[key].get('checker_passed', False) for key in keys),
            solver_checker_passes=sum(solver[key].get('checker_passed', False) for key in keys),
            mutual_sha256=sorted(mutual),
            twostep_sum=sum(v[0] for v in values) if values else None,
            solver_sum=sum(v[1] for v in values) if values else None,
            bound_sum=sum(v[2] for v in values) if values else None,
            twostep_mean=sum(v[0] for v in values)/len(values) if values else None,
            solver_mean=sum(v[1] for v in values)/len(values) if values else None,
            bound_mean=sum(v[2] for v in values)/len(values) if values else None,
            delta_mean=sum(v[0]-v[1] for v in values)/len(values) if values else None,
            twostep_wins=sum(a < b for a,b,_ in values),
            ties=sum(a == b for a,b,_ in values),
            solver_wins=sum(a > b for a,b,_ in values))
    return summaries


def validate_summary(folder, pairs):
    """Bind the published paired table to independently counted checkpoint rows."""
    summary = read(folder/'summary.json')
    published = {row['ship']: row for row in summary['paired_mutual_pass']}
    assert len(summary['paired_mutual_pass']) == len(published) == 4
    for group, actual in pairs.items():
        row = published['overall' if group == 'all' else group]
        expected = dict(frozen_n=actual['attempts'], mutual_n=actual['mutual_passes'],
            excluded_from_mutual_n=actual['attempts']-actual['mutual_passes'],
            twostep_pass=actual['twostep_checker_passes'], solver_pass=actual['solver_checker_passes'],
            twostep_shippable=actual['twostep_passes'], solver_shippable=actual['solver_passes'],
            mean_delta_twostep_minus_solver=actual['delta_mean'],
            twostep_wins=actual['twostep_wins'], ties=actual['ties'],
            twostep_losses=actual['solver_wins'])
        assert all(row[name] == value for name,value in expected.items()), (group, row, expected)
        assert sorted(pair['input_sha256'] for pair in row['pairs']) == actual['mutual_sha256'], group
        for method in ('twostep', 'solver', 'bound'):
            assert row[method] == dict(n=actual['mutual_passes'],
                sum=actual[f'{method}_sum'],mean=actual[f'{method}_mean']), (group,method)
    approved = Path(__file__).with_name('approved-small-v21-report.md').read_bytes()
    report = (ROOT/'experiments/mvp/runs/test-references.md').read_bytes()
    marker = b'<!-- BEGIN SMALL TEST V2.1 TWO-STEP REFERENCES -->'
    assert report.count(marker) == 1 and report.split(marker,1)[0] == approved
    assert summary['accepted_prefix_sha256'] == hashlib.sha256(approved).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, default=Path(__file__).with_name('small-v21-release-frozen.json'))
    parser.add_argument('--folder', type=Path, default=ROOT/'experiments/mvp/runs/test-twostep-small-v21')
    parser.add_argument('--solver-folder', type=Path, default=ROOT/'experiments/mvp/runs/test-solver-small-v21-screen')
    args = parser.parse_args()
    expected = {name: digest for name,digest in read(args.release)['hashes'].items()
                if name.startswith('test/small/')}
    assert len(expected) == len(set(expected.values())) == 15
    admitted = {digest: ROOT/'experiments/mvp/corpus'/name for name,digest in expected.items()}
    assert all(sha(path) == digest for digest,path in admitted.items())
    folder = args.folder.resolve()
    manifest = read(folder/'manifest.json')
    config = manifest['config']
    assert manifest['complete'] and manifest['n_records'] == len(manifest['selection']) == 15
    assert manifest['release_sha256'] == sha(args.release)
    assert manifest['config_sha256'] == object_sha(config)
    assert config['method'] == 'twostep' and config['seed_policy'] == 'instance'
    assert config['c2_global_screen'] == 0 and config['rule_freeze_v1_enabled'] is False
    assert config['ballast_v4_scope'] == 'finalize_only'
    validate_source_files(config)
    provenance = read(folder/'twostep-source-provenance.json')
    source = Path(config['source_root'])
    assert provenance['version'] == '4.3.0' and provenance['source_root'] == str(source)
    assert provenance['seed_policy'] == config['seed_policy']
    assert provenance['snapshot_sha256'] == sha(source/'snapshot.json')
    assert provenance['driver_sha256'] == config['source_pin']['experiments/mvp/refs_tools/run_small_v2.py']
    text = (source/'src/stowage/solver/twostep.py').read_text()
    assert re.search(r'^VERSION\s*=\s*[\"\']4\.3\.0[\"\']', text, re.MULTILINE)
    expected_sources = {name: digest for name,digest in read(source/'snapshot.json')['source_files'].items()
                        if Path(name).name.startswith('twostep') and name.endswith('.py')}
    assert provenance['twostep_source_sha256'] == expected_sources and expected_sources
    assert all(sha(source/name) == digest for name,digest in expected_sources.items())
    rows = direct_rows(folder)
    assert len(rows) == 15 and {row['input_sha256'] for _,row in rows} == set(admitted)
    checked = shipped = 0
    for checkpoint,row in rows:
        assert row['effective_seed'] == load_instance(admitted[row['input_sha256']]).seed
        assert row['planner'] == 'baseline.twostep'
        assert not row.get('scalable_config')
        if row.get('result') is not None:
            assert row['result']['planner'] == 'baseline.twostep'
            assert row['result']['planner_version'] == '4.3.0'
            assert row['result']['seed'] == row['effective_seed']
        if (checkpoint.parent/'raw-result.json').exists():
            raw = read(checkpoint.parent/'raw-result.json')
            assert raw['planner_version'] == '4.3.0' and not raw['repaired']
            assert raw['seed'] == row['effective_seed']
        did_check,did_ship = validate_row(checkpoint,row,config,admitted,official=False,bounds=False)
        checked += did_check
        shipped += did_ship
    solver_rows = direct_rows(args.solver_folder)
    solver = {row['input_sha256']:row for _,row in solver_rows}
    assert len(solver_rows) == len(solver) == 15 and set(solver) == set(admitted)
    twostep = {row['input_sha256']:row for _,row in rows}
    for key in admitted:
        assert twostep[key]['lower_bound'] == solver[key]['lower_bound'], key
    pairs = pair_summary(twostep,solver,admitted)
    validate_summary(folder, pairs)
    print(json.dumps(dict(valid=True,attempts=15,checked=checked,shippable=shipped,
        twostep_source_sha256=expected_sources, paired=pairs),sort_keys=True))


if __name__ == '__main__':
    main()

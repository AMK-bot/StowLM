"""Selection, paired-evidence and launch-limit regressions without running solvers."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location('engine_bench_' + name, HERE / f'{name}.py')
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_fixed_lists_match_rules_and_scorecard_allowlist():
    sets = module('sets')
    selected, _ = sets.select()
    assert {key: len(value) for key, value in selected.items()} == dict(val_small=45, val_tiny=40, medium=8)
    for name, expected in selected.items():
        assert sets.load_set(HERE / f'{name}.txt') == expected
    manifest = json.loads((HERE / 'MANIFEST.json').read_text())
    assert manifest['selection_uses_engine_results'] is False
    assert manifest['sets'] == selected
    assert not set(selected['val_small']) & set(sets.load_set('quick_small'))
    assert not set(selected['val_tiny']) & set(sets.load_set('quick_tiny'))


def test_shards_cover_each_map_once_and_limit_workers():
    run = module('run')
    groups = run.shards(list('abcdefg'), 3)
    assert sorted(value for group in groups for value in group) == list('abcdefg')
    assert max(map(len, groups)) - min(map(len, groups)) <= 1
    with pytest.raises(ValueError):
        run.shards(['a', 'a'], 2)
    with pytest.raises(ValueError):
        run.shards(['a'], 0)
    args = SimpleNamespace(budget_s=300, finalize_s=600, tries=3, seed=0)
    command = run.score_command('maps.txt', 'output', args)
    assert command[command.index('--workers') + 1] == '2'
    with pytest.raises(SystemExit):
        run.main(['--out', str(HERE / 'unused'), '--procs', '3'])


def test_comparison_unknowns_parts_pairs_and_size_separation():
    compare = module('compare')
    rows = [dict(instance_id='sim.small.1', size='small', passed=True, lifts=8, wall_s=3,
                 parts=dict(relocations=0, hatch_now=4)),
            dict(instance_id='sim.small.2', size='small', passed=True, lifts=100, wall_s=4),
            dict(instance_id='sim.medium.1', size='medium', passed=True, lifts=79, wall_s=5)]
    baseline = {'sim.small.1': dict(passed=True, lifts=9, wall_s=6,
                                   parts=dict(relocations=1, hatch_now=4)),
                'sim.small.2': dict(passed=None, lifts=None),
                'sim.medium.1': dict(passed=None, lifts=None)}
    report = compare.compare(rows, baseline)
    small, medium = report['by_size']['small'], report['by_size']['medium']
    assert small['passes'] == 2 and small['baseline_measured'] == 1
    assert small['mutual_passes'] == 1 and small['mean_lifts'] == 8
    assert small['paired_mean_change_ci95'] == [-1, -1]
    assert small['parts']['relocations']['mean_change'] == -1
    assert small['parts']['restow_later']['mean'] is None
    assert small['sum_wall_s'] == 7
    assert medium['baseline_measured'] == 0 and medium['mutual_passes'] == 0
    assert medium['mean_lifts'] is None and medium['paired_mean_change_ci95'] is None


def test_reject_stale_map_pairs():
    compare = module('compare')
    with pytest.raises(ValueError, match='Mismatched input_sha256'):
        compare.compare([dict(instance_id='x', input_sha256='new')],
                        {'x': dict(input_sha256='old')})


def test_full_baseline_retains_failed_and_unmeasured_evidence():
    compare = module('compare')
    baseline = compare.baseline_rows()
    assert baseline['sim.small.40100034']['passed'] is False
    assert baseline['sim.small.40100034']['lifts'] is None
    assert baseline['random.medium.185100000.0.2']['passed'] is None
    assert baseline['random.small.40100000.0.2']['parts']['relocations'] == 3


def test_new_comparators_and_historical_scope_are_explicit(monkeypatch, tmp_path, capsys):
    compare = module('compare')
    row = dict(instance_id='sim.small.1', input='same-approved-path', size='small',
               passed=True, lifts=8., wall_s=1.)
    monkeypatch.setattr(compare, 'read_rows', lambda path: {row['instance_id']: dict(row)})
    monkeypatch.setattr(compare, 'baseline_rows', lambda: {row['instance_id']: dict(row)})
    compare.main([str(tmp_path), '--engine11', str(tmp_path/'engine11'),
                  '--fresh-e9', str(tmp_path/'fresh')])
    printed = capsys.readouterr().out
    assert 'Historical stored Frontier 0.9.0 (unequal effort/load' in printed
    assert 'Frontier Engine 0.11.0' in printed
    assert 'Fresh Frontier 0.9.0 + E9' in printed

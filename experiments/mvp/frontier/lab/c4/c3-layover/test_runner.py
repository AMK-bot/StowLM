"""Guard benchmark cache identity, paired reporting and arrival conservation."""
from __future__ import annotations

from argparse import Namespace
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import pytest

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('_c4_test_'+name, HERE/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def arguments(**overrides):
    values = dict(budget_s=60., search_s=None, engine_extra_s=None,
        finalize_s=60., tries=2, seed=0, work_config='{}', finishing_config='{}',
        methods=['engine'], size='tiny', occupancy=.3, shard_index=0,
        shard_count=1, map_seed=331401000, out='results/testing', overwrite=False)
    return Namespace(**(values | overrides))


def test_cache_ignores_selectors_but_not_solver_work():
    runner = load('runner')
    baseline = runner.cache_config(arguments())
    assert baseline == runner.cache_config(arguments(out='results/other', shard_count=8,
        shard_index=7, methods=['layover'], map_seed=331401071))
    assert baseline != runner.cache_config(arguments(engine_extra_s=0.))
    assert baseline != runner.cache_config(arguments(finalize_s=300.))
    assert baseline != runner.cache_config(arguments(work_config='{"pricing_calls":4}'))
    assert baseline != runner.cache_config(arguments(finishing_config='{"max_evaluations":8}'))


def test_cohort_spec_is_fresh_balanced_and_recorded():
    cohort = load('cohort')
    assert len(cohort.SPEC) == 72
    assert {seed for _,_,seed in cohort.SPEC} == set(range(331401000, 331401072))
    for size in ('tiny', 'small', 'medium'):
        for occupancy in (.3, .5):
            assert sum(s == size and p == occupancy for s,p,_ in cohort.SPEC) == 12


def test_arm_order_is_counterbalanced_within_every_cell():
    runner, cohort = load('runner'), load('cohort')
    for start in range(0, 72, 12):
        orders = [tuple(runner.arm_order(dict(seed=seed))) for _,_,seed in cohort.SPEC[start:start+12]]
        assert len(set(orders)) == 6
        assert all(orders.count(order) == 2 for order in orders)


def test_report_pairs_only_mutual_passes_and_excludes_failed_objectives():
    report = load('report')
    records = [dict(path='x/a.json', size='tiny', occupancy_target=.3),
        dict(path='x/b.json', size='tiny', occupancy_target=.3)]
    def row(identity, arm, passed, lifts):
        return dict(instance_id=identity, method=arm, passed=passed, lifts=lifts,
            replay_complete=True, wall_s=10., parts=dict(restow_now=2, restow_later=4),
            aboard_restows=dict(now_lifts=1, future_lifts=2))
    rows = [row('a','engine',True,8), row('a','layover',True,5),
        row('b','engine',False,None), row('b','layover',True,99)]
    group = report.aggregate(rows, records)['groups'][0]
    assert group['methods']['engine']['mean_lifts'] == 8
    assert group['methods']['layover']['mean_lifts'] == 52
    assert group['pairs']['engine']['maps'] == 1
    assert group['pairs']['engine']['method_lifts'] == 5
    assert group['pairs']['frontier09']['maps'] == 0


def test_independent_replay_gate_requires_retained_arrival_cargo():
    runner = load('runner')
    instance = SimpleNamespace(load_list=['new'], discharge_list=['due'],
        onboard=SimpleNamespace(placements=[SimpleNamespace(container_id=b) for b in ('due', 'through')]))
    # Full-checker result is deliberately mocked true: the separate replay
    # gate must still expose disappearance of the retained through box.
    count = SimpleNamespace(weighted_nonproductive=4, relocations=0,
        hatch_cover_lifts=2, restow_lifts=2, future=[])
    replay = SimpleNamespace(legal=True,
        final_state=SimpleNamespace(ship=SimpleNamespace(placements=[SimpleNamespace(container_id='new')])))
    checked = SimpleNamespace(passed=True, count=count, violations=[])
    plan = SimpleNamespace(model_dump=lambda **kw: {})
    with patch('stowage.moves.apply_plan', return_value=replay), \
            patch('stowage.checker.check', return_value=checked) as checker, \
            patch.object(runner, 'aboard_restows', return_value={}):
        inspected = runner.inspect_plan(instance, plan)
    checker.assert_called_once_with(instance, plan)
    assert inspected['replay_complete'] is False


def test_future_attribution_missing_is_never_reported_as_zero():
    report = load('report')
    records = [dict(path='x/a.json', size='tiny', occupancy_target=.3)]
    row = dict(instance_id='a', method='engine', passed=True, lifts=8,
        replay_complete=True, wall_s=1., parts=dict(restow_now=2, restow_later=4),
        aboard_restows=dict(now_lifts=2, future_lifts=None))
    group = report.aggregate([row], records)['groups'][0]['methods']['engine']
    assert group['mean_aboard_restow_later'] is None
    assert group['aboard_future_attribution_missing'] == 1


def test_failed_finishing_preserves_complete_raw_replay_witness():
    runner = load('runner')
    from stowage.actions.space import Place
    instance = SimpleNamespace(load_list=['new'], discharge_list=[],
        onboard=SimpleNamespace(placements=[]))
    raw_plan = SimpleNamespace(model_dump=lambda **kw: {'moves': [], 'ballast': None})
    scored = SimpleNamespace(objective=7, plan=raw_plan)
    problem = SimpleNamespace(score=lambda actions: scored)
    replay = SimpleNamespace(legal=True,
        final_state=SimpleNamespace(ship=SimpleNamespace(placements=[SimpleNamespace(container_id='new')])))
    module = SimpleNamespace(
        propose=lambda *a, **kw: [(Place('new', '0010102'),)],
        finish=lambda *a, **kw: (None, {'failed_lane': 'local_lp'}))
    with patch.object(runner, 'load_method', return_value=module), \
            patch('alns.Problem', return_value=problem), \
            patch('stowage.moves.apply_plan', return_value=replay):
        row = runner.run_method(instance, arguments())
    assert row['passed'] is False
    assert row['replay_complete'] is True
    assert row['replay_evidence'] == [dict(index=0, search_lifts=7,
        plan={'moves': [], 'ballast': None})]
    assert row.get('lifts') is None


def test_final_report_rejects_stale_sources_changed_inputs_and_duplicate_rows(tmp_path):
    report = load('report')
    path = tmp_path/'a.json'
    path.write_text('{}')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    record = dict(path='a.json', sha256=digest, size='tiny', occupancy_target=.3, seed=331401000)
    row = dict(instance_id='a', method='engine', complete=True, input='a.json', input_sha256=digest,
        source_sha256={'runtime':'fresh'}, size='tiny', occupancy=.3, passed=False)
    with patch.object(report, 'ROOT', tmp_path):
        checked = report.validate_rows([row], [record], {'engine':{'runtime':'fresh'}}, allow_partial=True)
        assert checked['source_and_input_hashes_match'] is True
        assert checked['complete'] is False
        with pytest.raises(ValueError, match='source provenance'):
            report.validate_rows([row], [record], {'engine':{'runtime':'changed'}}, allow_partial=True)
        with pytest.raises(ValueError, match='duplicate'):
            report.validate_rows([row,row], [record], {'engine':{'runtime':'fresh'}}, allow_partial=True)
        path.write_text('{"modified":true}')
        with pytest.raises(ValueError, match='input hash'):
            report.validate_rows([row], [record], {'engine':{'runtime':'fresh'}}, allow_partial=True)

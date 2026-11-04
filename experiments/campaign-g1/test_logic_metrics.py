"""Logic-review regressions: failed attempts never leave success denominators."""
from types import SimpleNamespace

import pytest

import importlib.util
from pathlib import Path
import sys


def load_driver(name):
    spec = importlib.util.spec_from_file_location("logic_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_random_family_yield_keeps_blocking_ratio_strata():
    campaign = load_driver('campaign')
    rows=[]
    for noise in (0., .8):
        rows.append(dict(stage='4.7', input=dict(rung='tiny', vessel='feeder_1000',
            shard={'split_spec':dict(vessel='feeder_1000',generator='random_yard',
                port='laem_chabang_like',noise=None,blocking_ratio=noise)}),
            measurement=dict(plan_returned=False,checker_passed=None)))
    stats=campaign.solver_yield_summary(rows)['4.7']['per_rung_family']
    assert len(stats)==2, 'random-yard blocking-ratio strata must not merge under noise=None'
    assert {item['family']['noise'] for item in stats}=={0., .8}
    assert {item['family']['ship'] for item in stats}=={'feeder_800'}


def test_timeout_attempts_remain_in_solve_and_checker_denominators():
    campaign = load_driver('campaign')
    rows = [{'measurement': {'plan_returned': True, 'checker_passed': True,
                             'checker_violations': [], 'approved': True}},
            {'measurement': {'plan_returned': False, 'checker_passed': False,
                             'status': 'timeout'}},
            {'measurement': {'plan_returned': True, 'checker_passed': False,
                             'checker_violations': ['hard.rule'], 'status': 'timeout'}}]
    stats = campaign.stage_stats(rows)
    assert stats['pass_pct'] == pytest.approx(100 / 3)
    assert stats['plans_returned'] == 2
    assert stats['plans_returned_per_attempt'] == pytest.approx(2 / 3)
    assert stats['checker_passed_per_attempt'] == pytest.approx(1 / 3)
    assert stats['pass_among_returned_pct'] == 50


def case(stage, rung, digest, *, returned, checked, ship='ship-a', generator='random_yard', **measurement):
    return {'stage': stage, 'input': {'rung': rung, 'sha256': digest, 'vessel': ship,
            'group': f'{generator}/{rung}', 'shard': {'split_spec': {
                'vessel': ship, 'generator': generator, 'port': 'port-a', 'noise': .2}}},
            'measurement': {'plan_returned': returned, 'checker_passed': checked,
                            'checker_violations': [] if checked else ['hard.rule'], **measurement}}


def test_summary_reports_per_rung_family_yield_and_requires_leader_minimum(tmp_path):
    campaign = load_driver('campaign')
    rows = [case('4.7', 'small', 'a', returned=True, checked=True),
            case('4.7', 'small', 'b', returned=False, checked=False),
            case('4.7', 'small', 'c', returned=True, checked=False, ship='ship-b')]
    summary = campaign.synthesize(SimpleNamespace(diagnostic=True), {'epoch_id': 'diagnostic'}, rows, [], tmp_path)
    stage = summary['solver_yield']['4.7']
    assert stage['per_rung']['small']['plans_returned_per_attempt'] == pytest.approx(2/3)
    families = stage['per_rung_family']
    assert len(families) == 2
    first = next(row for row in families if row['family']['ship'] == 'ship-a')
    assert first['attempted'] == 2 and first['plans_returned_per_attempt'] == .5
    assert first['family'] == {'ship': 'ship-a', 'generator': 'random_yard', 'port': 'port-a', 'noise': .2}
    assert summary['min_yield_approval']['status'] == 'leader_approval_required'
    assert summary['min_yield']['small'] is None
    assert summary['g1_met'] is False
    markdown = (tmp_path / 'diagnostic-summary.md').read_text()
    assert 'Checker passed / attempted %' in markdown
    assert 'plans returned' in markdown


def test_small_gap_labels_proven_subset_and_reports_unproved_lower_bound_gap(tmp_path):
    campaign = load_driver('campaign')
    rows = [case('4.6', 'small', 'proved', returned=True, checked=None,
                 independent_proof=True, oracle_checker_passed=True, physical_objective=10),
            case('4.7', 'small', 'proved', returned=True, checked=True, approved=True, objective=12, gap_to_lb=.5),
            case('4.6', 'small', 'unproved', returned=False, checked=None, independent_proof=False),
            case('4.7', 'small', 'unproved', returned=True, checked=True, approved=True,
                 objective=20, gap_to_lb=.25, lower_bound={'weighted': 15})]
    summary = campaign.synthesize(SimpleNamespace(diagnostic=True), {'epoch_id': 'diagnostic'}, rows, [], tmp_path)
    assert summary['exact_vs_scalable_small_gap_population'] == 'on proved-optimal small instances only (n = 1)'
    unproved = summary['scalable_vs_lower_bound_unproved_small']
    assert unproved['matched_count'] == 1 and unproved['attempted_count'] == 1
    assert unproved['rows'][0]['relative_gap_to_objective'] == .25
    assert unproved['rows'][0]['lower_bound'] == 15


@pytest.mark.parametrize('claimed,artifact_present', [(False, True), (True, False)])
def test_returned_plan_flag_matches_committed_candidate_inventory(tmp_path, claimed, artifact_present):
    campaign = load_driver('campaign')
    identity = {'epoch_id': 'diagnostic'}
    result = tmp_path / 'result.json'
    campaign.atomic_json(result, {'attempt_id': 'same-attempt', 'stage': '4.7', 'plan_returned': claimed})
    if artifact_present:
        campaign.atomic_json(tmp_path / 'candidate-plan.json', {'instance_id': 'instance', 'moves': []})
    marker = tmp_path / 'done.json'
    campaign.write_done(marker, identity, result)
    assert campaign.verified_done(marker, identity) is None

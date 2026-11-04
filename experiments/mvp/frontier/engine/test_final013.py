"""Integration guards for the final three frozen 0.13 additions."""
from dataclasses import dataclass, replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from . import planner, peel, finishing, lifts
from .config import Config
from .test_planner import _setup


@dataclass
class Result:
    plan: object
    planner: str = 'frontier.engine'
    planner_version: str = '0.13.0'

    def model_copy(self, *, update):
        return replace(self, **update)


def fixture(monkeypatch, *, passed=True, size='medium'):
    instance, config, calls = _setup(monkeypatch, bound=1., costs=[4], passed={4} if passed else set())
    instance.size_class = size
    def finish(instance, plan, **kwargs):
        calls.append(plan)
        return (Result(plan) if passed else None), dict(passed=passed, weighted=4. if passed else None)
    monkeypatch.setattr(finishing, 'finish_chain', finish)
    monkeypatch.setattr(planner, 'check', lambda instance, plan: NS(
        passed=passed, count=NS(weighted_nonproductive=float(plan))))
    return instance, replace(config, assignment_lp=False, bound=False,
        unary_certificate=False, cached_mask=False, targeted_constructor=False,
        certified_start=False, witness_start=False, warm_polish=False,
        post_pass_exchange=False), calls


def test_checked_medium_pass_survives_failing_polisher(monkeypatch):
    instance, config, _ = fixture(monkeypatch)
    def broken(*args, **kwargs):
        raise RuntimeError('late lift polish failure')
    monkeypatch.setattr(lifts, 'polish_checked', broken)
    ladder = Mock(side_effect=AssertionError('ladder cannot run after a pass'))
    monkeypatch.setattr(peel, 'no_pass', ladder)
    result, row = planner.plan_and_check(instance, budget_s=0., config=config)
    assert result.plan == '4' and row['passed'] and row['lifts'] == 4.
    assert 'late lift polish failure' in row['stages']['lift_polish']['errors'][0]
    ladder.assert_not_called()


@pytest.mark.parametrize('passed,cost,accepted', [(True, 3., True), (True, 4., False),
                                               (False, 3., False), (True, 5., False)])
def test_polished_candidate_needs_fresh_full_pass_and_fewer_lifts(monkeypatch, passed, cost, accepted):
    instance, config, _ = fixture(monkeypatch)
    candidate = NS(cost=cost)
    monkeypatch.setattr(lifts, 'polish_checked', lambda *a, **k: (candidate, {'accepted': True}))
    def check(instance, plan):
        return NS(passed=passed if plan is candidate else True,
                  count=NS(weighted_nonproductive=cost if plan is candidate else 4.))
    monkeypatch.setattr(planner, 'check', check)
    result, row = planner.plan_and_check(instance, budget_s=0., config=config)
    assert row['passed'] and row['lifts'] == (cost if accepted else 4.)
    assert (result.plan is candidate) == accepted
    assert row['winner_lane'] == ('lift_polish' if accepted else 'frontier')


@pytest.mark.parametrize('level', [0, 1, 3])
def test_relaxed_ladder_rows_never_become_passes(monkeypatch, level):
    instance, config, _ = fixture(monkeypatch, passed=False)
    found = dict(relaxed=True, passed=False, completed={'level': level, 'full_checker_passed': False},
                 best_safe_partial={'percent_aboard': 75.}, level=level,
                 rules_broken=['expert.e8.away_from_heat'], relaxed_plan={'not': 'an L0 result'})
    ladder = Mock(return_value=found)
    monkeypatch.setattr(peel, 'no_pass', ladder)
    result, row = planner.plan_and_check(instance, budget_s=0., config=config)
    assert result is None and row['passed'] is False and not row['proved_optimal']
    assert row['level'] == level and row['rules_broken'] == ['expert.e8.away_from_heat']
    assert row['best_safe_partial']['percent_aboard'] == 75.
    assert ladder.call_args.kwargs['ladder'] is None
    assert ladder.call_args.kwargs['budgets'] == dict(proposal_s=240., finish_s=120., max_candidates=2)


@pytest.mark.parametrize('size', ['tiny', 'small'])
def test_tiny_small_outcomes_are_identical_with_final_additions_enabled(monkeypatch, size):
    from .assignment import method as assignment
    instance, config, _ = fixture(monkeypatch, size=size)
    runtime = Mock(side_effect=AssertionError('small LP runtime'))
    polish = Mock(side_effect=AssertionError('small lift polish'))
    ladder = Mock(side_effect=AssertionError('ladder after a pass'))
    monkeypatch.setattr(assignment, '_runtime', runtime)
    monkeypatch.setattr(lifts, 'polish_checked', polish)
    monkeypatch.setattr(peel, 'no_pass', ladder)
    outcomes = []
    for enabled in (False, True):
        result, row = planner.plan_and_check(instance, budget_s=0., config=replace(config,
            assignment_lp=enabled, lift_polish=enabled, peel_ladder=enabled))
        outcomes.append((result.plan, row['passed'], row['lifts'], row['winner'], row['archive']))
    assert outcomes[0] == outcomes[1]
    runtime.assert_not_called(); polish.assert_not_called(); ladder.assert_not_called()


def test_final_defaults_and_stage_switches():
    config = Config()
    assert (config.assignment_budget_s, config.assignment_finalize_s, config.assignment_tries) == (300., 180., 2)
    assert config.assignment_lp and config.lift_polish and config.peel_ladder
    assert config.lift_polish_s == 30.


def test_relaxed_row_reporting_preserves_tier_fields():
    from .bench.admission import outcome_row
    row = dict(passed=False, method_metadata={'engine': dict(completed={'level': 2},
        best_safe_partial={'percent_aboard': 90.}, rules_broken=['expert.e8.away_from_heat'],
        level=2, winner_lane=None)})
    outcome_row(row)
    assert row['passed'] is False and row['level'] == 2
    assert row['rules_broken'] == ['expert.e8.away_from_heat']


def test_peel_runtime_manifests_participate_in_source_identity(tmp_path, monkeypatch):
    from . import provenance
    monkeypatch.setattr(provenance, 'HERE', tmp_path)
    for name in ('tiers.toml', 'approved-maps.json', 'peel_ladder/copied-manifest.json',
                 'peel_ladder/assignment-frozen-hashes.json'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('initial')
    previous = provenance.source_digest()
    (tmp_path / 'peel_ladder/copied-manifest.json').write_text('changed')
    assert provenance.source_digest() != previous

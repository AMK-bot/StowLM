"""Public after-pass adapter gates; no vessel solve or archived input."""
from types import SimpleNamespace as NS

import pytest

from alns import Scored
from stowage.actions.space import Place

from . import checked

_real_setup = checked._setup


def report(passed=True, lifts=10., *, bound=True):
    return NS(passed=passed, count=NS(weighted_nonproductive=lifts), shut_out=[],
              parts={'native': NS(ran=True)}, bound=bound)


@pytest.fixture
def case(monkeypatch):
    instance = NS(id='map', load_list=['a'], discharge_list=[], onboard=NS(placements=[]))
    recipe = object()
    plan = NS(ballast=recipe)
    candidate = NS(ballast=recipe)
    original_report = report()
    monkeypatch.setattr(checked, 'report_matches_inputs', lambda r, *_: r.bound)
    monkeypatch.setattr(checked, 'get_action_space', lambda *args: NS(from_plan=lambda *args: [Place('a', 'slot')]))
    monkeypatch.setattr(checked, 'apply_plan', lambda *args: NS(legal=True,
        final_state=NS(ship=NS(placements=[NS(container_id='a')]))))
    monkeypatch.setattr(checked, '_setup', lambda *args: (object(), object()))
    return instance, plan, candidate, original_report


def test_polish_exception_preserves_exact_checked_plan_and_recipe(case, monkeypatch):
    instance, plan, _, original_report = case
    def fail(*args, **kwargs):
        assert args[1].plan is plan
        raise RuntimeError('optional local search failed')
    monkeypatch.setattr(checked, 'polish', fail)
    result, meta = checked.polish_checked(instance, plan, original_report, problem=object())
    assert result is plan and result.ballast is plan.ballast
    assert meta['held_plan_retained'] and not meta['improved']
    assert 'optional local search failed' in meta['error']


@pytest.mark.parametrize('passed,lifts,accept', [(True, 8., True), (False, 8., False),
                                               (True, 10., False), (True, 12., False)])
def test_only_strictly_cheaper_full_native_pass_can_replace_incumbent(case, monkeypatch, passed, lifts, accept):
    instance, plan, candidate, original_report = case
    seen = []
    monkeypatch.setattr(checked, 'polish', lambda *args, **kwargs: Scored((), 1., candidate))
    monkeypatch.setattr(checked, 'check', lambda _, concrete: seen.append(concrete) or report(passed, lifts))
    result, meta = checked.polish_checked(instance, plan, original_report, problem=object())
    assert seen == [candidate]
    assert result is (candidate if accept else plan)
    assert meta['improved'] is accept
    if accept:
        assert meta['lifts'] == 8. and meta['saved_lifts'] == 2.
        assert result.ballast is plan.ballast


def test_final_checker_exception_preserves_input_pass(case, monkeypatch):
    instance, plan, candidate, original_report = case
    monkeypatch.setattr(checked, 'polish', lambda *args, **kwargs: Scored((), 1., candidate))
    def fail(*args):
        raise ArithmeticError('native check failed')
    monkeypatch.setattr(checked, 'check', fail)
    result, meta = checked.polish_checked(instance, plan, original_report, problem=object())
    assert result is plan and meta['status'] == 'incumbent_retained_exception'


def test_bad_report_binding_needs_new_full_check(case, monkeypatch):
    instance, plan, _, original_report = case
    original_report.bound = False
    seen = []
    monkeypatch.setattr(checked, 'check', lambda *args: seen.append(args) or report(False))
    monkeypatch.setattr(checked, '_setup', lambda *args: pytest.fail('invalid pass must not enter search'))
    result, meta = checked.polish_checked(instance, plan, original_report, problem=object())
    assert result is plan and seen == [(instance, plan)]
    assert meta['status'] == 'input_not_a_bound_full_pass'


def test_arrival_and_short_budget_keep_the_checked_incumbent(case, monkeypatch):
    instance, plan, _, original_report = case
    monkeypatch.setattr(checked, '_setup', lambda *args: pytest.fail('skipped family must not enter setup'))
    instance.onboard.placements = [object()]
    result, meta = checked.polish_checked(instance, plan, original_report, problem=object())
    assert result is plan and meta['status'] == 'outside_empty_arrival_family'
    instance.onboard.placements = []
    result, meta = checked.polish_checked(instance, plan, original_report, budget_s=0., problem=object())
    assert result is plan and meta['status'] == 'insufficient_search_allowance'


def test_setup_exception_keeps_the_checked_incumbent(case, monkeypatch):
    instance, plan, _, original_report = case
    def fail(*args):
        raise ValueError('outside copied page family')
    monkeypatch.setattr(checked, '_setup', fail)
    result, meta = checked.polish_checked(instance, plan, original_report, problem=object())
    assert result is plan and meta['input_full_pass']


def test_setup_uses_checked_departure_tanks(case, monkeypatch):
    from ..assignment import pages_sight, gap, chainlab
    instance, plan, _, _ = case
    seen = []
    ship = NS(tank_recipe=plan.ballast)
    monkeypatch.setattr(checked, 'departure_condition', lambda i, p: seen.append((i, p)) or ship)
    monkeypatch.setattr(pages_sight, 'Model', lambda *args, **kwargs: 'model')
    monkeypatch.setattr(chainlab, 'condition_terms', lambda i, s: seen.append(s) or 'terms')
    monkeypatch.setattr(chainlab, 'motions', lambda terms: 'checked-motions')
    monkeypatch.setattr(gap, 'Assignment', lambda model, motion, settings: seen.append((model, motion)) or 'solver')
    assert _real_setup(instance, plan, object()) == ('model', 'solver')
    assert seen == [(instance, plan), ship, ('model', 'checked-motions')]

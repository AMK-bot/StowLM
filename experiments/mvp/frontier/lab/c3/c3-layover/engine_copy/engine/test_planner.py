"""Admission and proof decisions, independently of expensive physical solving."""
from types import SimpleNamespace
from unittest.mock import Mock

from stowage.actions.space import Place
from . import planner
from .config import Config
from . import bounds, finishing
from .scout import method as pages


def _setup(monkeypatch, *, bound, costs, passed):
    instance = SimpleNamespace(load_list=['box'])
    scored = {str(cost): SimpleNamespace(actions=(Place('box', str(cost)),),
              objective=float(cost), plan=str(cost)) for cost in costs}
    problem = SimpleNamespace(score=lambda actions: scored[actions[0].slot])
    monkeypatch.setattr(planner, 'Problem', lambda instance: problem)
    monkeypatch.setattr(planner, 'get_action_space', lambda name: SimpleNamespace(
        from_plan=lambda instance, plan: scored[plan].actions))
    monkeypatch.setattr(bounds, 'map_bound', lambda *args, **kwargs: (bound, {'scope': 'map'}))
    monkeypatch.setattr(bounds, 'relocation_bound', lambda *args, **kwargs: {'scope': 'fixed'})
    monkeypatch.setattr(pages, 'propose', lambda *args, **kwargs: [])
    finish_calls = []
    def finish(instance, plan, **kwargs):
        finish_calls.append(plan)
        result = Mock() if float(plan) in passed else None
        if result:
            result.plan = plan
            result.model_copy.return_value = result
        return result, dict(passed=result is not None, weighted=float(plan) if result else None)
    monkeypatch.setattr(finishing, 'finish_chain', finish)
    def collect(instance, **kwargs):
        return planner.portfolio.StartBank(problem,
            [('constructor', scored[str(cost)]) for cost in costs], None, None, None, set(), {})
    monkeypatch.setattr(planner.portfolio, 'collect_starts', collect)
    config = Config(e9_fix=False, search=False, fusion=False, classes=False)
    return instance, config, finish_calls


def test_cheapest_failure_does_not_hide_later_full_pass(monkeypatch):
    instance, config, calls = _setup(monkeypatch, bound=1., costs=[2, 3, 4], passed={3, 4})
    result, record = planner.plan_and_check(instance, budget_s=1., config=config)
    assert calls == ['2', '3']
    assert result is not None and record['lifts'] == 3.
    assert record['proved_optimal'] is False and record['gap'] == 2.
    assert record['replay_legal_stows'] == 3


def test_bound_equality_requires_full_gate_and_stops_after_pass(monkeypatch):
    instance, config, calls = _setup(monkeypatch, bound=2., costs=[2, 3], passed={2})
    result, record = planner.plan_and_check(instance, budget_s=1., config=config)
    assert calls == ['2'] and result is not None
    assert record['proved_optimal'] is True and record['gap'] == 0.
    assert record['stop_reason'] == 'full_checker_pass_matches_map_lower_bound'


def test_bound_never_discards_replay_candidate(monkeypatch):
    # Even an inconsistent diagnostic bound cannot itself reject a candidate.
    instance, config, calls = _setup(monkeypatch, bound=10., costs=[2], passed={2})
    result, record = planner.plan_and_check(instance, budget_s=1., config=config)
    assert calls == ['2'] and result is not None
    assert record['proved_optimal'] is False


def test_optional_constructor_error_does_not_erase_viable_incumbent(monkeypatch):
    instance, config, calls = _setup(monkeypatch, bound=1., costs=[2], passed={2})
    def broken_pages(*args, **kwargs):
        raise RuntimeError('optional page error')
    monkeypatch.setattr(pages, 'propose', broken_pages)
    result, record = planner.plan_and_check(instance, budget_s=1., config=config)
    assert result is not None and record['lifts'] == 2.
    assert record['stages']['starts']['errors'] == ['RuntimeError: optional page error']


def test_constructor_finish_callback_uses_frozen_signature(monkeypatch):
    instance, config, calls = _setup(monkeypatch, bound=2., costs=[2], passed={2})
    def collector(instance, **kwargs):
        kwargs['finish'](instance, '2', label='blocked', seed=0, finalize_s=300.)
        raise AssertionError('bound equality should have stopped construction')
    monkeypatch.setattr(planner.portfolio, 'collect_starts', collector)
    result, record = planner.plan_and_check(instance, budget_s=1., config=config)
    assert result is not None and calls == ['2']
    assert record['proved_optimal'] is True
    assert record['replay_legal_stows'] == 1


def test_tiny_fractional_gap_is_not_an_optimality_certificate(monkeypatch):
    instance, config, calls = _setup(monkeypatch, bound=0., costs=[1.e-8], passed={1.e-8})
    result, record = planner.plan_and_check(instance, budget_s=1., config=config)
    assert result is not None and record['proved_optimal'] is False
    assert record['gap'] == 1.e-8


def test_accelerated_finish_exception_retains_unchanged_v4_lane(monkeypatch):
    instance, config, calls = _setup(monkeypatch, bound=1., costs=[2], passed={2})
    from .frozen import planner as frozen
    good_result = Mock()
    good_result.plan = '2'
    good_result.model_copy.return_value = good_result
    def broken(*args, **kwargs):
        raise ArithmeticError('optional numerical path')
    native = Mock(return_value=(good_result, {'passed': True, 'weighted': 2.}))
    monkeypatch.setattr(finishing, 'finish_chain', broken)
    monkeypatch.setattr(frozen, '_finish', native)
    result, record = planner.plan_and_check(instance, budget_s=1., config=config)
    assert result is good_result and native.call_count == 1
    assert native.call_args.kwargs['finalize_s'] == 600.
    assert record['tries'][0]['acceleration_error'] == 'ArithmeticError: optional numerical path'


def test_medium_finish_ablation_also_covers_frozen_large_fallback(monkeypatch):
    instance, _, calls = _setup(monkeypatch, bound=1., costs=[2], passed={2})
    from .frozen import planner as frozen
    class MediumCall(list):
        def __len__(self):
            return 61
    instance.load_list = MediumCall(['box'])
    native = Mock(side_effect=AssertionError('native finisher must be disabled'))
    monkeypatch.setattr(frozen, '_finish', native)
    monkeypatch.setattr(planner, 'check', lambda *args: SimpleNamespace(
        passed=True, count=SimpleNamespace(weighted_nonproductive=2.)))
    def result(**kwargs):
        value = Mock()
        value.plan = kwargs['plan']
        value.model_copy.return_value = value
        return value
    monkeypatch.setattr(planner, 'PlannerResult', result)
    def big(instance, **kwargs):
        value, row = frozen._finish(instance, '2', label='big-check', seed=0, finalize_s=600.)
        return value, row
    monkeypatch.setattr(frozen, 'plan_big', big)
    config = Config(e9_fix=False, bound=False, search=False, fusion=False,
                    classes=False, finish=False)
    value, record = planner.plan_and_check(instance, budget_s=1000., config=config)
    assert value is not None and record['passed'] is True
    assert native.call_count == 0
    assert frozen._finish is native


def test_acceleration_exception_cannot_preserve_rejected_ballast(monkeypatch):
    instance, config, _ = _setup(monkeypatch, bound=1., costs=[2], passed={2})
    from .frozen import planner as frozen

    class Recipe(str):
        ballast = 'rejected'

        def model_copy(self, *, update):
            assert update == {'ballast': None}
            bare = Recipe(self)
            bare.ballast = None
            return bare

    scored = planner.Problem(instance).score((Place('box', '2'),))
    scored.plan = Recipe('2')
    monkeypatch.setattr(finishing, 'finish_chain', Mock(side_effect=ArithmeticError('LP')))
    native = Mock(return_value=(None, {'passed': False}))
    monkeypatch.setattr(frozen, '_finish', native)
    planner.plan_and_check(instance, budget_s=1., config=config)
    assert native.call_count == 1
    assert native.call_args.args[1] == scored.plan
    assert native.call_args.args[1].ballast is None
    assert scored.plan.ballast == 'rejected'

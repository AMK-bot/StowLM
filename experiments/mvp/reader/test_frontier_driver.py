"""Dependency seam tests; real-map/full-checker results live in BINDING.md."""
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.reader import frontier_driver as driver
from experiments.mvp.reader.test_constraints import FakeEngine, synthetic
from experiments.mvp.reader.constraints import compile_one_port_per_hatch


def test_binding_shares_exact_code_and_isolates_globals():
    original = driver.portfolio.plan_and_check
    marker = object()
    bound = driver._bind(original, {'Problem': marker})
    assert bound.__code__ is original.__code__
    assert bound.__globals__['Problem'] is marker
    assert original.__globals__['Problem'] is not marker
    assert bound.__kwdefaults__ == original.__kwdefaults__


def test_constructor_engine_hook_filters_and_restores(monkeypatch):
    import stowage.solver.twostep as twostep
    instance = synthetic()
    compiled = compile_one_port_per_hatch(instance, {'A': 'SGSIN', 'B': 'MYPKG'})
    original = lambda inst, *args, **kwargs: FakeEngine(inst)
    monkeypatch.setattr(twostep, 'make_feasibility_engine', original)
    with pytest.raises(RuntimeError, match='forced'):
        with driver._constructor_rule(compiled):
            engine = twostep.make_feasibility_engine(instance)
            assert '0030102' not in engine.legal_slots_for_box('x')
            assert '0030102' not in engine.copy().legal_slots_for_box('x')
            assert not engine.T.static[0, 2]
            assert twostep.make_feasibility_engine(NS(id='other')).__class__ is FakeEngine
            with pytest.raises(RuntimeError, match='already active'):
                with driver._constructor_rule(compiled):
                    pass
            raise RuntimeError('forced')
    assert twostep.make_feasibility_engine is original


def _tiny_portfolio(instance, **kwargs):
    # These global dependency names are rebound by the production driver.
    problem = Problem(instance)
    first = problem.score(())
    solve_master(problem, workers=2)
    result, row = _finish(instance, first.plan, seed=0, finalize_s=1, label='test')
    return result, {'tries': [row]}


def test_finalizer_instruction_gate_and_master_worker_cap(monkeypatch):
    instance = NS(id='test')
    calls = []
    constrained = NS(instance=instance, forbids=lambda *args: None,
                     violations=lambda plan: ['bad'] if plan == 'changed' else [])
    monkeypatch.setattr(driver.portfolio, 'plan_and_check', _tiny_portfolio)
    monkeypatch.setattr(driver, 'constrain_frontier_problem', lambda p, c: p)
    monkeypatch.setattr(driver.portfolio, 'Problem', lambda i: NS(score=lambda a: NS(plan='good')))
    monkeypatch.setattr(driver.portfolio, 'solve_master', lambda p, **kw: calls.append(kw['workers']))
    monkeypatch.setattr(driver.portfolio, '_finish',
                        lambda *a, **kw: (NS(plan='changed'), {'passed': True}))
    result, record = driver.plan_and_check(instance, constrained, workers=1)
    assert result is None
    assert calls == [1]
    assert record['tries'][0]['passed'] is False
    assert record['tries'][0]['instruction_violations'] == ['bad']
    assert record['instruction_audit']['finish_rejected'] == 1


def test_scored_start_rejected_before_portfolio_admission(monkeypatch):
    instance = NS(id='test')
    constrained = NS(instance=instance, forbids=lambda *args: None, violations=lambda plan: ['bad'])
    def run(instance, **kwargs):
        assert Problem(instance).score(()) is None
        return None, {}
    monkeypatch.setattr(driver.portfolio, 'plan_and_check', run)
    monkeypatch.setattr(driver, 'constrain_frontier_problem', lambda p, c: p)
    monkeypatch.setattr(driver.portfolio, 'Problem', lambda i: NS(score=lambda a: NS(plan='bad')))
    result, record = driver.plan_and_check(instance, constrained)
    assert result is None
    assert record['instruction_audit']['rejected'] == 1


def test_bad_workers_and_wrong_instance():
    with pytest.raises(ValueError, match='workers'):
        driver.plan_and_check(NS(id='a'), workers=3)
    with pytest.raises(ValueError, match='another instance'):
        driver.plan_and_check(NS(id='a'), NS(instance=NS(id='b')))


def test_standing_preference_only_toggles_blocked_start(monkeypatch):
    calls = []
    def run(instance, **kwargs):
        two = TwoStepPlanner().plan(instance)
        blocked = blocked_plan(instance)
        calls.append((two.plan, blocked.plan, blocked.status))
        return None, {}
    monkeypatch.setattr(driver.portfolio, 'plan_and_check', run)
    monkeypatch.setattr(driver.portfolio, 'TwoStepPlanner',
                        lambda **kw: NS(plan=lambda inst, **opts:NS(plan='two')))
    monkeypatch.setattr(driver.portfolio, 'blocked_plan', lambda inst, **kw:NS(plan='blocked', status='ok'))
    instance = NS(id='x')
    _, enabled = driver.plan_and_check(instance, enable_port_blocked=True)
    _, disabled = driver.plan_and_check(instance, enable_port_blocked=False)
    assert calls == [('two','blocked','ok'), ('two',None,'disabled_by_standing_brief')]
    assert enabled['port_blocked_enabled'] is True
    assert disabled['port_blocked_enabled'] is False

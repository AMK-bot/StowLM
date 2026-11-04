"""Deterministic work policy and coupled equilibrium under slow contraction."""
from types import SimpleNamespace
import numpy as np
import pytest
from stowage.checker import stability_budget as clock
from stowage.checker.stability_equilibrium import floating_equilibrium


def test_nested_diagnostic_work_limit_is_not_renewed():
    with pytest.raises(clock.StabilityTimeout):
        with clock.diagnostic_budget(2):
            clock.check_budget('first')
            with clock.diagnostic_budget(100):
                clock.check_budget('second')
                clock.check_budget('outer exhausted')
    clock.check_budget('context restored')


def test_scheduler_delay_does_not_reject_completed_physics(monkeypatch):
    import stowage.checker.stability as module
    from stowage.model import PartReport
    from stowage.model.fixtures import build_instance
    now=[0.]
    import time
    monkeypatch.setattr(time,'monotonic',lambda:now[0])
    def late(*args,**kwargs):
        now[0]=31.
        return PartReport(name='stability')
    monkeypatch.setattr(module,'_check_stability',late)
    instance=build_instance()
    report=module.check_stability(instance,instance.onboard,call_index=0)
    assert report.ran
    assert not report.violations
    assert 'elapsed_s' not in report.metrics


def test_cache_hit_and_miss_poll_counts_do_not_change_verdict():
    for polls in (1, 10000):
        with clock.budget(1e-9):
            for _ in range(polls):
                clock.check_budget('cache-dependent work')


def test_fixed_numerical_failure_is_hard_and_report_clock_free(monkeypatch):
    from stowage.checker import stability
    from stowage.model.fixtures import build_instance
    def exhausted(*args, **kwargs):
        raise ValueError('fixed numerical iteration cap exhausted')
    monkeypatch.setattr(stability, '_check_stability', exhausted)
    instance = build_instance()
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id == 'checker.internal_error'
    assert report.violations[0].severity == 'hard'
    assert report.metrics == {'computation_failed': 1., 'diagnostic_work_limit_exceeded': 0.}


@pytest.mark.parametrize('value',['0','-1','nan','inf','broken'])
def test_invalid_budget_fails_closed(monkeypatch,value):
    from stowage.checker.stability import check_stability
    from stowage.model.fixtures import build_instance
    monkeypatch.setenv('STOWAGE_STABILITY_TIME_BUDGET_S',value)
    instance=build_instance()
    report=check_stability(instance,instance.onboard,call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id=='checker.internal_error'


def test_slow_liquid_fixed_point_uses_coupled_root():
    count=[0]
    def equilibrium(mass,x,y,z,rho):
        count[0]+=1
        return SimpleNamespace(heel_rad=y,trim_m=0.)
    condition=SimpleNamespace(mass_kg=1.,lcg_m=0.,tcg_m=0.,kg_m=2.)
    result=floating_equilibrium(SimpleNamespace(equilibrium=equilibrium),condition,1025.,100.,
        lambda c,heel,trim:np.array([0.,.95*heel+.05,2.]))
    assert result.heel_rad==pytest.approx(1.,abs=1e-7)
    assert count[0]<30


def test_unsolvable_liquid_equilibrium_is_rejected():
    condition=SimpleNamespace(mass_kg=1.,lcg_m=0.,tcg_m=0.,kg_m=2.)
    solver=SimpleNamespace(equilibrium=lambda m,x,y,z,r:SimpleNamespace(heel_rad=y,trim_m=0.))
    with pytest.raises(ValueError,match='did not converge'):
        floating_equilibrium(solver,condition,1025.,100.,
            lambda c,heel,trim:np.array([0.,heel+1.,2.]))

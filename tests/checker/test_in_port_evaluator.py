"""D64 rev3 evaluator: load line, berth, errors and continuation guards."""
import math
from types import SimpleNamespace

import pytest

from stowage.model.fixtures import build_instance
from stowage.checker.in_port_evaluator import draught_limits, equilibrium_nonconvergence, near_limit


def test_t12_load_line_uses_mean_and_density_allowance():
    i = build_instance()
    i.vessel.hull.max_draught_m = 5.
    i.port.water_density_kg_m3 = 1000.
    f = SimpleNamespace(t_aft_m=5.6, t_fwd_m=4.5, mean_draught_m=5.05, heel_rad=0.)
    g = SimpleNamespace(full_submerged=lambda *args: SimpleNamespace(waterplane_area_m2=1000.))
    metrics, margins, broken = draught_limits(i, g, f, 10_000_000.)
    assert metrics['fresh_water_allowance_m'] == pytest.approx(10_000_000./1000./(4.*10.25)/1000.)
    assert margins['draught_mean_m'] == pytest.approx(5.+10_000_000./1000./(4.*10.25)/1000.-5.05)
    assert not broken


def test_t12_berth_adds_low_side_immersion():
    i = build_instance()
    i.vessel.hull.max_draught_m = 10.
    i.port.max_draught_m = 4.01
    f = SimpleNamespace(t_aft_m=4., t_fwd_m=3.9, mean_draught_m=3.95, heel_rad=math.radians(2.))
    g = SimpleNamespace(full_submerged=lambda *args: SimpleNamespace(waterplane_area_m2=1000.))
    metrics, margins, broken = draught_limits(i, g, f, 10_000_000.)
    assert margins['port_draught_m'] < 0
    assert metrics['berth_draught_m'] == pytest.approx(4.+i.vessel.hull.beam_m/2*math.sin(f.heel_rad))
    assert broken[0][3].startswith('port_draught_m')


@pytest.mark.parametrize('message,expected', [
    ('no converged floating equilibrium (scaled residual 3)', True),
    ('liquid/hull equilibrium root did not converge', True),
    ('nonfinite or unavailable upright equilibrium draughts', False),
    ('divide by zero', False), ('missing geometry', False)])
def test_only_nonconvergence_is_p1(message, expected):
    assert equilibrium_nonconvergence(ValueError(message)) is expected


def test_warm_guard_bands_are_not_allowances():
    assert near_limit({'gm_m': .151}, {'gm_m': .001})
    assert near_limit({}, {'draught_mean_m': -.01})
    assert near_limit({}, {'list_rad': math.radians(.1)})
    assert not near_limit({'gm_m': 1.}, {'gm_m': .85, 'trim_m': .5})


def test_continuation_solves_same_mass_moment_equations():
    import numpy as np
    from stowage.checker.in_port_evaluator import _Continuation
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.vessel.hydrostatics import FloatingCondition
    class Geometry:
        beam_m = 10.
        def submerged(self, ta, tf, heel, rho):
            return SimpleNamespace(volume_m3=(ta+tf)*100.,
                centroid_m=(50.+ta-tf, heel*10., 2.))
        _solver_submerged = submerged
        def _axes(self, *args):
            return None, np.array([1., 0., 0.]), np.array([0., 1., 0.])
    continuation = _Continuation()
    continuation.previous = FloatingCondition(2., 2., 0., None, 0.)
    c = LoadingCondition(410_000., 50.2, .1, 2., 0., 0.)
    result = continuation.solve(Geometry(), c, 1000., 100., 1)
    assert result.t_aft_m == pytest.approx(2.15)
    assert result.t_fwd_m == pytest.approx(1.95)
    assert result.heel_rad == pytest.approx(.01)
    assert result.residual_norm < 2e-9


def test_cold_guard_is_applied_with_direct_geometry(monkeypatch):
    # The production guard must remain active when direct geometry is forced.
    # Exercise its decision with a real primitive-shaped synthetic condition.
    import numpy as np
    from stowage.checker import stability, stability_liquid, stability_tables
    from stowage.checker.in_port_evaluator import _Continuation, check_state
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.vessel.hydrostatics import FloatingCondition
    i = build_instance()
    condition = LoadingCondition(1e6, 50., 0., 3., .1, 1e5)
    f = FloatingCondition(2., 2., 0., None, 0.)
    context = SimpleNamespace(correct_loading=lambda c:c, max_fsm_tank_ids=())
    geometry = SimpleNamespace(equilibrium=lambda *args:f)
    calls = []
    monkeypatch.setattr(stability, '_canonical_fleet_policy', lambda *args:None)
    monkeypatch.setattr(stability, '_context', lambda *args:(geometry, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *args, **kw:condition)
    def criteria(*args):
        calls.append(1)
        return {'gm_m':.151}, {'gm_m':.001}, []
    monkeypatch.setattr(stability, '_in_port_criteria', criteria)
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT','1')
    continuation = _Continuation()
    continuation.previous = f
    monkeypatch.setattr(continuation, 'solve', lambda *args:f)
    report = check_state(i, i.onboard, context, continuation)
    assert report.ran and not report.violations, report.error
    assert len(calls) == 2
    assert report.metrics['in_port_cold_limit_fallback'] == 1.


def test_nonfinite_floating_output_is_internal_error(monkeypatch):
    from stowage.checker import stability
    from stowage.checker.in_port_evaluator import _Continuation, check_state
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.vessel.hydrostatics import FloatingCondition
    i = build_instance()
    c = LoadingCondition(1e6, 50., 0., 3., .1, 1e5)
    f = FloatingCondition(float('nan'), 2., 0., None, 0.)
    context = SimpleNamespace(correct_loading=lambda c:c, max_fsm_tank_ids=())
    geometry = SimpleNamespace(equilibrium=lambda *args:f)
    monkeypatch.setattr(stability, '_canonical_fleet_policy', lambda *args:None)
    monkeypatch.setattr(stability, '_context', lambda *args:(geometry, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *args, **kw:c)
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT','1')
    report = check_state(i, i.onboard, context, warm_start=False)
    assert not report.ran
    assert report.error
    assert {v.rule_id for v in report.violations} == {'checker.internal_error'}


def test_worked_fsm_applied_before_method2_equilibrium(monkeypatch):
    from dataclasses import replace
    from stowage.checker import stability
    from stowage.checker.in_port_evaluator import check_state
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.vessel.hydrostatics import FloatingCondition
    i = build_instance()
    original = LoadingCondition(1e6, 50., 0., 3., .1, 1e5)
    context = SimpleNamespace(correct_loading=lambda c:replace(c, fsc_m=.3, fsm_kgm=3e5),
                              max_fsm_tank_ids=('worked',))
    calls = []
    def equilibrium(*args):
        calls.append(args)
        return FloatingCondition(2., 2., 0., None, 0.)
    g = SimpleNamespace(equilibrium=equilibrium)
    monkeypatch.setattr(stability, '_canonical_fleet_policy', lambda *args:None)
    monkeypatch.setattr(stability, '_context', lambda *args:(g, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *args, **kw:original)
    def criteria(instance, ship, geometry, params, condition, floating, method):
        assert method == 2
        assert condition.fsc_m == .3
        return {'gm_m': 1.}, {'gm_m': .85}, []
    monkeypatch.setattr(stability, '_in_port_criteria', criteria)
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT','1')
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD','2')
    report = check_state(i, i.onboard, context, warm_start=False)
    assert report.ran and not report.violations, report.error
    assert len(calls) == 1
    assert calls[0][3] == pytest.approx(3.3)


def test_state_timings_are_separate_and_median_uses_only_first_eight(monkeypatch):
    from stowage.checker import in_port_evaluator as module
    from stowage.model import PartReport
    i = build_instance()
    durations = [.01, .02, .03, .04, .05, .06, .07, .08, 10., 20.]
    ticks = iter(value for n, duration in enumerate(durations)
                 for value in (n*100., n*100.+duration))
    monkeypatch.setattr(module, 'monotonic', lambda: next(ticks))
    expected = PartReport(name='in_port', metrics={'gm_m': 1.}, margins={'gm_m': .85})
    monkeypatch.setattr(module, 'check_state', lambda *args, **kwargs: expected.model_copy(deep=True))
    with module.InPortEvaluator(i, SimpleNamespace()) as evaluator:
        batch = evaluator.evaluate([i.onboard]*len(durations))
    assert batch.state_times_s == pytest.approx(durations)
    assert batch.measured_state_time_s == pytest.approx(.045)
    assert batch.measured_state_count == 8
    assert batch.total_states == 10 and batch.failure_index is None and batch.enabled
    assert all(report.model_dump_json() == expected.model_dump_json() for report in batch.reports)


def test_timed_early_exit_returns_only_completed_report_prefix(monkeypatch):
    from stowage.checker import in_port_evaluator as module
    from stowage.model import PartReport, Violation
    i = build_instance()
    ticks = iter((0., .1, 100., 100.2, 200., 200.3))
    monkeypatch.setattr(module, 'monotonic', lambda: next(ticks))
    count = []
    def check(*args, **kwargs):
        count.append(1)
        return PartReport(name='in_port', violations=[Violation(rule_id='port.stability.cargo_operations',
            severity='hard', call_index=0, message='test failure')] if len(count) == 3 else [])
    monkeypatch.setattr(module, 'check_state', check)
    with module.InPortEvaluator(i, SimpleNamespace()) as evaluator:
        batch = evaluator.evaluate([i.onboard]*10, stop_at_first_failure=True)
    assert len(count) == len(batch.reports) == 3
    assert batch.state_times_s == pytest.approx((.1, .2, .3))
    assert batch.measured_state_time_s == pytest.approx(.2)
    assert batch.measured_state_count == 3
    assert batch.failure_index == 2 and batch.total_states == 10


def test_worker_state_timing_excludes_startup_queue_and_parent_wait(monkeypatch):
    from stowage.checker import in_port_evaluator as module
    from stowage.model import PartReport
    i = build_instance()
    clock = [0.]
    durations = iter((.03, .05, .07))
    report = PartReport(name='in_port', metrics={'gm_m': 1.})
    monkeypatch.setattr(module, 'monotonic', lambda: clock[0])
    def check(*args, **kwargs):
        clock[0] += next(durations)
        return report.model_copy(deep=True)
    monkeypatch.setattr(module, 'check_state', check)
    class Future:
        def __init__(self, value): self.value = value
        def result(self):
            clock[0] += 30.  # IPC/result retrieval outside the worker solve.
            return self.value
        def cancel(self): return False
    class Pool:
        def __init__(self, *, initializer, initargs, **kwargs):
            clock[0] += 1000.  # Worker startup/import time.
            initializer(*initargs)
        def submit(self, function, ship):
            clock[0] += 20.  # Queue delay before the worker receives the ship.
            return Future(function(ship))
        def shutdown(self, **kwargs): clock[0] += 500.
    monkeypatch.setattr(module, 'ProcessPoolExecutor', Pool)
    monkeypatch.setenv('OPENBLAS_NUM_THREADS', '1')
    monkeypatch.setenv('OMP_NUM_THREADS', '1')
    with module.InPortEvaluator(i, SimpleNamespace(), workers=2) as evaluator:
        batch = evaluator.evaluate([i.onboard]*3)
    assert batch.state_times_s == pytest.approx((.03, .05, .07))
    assert batch.measured_state_time_s == pytest.approx(.05)
    assert batch.measured_state_count == 3
    assert clock[0] > 1600.
    assert all(item.model_dump_json() == report.model_dump_json() for item in batch.reports)


def test_empty_batch_has_no_measured_states(monkeypatch):
    from stowage.checker import in_port_evaluator as module
    i = build_instance()
    with module.InPortEvaluator(i, SimpleNamespace()) as evaluator:
        batch = evaluator.evaluate([])
    assert batch.state_times_s == ()
    assert batch.measured_state_time_s == 0.
    assert batch.measured_state_count == 0


def _coupled_root_fault_report(monkeypatch, value=float('nan')):
    """Review High reproducer using the production coupled-root boundary."""
    import numpy as np
    from stowage.checker import stability, stability_equilibrium, stability_loading
    from stowage.checker.in_port_evaluator import check_state
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.vessel.hydrostatics import FloatingCondition
    instance = build_instance()
    condition = LoadingCondition(1e6, 50., 0., 3., .1, 1e5)
    geometry = SimpleNamespace(equilibrium=lambda *args: FloatingCondition(2., 2., 0., None, 0.))
    context = SimpleNamespace(correct_loading=lambda c: c, max_fsm_tank_ids=())
    count = iter(range(1, 30))
    monkeypatch.setattr(stability, '_canonical_fleet_policy', lambda *args: None)
    monkeypatch.setattr(stability, '_context', lambda *args: (geometry, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *args, **kwargs: condition)
    monkeypatch.setattr(stability_loading, 'liquid_cg', lambda *args: np.full(3, next(count)))
    monkeypatch.setattr(stability_equilibrium, 'root', lambda *args, **kwargs: SimpleNamespace(x=np.full(3, value)))
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT', '1')
    return check_state(instance, instance.onboard, context, warm_start=False)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_coupled_root_nonfinite_is_internal_error_not_p1(monkeypatch, value):
    report = _coupled_root_fault_report(monkeypatch, value)
    assert not report.ran
    assert report.error and 'nonfinite' in report.error.lower()
    assert {v.rule_id for v in report.violations} == {'checker.internal_error'}


@pytest.mark.parametrize('actual', [(float('nan'), 0., 3.), (50., float('inf'), 3.),
                                   (1e200, 1e200, 1e200)])
def test_nonfinite_liquid_cg_or_residual_norm_is_internal(monkeypatch, actual):
    import numpy as np
    from stowage.checker import stability_loading
    report = _coupled_root_fault_report(monkeypatch, 0.)
    monkeypatch.setattr(stability_loading, 'liquid_cg', lambda *args: np.asarray(actual))
    from stowage.checker.in_port_evaluator import check_state
    # Reuse the production-shaped context/geometry installed by the helper.
    instance = build_instance()
    context = SimpleNamespace(correct_loading=lambda c: c, max_fsm_tank_ids=())
    report = check_state(instance, instance.onboard, context, warm_start=False)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.internal_error'}
    assert report.error and 'nonfinite' in report.error.lower()


@pytest.mark.parametrize('fault', ['root_nan', 'trial_inf', 'liquid_nan', 'residual_overflow', 'volume_inf', 'table_root_nan'])
def test_warm_nonfinite_cannot_be_erased_by_cold_fallback(monkeypatch, fault):
    import numpy as np
    from stowage.checker import in_port_evaluator as module, stability_loading
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.vessel.hydrostatics import FloatingCondition
    class Geometry:
        beam_m = 10.
        def equilibrium(self, *args):
            return FloatingCondition(2., 2., 0., None, 0.)
        def submerged(self, ta, tf, heel, rho):
            return SimpleNamespace(volume_m3=float('inf') if fault == 'volume_inf' else (ta+tf)*100.,
                centroid_m=(1e200, 1e200, 1e200) if fault == 'residual_overflow' else (50.+ta-tf, heel*10., 2.))
        _solver_submerged = submerged
        def _axes(self, *args):
            return None, np.array([1., 0., 0.]), np.array([0., 1., 0.])
    geometry = Geometry()
    if fault == 'table_root_nan':
        geometry._condition = lambda *args, **kwargs: (FloatingCondition(2., 2., 0., None, 0.), None)
    continuation = module._Continuation()
    continuation.previous = FloatingCondition(2., 2., 0., None, 0.)
    condition = LoadingCondition(410_000., 50.2, .1, 2., 0., 0.)
    if fault == 'liquid_nan':
        monkeypatch.setattr(stability_loading, 'liquid_cg', lambda *args: (float('nan'), 0., 2.))
    def injected_root(residual, seed, **kwargs):
        if fault in {'root_nan', 'table_root_nan'}:
            return SimpleNamespace(x=np.full(3, np.nan))
        residual(np.array([float('inf'), 2., 0.]) if fault == 'trial_inf' else np.asarray(seed))
        return SimpleNamespace(x=np.asarray(seed))
    monkeypatch.setattr(module, 'root', injected_root)
    with pytest.raises(RuntimeError, match='nonfinite'):
        continuation.solve(geometry, condition, 1000., 100., 1)


def test_nonfinite_table_result_cannot_be_erased_by_direct_retry(monkeypatch):
    from stowage.checker import stability, stability_tables
    from stowage.checker.in_port_evaluator import check_state
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.vessel.hydrostatics import FloatingCondition
    instance = build_instance()
    condition = LoadingCondition(1e6, 50., 0., 3., .1, 1e5)
    direct = SimpleNamespace(equilibrium=lambda *args: FloatingCondition(2., 2., 0., None, 0.))
    table = SimpleNamespace(equilibrium=lambda *args: FloatingCondition(float('nan'), 2., 0., None, 0.))
    context = SimpleNamespace(correct_loading=lambda c: c, max_fsm_tank_ids=())
    monkeypatch.setattr(stability, '_canonical_fleet_policy', lambda *args: None)
    monkeypatch.setattr(stability, '_context', lambda *args: (direct, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *args, **kwargs: condition)
    monkeypatch.setattr(stability_tables, 'cached_geometry', lambda *args: table)
    monkeypatch.setattr(stability, '_in_port_criteria', lambda *args: ({'gm_m': 1.}, {'gm_m': .85}, []))
    monkeypatch.delenv('STOWAGE_STABILITY_DIRECT', raising=False)
    monkeypatch.setenv('STOWAGE_STABILITY_LIQUID_DIRECT', '1')
    report = check_state(instance, instance.onboard, context, warm_start=False)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.internal_error'}
    assert report.error and 'nonfinite' in report.error.lower()


def test_finite_coupled_nonconvergence_remains_physical_p1(monkeypatch):
    report = _coupled_root_fault_report(monkeypatch, 0.)
    assert report.ran and report.error is None
    assert {v.rule_id for v in report.violations} == {'port.stability.cargo_operations'}
    assert 'root did not converge' in report.violations[0].message


def test_nonfinite_error_reports_have_no_nondeterministic_duration(monkeypatch):
    first = _coupled_root_fault_report(monkeypatch)
    second = _coupled_root_fault_report(monkeypatch)
    assert first.model_dump_json() == second.model_dump_json()
    assert 'elapsed_s' not in first.metrics


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_cold_table_nonfinite_root_is_not_discarded_for_finite_least_squares(monkeypatch, value):
    import numpy as np
    from stowage.checker import stability, stability_tables
    from stowage.checker.in_port_evaluator import check_state
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.checker.stability_tables import TableGeometry
    from stowage.vessel.hydrostatics import FloatingCondition
    instance = build_instance()
    condition = LoadingCondition(400_000., 50., 0., 2., .1, 40_000.)
    direct = SimpleNamespace(lpp_m=100., beam_m=10., x_m=np.array([25.,75.]),
        integration_weights_m=np.array([50.,50.]),
        equilibrium=lambda *args: FloatingCondition(2.,2.,0.,None,0.),
        _axes=lambda *args: (None,np.array([1.,0.,0.]),np.array([0.,1.,0.])))
    table = TableGeometry.__new__(TableGeometry)
    table.direct, table.density_kg_m3 = direct, 1000.
    table.qualified_min_mass_kg, table.qualified_max_mass_kg = 0., float('inf')
    table.grid = {'immersion_m': np.array([.1, 5.]), 'trim_m': np.array([-5., 5.]),
                  'heel_rad': np.array([0., .5])}
    table._values = lambda q, trim, phi: np.array([200*q, (50+trim)*200*q, phi*10*200*q, 2*200*q])
    context = SimpleNamespace(correct_loading=lambda c:c, max_fsm_tank_ids=())
    least_squares_calls = []
    monkeypatch.setattr(stability, '_canonical_fleet_policy', lambda *args: None)
    monkeypatch.setattr(stability, '_context', lambda *args:(direct, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *args, **kwargs:condition)
    monkeypatch.setattr(stability, '_in_port_criteria', lambda *args: ({'gm_m':1.}, {'gm_m':.85}, []))
    monkeypatch.setattr(stability_tables, 'cached_geometry', lambda *args:table)
    monkeypatch.setattr(stability_tables, 'root', lambda *args, **kwargs:SimpleNamespace(x=np.full(3,value)))
    def finite_fallback(*args, **kwargs):
        least_squares_calls.append(1)
        return SimpleNamespace(x=np.array([2.,0.,0.]))
    monkeypatch.setattr(stability_tables, 'least_squares', finite_fallback)
    monkeypatch.delenv('STOWAGE_STABILITY_DIRECT', raising=False)
    monkeypatch.setenv('STOWAGE_STABILITY_LIQUID_DIRECT', '1')
    instance.port.water_density_kg_m3 = 1000.
    # FWA is unrelated to this equilibrium fault; the summer direct geometry
    # is intentionally minimal and must never be reached after the invalid root.
    from stowage.checker import in_port_evaluator as module
    monkeypatch.setattr(module, 'draught_limits', lambda *args:({}, {}, []))
    report = check_state(instance, instance.onboard, context, warm_start=False)
    assert not report.ran
    assert report.error and 'nonfinite' in report.error.lower()
    assert {v.rule_id for v in report.violations} == {'checker.internal_error'}
    assert least_squares_calls == []


def _toy_equilibrium_table():
    import numpy as np
    from stowage.checker.stability_tables import TableGeometry
    table = TableGeometry.__new__(TableGeometry)
    table.direct = SimpleNamespace(lpp_m=100., beam_m=10., x_m=np.array([25.,75.]),
        integration_weights_m=np.array([50.,50.]),
        _axes=lambda *args:(None,np.array([1.,0.,0.]),np.array([0.,1.,0.])))
    table.density_kg_m3 = 1000.
    table.qualified_min_mass_kg, table.qualified_max_mass_kg = 0., float('inf')
    table.grid = {'immersion_m':np.array([.1,5.]), 'trim_m':np.array([-5.,5.]),
                  'heel_rad':np.array([0.,.5])}
    table._values = lambda q, trim, phi: np.array([200*q,(50+trim)*200*q,phi*10*200*q,2*200*q])
    return table


def test_checked_cold_table_preserves_equations_and_finite_bounded_retry(monkeypatch):
    import numpy as np
    from stowage.checker import stability_tables as tables
    from stowage.checker.in_port_evaluator import _checked_table_equilibrium
    table = _toy_equilibrium_table()
    args = (400_000.,50.2,.1,2.,1000.)
    reference = table.equilibrium(*args)
    checked = _checked_table_equilibrium(table,*args)
    assert (checked.t_aft_m,checked.t_fwd_m,checked.heel_rad) == pytest.approx(
        (reference.t_aft_m,reference.t_fwd_m,reference.heel_rad),abs=1e-12)
    native_least_squares = tables.least_squares
    retries = []
    monkeypatch.setattr(tables,'root',lambda *args,**kwargs:SimpleNamespace(x=np.array([2.3,0.,0.])))
    def bounded_retry(*args,**kwargs):
        retries.append(kwargs)
        return native_least_squares(*args,**kwargs)
    monkeypatch.setattr(tables,'least_squares',bounded_retry)
    retried = _checked_table_equilibrium(table,*args)
    assert len(retries) == 1 and 'bounds' in retries[0]
    assert (retried.t_aft_m,retried.t_fwd_m,retried.heel_rad) == pytest.approx(
        (reference.t_aft_m,reference.t_fwd_m,reference.heel_rad),abs=1e-9)
    assert retried.residual_norm <= 2e-6


@pytest.mark.parametrize('fault',['root_trial_nan','least_squares_root_inf','least_squares_trial_nan'])
def test_cold_table_invalid_intermediate_or_retry_cannot_escape(monkeypatch,fault):
    import numpy as np
    from stowage.checker import stability_tables as tables
    from stowage.checker.in_port_evaluator import _checked_table_equilibrium
    table = _toy_equilibrium_table()
    def root(residual,seed,**kwargs):
        if fault == 'root_trial_nan':
            residual(np.full(3,np.nan))
        return SimpleNamespace(x=np.array([2.3,0.,0.]))
    def least_squares(residual,seed,**kwargs):
        if fault == 'least_squares_trial_nan':
            residual(np.full(3,np.nan))
        return SimpleNamespace(x=np.full(3,np.inf))
    monkeypatch.setattr(tables,'root',root)
    monkeypatch.setattr(tables,'least_squares',least_squares)
    with pytest.raises(RuntimeError,match='nonfinite'):
        _checked_table_equilibrium(table,400_000.,50.,0.,2.,1000.)


def _direct_upright_geometry():
    import numpy as np
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.vessel.hydrostatics import FloatingCondition
    class Geometry:
        lpp_m, beam_m, design_draught_m, max_z_m = 100., 10., 2., 10.
        x_m, integration_weights_m = np.array([25.,75.]), np.array([50.,50.])
        free_trim_curve = StabilityGeometry.free_trim_curve
        def submerged(self,ta,tf,heel,rho):
            return SimpleNamespace(volume_m3=(ta+tf)*100.,displacement_kg=(ta+tf)*100.*rho,
                centroid_m=(50.+ta-tf,0.,1.),bm_t_m=5.,waterplane_area_m2=1000.)
        full_submerged = submerged
        def _submerged_many(self,pairs,heel,rho):
            return [self.submerged(*pair,heel,rho) for pair in pairs]
        def equilibrium(self,*args):
            return FloatingCondition(2.,2.,0.,self.submerged(2.,2.,0.,args[-1]),0.)
        def _axes(self,*args):
            return None,np.array([1.,0.,0.]),np.array([0.,1.,0.])
        def waterplane_normal(self,*args): return np.array([0.,0.,1.])
    return Geometry()


@pytest.mark.parametrize('table_mode',[False,True])
@pytest.mark.parametrize('value',[float('nan'),float('inf')])
def test_upright_nonfinite_root_cannot_be_discarded_for_finite_retry(monkeypatch,table_mode,value):
    import numpy as np
    import scipy.optimize
    from stowage.checker import in_port_evaluator as module,stability,stability_tables
    from stowage.checker.stability_loading import LoadingCondition
    instance = build_instance()
    instance.port.water_density_kg_m3 = 1000.
    direct = _direct_upright_geometry()
    table = _toy_equilibrium_table()
    table.direct,table._full_cache = direct,{}
    context = SimpleNamespace(correct_loading=lambda c:c,max_fsm_tank_ids=())
    condition = LoadingCondition(400_000.,50.,0.,2.,.1,40_000.)
    monkeypatch.setattr(stability,'_canonical_fleet_policy',lambda *args:None)
    monkeypatch.setattr(stability,'_context',lambda *args:(direct,{}))
    monkeypatch.setattr(stability,'loading_condition',lambda *args,**kwargs:condition)
    monkeypatch.setattr(stability_tables,'cached_geometry',lambda *args:table if table_mode else None)
    monkeypatch.setattr(module,'draught_limits',lambda *args:({}, {}, []))
    native_root = scipy.optimize.root
    retries = []
    def faulty_root(function,seed,**kwargs):
        if len(seed) == 2: return SimpleNamespace(x=np.full(2,value))
        return native_root(function,seed,**kwargs)
    def finite_retry(function,seed,**kwargs):
        retries.append(1)
        return SimpleNamespace(x=np.array([2.,0.]))
    monkeypatch.setattr(module,'root',faulty_root)
    monkeypatch.setattr(scipy.optimize,'root',faulty_root)
    monkeypatch.setattr(stability_tables,'root',faulty_root)
    monkeypatch.setattr(scipy.optimize,'least_squares',finite_retry)
    monkeypatch.setattr(stability_tables,'least_squares',finite_retry)
    monkeypatch.delenv('STOWAGE_STABILITY_DIRECT',raising=False)
    monkeypatch.setenv('STOWAGE_STABILITY_LIQUID_DIRECT','1')
    report = module.check_state(instance,instance.onboard,context,warm_start=False)
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.internal_error'}
    assert report.error and 'nonfinite' in report.error.lower()
    assert not retries


@pytest.mark.parametrize('table_mode',[False,True])
def test_checked_upright_matches_primitive_with_actual_cg_callback(table_mode):
    from stowage.checker.in_port_evaluator import _CheckedCriteriaGeometry
    geometry = _toy_equilibrium_table() if table_mode else _direct_upright_geometry()
    callback = lambda phi,trim:(50.2+.05*trim,0.,2.)
    args = ([0.],400_000.,50.2,2.,1000.)
    kwargs = {'cg_callback':callback,'initial_drafts':(2.,2.)}
    expected = geometry.free_trim_curve(*args,**kwargs)
    checked = _CheckedCriteriaGeometry(geometry).free_trim_curve(*args,**kwargs)
    assert checked[0] == pytest.approx(expected[0],abs=1e-12)
    assert checked[1] == pytest.approx(expected[1],abs=1e-12)


def test_checked_direct_upright_retains_finite_least_squares_retry(monkeypatch):
    import numpy as np
    from stowage.checker import in_port_evaluator as module
    geometry = _direct_upright_geometry()
    args = ([0.],400_000.,50.2,2.,1000.)
    expected = geometry.free_trim_curve(*args,initial_drafts=(2.,2.))
    monkeypatch.setattr(module,'root',lambda *args,**kwargs:SimpleNamespace(x=np.array([2.3,0.])))
    checked = module._CheckedCriteriaGeometry(geometry).free_trim_curve(*args,initial_drafts=(2.,2.))
    assert checked[1] == pytest.approx(expected[1],abs=1e-9)


@pytest.mark.parametrize('table_mode',[False,True])
@pytest.mark.parametrize('fault',['root_trial_nan','least_squares_nan','least_squares_trial_inf','cg_nan'])
def test_checked_upright_invalid_iterates_and_retry_roots_are_internal(monkeypatch,table_mode,fault):
    import numpy as np
    import scipy.optimize
    from stowage.checker import in_port_evaluator as module,stability_tables
    geometry = _toy_equilibrium_table() if table_mode else _direct_upright_geometry()
    def root(residual,seed,**kwargs):
        if fault == 'root_trial_nan': residual(np.full(2,np.nan))
        residual(seed)
        return SimpleNamespace(x=np.array([2.3,0.]))
    def least_squares(residual,seed,**kwargs):
        if fault == 'least_squares_trial_inf': residual(np.full(2,np.inf))
        return SimpleNamespace(x=np.full(2,np.nan))
    monkeypatch.setattr(module,'root',root)
    monkeypatch.setattr(stability_tables,'root',root)
    monkeypatch.setattr(stability_tables,'least_squares',least_squares)
    monkeypatch.setattr(scipy.optimize,'least_squares',least_squares)
    callback = (lambda *args:(50.,0.,float('nan'))) if fault == 'cg_nan' else None
    with pytest.raises(RuntimeError,match='nonfinite'):
        module._CheckedCriteriaGeometry(geometry).free_trim_curve([0.],400_000.,50.2,2.,1000.,
            cg_callback=callback,initial_drafts=(2.,2.))


@pytest.mark.parametrize('message', [
    'array must not contain infs or NaNs',
    'non-finite upright residual',
    'infinities in equilibrium matrix',
])
def test_nonfinite_library_error_wording_cannot_enable_retry(message):
    from stowage.checker.in_port_evaluator import _nonfinite_error
    assert _nonfinite_error(ValueError(message))


def _physical_cache_case():
    """Small fully grouped table condition; every physical array is explicit."""
    import numpy as np
    from stowage.checker.stability_tables import TableGeometry
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.checker.stability_liquid_batch import CGGroup
    geometry=TableGeometry.__new__(TableGeometry)
    geometry.direct=SimpleNamespace(_edge_generation=1)
    geometry.grid={'immersion_m':np.array([0.,1.,2.,3.]),
        'trim_m':np.array([-1.,0.,1.,2.]),'heel_rad':np.array([0.,.1,.2,.3])}
    geometry.values=np.zeros((4,4,4,4))
    geometry.density_kg_m3=1025.
    geometry.qualified_min_mass_kg=1.
    geometry.qualified_max_mass_kg=1e9
    def condition(fsc=0.):
        group=CGGroup(np.array([-.1,0.,.1,.2]),np.array([-.1,0.,.1,.2]),
                      np.zeros((4,4,3)))
        return LoadingCondition(1e6,50.,0.,3.,fsc,fsc*1e6,
                                liquid_cg_groups=(group,))
    return geometry,condition


def test_completed_cold_physical_cache_replays_liquid_state_across_regulatory_fsm(monkeypatch):
    import numpy as np
    from stowage.checker import in_port_evaluator as module
    from stowage.vessel.hydrostatics import FloatingCondition
    from stowage.model import TankSoundingPoint
    geometry,condition=_physical_cache_case()
    calls=[]
    def physical(g,c,rho,length):
        calls.append(c.fsc_m)
        c.liquid_usage['table']=7
        c.cg_cache[.1,.01]=(50.,0.,3.)
        c.upright_cache[123,2.]=TankSoundingPoint(volume_m3=2.,lcg_m=1.,tcg_m=0.,vcg_m=2.,fsm_m4=3.)
        return FloatingCondition(2.,2.,0.,None,0.)
    monkeypatch.setattr(module,'_checked_coupled_equilibrium',physical)
    continuation=module._Continuation()
    first,second=condition(.1),condition(.2)
    a=continuation.cold(geometry,first,1025.,100.,1)
    b=continuation.cold(geometry,second,1025.,100.,1)
    assert len(calls)==1
    assert a==b and a is not b
    assert second.liquid_usage==first.liquid_usage
    assert second.cg_cache==first.cg_cache
    assert second.upright_cache==first.upright_cache
    second.liquid_usage['table']=999
    second.cg_cache.clear()
    second.upright_cache[123,2.].vcg_m=99.
    third=condition(.3)
    continuation.cold(geometry,third,1025.,100.,1)
    assert third.liquid_usage['table']==7
    assert third.cg_cache==first.cg_cache
    assert third.upright_cache[123,2.].vcg_m==2.
    # cached_geometry recreates an adapter around the same physical source.
    recreated=type(geometry).__new__(type(geometry))
    recreated.__dict__.update(geometry.__dict__)
    continuation.cold(recreated,condition(.35),1025.,100.,1)
    assert len(calls)==1
    # Every physical array change invalidates the cache, even in-place edits.
    changed=condition(.4)
    changed.liquid_cg_groups[0].shift_grid[0,0,0]=1.
    continuation.cold(geometry,changed,1025.,100.,1)
    geometry.values[0,0,0,0]=1.
    continuation.cold(geometry,condition(),1025.,100.,1)
    assert len(calls)==3


def test_completed_cold_cache_preserves_full_report_bytes_and_liquid_query_metrics(monkeypatch):
    from stowage.checker import in_port_evaluator as module,stability,stability_tables,stability_liquid
    from stowage.vessel.hydrostatics import FloatingCondition
    geometry,condition=_physical_cache_case()
    instance=build_instance()
    calls=[]
    def physical(g,c,rho,length):
        calls.append(1)
        c.liquid_usage['table']=7
        c.cg_cache[0.,0.]=(50.,0.,3.)
        return FloatingCondition(2.,2.,0.,None,0.)
    monkeypatch.setattr(module,'_checked_coupled_equilibrium',physical)
    monkeypatch.setattr(stability,'_canonical_fleet_policy',lambda *_:None)
    monkeypatch.setattr(stability,'loading_condition',lambda *args,**kwargs:condition(.2))
    monkeypatch.setattr(stability_tables,'cached_geometry',lambda *args:None)
    monkeypatch.setattr(stability_liquid,'attach_tables',lambda c,*args,**kwargs:c)
    monkeypatch.setattr(module,'near_limit',lambda *args:False)
    def criteria(instance,ship,g,params,c,floating,method):
        return dict(gm_m=1.-c.fsc_m,liquid_table_queries=float(c.liquid_usage['table'])),dict(gm_m=.85-c.fsc_m),[]
    monkeypatch.setattr(stability,'_in_port_criteria',criteria)
    context=SimpleNamespace(geometry=geometry,params={},correct_loading=lambda c:c,max_fsm_tank_ids=())
    continuation=module._Continuation()
    first=module.check_state(instance,instance.onboard,context,continuation,warm_start=False)
    second=module.check_state(instance,instance.onboard,context,continuation,warm_start=False)
    assert first.ran and second.ran
    assert len(calls)==1
    assert first.model_dump_json()==second.model_dump_json()
    assert second.metrics['liquid_table_queries']==7.


def test_completed_cold_cache_is_bounded_and_bypasses_direct_liquid_fallback(monkeypatch):
    from dataclasses import replace
    from stowage.checker import in_port_evaluator as module
    from stowage.vessel.hydrostatics import FloatingCondition
    geometry,condition=_physical_cache_case()
    calls=[]
    def physical(g,c,rho,length):
        calls.append(c.kg_m)
        return FloatingCondition(2.,2.,0.,None,0.)
    monkeypatch.setattr(module,'_checked_coupled_equilibrium',physical)
    monkeypatch.setattr(module,'_COLD_PHYSICAL_CACHE_LIMIT',2)
    continuation=module._Continuation()
    for kg in (3.,4.,5.,3.):
        continuation.cold(geometry,replace(condition(),kg_m=kg),1025.,100.,1)
    assert calls==[3.,4.,5.,3.]
    def fallback(g,c,rho,length):
        calls.append(9.)
        c.liquid_usage['direct']=1
        return FloatingCondition(2.,2.,0.,None,0.)
    monkeypatch.setattr(module,'_checked_coupled_equilibrium',fallback)
    for _ in range(2):
        continuation.cold(geometry,condition(),1025.,100.,1)
    assert calls[-2:]==[9.,9.]


def test_completed_cold_cache_never_stores_nonfinite_returned_state(monkeypatch):
    from stowage.checker import in_port_evaluator as module
    from stowage.vessel.hydrostatics import FloatingCondition
    geometry,condition=_physical_cache_case()
    monkeypatch.setattr(module,'_checked_coupled_equilibrium',lambda *args:
        FloatingCondition(float('nan'),2.,0.,None,0.))
    continuation=module._Continuation()
    for _ in range(2):
        with pytest.raises(module._NonfiniteNumericalError):
            continuation.cold(geometry,condition(),1025.,100.,1)


def test_completed_cold_cache_releases_table_wrapper_but_retains_direct_identity(monkeypatch):
    import gc
    import weakref
    from stowage.checker import in_port_evaluator as module
    from stowage.vessel.hydrostatics import FloatingCondition
    geometry,condition=_physical_cache_case()
    class Direct:
        _edge_generation=1
    direct=Direct()
    geometry.direct=direct
    wrapper_ref,direct_ref=weakref.ref(geometry),weakref.ref(direct)
    monkeypatch.setattr(module,'_checked_coupled_equilibrium',lambda *args:
        FloatingCondition(2.,2.,0.,None,0.))
    continuation=module._Continuation()
    continuation.cold(geometry,condition(),1025.,100.,1)
    del geometry,direct
    gc.collect()
    assert wrapper_ref() is None
    assert direct_ref() is not None  # Prevent reuse of the id in the physical key.
    del continuation
    gc.collect()
    assert direct_ref() is None

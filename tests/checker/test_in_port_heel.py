"""D74 R2 intervals use a labelled linearisation and cold endpoint evidence."""
import math
from types import SimpleNamespace

import pytest

from stowage.checker.in_port_heel import compute_heel_interval, ship_at_split
from stowage.model import PartReport, TankPair, Violation
from stowage.model.fixtures import build_instance


@pytest.fixture
def heel_case():
    i = build_instance()
    pair = TankPair(id='explicit', port_tank='WB-P', starboard_tank='WB-S')
    context = SimpleNamespace(instance=i, pair=pair,
        _pair_moment=lambda port, starboard, pm, sm: 5.*(pm-sm))
    ideal = PartReport(name='in_port', metrics=dict(displacement_kg=4e6,
        gm_m=1., list_rad=0.), margins=dict(list_rad=i.vessel.hull.max_list_rad))
    return i, i.onboard, context, ideal


def _checker(i, ship, *, factor=1., covers=False):
    split = ship.tank_masses_kg['WB-P']
    angle = factor*(split-51_250.)*10./4e6
    margins = {'list_rad': i.vessel.hull.max_list_rad-abs(angle)}
    metrics = {'displacement_kg':4e6, 'gm_m':1., 'list_rad':angle}
    if covers:
        margins['open_hatch_immersion_angle_rad'] = .5-abs(angle)
    return PartReport(name='in_port', metrics=metrics, margins=margins)


def test_split_preserves_total_other_masses_and_input(heel_case):
    _, ship, context, _ = heel_case
    original = ship.model_dump()
    changed = ship_at_split(context, ship, 10_000.)
    assert changed.tank_masses_kg['WB-P'] == 10_000.
    assert changed.tank_masses_kg['WB-S'] == 92_500.
    assert changed.tank_masses_kg['FO-1'] == ship.tank_masses_kg['FO-1']
    assert ship.model_dump() == original
    for invalid in (-1., 102_501., math.nan):
        with pytest.raises(ValueError):
            ship_at_split(context, ship, invalid)


def test_interval_linearises_moment_at_actual_fluid_gm_and_verifies_endpoints(heel_case):
    i, ship, ctx, ideal = heel_case
    calls = []
    def exact(candidate):
        calls.append(candidate)
        return _checker(i, candidate)
    interval = compute_heel_interval(i, ship, ctx, ideal, exact_check=exact)
    radius = 4e6*ideal.metrics['gm_m']*i.vessel.hull.max_list_rad/10.
    assert interval.lo_kg == pytest.approx(51_250.-radius)
    assert interval.hi_kg == pytest.approx(51_250.+radius)
    assert interval.ideal_split_kg == 51_250.
    assert interval.feasible
    assert len(calls) == 2
    assert all(r.margins['list_rad'] >= -1e-10 for r in interval.endpoint_reports)


def test_failed_physical_bounds_shrink_only_after_exact_checks(heel_case):
    i, ship, ctx, ideal = heel_case
    interval = compute_heel_interval(i, ship, ctx, ideal,
        exact_check=lambda candidate:_checker(i, candidate, factor=1.2))
    original_radius = 4e6*i.vessel.hull.max_list_rad/10.
    assert interval.lo_kg > 51_250.-original_radius
    assert interval.hi_kg < 51_250.+original_radius
    assert interval.shrunk
    assert all(r.margins['list_rad'] >= -1e-10 for r in interval.endpoint_reports)


def test_open_hatch_p4_endpoint_failure_shrinks_even_when_list_passes(heel_case):
    i, ship, ctx, ideal = heel_case
    ship.covers_off = {i.vessel.hatch_covers[0].id:'quay'}
    ideal.margins['open_hatch_immersion_angle_rad'] = .01
    def exact(candidate):
        report = _checker(i, candidate)
        report.margins['open_hatch_immersion_angle_rad'] = .01-abs(report.metrics['list_rad'])
        return report
    interval = compute_heel_interval(i, ship, ctx, ideal, exact_check=exact)
    assert interval.shrunk
    assert all(r.margins['open_hatch_immersion_angle_rad'] > 0 for r in interval.endpoint_reports)


@pytest.mark.parametrize('failure', ['nan', 'internal'])
def test_bad_endpoint_calculation_fails_closed_instead_of_shrinking(heel_case, failure):
    i, ship, ctx, ideal = heel_case
    def exact(candidate):
        if failure == 'nan':
            report = _checker(i, candidate)
            report.metrics['list_rad'] = math.nan
            return report
        return PartReport(name='in_port', ran=False, error='missing numerical proof',
            violations=[Violation(rule_id='checker.internal_error', severity='hard',
                                  call_index=0, message='bad root')])
    with pytest.raises(ValueError):
        compute_heel_interval(i, ship, ctx, ideal, exact_check=exact)


@pytest.mark.parametrize('field,value', [('gm_m',math.nan),('gm_m',0.),('displacement_kg',math.inf)])
def test_invalid_linearisation_loading_fails_closed(heel_case, field, value):
    i, ship, ctx, ideal = heel_case
    ideal.metrics[field] = value
    with pytest.raises(ValueError):
        compute_heel_interval(i, ship, ctx, ideal,
            exact_check=lambda candidate:_checker(i,candidate))


def test_dry_pair_has_a_single_exactly_verified_split(heel_case):
    i, ship, ctx, ideal = heel_case
    ship.tank_masses_kg.update({'WB-P':0.,'WB-S':0.})
    calls = []
    def exact(candidate):
        calls.append(candidate)
        return ideal.model_copy(deep=True)
    interval = compute_heel_interval(i,ship,ctx,ideal,exact_check=exact)
    assert interval.lo_kg == interval.hi_kg == interval.ideal_split_kg == 0.
    assert len(calls) == 1


def test_interval_uses_effective_in_port_bound_not_seagoing_hull_limit(heel_case):
    i,ship,ctx,ideal = heel_case
    assert i.vessel.hull.max_list_rad == pytest.approx(math.radians(3.))
    port_limit = math.radians(2.)
    ideal.margins['list_rad'] = port_limit
    def exact(candidate):
        report = _checker(i,candidate)
        report.margins['list_rad'] = port_limit-abs(report.metrics['list_rad'])
        return report
    interval = compute_heel_interval(i,ship,ctx,ideal,exact_check=exact)
    radius = 4e6*port_limit/10.
    assert interval.lo_kg == pytest.approx(51_250.-radius)
    assert interval.hi_kg == pytest.approx(51_250.+radius)


def test_negative_endpoint_margin_never_counts_as_exact_pass(heel_case):
    i,ship,ctx,ideal = heel_case
    def exact(candidate):
        report = _checker(i,candidate)
        if candidate.tank_masses_kg['WB-P'] != 51_250.:
            report.margins['list_rad'] = -5e-11
        return report
    interval = compute_heel_interval(i,ship,ctx,ideal,exact_check=exact)
    assert interval.lo_kg == interval.hi_kg == 51_250.
    assert all(r.margins['list_rad'] >= 0. for r in interval.endpoint_reports)


def test_interval_generation_is_explicit_and_endpoint_checks_are_direct_cold(monkeypatch, heel_case):
    from stowage.checker import in_port_evaluator as evaluator
    i,ship,ctx,ideal = heel_case
    calls = []
    def evaluate(instance, candidate, context, continuation, **kwargs):
        calls.append(kwargs)
        return _checker(i,candidate)
    monkeypatch.setattr(evaluator,'_evaluate',evaluate)
    ordinary = evaluator.check_state(i,ship,ctx,warm_start=False)
    assert 'heel_lo_kg' not in ordinary.metrics
    calls.clear()
    report = evaluator.check_state(i,ship,ctx,warm_start=False,heel_intervals=True)
    assert report.ran and not report.error, report.model_dump()
    assert 'heel_lo_kg' in report.metrics
    assert 'heel_hi_kg' in report.metrics
    assert report.metrics['heel_ideal_split_kg'] == 51_250.
    assert len(calls) == 3
    assert all(call['warm_start'] is False for call in calls)
    assert all(call['force_direct'] is True for call in calls[1:])


def test_evaluator_explicit_interval_option_reaches_state_checks(monkeypatch, heel_case):
    from stowage.checker import in_port_evaluator as module
    i,ship,ctx,ideal = heel_case
    calls = []
    def check(*args,**kwargs):
        calls.append(kwargs)
        return ideal.model_copy(deep=True)
    monkeypatch.setattr(module,'check_state',check)
    with module.InPortEvaluator(i,ctx,workers=1,warm_start=False,heel_intervals=True) as evaluator:
        result = evaluator.evaluate([ship])
    assert len(result.reports) == 1
    assert calls[0]['heel_intervals'] is True
    assert calls[0]['warm_start'] is False


def test_unavailable_endpoint_cannot_be_reclassified_as_physical_changeover(monkeypatch, heel_case):
    from stowage.checker import in_port_evaluator as module
    i,ship,ctx,ideal = heel_case
    calls = []
    def evaluate(*args,**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return ideal.model_copy(deep=True)
        return PartReport(name='in_port', ran=False,
            error='liquid/hull equilibrium root did not converge',
            violations=[Violation(rule_id='checker.internal_error', severity='hard',
                                  call_index=0, message='numerical endpoint unavailable')])
    monkeypatch.setattr(module,'_evaluate',evaluate)
    report = module.check_state(i,ship,ctx,warm_start=False,heel_intervals=True)
    assert not report.ran and report.error
    assert {v.rule_id for v in report.violations} == {'checker.internal_error'}


@pytest.mark.parametrize('workers',[1,2])
def test_interval_batch_enriches_copies_without_resolving_base_and_reuses_pool(monkeypatch,heel_case,workers):
    from stowage.checker import in_port_evaluator as module
    from stowage.checker import in_port_heel
    i,ship,ctx,ideal = heel_case
    pools,calls = [],[]
    class Future:
        def __init__(self,value): self.value=value
        def result(self): return self.value
        def cancel(self): return False
    class Pool:
        def __init__(self,*,initializer,initargs,**kwargs):
            pools.append(self)
            initializer(*initargs)
        def submit(self,function,*args): return Future(function(*args))
        def shutdown(self,**kwargs): pass
    def interval(instance,candidate,context,report):
        calls.append(report.metrics['displacement_kg'])
        return in_port_heel.HeelInterval(1.,2.,1.5,(report,report))
    monkeypatch.setattr(module,'ProcessPoolExecutor',Pool)
    monkeypatch.setattr(module,'check_state',lambda *args,**kwargs:ideal.model_copy(deep=True))
    monkeypatch.setattr(in_port_heel,'compute_heel_interval',interval)
    monkeypatch.setenv('OPENBLAS_NUM_THREADS','1')
    monkeypatch.setenv('OMP_NUM_THREADS','1')
    originals=[ideal.model_copy(deep=True),ideal.model_copy(deep=True)]
    with module.InPortEvaluator(i,ctx,workers=workers,warm_start=False) as evaluator:
        evaluator.evaluate([ship])  # Initialize the same pool used by enrichment.
        monkeypatch.setattr(module,'check_state',lambda *args,**kwargs:pytest.fail('base state solved twice'))
        batch=evaluator.evaluate_intervals([ship,ship],originals)
    assert len(pools)==(1 if workers==2 else 0)
    assert len(batch.reports)==len(batch.state_times_s)==len(calls)==2
    assert batch.failure_index is None
    for original,report in zip(originals,batch.reports,strict=True):
        assert 'heel_lo_kg' not in original.metrics
        assert report.metrics['heel_lo_kg']==1.
        assert report.metrics['gm_m']==original.metrics['gm_m']


def test_interval_batch_numerical_failure_retains_base_evidence_and_input_report(monkeypatch,heel_case):
    from stowage.checker import in_port_evaluator as module
    from stowage.checker import in_port_heel
    i,ship,ctx,ideal=heel_case
    def invalid(*args,**kwargs):
        raise in_port_heel.HeelIntervalNumericalError('nonfinite endpoint root')
    monkeypatch.setattr(in_port_heel,'compute_heel_interval',invalid)
    with module.InPortEvaluator(i,ctx,workers=1,warm_start=False) as evaluator:
        batch=evaluator.evaluate_intervals([ship],[ideal])
    report=batch.reports[0]
    assert not report.ran and report.error
    assert report.metrics['gm_m']==ideal.metrics['gm_m']
    assert report.margins==ideal.margins
    assert batch.failure_index==0
    assert ideal.ran and not ideal.error and not ideal.violations


def test_empty_linearised_band_is_plan_level_r2_not_a_new_state_criterion(monkeypatch,heel_case):
    from stowage.checker import in_port_evaluator as module
    from stowage.checker import in_port_heel
    i,ship,ctx,ideal=heel_case
    monkeypatch.setattr(in_port_heel,'compute_heel_interval',lambda *args,**kwargs:
        in_port_heel.HeelInterval(1.,0.,51_250.,(),True,True))
    with module.InPortEvaluator(i,ctx,workers=1,warm_start=False) as evaluator:
        report=evaluator.evaluate_intervals([ship],[ideal]).reports[0]
    assert report.ran and not report.error
    assert report.metrics['heel_interval_feasible']==0.
    assert report.metrics['heel_interval_linearised_miss']==1.
    assert not report.violations  # The plan-level sweep owns heel_step.



def test_outer_band_is_capacity_only_and_certifies_only_checked_ideal(heel_case):
    from stowage.checker.in_port_heel import heel_outer_band
    _, ship, context, ideal = heel_case
    context._pair_moment = lambda *args: pytest.fail('outer bounds must not solve soundings')
    band = heel_outer_band(context, ship, ideal)
    assert (band.lo_kg, band.hi_kg) == (0., 102_500.)
    assert band.ideal_split_kg == 51_250.
    assert band.ideal_certified
    assert band.metrics()['heel_interval_is_outer_bound'] == 1.
    assert 'heel_interval_feasible' not in band.metrics()
    failed = ideal.model_copy(deep=True)
    failed.margins['list_rad'] = -1e-12
    assert not heel_outer_band(context, ship, failed).ideal_certified


def test_outer_band_cannot_hide_nonfinite_or_unavailable_ideal(heel_case):
    from stowage.checker.in_port_heel import heel_outer_band
    _, ship, context, ideal = heel_case
    ideal.metrics['gm_m'] = math.nan
    with pytest.raises(ValueError):
        heel_outer_band(context, ship, ideal)
    unavailable = PartReport(name='in_port', ran=False, error='root unavailable')
    with pytest.raises(ValueError):
        heel_outer_band(context, ship, unavailable)

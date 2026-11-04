"""Integration regressions from the task 3.1 independent physics review."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import stability
from stowage.checker.stability_geometry import StabilityGeometry
from tests.checker.test_stability_policy_fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.tanks import TankGeometry


def balanced_feeder(name='feeder_500', *, half_ballast=False):
    """Real fleet geometry at design displacement, with one fixed-load slice."""
    params = load_ship_params(name)
    geometry = StabilityGeometry(params)
    instance = build_instance()
    # Canonical D40 policy/evidence belongs to the full physical vessel even
    # when its test loading is represented by a single aggregate fixed mass.
    vessel = load_vessel(name)
    masses = {tank.id: .5*tank.capacity_m3*tank.content_density_kg_m3
              for tank in vessel.tanks if half_ballast and tank.id in {'DB-M-P', 'DB-M-S'}}
    raw_tanks = {raw['id']: raw for raw in params['tanks']}
    tank_moment = sum(mass*TankGeometry.from_params(raw_tanks[tid]).sounding(
        mass/next(t.content_density_kg_m3 for t in vessel.tanks if t.id == tid)).lcg_m
        for tid, mass in masses.items())
    submerged = geometry.full_submerged(geometry.design_draught_m, geometry.design_draught_m)
    fixed_mass = submerged.displacement_kg-vessel.hull.lightship_mass_kg-sum(masses.values())
    fixed_lcg = (submerged.displacement_kg*submerged.lcb_m
                 - vessel.hull.lightship_mass_kg*vessel.hull.lightship_lcg_m
                 - tank_moment)/fixed_mass
    vessel.fixed_loads = [(fixed_lcg, 0., 6., fixed_mass)]
    instance.vessel = vessel
    instance.onboard = instance.onboard.model_copy(update={
        'vessel_id': vessel.id, 'tank_masses_kg': masses})
    return instance


@pytest.mark.slow
def test_exact_half_fill_ballast_runs_and_matches_actual_section_fsc(monkeypatch):
    monkeypatch.setenv("STOWAGE_STABILITY_FREE_SURFACE_METHOD", "1")
    instance = balanced_feeder(half_ballast=True)
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert report.ran, report.error
    assert len(instance.onboard.tank_masses_kg) == 2
    params = load_ship_params(instance.vessel.id)
    moment = 0.
    for raw in params['tanks']:
        if raw['id'] in instance.onboard.tank_masses_kg:
            tank = TankGeometry.from_params(raw)
            density = next(t.content_density_kg_m3 for t in instance.vessel.tanks if t.id == raw['id'])
            volume = instance.onboard.tank_masses_kg[raw['id']]/density
            moment += density*tank.sounding(volume).fsm_m4
    assert report.metrics['fsc_m'] == pytest.approx(moment/report.metrics['displacement_kg'], abs=1e-10)
    assert report.metrics['equilibrium_residual'] < 2e-6


@pytest.mark.parametrize('failure_site', ['equilibrium', 'free_trim_curve', pytest.param('curve_correction',marks=pytest.mark.slow), 'liquid_cg'])
def test_numerical_failures_have_checker_rule_id(monkeypatch, failure_site):
    instance = balanced_feeder(half_ballast=True)
    # This regression injects failures into the direct reference solver. A
    # qualified cross-curve cache must not bypass the method under test.
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT', '1')
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1')

    def fail(*args, **kwargs):
        raise ValueError('injected numerical solver failure')

    if failure_site in {'equilibrium', 'free_trim_curve'}:
        monkeypatch.setattr(StabilityGeometry, failure_site, fail)
    elif failure_site == 'curve_correction':
        monkeypatch.setattr(stability, failure_site, fail)
    else:
        from stowage.checker import stability_loading
        monkeypatch.setattr(stability_loading, failure_site, fail)
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert 'injected numerical solver failure' in report.error
    assert {v.rule_id for v in report.violations} == {'checker.internal_error'}


@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
def test_all_feeders_select_d21_container_criteria(name):
    instance = balanced_feeder(name)
    assert instance.vessel.hull.lpp_m > 100.
    # Deliberately impossible A-area override must not activate an excluded rule.
    instance.rule_overrides['iscode.a2_2.gz_area_0_30'] = {'minimum': 100.}
    report = stability.check_stability(instance, instance.onboard, call_index=0)
    assert report.ran, report.error
    assert report.metrics['criteria_part_b_2_3_active'] == 1.
    assert report.metrics['criteria_part_a_2_2_1_to_3_active'] == 0.
    assert report.metrics['criteria_initial_gm_active'] == 1.
    assert report.metrics['criteria_weather_active'] == 1.
    forbidden = {'iscode.a2_2.gz_area_0_30', 'iscode.a2_2.gz_area_0_40',
                 'iscode.a2_2.gz_area_30_40', 'iscode.a2_2.gz_at_30',
                 'iscode.a2_2.max_gz_angle'}
    assert not forbidden.intersection(v.rule_id for v in report.violations)


def test_deck_edge_immersion_includes_heeled_sinkage_and_trim():
    target = .4
    # At the forward deck edge, depth = T0 + 3*phi + half_beam*tan(phi).
    # Choose T0 so the immersion angle is exactly 0.4 rad, then supply the
    # independently constructed heeled draughts to the checker helper.
    upright = 12.-3.*target-8.*math.tan(target)
    angles = np.array([0., .2, .6, 1.])
    drafts = np.column_stack([upright+2*angles, upright+3*angles])
    section = np.array([[-8., 0.], [8., 0.], [8., 12.], [-8., 12.]])
    geometry = SimpleNamespace(depth_m=12., lpp_m=80., x_m=np.array([0., 40., 80.]),
                               sections=[[section], [section], [section]])
    actual = stability._deck_edge_angle(geometry, angles, drafts)
    assert actual == pytest.approx(target, abs=1e-9)
    assert actual < math.atan((geometry.depth_m-upright)/8.)


def test_declared_flooding_uses_real_free_trim_and_preserves_windward_roll(monkeypatch):
    # This test compares the flooding root to direct polygon equilibrium at
    # sub-micron precision. Openings are intentionally absent from D30 KN keys:
    # they affect the flooding boundary, not the tabulated buoyancy integrals.
    # Test the direct root here; independent interpolation tests cover L5 error.
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT', '1')
    import math
    import numpy as np
    instance=balanced_feeder()
    params=load_ship_params('feeder_500')
    # Keep leeward flooding inside the windward roll excursion on the KCS hull;
    # this is the boundary that the former symmetric truncation got wrong.
    opening_z = 9.
    params['envelope']['openings']=[dict(x_m=8.,y_m=7.,z_m=opening_z)]
    geometry=StabilityGeometry(params)
    monkeypatch.setattr(stability,'_context',lambda vessel:(geometry,params))
    report=stability.check_stability(instance,instance.onboard,call_index=0)
    assert report.ran,report.error
    phi=report.metrics['angle_flooding_rad']
    condition=stability.loading_condition(instance,instance.onboard,ship_params=params)
    _,drafts=geometry.free_trim_curve([phi],condition.mass_kg,condition.lcg_m,condition.kg_m,1025.)
    ta,tf=drafts[0]
    assert opening_z-(ta+(tf-ta)*8./geometry.lpp_m+7.*math.tan(phi))==pytest.approx(0.,abs=1e-7)
    assert report.metrics['weather_phi0_rad']-report.metrics['weather_phi1_rad'] < -phi
    assert report.metrics['weather_area_a_mrad']>0
    assert report.metrics['weather_area_b_mrad']>0
    assert np.isfinite(report.metrics['weather_ratio'])
    assert report.margins['weather_table_applicability']==0.

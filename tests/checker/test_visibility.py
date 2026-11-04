"""Analytical SOLAS sightlines plus numerical loading-condition integration."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import visibility
from stowage.model import Placement
from stowage.model.fixtures import build_instance as _build_instance
from stowage.vessel.fleet import load_ship_params


def build_instance():
    instance = _build_instance()
    # Sightline unit tests declare their obstruction evidence explicitly; the
    # legacy fixture's aggregate background cargo has no D42 box records.
    instance.vessel.fixed_loads = []
    return instance


class Plane:
    def __init__(self, trim=0.):
        self.trim = trim

    def waterplane_normal(self, aft, fore, heel):
        normal = np.array([self.trim/120,0.,1.])
        return normal/np.linalg.norm(normal)


def condition(monkeypatch, *, aft=5., fore=5.):
    c = SimpleNamespace(t_aft_m=aft,t_fwd_m=fore,heel_rad=0.,geometry=Plane(aft-fore))
    monkeypatch.setattr(visibility,'_loading_condition',lambda *_:c)


def deck_box(instance, *, height=10., x=60., y=0., eye=25.):
    p = Placement(slot='0010182',container_id=instance.containers[1].id)
    instance.containers[1].height_m = height
    instance.containers[1].oog = None
    cell = next(c for c in instance.vessel.cells if c.id == p.slot)
    cell.lcg_m = x
    cell.tcg_m = y
    instance.vessel.bridge.x_m = 0.
    instance.vessel.bridge.eye_z_m = eye
    instance.onboard.placements = [p]
    return instance.onboard


def test_missing_bridge_or_offsets_is_hard():
    instance = build_instance()
    report = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id == 'checker.missing_vessel_data'
    instance.vessel.bridge = None
    assert not visibility.check_visibility(instance,instance.onboard,call_index=0).ran


def test_hand_computed_tangent_pass_and_fail(monkeypatch):
    condition(monkeypatch)
    instance = build_instance()
    ship = deck_box(instance,height=10.)
    report = visibility.check_visibility(instance,ship,call_index=2)
    # Eye z=25, sea=5: h_eye=20. Roof=9+10=19: h_roof=14.
    # Furthest forward corner x=60+6.058/2=63.029. Tangency to
    # conning eye is x=-2: sea range=20*65.029/(20-14)=216.7633333;
    # eye-to-bow=130, so blind distance=86.7633333m < limit=256m.
    assert report.ran and not report.violations
    assert report.metrics['blind_distance_m'] == pytest.approx(20*(63.029+2)/6-130)
    instance.containers[1].height_m = 14.
    failed = visibility.check_visibility(instance,ship,call_index=2)
    assert failed.metrics['blind_distance_m'] > failed.metrics['limit_m']
    assert failed.violations[0].call_index == 2


def test_actual_heights_ignore_nominal_cell_floor(monkeypatch):
    condition(monkeypatch)
    instance = build_instance()
    ship = deck_box(instance,height=5.)
    top = instance.containers[2]
    top.height_m = 5.
    top.oog = None
    ship.placements.append(Placement(slot='0010184',container_id=top.id))
    top_cell = next(c for c in instance.vessel.cells if c.id=='0010184')
    top_cell.vcg_base_m = 999.
    top_cell.lcg_m = 60.
    top_cell.tcg_m = 0.
    report = visibility.check_visibility(instance,ship,call_index=0)
    assert report.metrics['blind_distance_m'] == pytest.approx(20*(63.029+2)/6-130)


def test_side_stack_outside_forward_wedge(monkeypatch):
    condition(monkeypatch)
    instance = build_instance()
    ship = deck_box(instance,height=14.,x=20.,y=20.)
    report = visibility.check_visibility(instance,ship,call_index=0)
    assert report.metrics['blind_distance_m'] == 0.
    assert not report.violations


def test_horizon_blocking_stack_reports_sectors(monkeypatch):
    condition(monkeypatch)
    instance = build_instance()
    ship = deck_box(instance,height=20.,x=20.)
    report = visibility.check_visibility(instance,ship,call_index=0)
    assert math.isinf(report.metrics['blind_distance_m'])
    assert report.metrics['max_blind_sector_rad'] > math.radians(5)
    assert any('individual' in v.message for v in report.violations)


def test_trim_changes_visibility(monkeypatch):
    instance = build_instance()
    ship = deck_box(instance,height=10.)
    condition(monkeypatch)
    even = visibility.check_visibility(instance,ship,call_index=0)
    condition(monkeypatch,aft=6.,fore=4.)
    trimmed = visibility.check_visibility(instance,ship,call_index=0)
    assert trimmed.ran
    assert trimmed.metrics['trim_m'] == 2.
    assert trimmed.metrics['blind_distance_m'] != pytest.approx(even.metrics['blind_distance_m'])


def test_numerical_equilibrium_uses_declared_fleet_geometry():
    # Fixture builder supplies geometry and containers; replace only its clearly
    # labelled placeholder hull particulars with the numerical fleet solid.
    instance = build_instance()
    p = load_ship_params('feeder_500')['hull']
    h = instance.vessel.hull
    for key in ('lpp_m','loa_m','beam_m','depth_m','hull_offsets_ref','lightship_mass_kg',
                'lightship_lcg_m','lightship_vcg_m'):
        setattr(h,key,p[key])
    instance.vessel.tanks = []
    instance.vessel.fixed_loads = []
    instance.onboard.tank_masses_kg = {}
    c = visibility._loading_condition(instance,instance.onboard)
    assert c.mass_kg == p['lightship_mass_kg']
    assert c.t_aft_m > c.t_fwd_m > 0.
    assert c.gm_m > 0.
    assert c.geometry.submerged(c.t_aft_m,c.t_fwd_m,c.heel_rad).displacement_kg == pytest.approx(c.mass_kg,rel=1e-6)
    instance.vessel.fixed_loads = [background_load(x=85.,mass=100000.)]
    loaded = visibility._loading_condition(instance,instance.onboard)
    assert loaded.mass_kg == c.mass_kg+100000.
    assert loaded.lcg_m > c.lcg_m
    assert loaded.t_aft_m-loaded.t_fwd_m < c.t_aft_m-c.t_fwd_m
    # Removing geometry preserves exactly the same physical floating condition.
    instance.vessel.fixed_loads = [instance.vessel.fixed_loads[0].model_copy(update={'silhouette':None})]
    point = visibility._loading_condition(instance,instance.onboard)
    assert point.mass_kg == loaded.mass_kg
    assert point.t_aft_m == pytest.approx(loaded.t_aft_m,abs=1e-9)
    assert point.t_fwd_m == pytest.approx(loaded.t_fwd_m,abs=1e-9)


def test_clear_angle_and_total_sector_limits(monkeypatch):
    condition(monkeypatch)
    instance = build_instance()
    deck_box(instance,height=20.,x=50.,y=-2.)
    second = instance.containers[2]
    second.height_m = 20.
    second.oog = None
    instance.onboard.placements.append(Placement(slot='0010282',container_id=second.id))
    cell = next(c for c in instance.vessel.cells if c.id=='0010282')
    cell.lcg_m,cell.tcg_m = 50.,2.
    report = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert any('clear angle' in v.message for v in report.violations)
    for p,y in zip(instance.onboard.placements,(-10.,0.)):
        cell = next(c for c in instance.vessel.cells if c.id==p.slot)
        cell.lcg_m,cell.tcg_m = 20.,y
    third = instance.containers[3]
    third.height_m = 20.
    third.oog = None
    instance.onboard.placements.append(Placement(slot='0030182',container_id=third.id))
    cell = next(c for c in instance.vessel.cells if c.id=='0030182')
    cell.lcg_m,cell.tcg_m = 20.,10.
    report = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert any('total cargo' in v.message for v in report.violations)


def test_directional_oog_extents_change_actual_footprint(monkeypatch):
    from stowage.model.cargo import OutOfGauge

    condition(monkeypatch)
    instance = build_instance()
    ship = deck_box(instance,height=10.)
    box = instance.containers[1]
    # Real fixture-builder OOG object, with its declared height retained.
    box.oog = instance.containers[3].oog.model_copy(update={
        'over_height_m':0., 'over_length_fore_m':60.,
        'over_length_aft_m':2., 'over_width_port_m':3.,
        'over_width_starboard_m':1.})
    failed = visibility.check_visibility(instance,ship,call_index=0)
    # Forward edge moves from 63.029 to123.029; asymmetric centre
    # shift must not move it by half the sum of the two overhangs.
    assert failed.ran
    assert failed.metrics['blind_distance_m'] == pytest.approx(20*(123.029+2)/6-130)
    assert failed.violations
    box.oog = OutOfGauge(over_length_aft_m=60.)
    passed = visibility.check_visibility(instance,ship,call_index=0)
    assert passed.ran and not passed.violations
    assert passed.metrics['blind_distance_m'] == pytest.approx(20*(63.029+2)/6-130)

    # A port-side extension from a starboard stack reaches into ±10°;
    # the same amount extending to starboard remains outside that wedge.
    ship = deck_box(instance,height=14.,x=20.,y=-20.)
    box.oog = OutOfGauge(over_width_starboard_m=18.)
    outside = visibility.check_visibility(instance,ship,call_index=0)
    assert outside.metrics['blind_distance_m'] == 0.
    box.oog = OutOfGauge(over_width_port_m=18.)
    inside = visibility.check_visibility(instance,ship,call_index=0)
    assert inside.metrics['blind_distance_m'] > 0.


def test_below_eye_outside_forward_wedge_can_fail_sector(monkeypatch):
    from stowage.model.cargo import OutOfGauge

    condition(monkeypatch)
    instance = build_instance()
    ship = deck_box(instance,height=15.5,x=20.,y=10.)
    box = instance.containers[1]
    # Roof z=24.5 is below eye z=25. Even the aft edge x=16.971
    # is 18.971m forward of the conning eye: its forward tangent alone
    # reaches20*18.971/0.5=758.84m, beyond eye-to-bow+limit=386m.
    # A 6m port overhang makes its angle exceed10deg wholly outside ±10.
    box.oog = OutOfGauge(over_width_port_m=6.)
    report = visibility.check_visibility(instance,ship,call_index=0)
    assert report.ran
    assert report.metrics['blind_distance_m'] == 0.
    assert report.metrics['max_blind_sector_rad'] > math.radians(10)
    assert any('individual cargo blind sector' in v.message for v in report.violations)
    assert not any('V/22.1.1:' in v.message for v in report.violations)


def test_below_eye_short_sea_shadow_is_not_a_blind_sector(monkeypatch):
    from stowage.model.cargo import OutOfGauge

    condition(monkeypatch)
    instance = build_instance()
    ship = deck_box(instance,height=10.,x=20.,y=10.)
    # Roof h=14, h_eye=20: even the farthest corner tangent range
    # 20*hypot(25.029,17.219)/6 is below eye-to-bow+limit=386m.
    instance.containers[1].oog = OutOfGauge(over_width_port_m=6.)
    report = visibility.check_visibility(instance,ship,call_index=0)
    assert report.ran and not report.violations
    assert report.metrics['max_blind_sector_rad'] == 0.
    assert report.metrics['total_blind_sector_rad'] == 0.


def test_below_eye_sector_threshold_clips_box_analytically(monkeypatch):
    from stowage.model.cargo import OutOfGauge

    condition(monkeypatch)
    instance = build_instance()
    ship = deck_box(instance,height=14.5,x=20.,y=10.)
    instance.containers[1].oog = OutOfGauge(over_width_port_m=6.)
    # Roof h=18.5, eye H=20 at x=-2: the radial cutoff on the roof
    # is (130+256)*(20-18.5)/20=28.95m. It intersects both the front
    # x=25.029 edge and port y=17.219 edge inside their endpoints.
    # The two exact circle/edge intersections bound the blind sector.
    report = visibility.check_visibility(instance,ship,call_index=0)
    expected = math.asin(17.219/28.95)-math.acos(25.029/28.95)
    assert report.metrics['max_blind_sector_rad'] == pytest.approx(expected)


def test_asymmetric_oog_mass_moments_in_loading_equilibrium():
    from stowage.model.cargo import OutOfGauge

    instance = build_instance()
    params = load_ship_params('feeder_500')['hull']
    for key in ('lpp_m','loa_m','beam_m','depth_m','hull_offsets_ref',
                'lightship_mass_kg','lightship_lcg_m','lightship_vcg_m'):
        setattr(instance.vessel.hull,key,params[key])
    instance.vessel.tanks = []
    instance.vessel.fixed_loads = []
    ship = deck_box(instance,height=2.591,x=60.,y=0.)
    ship.tank_masses_kg = {}
    box = instance.containers[1]
    box.oog = OutOfGauge(over_length_fore_m=2.,over_width_port_m=1.)
    loaded = visibility._loading_condition(instance,ship)
    # A uniform expanded envelope moves this cargo CG 1m forward and .5m
    # port. Two point masses then give these independent mass moments.
    total = params['lightship_mass_kg']+box.gross_mass_kg
    assert loaded.lcg_m == pytest.approx(
        (params['lightship_mass_kg']*params['lightship_lcg_m']+box.gross_mass_kg*61)/total)
    assert loaded.tcg_m == pytest.approx(box.gross_mass_kg*.5/total)


def background_load(*, x=60., y=0., bottom=9., top=23., mass=12000.):
    from stowage.model.vessel import FixedLoad, FixedLoadSilhouette

    return FixedLoad(x_m=x,y_m=y,z_m=(bottom+top)/2,mass_kg=mass,kind='non_cargo',
        silhouette=FixedLoadSilhouette(x_min_m=x-3.029,x_max_m=x+3.029,
            y_min_m=y-1.219,y_max_m=y+1.219,z_min_m=bottom,z_max_m=top))


@pytest.mark.parametrize('aft,fore',[(5.,5.),(6.,4.),(4.,6.)])
@pytest.mark.parametrize('kind',['non_cargo'])
def test_background_silhouette_equals_planned_cargo_including_trim(monkeypatch,aft,fore,kind):
    condition(monkeypatch,aft=aft,fore=fore)
    instance = build_instance()
    ship = deck_box(instance,height=14.)
    planned = visibility.check_visibility(instance,ship,call_index=0)
    instance.vessel.fixed_loads.append(background_load().model_copy(update={'kind':kind}))
    ship.placements = []
    background = visibility.check_visibility(instance,ship,call_index=0)
    assert background.ran
    assert background.metrics == planned.metrics
    assert background.margins == planned.margins
    assert [(v.rule_id,v.measured,v.limit) for v in background.violations] == [
        (v.rule_id,v.measured,v.limit) for v in planned.violations]
    assert background.violations


def test_explicit_background_behind_bridge_low_or_submerged_is_not_obstacle(monkeypatch):
    condition(monkeypatch)
    instance = build_instance()
    instance.vessel.bridge.x_m = 0.
    for load in (background_load(x=-20.,top=50.),
                 background_load(bottom=2.,top=8.),
                 background_load(bottom=2.,top=4.)):
        instance.vessel.fixed_loads = [load]
        report = visibility.check_visibility(instance,instance.onboard,call_index=0)
        assert report.ran and not report.violations
        assert report.metrics['blind_distance_m'] == 0.
        assert report.metrics['total_blind_sector_rad'] == 0.


def test_fixed_load_mass_and_moments_count_once_independently_of_silhouette(monkeypatch):
    instance = build_instance()
    instance.vessel.tanks = []
    instance.onboard.tank_masses_kg = {}
    hull = instance.vessel.hull
    hull.hull_offsets_ref = 'params/fleet/feeder_500.toml#hull_form'
    solid = SimpleNamespace(lpp_m=hull.lpp_m,beam_m=hull.beam_m,depth_m=hull.depth_m)
    def equilibrium(mass,x,y,z,rho):
        return SimpleNamespace(t_aft_m=5.,t_fwd_m=4.,heel_rad=0.,trim_m=1.,
                               submerged=SimpleNamespace(vcb_m=2.,bm_t_m=6.))
    solid.equilibrium = equilibrium
    monkeypatch.setattr(visibility,'_geometry',lambda _: (solid,{}))
    fixed = background_load(x=90.,y=5.,bottom=11.,top=25.,mass=600000.)
    instance.vessel.fixed_loads = [fixed]
    c = visibility._loading_condition(instance,instance.onboard)
    expected_mass = hull.lightship_mass_kg+fixed.mass_kg
    assert c.mass_kg == expected_mass
    assert c.lcg_m == pytest.approx((hull.lightship_mass_kg*hull.lightship_lcg_m+fixed.mass_kg*fixed.x_m)/expected_mass)
    assert c.tcg_m == pytest.approx(fixed.mass_kg*fixed.y_m/expected_mass)
    assert c.kg_m == pytest.approx((hull.lightship_mass_kg*hull.lightship_vcg_m+fixed.mass_kg*fixed.z_m)/expected_mass)
    instance.vessel.fixed_loads = [fixed.model_copy(update={'silhouette':None})]
    assert visibility._loading_condition(instance,instance.onboard) == c


@pytest.mark.parametrize('assignment',['direct','model_copy'])
def test_raw_legacy_fixed_load_matches_named_mass_moments(monkeypatch,assignment):
    from stowage.model.vessel import FixedLoad

    instance = build_instance()
    instance.vessel.tanks = []
    instance.onboard.tank_masses_kg = {}
    hull = instance.vessel.hull
    hull.hull_offsets_ref = 'params/fleet/feeder_500.toml#hull_form'
    solid = SimpleNamespace(lpp_m=hull.lpp_m,beam_m=hull.beam_m,depth_m=hull.depth_m)
    solid.equilibrium = lambda *args: SimpleNamespace(t_aft_m=5.,t_fwd_m=4.,
        heel_rad=0.,trim_m=1.,submerged=SimpleNamespace(vcb_m=2.,bm_t_m=6.))
    monkeypatch.setattr(visibility,'_geometry',lambda _: (solid,{}))
    point = (90.,5.,18.,600000.)
    instance.vessel.fixed_loads = [FixedLoad.model_validate(point)]
    named = visibility._loading_condition(instance,instance.onboard)
    if assignment == 'model_copy':
        instance.vessel = instance.vessel.model_copy(update={'fixed_loads':[point]})
    else:
        instance.vessel.fixed_loads = [point]
    raw = visibility._loading_condition(instance,instance.onboard)
    assert raw == named
    normalised = visibility._fixed_loads(instance.vessel)
    assert normalised[0].kind == 'unknown'
    assert normalised[0].silhouette is None
    assert normalised[0].as_point_mass() == point


@pytest.mark.parametrize('assignment',['direct','model_copy'])
def test_legacy_fixed_tuple_with_unknown_geometry_is_hard_missing_data(monkeypatch,assignment):
    condition(monkeypatch)
    instance = build_instance()
    instance.vessel.bridge.x_m = 0.
    explicit = background_load(top=24.5)
    instance.vessel.fixed_loads = [explicit]
    assert visibility.check_visibility(instance,instance.onboard,call_index=0).violations
    records = [(80.,0.,40.,200000.),explicit]
    if assignment == 'model_copy':
        instance.vessel = instance.vessel.model_copy(update={'fixed_loads':records})
    else:
        instance.vessel.fixed_loads = records
    actual = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert not actual.ran
    assert actual.violations[0].rule_id == 'checker.missing_vessel_data'
    assert actual.violations[0].severity == 'hard'
    instance.vessel.fixed_loads = [records[0]]
    unknown_geometry = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert not unknown_geometry.ran
    assert unknown_geometry.violations[0].rule_id == 'checker.missing_vessel_data'


@pytest.mark.parametrize('kind',['cargo','other','non_cargo'])
def test_missing_fixed_silhouette_never_certifies_visibility(monkeypatch,kind):
    condition(monkeypatch)
    instance = build_instance()
    instance.vessel.bridge.x_m = 0.
    # A CG (even a submerged/aft CG) cannot bound the occupied geometry.
    unknown = background_load(x=-20.,bottom=2.,top=4.).model_copy(
        update={'kind':kind,'silhouette':None})
    instance.vessel.fixed_loads = [unknown]
    report = visibility.check_visibility(instance,instance.onboard,call_index=3)
    assert not report.ran
    assert report.error and 'silhouette' in report.error
    assert report.metrics == {} and report.margins == {}
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == 'checker.missing_vessel_data'
    assert violation.severity == 'hard' and violation.call_index == 3


@pytest.mark.parametrize('geometry',['unknown','submerged','obstructing'])
def test_full_checker_cannot_pass_unknown_fixed_geometry(monkeypatch,geometry):
    from stowage.checker import full
    from stowage.model import (
        MovePlan, PartReport, Provenance, VoyageConditionIdentity, VoyageSpec,
    )
    from stowage.vessel.fleet_voyage import build_voyage_policy

    condition(monkeypatch)
    instance = build_instance()
    instance.vessel.voyage_policy = build_voyage_policy(
        instance.vessel.tanks,
        parameter_source='APPROXIMATION: synthetic visibility full-gate fixture',
        vessel_id=instance.vessel.id,
    )
    instance.voyage = VoyageSpec(
        departure=VoyageConditionIdentity(
            id='visibility-regression.departure', phase='departure', call_index=0,
            locode=instance.port.locode,
            environment_id='synthetic.TH-LCH.departure',
            density_id='synthetic.departure.1025kg-m3',
            water_density_kg_m3=instance.port.water_density_kg_m3,
            provenance=Provenance(created_by='test_visibility.full_gate', parents=[
                'ASSUMPTION: synthetic departure environment uses the declared fixture port density',
            ]),
        ),
        arrival=VoyageConditionIdentity(
            id='visibility-regression.arrival', phase='arrival', call_index=1,
            locode=instance.rotation.calls[1].locode,
            environment_id='synthetic.TH-BKK.arrival',
            density_id='synthetic.arrival.1000kg-m3', water_density_kg_m3=1000.,
            provenance=Provenance(created_by='test_visibility.full_gate', parents=[
                'ASSUMPTION: synthetic freshwater next-call environment, density 1000 kg/m3',
            ]),
        ),
        provenance=Provenance(created_by='test_visibility.full_gate', parents=[
            'APPROXIMATION: synthetic visibility full-gate fixture; D50 endpoint voyage',
        ]),
    )
    instance.load_list = []
    instance.vessel.bridge.x_m = 0.
    known = background_load(bottom=2.,top=4.) if geometry == 'submerged' else background_load(top=50.)
    if geometry == 'unknown':
        known = known.model_copy(update={'silhouette':None})
    instance.vessel.fixed_loads = [known]
    # Exercise real replay and full-verdict aggregation with the real visibility
    # part. Other parts are isolated: this is a visibility gate regression,
    # not certification of the placeholder fixture's unrelated physics.
    for name in ('check_static_rules','check_stability','check_strength','check_lashing'):
        monkeypatch.setattr(full,name,lambda *args,**kwargs: PartReport(name='isolated'))
    plan = MovePlan(id='visibility-regression',instance_id=instance.id,call_index=0)
    report = full.check(instance,plan)
    assert report.parts['voyage'].ran, report.parts['voyage'].error
    assert report.passed == (geometry == 'submerged')
    part = report.parts['visibility']
    assert part.ran == (geometry != 'unknown')
    if geometry == 'unknown':
        assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
                   for v in report.violations)
    elif geometry == 'obstructing':
        assert any(v.rule_id == 'solas.v22.visibility' for v in report.violations)


def test_invalid_legacy_fixed_tuple_is_hard_missing_data(monkeypatch):
    condition(monkeypatch)
    instance = build_instance()
    instance.vessel = instance.vessel.model_copy(update={'fixed_loads':[(80.,0.,200000.)]})
    report = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id == 'checker.missing_vessel_data'
    assert report.violations[0].severity == 'hard'

"""D29 cargo requires securing evidence; an envelope is not a certificate."""
from types import SimpleNamespace

import pytest

from stowage.checker import lashing as l
from stowage.checker.lashing import check_lashing
from stowage.model import FixedLoad, FixedLoadSilhouette, Placement
from stowage.model.fixtures import build_instance
from stowage.model import VesselFieldProvenance


@pytest.fixture(autouse=True)
def explicit_unit_test_guide_design(monkeypatch):
    # These fixtures isolate IRS force/stress arithmetic and background evidence;
    # their placeholder hull has no declared lashing manual. Give the mocked
    # design an explicit synthetic cap; D56's real cap has dedicated tests.
    original_builder = build_instance
    def unit_instance(*args, **kwargs):
        instance = original_builder(*args, **kwargs)
        instance.vessel.hull.max_gm_lashing_m = 1e9
        instance.vessel.hull.max_gm_lashing_m_provenance = VesselFieldProvenance(
            assumption='Unit-test cap for fixed guide/stress arithmetic, not a vessel design')
        return instance
    monkeypatch.setitem(globals(), 'build_instance', unit_instance)
    design = l.CellGuideDesign(l.CELL_GUIDE_SECTION_MODULUS_M3,
        l.CELL_GUIDE_AREA_M2, 1e9, '00101H', 0., 0., 2)
    monkeypatch.setattr(l, 'derive_cell_guide_design', lambda _: design)



def _condition(monkeypatch):
    # Hold the loading condition fixed to isolate silhouette/securing behavior.
    # Visibility integration tests separately check fixed mass and CG accounting.
    monkeypatch.setattr('stowage.checker.visibility._loading_condition', lambda *_: SimpleNamespace(
        mass_kg=9600000., gm_m=1., t_aft_m=6., t_fwd_m=6.,
        geometry=SimpleNamespace(submerged=lambda *_: SimpleNamespace(volume_m3=.65*120*20*7))))


def _background():
    return FixedLoad(x_m=60.,y_m=5.,z_m=20.,mass_kg=1000000.,kind='cargo',
                     silhouette=FixedLoadSilhouette(x_min_m=40.,x_max_m=80.,
                         y_min_m=.1,y_max_m=10.,z_min_m=9.,z_max_m=40.))


@pytest.mark.parametrize('planned', [False, True])
@pytest.mark.parametrize('silhouette', [False, True])
def test_background_cargo_without_securing_evidence_fails_closed(monkeypatch, planned, silhouette):
    _condition(monkeypatch)
    instance = build_instance()
    background = _background()
    instance.vessel.fixed_loads = [background if silhouette else
                                 background.model_copy(update={'silhouette': None})]
    if planned:
        box = instance.containers[1]
        box.oog = None
        instance.onboard.placements = [Placement(slot='0010182', container_id=box.id)]
    report = check_lashing(instance, instance.onboard, call_index=2)
    assert not report.ran
    assert report.error and 'background cargo' in report.error.lower()
    assert not report.metrics  # zero demand would misrepresent unchecked cargo
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
               and v.call_index == 2 for v in report.violations)


def test_full_checker_cannot_certify_unchecked_background_cargo(monkeypatch):
    from stowage.checker import full
    from stowage.model import (MovePlan, PartReport, Provenance,
                               VoyageConditionIdentity, VoyageSpec)
    from stowage.vessel.fleet_voyage import build_voyage_policy

    _condition(monkeypatch)
    instance = build_instance()
    instance.load_list = []
    instance.vessel.fixed_loads = []
    instance.vessel.voyage_policy = build_voyage_policy(
        instance.vessel.tanks, parameter_source='synthetic model fixture',
        vessel_id=instance.vessel.id)
    evidence = Provenance(created_by='test_lashing_background.synthetic_voyage',
        parents=['DECLARATION: synthetic next-call environment and seawater density'])
    instance.voyage = VoyageSpec(**{
        phase: VoyageConditionIdentity(id=f'background-securing.{phase}', phase=phase,
            call_index=index, locode=instance.rotation.calls[index].locode,
            environment_id=f'synthetic.{phase}', density_id='synthetic-seawater-1025',
            water_density_kg_m3=instance.port.water_density_kg_m3, provenance=evidence)
        for index, phase in enumerate(('departure', 'arrival'))}, provenance=evidence)
    plan = MovePlan(id='background-securing', instance_id=instance.id, call_index=0)
    # Exercise real replay, lashing and the full-checker gate. Other owners'
    # criteria are isolated to make a rejected lashing part the only cause.
    for name in ('check_static_rules', 'check_stability', 'check_strength', 'check_visibility'):
        monkeypatch.setattr(full, name, lambda *_, **__: PartReport(name='isolated'))
    clear = full.check(instance, plan)
    assert clear.passed and clear.parts['lashing'].ran
    instance.vessel.fixed_loads = [_background()]
    unchecked = full.check(instance, plan)
    assert not unchecked.passed
    assert not unchecked.parts['lashing'].ran
    assert unchecked.parts['lashing'].violations[0].rule_id == 'checker.missing_vessel_data'


def test_capacity_audit_is_not_independent_securing_evidence(monkeypatch):
    _condition(monkeypatch)
    instance = build_instance()
    instance.vessel.fixed_loads = [_background()]
    # Even capacity/placement records cannot certify unmodelled rods, fittings,
    # guide contacts or individual container end-frame properties.
    instance.vessel.sources.append('stowage.slice_condition:' +
        '{"version":"c3-fixed-background-6","background_cells":[],' +
        '"background_stacks":[],"securing_passed":true}')
    report = check_lashing(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert any(v.rule_id == 'checker.missing_vessel_data' for v in report.violations)


def test_zero_mass_background_does_not_create_an_unchecked_stack(monkeypatch):
    _condition(monkeypatch)
    instance = build_instance()
    instance.vessel.fixed_loads = [_background().model_copy(update={'mass_kg': 0., 'cargo_boxes': []})]
    report = check_lashing(instance, instance.onboard, call_index=0)
    assert report.ran and not report.violations
    assert report.metrics['physical_box_count'] == 0


def test_legacy_and_structured_fixed_loads_reach_equilibrium_but_unknown_cannot_certify():
    """A slice model_copy can retain raw tuples: exercise the real shared helper."""
    from stowage.vessel.fleet import load_ship_params

    instance = build_instance()
    params = load_ship_params('feeder_500')['hull']
    for key in ('lpp_m','loa_m','beam_m','depth_m','hull_offsets_ref',
                'lightship_mass_kg','lightship_lcg_m','lightship_vcg_m','scantling_draught_m'):
        setattr(instance.vessel.hull,key,params[key])
    instance.vessel.tanks = []
    instance.onboard.tank_masses_kg = {}
    box = instance.containers[1]
    box.oog = None
    instance.onboard.placements = [Placement(slot='0010182',container_id=box.id)]
    forward = FixedLoad(x_m=85.,y_m=0.,z_m=4.,mass_kg=100000.)
    aft = FixedLoad(x_m=25.,y_m=0.,z_m=4.,mass_kg=50000.)
    instance.vessel = instance.vessel.model_copy(update={'fixed_loads':[forward,aft]})
    from stowage.checker.visibility import _loading_condition
    structured = _loading_condition(instance,instance.onboard)
    assert not check_lashing(instance,instance.onboard,call_index=0).ran
    # model_copy intentionally bypasses validation, matching the fresh-slice
    # failure path. Include both representations in the same input collection.
    instance.vessel = instance.vessel.model_copy(update={
        'fixed_loads':[forward.as_point_mass(),aft]})
    legacy = _loading_condition(instance,instance.onboard)
    assert isinstance(instance.vessel.fixed_loads[0],tuple)
    assert legacy.mass_kg == structured.mass_kg
    assert legacy.gm_m == structured.gm_m
    assert not check_lashing(instance,instance.onboard,call_index=0).ran

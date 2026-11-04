"""Independent mathematical oracles and calibrated synthetic §5 scalar probes.

Sources and the reported values are transcribed in docs/data-sources.md §5:
MAIB 14/2020 (Ever Smart), Bahamas Maritime Authority (MOL Comfort), MAIB
9/2008 (MSC Napoli), and MAIB 21/2007 (Annabella). None of these tests models
an actual accident ship, wave history, whipping, or its certified envelope.
They test the full-checker's numerical read-outs and tagged outcomes at the
reported scale. Strength envelopes are calibrated against one unchanged
synthetic condition: these are ratio/output regressions, not independent
validation of the hull-girder solver. Fixed-input mathematical oracles below
run by default; the calibrated scalar probes remain explicitly slow.

IACS UR L5 Rev.4 tolerances (docs/data-sources.md §4) are used for GM: the
larger of 1% and 0.05 m. L5 does not prescribe strength-ratio tolerances;
those deliberately calibrated ratios use ordinary numerical precision.
"""
from __future__ import annotations

import pytest

from stowage.checker.full import check
from stowage.checker.strength import check_strength
from stowage.checker.visibility import _loading_condition
from stowage.model import (BallastCondition, MovePlan, Placement, Provenance,
                          ShipState, VoyageConditionIdentity,
                          VoyageConsumption, VoyageSpec)
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel import slice_vessel, recondition_slice, departure_tank_masses
from stowage.vessel.fleet_voyage import build_voyage_policy


def _declare_scalar_probe_voyage(instance):
    """Declare unchanged liquid endpoints for the isolated scalar probes."""
    evidence = Provenance(created_by='test_full_cases.synthetic_voyage', parents=[
        'APPROXIMATION: synthetic zero-consumption leg isolates calibrated scalar readouts',
        'DECLARATION: synthetic next-call environment and seawater density'])
    assert instance.vessel.voyage_policy is not None
    instance.voyage = VoyageSpec(**{
        phase: VoyageConditionIdentity(
            id=f'scalar-probe.{phase}', phase=phase, call_index=index,
            locode=instance.rotation.calls[index].locode,
            environment_id=f'scalar-probe.{phase}', density_id='synthetic-seawater-1025',
            water_density_kg_m3=instance.port.water_density_kg_m3,
            provenance=evidence)
        for index, phase in enumerate(('departure', 'arrival'))},
        consumption=[VoyageConsumption(group_id=group.id, mass_kg=0., provenance=evidence)
                     for group in instance.vessel.voyage_policy.groups],
        provenance=evidence)


def _analogue(ship_name='feeder_1700'):
    """Use a production KCS whole hull with an evidenced synthetic background.

    The largest available ship is closest in capacity to the three large
    casualty ships. Annabella uses the 1,000 TEU class, closest to 868 TEU.
    """
    instance = build_instance()
    params = load_ship_params(ship_name)
    assert params['hull_form']['source']['document'].startswith('KRISO KCS')
    parent = load_vessel(ship_name)
    # Retain one middle bay, including hold/deck cells, so the production
    # builder supplies actual D42 box records/catalogs for the omitted cargo.
    bays = sorted({stack.bay for stack in parent.stacks})
    instance.vessel = recondition_slice(
        slice_vessel(parent, [bays[len(bays)//2]], name=ship_name+'-case'),
        expected_load_mass_kg=0., seed=41)
    instance.family['vessel'] = ship_name
    hull = instance.vessel.hull
    assert hull is not None and hull.stations
    assert all(load.cargo_boxes for load in instance.vessel.fixed_loads
               if load.mass_kg > 0 and load.kind == 'cargo')
    instance.onboard = ShipState(vessel_id=instance.vessel.id,
                                tank_masses_kg=departure_tank_masses(instance.vessel))
    instance.load_list = []
    # Weight classes and their ordering use current catalogue defaults; the
    # accidental-case probes do not relax current cargo-weight rules.
    instance.rule_overrides = {}
    for box in instance.containers:
        box.length_ft, box.iso_type = 20, '22G1'
        box.reefer, box.reefer_setpoint_c = False, None
        box.dg, box.oog, box.special_stowage = None, None, []
        box.height_m, box.pod = 2.591, 'THBKK'
    for stack in instance.yard.stacks:
        stack.length_ft = 20
    _declare_scalar_probe_voyage(instance)
    instance = type(instance).model_validate(instance.model_dump())
    plan = MovePlan(id='case.approximation', instance_id=instance.id, call_index=0,
                    ballast=BallastCondition(tank_masses_kg={}, source='planner'))
    return instance, plan


@pytest.mark.slow
def test_ever_smart_approximation_reported_departure_gm(record_property):
    """Calibrate GM to 0.949 m; the 13.57 m real-ship draught is unavailable."""
    instance, plan = _analogue()
    target = .949
    hull = instance.vessel.hull
    # APPROXIMATION: choose synthetic lightship KG for the published GM.
    # The empty, symmetric condition changes KG without moving the waterline.
    for _ in range(3):
        actual = _loading_condition(instance, instance.onboard)
        hull.lightship_vcg_m += (actual.gm_m - target) * actual.mass_kg / hull.lightship_mass_kg
    report = check(instance, plan)
    record_property('gm_m', report.parts['stability'].metrics['gm_m'])
    record_property('vessel_id', instance.vessel.id)
    assert all(part.ran for part in report.parts.values())
    assert report.parts['stability'].metrics['gm_m'] == pytest.approx(target, abs=max(.01*target, .05))
    assert report.parts['lashing'].metrics['gm_m'] == pytest.approx(target, abs=max(.01*target, .05))


def _calibrated_strength(instance, *, bm_ratio, sf_ratio):
    """Circular output probe: set limits from the continuous calculated peaks."""
    # Unit envelopes expose continuous critical-cut peaks, including locations
    # between the original station readouts. This remains deliberately circular
    # as a scalar-output probe; the independent beam test below is the oracle.
    for station in instance.vessel.hull.stations:
        station.max_bending_nm = station.max_shear_n = 1.
        station.min_bending_nm = station.min_shear_n = -1.
    baseline = check_strength(instance, instance.onboard, call_index=0)
    assert baseline.ran, baseline.error
    peak_bm = baseline.metrics['max_bm_ratio']
    peak_sf = baseline.metrics['max_sf_ratio']
    assert peak_bm > 0 and peak_sf > 0
    # APPROXIMATION: these synthetic envelopes reproduce only utilization
    # magnitudes, not the real ships' permissible bending/shear curves.
    for station in instance.vessel.hull.stations:
        station.max_bending_nm = peak_bm / bm_ratio
        station.min_bending_nm = -peak_bm / bm_ratio
        station.max_shear_n = peak_sf / sf_ratio
        station.min_shear_n = -peak_sf / sf_ratio


@pytest.mark.slow
def test_mol_comfort_approximation_reported_99_percent_bm_94_percent_sf(record_property):
    """Reported still-water utilization is below unity, despite the later loss.

    Cannot reconstruct the 8,110 TEU hull, its wave encounter, or its failure.
    This must not be misrepresented as predicting that historical casualty.
    """
    instance, plan = _analogue()
    _calibrated_strength(instance, bm_ratio=.99, sf_ratio=.94)
    report = check(instance, plan)
    assert all(part.ran for part in report.parts.values())
    part = report.parts['strength']
    assert part.ran
    record_property('max_bm_ratio', part.metrics['max_bm_ratio'])
    record_property('max_sf_ratio', part.metrics['max_sf_ratio'])
    record_property('vessel_id', instance.vessel.id)
    assert part.metrics['max_bm_ratio'] == pytest.approx(.99, rel=1e-6)
    assert part.metrics['max_sf_ratio'] == pytest.approx(.94, rel=1e-6)
    assert not any(v.rule_id.startswith('iacs.s11a.') for v in part.violations)


@pytest.mark.slow
@pytest.mark.parametrize('ratio', [.88, 1.16, .99], ids=['harbour-88', 'sea-116', 'revised-99'])
def test_msc_napoli_approximation_reported_bending_utilization(ratio, record_property):
    """Calibrated output/rejection probe; no independent casualty prediction."""
    instance, plan = _analogue()
    _calibrated_strength(instance, bm_ratio=ratio, sf_ratio=.5)
    report = check(instance, plan)
    assert all(part.ran for part in report.parts.values())
    part = report.parts['strength']
    assert part.ran
    record_property('max_bm_ratio', part.metrics['max_bm_ratio'])
    record_property('vessel_id', instance.vessel.id)
    assert part.metrics['max_bm_ratio'] == pytest.approx(ratio, rel=1e-6)
    violations = [v for v in part.violations if v.rule_id == 'iacs.s11a.longitudinal_strength']
    assert bool(violations) is (ratio > 1)
    if ratio > 1:
        assert not report.passed
        assert all(v.severity == 'hard' for v in violations)


@pytest.mark.slow
def test_annabella_approximation_96_t_limit_and_securing_collapse(record_property):
    """Reproduce a published 20-foot 96 t limit with a 100 t synthetic stack.

    APPROXIMATION: 4 t excess is an invented threshold probe, not Annabella's
    reported cargo mass. Its real limit was valid only for GM <=0.8 m; this
    tests a supplied limit, not a GM-dependent loading-manual interpolation.
    The companion deck stack demonstrates securing rejection on this analogue,
    not reconstruction of the vessel's hold/cell-guide collapse sequence.
    """
    instance, plan = _analogue('feeder_800')
    lower, upper = instance.containers[:2]
    lower.gross_mass_kg = upper.gross_mass_kg = 50000
    instance.stage = 'B'  # A preloaded departure condition, not a stage-A loading plan.
    onboard_ids = {lower.id, upper.id}
    for stack in instance.yard.stacks:
        stack.boxes = [cid for cid in stack.boxes if cid not in onboard_ids]
    instance.yard.stacks = [stack for stack in instance.yard.stacks if stack.boxes]
    instance.yard.quay_buffer = [cid for cid in instance.yard.quay_buffer if cid not in onboard_ids]
    hold = next(s for s in instance.vessel.stacks if not s.on_deck and len(s.cell_ids) >= 2)
    instance.onboard.placements = [Placement(slot=slot, container_id=box.id)
                                   for slot, box in zip(hold.cell_ids, (lower, upper))]
    hold.max_mass_20_kg = 96000
    instance = type(instance).model_validate(instance.model_dump())
    report = check(instance, plan)
    assert all(part.ran for part in report.parts.values())
    overweight = [v for v in report.violations if v.rule_id == 'expert.physical.stack_weight']
    record_property('vessel_id', instance.vessel.id)
    record_property('hold_stack_id', hold.id)
    record_property('stack_mass_kg', 100000)
    record_property('stack_limit_kg', 96000)
    assert overweight and not report.passed
    assert any(v.measured == 100000 and v.limit == 96000 and v.unit == 'kg' for v in overweight)
    # Shift the same severe loading to a deck stack for the independent lashing
    # part; IRS also rejects each 50 t box above its tabulated maximum mass.
    stack = next(s for s in instance.vessel.stacks if s.on_deck and len(s.cell_ids) >= 2)
    instance.onboard.placements = [Placement(slot=slot, container_id=box.id)
                                   for slot, box in zip(stack.cell_ids, (lower, upper))]
    instance = type(instance).model_validate(instance.model_dump())
    deck = check(instance, plan)
    assert all(part.ran for part in deck.parts.values())
    assert any(v.rule_id == 'iacs.member_irs.container_securing' and v.severity == 'hard'
               for v in deck.parts['lashing'].violations)
    record_property('deck_stack_id', stack.id)
    record_property('lashing_hard_count', sum(v.severity == 'hard' for v in deck.parts['lashing'].violations))
    assert not deck.passed


def _reference_prism():
    """An exact solid supplied as sections, with no output-fitted parameters."""
    import math
    import numpy as np
    from stowage.vessel.hydrostatics import HullGeometry

    solid = object.__new__(HullGeometry)
    solid.lpp_m, solid.beam_m, solid.depth_m = 100., 12., 8.
    solid.design_draught_m, solid.density_kg_m3 = 4., 1025.
    solid.max_z_m, solid.openings = 8., []
    solid.x_m = np.array([50.-50./math.sqrt(3), 50.+50./math.sqrt(3)])
    solid.integration_weights_m = np.array([50., 50.])
    section = np.array([[-6., 0.], [6., 0.], [6., 8.], [-6., 8.]])
    solid.sections = [[section.copy()], [section.copy()]]
    solid._finish_geometry()
    return solid


def test_full_report_strength_matches_independent_prism_beam(monkeypatch):
    """Uniform buoyancy plus central weight has a closed-form force/moment.

    This verifies SI readouts and rejection through the real full aggregator.
    Only geometry input lookup is replaced; no calculation or verdict is stubbed.
    Other parts retain their own evidence requirements; no overall pass is claimed.
    """
    from stowage.checker import strength, visibility
    from stowage.model import FixedLoad, FixedLoadSilhouette, Station, VesselFieldProvenance

    instance = build_instance()
    instance.load_list = []
    solid = _reference_prism()
    mass = 2_460_000.  # Independent input: half of rho*L*B*T = 4,920,000 kg.
    # APPROXIMATION: explicit test-only securing/loading manual declarations,
    # not evidence inferred from the placeholder vessel or a checker readout.
    # The fixed 3.3 m design cap and signed trim interval are fixture inputs;
    # D56 still rejects evaluated GM above the declared cap without tolerance.
    design_gm_evidence = VesselFieldProvenance(assumption=(
        'APPROXIMATION: independent synthetic prism securing design maximum GM '
        'is explicitly declared as 3.3 m; not an approved vessel manual'))
    trim_lower_evidence = VesselFieldProvenance(assumption=(
        'APPROXIMATION: independent synthetic prism loading trim minimum is -1 m'))
    trim_upper_evidence = VesselFieldProvenance(assumption=(
        'APPROXIMATION: independent synthetic prism loading trim maximum is +1 m'))
    instance.vessel.hull = instance.vessel.hull.model_copy(update=dict(
        lpp_m=100., beam_m=12., depth_m=8.,
        max_gm_lashing_m=3.3, max_gm_lashing_m_provenance=design_gm_evidence,
        min_trim_m=-1., max_trim_m=1.,
        min_trim_m_provenance=trim_lower_evidence,
        max_trim_m_provenance=trim_upper_evidence,
        lightship_mass_kg=mass, lightship_lcg_m=50., lightship_tcg_m=0.,
        lightship_vcg_m=2., lightship_distribution=[(0., 100., mass)],
        hull_offsets_ref='params/fleet/full_reference.toml',
        stations=[Station(x_m=x, min_shear_n=-20_000_000., max_shear_n=20_000_000.,
                          min_bending_nm=-200_000_000., max_bending_nm=200_000_000.)
                  for x in (0., 25., 50., 75., 100.)]))
    instance.vessel.tanks = []
    instance.vessel.fixed_loads = [FixedLoad(
        x_m=50., y_m=0., z_m=2., mass_kg=mass, kind='non_cargo',
        silhouette=FixedLoadSilhouette(x_min_m=49., x_max_m=51.,
                                      y_min_m=-1., y_max_m=1., z_min_m=1., z_max_m=3.))]
    instance.onboard = ShipState(vessel_id=instance.vessel.id)
    instance.port.water_density_kg_m3 = 1025.
    instance.vessel.voyage_policy = build_voyage_policy(
        instance.vessel.tanks, parameter_source='independent no-tank prism declaration',
        vessel_id=instance.vessel.id)
    _declare_scalar_probe_voyage(instance)
    monkeypatch.setattr(strength, '_geometry', lambda name: ({}, solid))
    monkeypatch.setattr(visibility, '_geometry', lambda name: (solid, {}))
    plan = MovePlan(id='independent-prism', instance_id=instance.id, call_index=0)
    report = check(instance, plan)
    part = report.parts['strength']
    assert part.ran, part.error
    # Integrating q=M*g/L gives SF=q*x before the central point and
    # BM=-q*x^2/2. Midship has SF +/-M*g/2 and BM=-M*g*L/8.
    expected_shear = mass*9.80665/2
    expected_bending = -mass*9.80665*100./8
    assert report.parts['lashing'].ran, report.parts['lashing'].error
    assert report.parts['lashing'].metrics['max_gm_lashing_m'] == 3.3
    # KB=T/2=2, BM=B^2/(12*T)=3, and the independent input KG is 2.
    assert report.parts['lashing'].metrics['gm_m'] == pytest.approx(3., abs=1e-9)
    assert part.metrics['displacement_kg'] == pytest.approx(4_920_000.)
    assert part.metrics['draught_aft_m'] == pytest.approx(4., abs=1e-7)
    assert part.metrics['draught_fwd_m'] == pytest.approx(4., abs=1e-7)
    assert part.metrics['sf_n@25'] == pytest.approx(expected_shear/2, abs=.01)
    assert part.metrics['bm_nm@50'] == pytest.approx(expected_bending, abs=.01)
    assert part.metrics['max_sf_ratio'] == pytest.approx(expected_shear/20_000_000., abs=1e-9)
    assert part.metrics['max_bm_ratio'] == pytest.approx(-expected_bending/200_000_000., abs=1e-9)
    assert any(v.rule_id == 'iacs.s11a.longitudinal_strength' for v in report.violations)
    assert not report.passed


def test_accelerated_prism_interpolation_matches_independent_closed_form(tmp_path):
    """Bounded real table interpolation, independent of shared fleet caches.

    This 64-node numerical regression does not certify KCS/casualty conditions
    or bypass the production table's required qualification certificate.
    """
    import math
    import numpy as np
    from stowage.checker.stability_tables import load_table_geometry

    solid = _reference_prism()
    grid = dict(immersion_m=np.array([3.8, 3.9, 4.1, 4.2]),
                trim_m=np.array([-.03, -.01, .01, .03]),
                heel_rad=np.array([0., .005, .01, .015]))
    table = load_table_geometry(solid, {}, 1025., build=True, grid=grid, cache_dir=tmp_path)
    assert table is not None
    # Below deck-edge immersion, the wet depth is d+s*y with s=tan(phi).
    # Integrating columns across [-B/2,B/2] gives these elementary moments.
    for d, phi in ((3.95, .003), (4., .007), (4.05, .012)):
        result = table.submerged(d, d, phi)
        expected_y = math.tan(phi)*12.**2/(12*d)
        expected_z = d/2+math.tan(phi)**2*12.**2/(24*d)
        assert result.volume_m3 == pytest.approx(100.*12.*d, abs=2e-5)
        assert result.centroid_m == pytest.approx((50., expected_y, expected_z), abs=2e-7)

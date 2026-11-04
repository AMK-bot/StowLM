"""Operational boundary on synthetic cargo with real fleet hull geometry.

APPROXIMATION: broad test-only strength envelopes and a declared synthetic
lightship condition isolate solver integration from loading-manual calibration.
This is a synthetic test, not a gold plan.
"""
import pytest

from stowage.checker import check
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.model import (BallastCondition, Provenance, ShipState, Station,
                           VoyageConditionIdentity, VoyageSpec)
from stowage.model.fixtures import build_instance
from stowage.solver.exact import ExactPlanner
from stowage.vessel.fleet import load_ship_params, load_vessel


@pytest.mark.slow
def test_real_hull_checked_candidate_never_bypasses_mask():
    instance = build_instance()
    params = load_ship_params('feeder_500')
    # Preserve the builder's complete D40 policy/provenance and tank geometry.
    vessel = load_vessel('feeder_500')
    length = params['hull']['lpp_m']
    # APPROXIMATION: synthetic test limits, never class-approved envelopes.
    envelope_left = min(s.x_m for s in vessel.hull.stations)
    envelope_right = max(s.x_m for s in vessel.hull.stations)
    geometry = StabilityGeometry(params)
    # IACS S11A2.2.1, data/raw/iacs/ur-s11a.pdf, PDF p.8: "calculated at each
    # section along the ship length". Include the KCS overhangs outside AP/FP.
    vessel.hull.stations = [
        Station(x_m=x, max_shear_n=1e10, min_shear_n=-1e10,
                max_bending_nm=1e12, min_bending_nm=-1e12)
        for x in (envelope_left, length / 2., envelope_right)]
    equilibrium = geometry.full_submerged(5.5, 5.5)
    # APPROXIMATION: this integration fixture has a synthetic lightship at the
    # independently integrated 5.5 m equilibrium, not unrecorded cargo ballast.
    # Keeping arbitrary cargo point loads would bypass D42 securing evidence.
    vessel.hull.lightship_mass_kg = equilibrium.displacement_kg
    vessel.hull.lightship_lcg_m = equilibrium.lcb_m
    # APPROXIMATION: declared synthetic lightship KG=5.5 m keeps this loading
    # condition inside D56's production lashing GM domain. The manual's design
    # GM and securing/strength limits remain unchanged and checked in full.
    vessel.hull.lightship_vcg_m = 5.5
    forward_mass = equilibrium.displacement_kg * (equilibrium.lcb_m - length / 4) / (length / 2)
    vessel.hull.lightship_distribution = [
        (0., length / 2, equilibrium.displacement_kg - forward_mass),
        (length / 2, length, forward_mass)]
    vessel.fixed_loads = []
    stack = min((s for s in vessel.stacks if s.on_deck),
                key=lambda s: (abs(next(c.lcg_m for c in vessel.cells
                                       if c.id == s.cell_ids[0]) - length / 2.), s.id))
    stack = stack.model_copy(update={'cell_ids': stack.cell_ids[:1], 'hatch_cover_id': None})
    vessel.stacks = [stack]
    vessel.cells = [c for c in vessel.cells if c.id in stack.cell_ids]
    vessel.hatch_covers = []
    box = instance.containers[1].model_copy(update={
        'dg': None, 'special_stowage': [], 'oog': None, 'gross_mass_kg': 5000.})
    instance.vessel = vessel
    instance.containers = [box]
    instance.load_list = [box.id]
    instance.discharge_list = []
    instance.onboard = ShipState(vessel_id=vessel.id)
    instance.yard.stacks = [instance.yard.stacks[0].model_copy(update={
        'key': 'A-001-01', 'length_ft': 20, 'boxes': [box.id]})]
    instance.port.max_draught_m = vessel.hull.max_draught_m
    instance.rule_overrides = {}
    # DECLARATION: this synthetic leg uses seawater at both calls. All actual
    # consumable tanks start empty, so real draw-down keeps every D57 sample empty.
    evidence = Provenance(created_by='test_exact_integration.synthetic_voyage',
        parents=['DECLARATION: synthetic next-call environment and seawater density'])
    instance.voyage = VoyageSpec(**{
        phase: VoyageConditionIdentity(
            id=f'exact-integration.{phase}', phase=phase, call_index=index,
            locode=instance.rotation.calls[index].locode,
            environment_id=f'synthetic-exact.{phase}', density_id='synthetic-seawater-1025',
            water_density_kg_m3=instance.port.water_density_kg_m3,
            provenance=evidence)
        for index, phase in enumerate(('departure', 'arrival'))}, provenance=evidence)
    candidate = get_action_space().expand(instance, [Place(box.id, stack.cell_ids[0])])
    candidate.ballast = BallastCondition(source='planner', tank_masses_kg={})
    report = check(instance, candidate)
    assert report.passed, [(v.rule_id, v.message) for v in report.violations]
    condition_ids = (instance.voyage.departure.id,
                     f'{instance.voyage.departure.id}:mid_voyage',
                     instance.voyage.arrival.id)
    expected_conditions = {
        f'condition:{identity}:{name}' for identity in condition_ids
        for name in ('stability', 'strength', 'visibility', 'lashing', 'constraints')}
    assert {name for name in report.parts if name.startswith('condition:')} == expected_conditions
    assert all(report.parts[name].ran for name in expected_conditions)
    result = ExactPlanner(num_workers=1).plan(instance, time_limit_s=15.)
    if result.status == 'ok':
        assert result.plan.ballast.source == 'planner'
        assert result.metrics['optimality_status'] == 'OPTIMAL'
        assert result.metrics['objective'] == 0.
        assert check(instance, result.plan).passed
        assert result.metrics["configured_engine_replay_verified"]
        assert result.metrics["checker_passed"]
        assert result.metrics["mask_verified"]
        assert result.metrics["shippable"]
        assert result.metrics["g1_status"] == "verified"
        from stowage.feasibility import make_feasibility_engine
        assert result.metrics["feasibility_config"]["extra"] == {}
        engine = make_feasibility_engine(instance, get_action_space())
        for action in get_action_space().from_plan(instance, result.plan):
            engine.push(action)
        assert engine.is_complete()
    else:
        # The conservative shared model may reject even this exact-checked
        # candidate. It must never be emitted while the mask forbids its action.
        if result.metrics.get("model_unavailable"):
            assert result.status == "error"
            assert result.metrics["optimality_status"] == "TIME_LIMIT"
            assert result.metrics["model_unavailable_reason"]
        else:
            assert result.status in ("timeout", "infeasible"), (result.message, result.metrics)
        assert result.metrics["optimality_scope"] == "engine_model_weighted_lifts"
        assert result.plan is None

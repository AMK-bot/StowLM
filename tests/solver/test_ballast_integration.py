"""Ballast contracts against real fleet equilibrium and synthetic legal stows.

The model fixtures deliberately omit numerical hull offsets. For physics tests we
retain their planning context and use fleet geometry, tanks and operating limits.
APPROXIMATION: test-only SF/BM permissibles isolate ballast from class-envelope
calibration; these are synthetic test loads, never operational certificates.
"""
from __future__ import annotations

import time

import pytest

from stowage.checker.lashing import check_lashing
from stowage.checker.stability import check_stability
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.strength import check_strength
from stowage.checker.visibility import check_visibility
from stowage.model import BallastCondition, MovePlan, ShipState, Station
from stowage.model.vessel import FixedLoad, FixedLoadSilhouette
from stowage.model.voyage import VoyageSpec, derive_arrival_liquid_state
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load, HatchCoverOff, HatchCoverOn
from stowage.moves import apply_plan
from stowage.solver.ballast import finish_ballast
from stowage.vessel.fleet import load_ship_params, load_vessel


def _fleet_context(name):
    instance = build_instance()
    vessel = load_vessel(name).model_copy(deep=True)
    # Retain the builder's signed loading limits, displacement policy and
    # evidence instead of copying scalar limits onto a stale fixture hull.
    aft, fore = vessel.hull.stations[0].x_m, vessel.hull.stations[-1].x_m
    # APPROXIMATION: broad test-only strength envelopes, not class approval.
    # Cover the actual buoyant beam, including retained AP/FP overhangs.
    vessel.hull.stations = [Station(x_m=x, max_shear_n=1e10, min_shear_n=-1e10,
                                   max_bending_nm=1e12, min_bending_nm=-1e12)
                            for x in (aft, (aft+fore)/2, fore)]
    instance.vessel = vessel
    instance.onboard = ShipState(vessel_id=vessel.id)
    instance.port.max_draught_m = vessel.hull.max_draught_m
    instance.voyage = VoyageSpec.model_validate(dict(
        departure=dict(id='ballast-departure', phase='departure', call_index=instance.call_index,
                       locode=instance.port.locode, environment_id='synthetic-departure-water',
                       density_id='synthetic-seawater', water_density_kg_m3=1025.),
        arrival=dict(id='ballast-arrival', phase='arrival', call_index=instance.call_index+1,
                     locode=instance.rotation.calls[instance.call_index+1].locode,
                     environment_id='synthetic-arrival-water', density_id='synthetic-seawater',
                     water_density_kg_m3=1025.),
    ))
    _balance_fixed_load(instance, draught=5.5 if name == 'feeder_500' else None,
                        # Keep the synthetic initial condition inside the
                        # unchanged D56 design-GM envelope, with room for the
                        # trim-correction test's added low ballast. At 5.3 m
                        # the declared envelope remains below 5.5 m waterline.
                        cargo_z=5.3 if name == 'feeder_500' else 7.)
    return instance


def _balance_fixed_load(instance, *, draught=None, cargo_z=4.5):
    name = instance.vessel.slice_of or instance.vessel.id
    geometry = StabilityGeometry(load_ship_params(name))
    # Leave a declared 5 cm test loading reserve below the summer boundary.
    # Exact-boundary mass can yield a positive numerical draft residual; no
    # checker tolerance or operational limit is relaxed for this fixture.
    depth = geometry.design_draught_m-.05 if draught is None else draught
    condition = geometry.full_submerged(depth, depth)
    hull = instance.vessel.hull
    mass = condition.displacement_kg - hull.lightship_mass_kg
    x = (condition.displacement_kg * condition.lcb_m
         - hull.lightship_mass_kg * hull.lightship_lcg_m) / mass
    # APPROXIMATION: a test-only submerged non-cargo balancing mass isolates
    # ballast physics from booking placement; it is not background cargo or a
    # production loading condition. Its explicit envelope enters visibility
    # and windage instead of treating unknown geometry as invisible.
    instance.vessel.fixed_loads = [FixedLoad(
        x_m=x, y_m=0., z_m=cargo_z, mass_kg=mass,
        kind='non_cargo', silhouette=FixedLoadSilhouette(
            x_min_m=x-1., x_max_m=x+1., y_min_m=-1., y_max_m=1.,
            z_min_m=cargo_z-.1, z_max_m=cargo_z+.1),
    )]
    instance.vessel.sources.append(
        'ASSUMPTION: ballast integration fixture balancing mass is synthetic '
        'non-cargo with an explicit submerged envelope; not a vessel loading plan.')


def _plan(instance, **kwargs):
    return MovePlan(id='ballast-test', instance_id=instance.id,
                    call_index=instance.call_index, **kwargs)


def _assert_physics(instance, outcome):
    assert outcome.status == 'ok', outcome.model_dump(exclude={'provenance', 'plan'})
    assert outcome.plan.ballast.source == 'finisher'
    replay = apply_plan(instance, outcome.plan)
    assert replay.legal, replay.violations
    ship = replay.final_state.ship.model_copy(deep=True)
    ship.tank_masses_kg.update(outcome.plan.ballast.tank_masses_kg)
    assert set(outcome.parts) == {'stability', 'strength', 'visibility', 'lashing',
                                  'arrival/stability', 'arrival/strength'}
    for checker in (check_stability, check_strength, check_visibility, check_lashing):
        part = checker(instance, ship, call_index=instance.call_index)
        assert part.ran, part.error
        assert not [v for v in part.violations if v.severity == 'hard'], part.violations
    liquid = derive_arrival_liquid_state(ship, instance.voyage, vessel=instance.vessel)
    arrival = ship.model_copy(update={'tank_masses_kg': liquid.tank_masses_kg})
    arrival_instance = instance.model_copy(update={'port': instance.port.model_copy(update={
        'locode': instance.voyage.arrival.locode,
        'water_density_kg_m3': instance.voyage.arrival.water_density_kg_m3,
        'max_draught_m': None, 'port_rules': []}), 'call_index': instance.voyage.arrival.call_index})
    for checker in (check_stability, check_strength):
        part = checker(arrival_instance, arrival, call_index=instance.voyage.arrival.call_index)
        assert part.ran, part.error
        assert not [v for v in part.violations if v.severity == 'hard'], part.violations
    for tank in instance.vessel.tanks:
        if tank.adjustable:
            mass = outcome.plan.ballast.tank_masses_kg.get(tank.id, 0.)
            assert 0 <= mass <= tank.capacity_m3 * tank.content_density_kg_m3


def test_existing_planner_ballast_is_kept_unchanged():
    instance = build_instance()
    plan = _plan(instance, ballast=BallastCondition(tank_masses_kg={'WB-P': 123.}, source='planner'))
    outcome = finish_ballast(instance, plan, time_limit_s=0.)
    assert outcome.status == 'kept'
    assert outcome.plan == plan
    assert outcome.proof is None


def test_illegal_moves_raise_instead_of_reporting_infeasible():
    instance = build_instance()
    box = instance.containers[0]
    plan = _plan(instance, moves=[Load(call_index=0, container_id=box.id,
                                     src='A-001-01-02', dst='0020182')])
    with pytest.raises(ValueError):
        finish_ballast(instance, plan)


def test_zero_budget_never_claims_infeasibility():
    instance = build_instance()
    outcome = finish_ballast(instance, _plan(instance), time_limit_s=0.)
    assert outcome.status == 'not_found'
    assert outcome.plan is None and outcome.proof is None
    assert outcome.wall_time_s < .5


def test_missing_fixture_physics_does_not_fabricate_a_solution():
    instance = build_instance()
    outcome = finish_ballast(instance, _plan(instance), time_limit_s=1.)
    assert outcome.status == 'not_found'
    assert outcome.plan is None and outcome.proof is None


@pytest.mark.slow
@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
def test_fleet_legal_stow_exact_parts_and_call_timing(name, record_property):
    instance = _fleet_context(name)
    started = time.monotonic()
    outcome = finish_ballast(instance, _plan(instance), time_limit_s=30., seed=42)
    elapsed = time.monotonic() - started
    record_property('ship', name)
    record_property('ballast_seconds', elapsed)
    _assert_physics(instance, outcome)
    # One atomic exact/linear operation may finish just beyond the deadline.
    assert elapsed < 35.


@pytest.mark.slow
def test_random_yard_tiny_legal_stow_and_determinism():
    from stowage.generate import random_yard
    # Generate from the canonical fleet parent: injecting this test's already
    # loaded balancing mass before D58 construction would leave no capacity
    # for the generator's independently balanced cargo background.
    report = random_yard.RandomGenerationReport()
    instance = random_yard.generate_random_instance('tiny', seed=12, blocking_ratio=0.,
                                                   report=report)
    assert report.accepted and report.background_checks >= 1
    assert report.background_last['accepted']
    base = _fleet_context('feeder_500')
    # Apply the pre-existing synthetic strength fixture only after the real
    # generator and its unchanged empty-background acceptance gate succeed.
    instance.vessel.hull.stations = [s.model_copy(deep=True)
                                    for s in base.vessel.hull.stations]
    # The generator owns booking/context selection; this integration fixture
    # explicitly supplies the same synthetic voyage-water assumptions as base.
    instance.voyage = base.voyage.model_copy(deep=True)
    instance.voyage.departure.locode = instance.port.locode
    instance.voyage.arrival.locode = instance.rotation.calls[instance.call_index+1].locode
    _balance_fixed_load(instance, draught=5.5)
    # Start fixed consumables empty so the balanced synthetic background is exact.
    instance.onboard.tank_masses_kg = {}
    moves = [HatchCoverOff(call_index=0, cover_id=h.id) for h in instance.vessel.hatch_covers]
    hold = sorted((c for c in instance.vessel.cells if int(c.id[-2:]) < 80), key=lambda c: c.id)
    deck = sorted((c for c in instance.vessel.cells if int(c.id[-2:]) >= 80), key=lambda c: c.id)
    stacks = {s.boxes[0]: s.key for s in instance.yard.stacks}
    boxes = iter(sorted(instance.load_list))
    # D36 bookings occupy only part of a decision region. Stop at the actual
    # booked count instead of assuming every cell has a corresponding box.
    for cell, cid in zip(hold, boxes):
        moves.append(Load(call_index=0, container_id=cid, src=stacks[cid]+'-01', dst=cell.id))
    moves += [HatchCoverOn(call_index=0, cover_id=h.id) for h in instance.vessel.hatch_covers]
    for cell, cid in zip(deck, boxes):
        moves.append(Load(call_index=0, container_id=cid, src=stacks[cid]+'-01', dst=cell.id))
    assert next(boxes, None) is None, 'fixture must load every booked box'
    plan = _plan(instance, moves=moves)
    assert apply_plan(instance, plan).legal
    first = finish_ballast(instance, plan, seed=7)
    _assert_physics(instance, first)
    second = finish_ballast(instance, plan, seed=7)
    assert second.status == first.status
    assert second.plan.ballast == first.plan.ballast
    assert plan.ballast is None


@pytest.mark.slow
def test_ballast_corrects_exact_trim_for_a_legal_stow():
    instance = _fleet_context('feeder_500')
    # D40 trim bounds are signed. A forward mass shift produces negative
    # trim, so this test tightens both sides through the supported override
    # contract. Canonical fleet hull declarations retain their source identity.
    minimum_trim, maximum_trim = -.05, .05
    instance.rule_overrides['iscode.loading.trim'] = {
        'minimum': minimum_trim, 'maximum': maximum_trim}
    instance.vessel.sources.append(
        'APPROXIMATION: synthetic integration-test signed trim window [-0.05, 0.05] m '
        'isolates ballast correction; not a vessel loading-manual limit.')
    load = instance.vessel.fixed_loads[0]
    instance.vessel.fixed_loads = [load.model_copy(update={'x_m': load.x_m + .5})]
    initial = check_stability(instance, instance.onboard, call_index=0)
    assert initial.ran, initial.error
    assert initial.metrics['trim_m'] < minimum_trim
    assert any(v.rule_id == 'iscode.loading.trim' for v in initial.violations)
    outcome = finish_ballast(instance, _plan(instance), time_limit_s=30., seed=19)
    _assert_physics(instance, outcome)
    assert sum(outcome.plan.ballast.tank_masses_kg.values()) > 0.

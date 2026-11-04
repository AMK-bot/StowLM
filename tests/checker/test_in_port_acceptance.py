"""D64 integration acceptance; numerical checks use the complete public replay.

T4 calls the production berth initializer and requires an admitted slow run.
An empty-tank diagnostic is not the full-size empty-ship acceptance test.
"""
from __future__ import annotations

from collections import defaultdict, deque
from importlib import import_module
from importlib.util import find_spec
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from stowage.checker import full
from stowage.checker.stability_states import InPortStateBuilder
from stowage.conventions import CONTAINER_HEIGHT_STD_M, make_container_id
from stowage.generate.random_voyage import build_voyage_spec
from stowage.model import (BallastCondition, HatchCoverOff, HatchCoverOn,
                           Instance, Load, MovePlan)
from stowage.model.fixtures import build_instance
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import ShipState
from stowage.model.yard import YardState
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_manual import (STANDARD_ROUTE, manual_port,
                                         standard_manual_conditions)


FLEET = ("feeder_500", "feeder_800", "feeder_1700")
ROOT = Path(__file__).resolve().parents[2]


def _alternating_slots(vessel, cargo_cells, *, one_side_first=False):
    """Bottom-up, centre-out longitudinally, far-to-near within each side.

    APPROXIMATION: this is one total lift order for concurrent cranes (D64 A5).
    Alternation acts between the port/starboard queues at each tier; each queue
    visits its outer rows first, with longitudinal ties nearest the LCB first.
    No row-number convention is substituted for the actual transverse geometry.
    """
    cells = {cell.id: cell for cell in vessel.cells}
    stacks = {stack.id: stack for stack in vessel.stacks}
    if one_side_first:
        return sorted((slot for slot, _mass in cargo_cells), key=lambda slot: (
            stacks[cells[slot].stack_id].on_deck,
            cells[slot].tcg_m > 0,
            stacks[cells[slot].stack_id].cell_ids.index(slot),
            -abs(cells[slot].tcg_m),
            abs(cells[slot].lcg_m-.485*vessel.hull.lpp_m), slot))
    grouped = defaultdict(list)
    for slot, _mass in cargo_cells:
        cell = cells[slot]
        stack = stacks[cell.stack_id]
        grouped[(stack.on_deck, stack.cell_ids.index(slot))].append(slot)
    result = []
    for key in sorted(grouped):
        queues = []
        for side in (-1, 1):
            queues.append(deque(sorted(
                (slot for slot in grouped[key]
                 if (cells[slot].tcg_m < 0) == (side < 0)),
                key=lambda slot: (-abs(cells[slot].tcg_m),
                    abs(cells[slot].lcg_m - .485*vessel.hull.lpp_m),
                    cells[slot].lcg_m, slot))))
        while any(queues):
            for queue in queues:
                if queue:
                    result.append(queue.popleft())
    return result


def _production_empty_instance(vessel, arrival_masses):
    """Use production port/route/voyage models with the fixed D27 manual route.

    ASSUMPTION: assemble the required deterministic C1 diagnostic from public
    production builders; stochastic generation would replace its prescribed
    cargo. This does not claim random/simulated generator acceptance. The whole
    current operational profile, including its yard and crane H, is unchanged.
    """
    port, next_port = manual_port(STANDARD_ROUTE[0]), manual_port(STANDARD_ROUTE[1])
    rotation = Rotation(service=f"D64.C1.{vessel.id}", calls=[
        PortCall(locode=locode, index=index, archetype=(
            port.archetype if index == 0 else next_port.archetype if index == 1 else None))
        for index, locode in enumerate(STANDARD_ROUTE)])
    return Instance(id=f"D64.T4.{vessel.id}", stage="A", size_class="full", seed=0,
        family={"vessel": vessel.id, "port": port.archetype, "generator": "D27.C1.acceptance"},
        vessel=vessel, port=port, rotation=rotation, call_index=0,
        containers=[], load_list=[], yard=YardState(yard_id=port.yard.id),
        onboard=ShipState(vessel_id=vessel.id, tank_masses_kg=dict(arrival_masses)),
        voyage=build_voyage_spec(rotation, 0, port, seed=0))


def manual_empty_ship_plan(name, arrival_masses, *, one_side_first=False):
    """Exact C1 cargo on the complete vessel and unchanged production profile."""
    vessel = load_vessel(name)
    recipe = next(row for row in standard_manual_conditions(vessel, load_ship_params(name))
                  if row["condition"] == "C1")
    base = _production_empty_instance(vessel, arrival_masses)
    payload = base.model_dump(mode="python")
    ground = iter((block.id, bay, row) for block in base.port.yard.blocks
                  for bay in range(1, block.n_bays+1) for row in range(1, block.n_rows+1))
    capacity = sum(block.n_bays*block.n_rows for block in base.port.yard.blocks)
    assert recipe["boxes"] <= capacity, "C1 cargo exceeds unchanged production yard ground capacity"
    containers, identifiers, sources, yard_stacks = [], {}, {}, []
    for serial, (slot, mass) in enumerate(recipe["cargo_cells"], 1):
        identifier = make_container_id("QZXU", serial)
        identifiers[slot] = identifier
        containers.append(dict(id=identifier, iso_type="22G1", length_ft=20,
            height_m=CONTAINER_HEIGHT_STD_M, tare_kg=2200., gross_mass_kg=mass,
            max_gross_mass_kg=30480., pol="THLCH", pod="THBKK"))
        block_id, bay, row = next(ground)
        key = f"{block_id}-{bay:03d}-{row:02d}"
        sources[identifier] = f"{key}-01"
        yard_stacks.append(dict(key=key, length_ft=20, boxes=[identifier]))
    payload.update(id=f"D64.T4.{name}", size_class="full", vessel=vessel,
        containers=containers, load_list=list(identifiers.values()), discharge_list=[],
        onboard=dict(vessel_id=vessel.id, placements=[], tank_masses_kg=arrival_masses))
    payload["yard"]["stacks"] = yard_stacks
    instance = Instance.model_validate(payload)
    slots = _alternating_slots(vessel, recipe["cargo_cells"], one_side_first=one_side_first)
    cells = {cell.id: cell for cell in vessel.cells}
    stacks = {stack.id: stack for stack in vessel.stacks}
    covers = sorted({stacks[cells[slot].stack_id].hatch_cover_id for slot in slots
                     if not stacks[cells[slot].stack_id].on_deck})
    moves = [HatchCoverOff(call_index=0, cover_id=cover) for cover in covers]
    closed = False
    for slot in slots:
        if stacks[cells[slot].stack_id].on_deck and not closed:
            moves.extend(HatchCoverOn(call_index=0, cover_id=cover) for cover in covers)
            closed = True
        identifier = identifiers[slot]
        moves.append(Load(call_index=0, container_id=identifier,
                          src=sources[identifier], dst=slot))
    if not closed:
        moves.extend(HatchCoverOn(call_index=0, cover_id=cover) for cover in covers)
    departure_masses = {tank.id: recipe["departure_tank_masses_kg"][tank.id]
                        for tank in vessel.tanks if tank.adjustable}
    for pair in vessel.tank_pairs:
        # D64 fixes the pair total to the generator's arrival pre-fill; the
        # historical C1 cargo is unchanged and the replay chooses each split.
        for tid in (pair.port_tank, pair.starboard_tank):
            departure_masses[tid] = arrival_masses.get(tid, 0.)
    plan = MovePlan(id=f"{instance.id}.{'one-side' if one_side_first else 'alternating'}",
        instance_id=instance.id, call_index=0, moves=moves,
        ballast=BallastCondition(tank_masses_kg=departure_masses, source="planner"))
    assert len([move for move in moves if move.kind == "load"]) == recipe["boxes"]
    return instance, plan


def _prepare_production_arrival(instance):
    """Required production hook; never replace it with a test ballast recipe."""
    module = "stowage.vessel.fleet_berth"
    if find_spec(module) is None:
        pytest.skip("WAITING on w2-bg-accept: production "
                    "stowage.vessel.fleet_berth.prepare_arrival "
                    "(berth trim + 50% HEEL) has not landed; T4 is NOT passing")
    owner = import_module(module)
    initialize = getattr(owner, "prepare_arrival", None)
    assert callable(initialize), "Generator berth module lacks the required initializer"
    # Generator semantics share the same activation switch as the checker.
    # The helper is called before each test's replay context is entered.
    with rule_freeze_v1(enabled=True):
        return initialize(instance)


def _generator_berth_arrival(name):
    with rule_freeze_v1(enabled=True):
        instance = _production_empty_instance(load_vessel(name), {})
        return _prepare_production_arrival(instance).onboard.tank_masses_kg


def _prepared_manual_plan(name, *, one_side_first=False):
    """Prepare and replay the same C1 production instance, retaining its audit."""
    with rule_freeze_v1(enabled=True):
        instance, plan = manual_empty_ship_plan(name, {}, one_side_first=one_side_first)
        instance = _prepare_production_arrival(instance)
        for pair in instance.vessel.tank_pairs:
            for tid in (pair.port_tank, pair.starboard_tank):
                plan.ballast.tank_masses_kg[tid] = instance.onboard.tank_masses_kg[tid]
        return instance, plan


def _record_t4_profile(record_property, instance):
    record_property("port_archetype", instance.port.archetype)
    record_property("port_max_draught_m", json.dumps(instance.port.max_draught_m))
    record_property("port_max_crane_height_tiers", instance.port.max_crane_height_tiers)
    record_property("port_profile_sources", json.dumps(instance.port.sources))


def _record_t4_replay(record_property, report, plan, *, label=""):
    record_property(f"{label}k", report.metrics.get("k"))
    record_property(f"{label}lifts", len(plan.moves))
    record_property(f"{label}state_count", report.metrics.get("state_count"))
    first_failure = None
    if report.violations:
        first = report.violations[0]
        state_index = report.metrics.get("failure.state_index")
        prefix = f"state:{int(state_index)}." if state_index is not None else ""
        first_failure = dict(violation=first.model_dump(mode="json"), state_index=state_index,
            k=report.metrics.get("k"), margins={key: value
                for key, value in report.margins.items() if key.startswith(prefix)})
    record_property(f"{label}first_failure", json.dumps(first_failure, sort_keys=True))


@pytest.mark.slow
@pytest.mark.parametrize("name", FLEET)
def test_t4_full_size_empty_ship_every_lift(name, record_property):
    instance, plan = _prepared_manual_plan(name)
    arrival_masses = instance.onboard.tank_masses_kg
    _record_t4_profile(record_property, instance)
    with rule_freeze_v1(enabled=True):
        tanks = {tank.id: tank for tank in instance.vessel.tanks}
        assert instance.vessel.tank_pairs, "T4 needs explicit model-owned anti-heeling pairs"
        for pair in instance.vessel.tank_pairs:
            for tid in (pair.port_tank, pair.starboard_tank):
                tank = tanks[tid]
                assert arrival_masses[tid] == pytest.approx(
                    .5*tank.capacity_m3*tank.content_density_kg_m3)
        # First certify generator s0 with the arrival FSM convention. This
        # zero-lift certificate keeps arrival fills: D74 must not interpret it
        # as the full departure programme with no cargo time to pump. The
        # complete plan below retains every original departure ballast fill.
        empty = MovePlan(id=f"{plan.id}.arrival", instance_id=instance.id,
            call_index=0, ballast=BallastCondition(source='planner',
                tank_masses_kg={tank.id:arrival_masses.get(tank.id,0.)
                    for tank in instance.vessel.tanks if tank.adjustable}))
        initial = full._in_port(instance, empty)
        _record_t4_replay(record_property, initial, empty, label="arrival.")
        assert initial.ran and not initial.violations, initial.model_dump()
        trim = [value for key, value in initial.metrics.items() if key.endswith(".trim_m")]
        assert trim, initial.metrics
        limit = instance.vessel.hull.lpp_m*math.tan(math.radians(1.))
        assert all(abs(value) <= .5*limit for value in trim), (trim, limit)
        telemetry = {}
        result = full._in_port(instance, plan, telemetry=telemetry)
        _record_t4_replay(record_property, result, plan)
        for key in ("t_hat_s", "t_hat_state_count"):
            record_property(key, telemetry.get(key))
        assert result.metrics["state_count"] == len(plan.moves)+1
        assert result.ran and not result.error and not result.violations, result.model_dump()


@pytest.mark.slow
@pytest.mark.parametrize("name", FLEET)
def test_t4_one_side_first_list_failure_requires_exhausted_heel(name, record_property):
    instance, plan = _prepared_manual_plan(name, one_side_first=True)
    _record_t4_profile(record_property, instance)
    with rule_freeze_v1(enabled=True):
        result = full._in_port(instance, plan)
        _record_t4_replay(record_property, result, plan)
        assert result.ran and result.error is None, result.model_dump()
        assert result.metrics["state_count"] == len(plan.moves)+1, result.model_dump()
        failed = [(key, value) for key, value in result.margins.items()
                  if key.endswith(".list_rad") and value < 0]
        assert failed, "One-side-first C1 series did not exercise P3 beyond HEEL authority"
        first_key, first_margin = failed[0]
        prefix = first_key.removesuffix(".list_rad")
        record_property("first_negative_list", json.dumps(dict(
            state=prefix, margin_rad=first_margin, k=result.metrics["k"],
            heel_authority_kgm=result.metrics[f"{prefix}.heel_authority_kgm"],
            heel_authority_remaining_kgm=result.metrics[f"{prefix}.heel_authority_remaining_kgm"],
            heel_residual_moment_kgm=result.metrics[f"{prefix}.heel_residual_moment_kgm"]), sort_keys=True))
        for key, _margin in failed:
            prefix = key.removesuffix(".list_rad")
            assert result.metrics[f"{prefix}.heel_authority_kgm"] > 0
            assert result.metrics[f"{prefix}.heel_authority_remaining_kgm"] == pytest.approx(0., abs=1e-6)
            assert abs(result.metrics[f"{prefix}.heel_residual_moment_kgm"]) > 0


@pytest.mark.slow
@pytest.mark.parametrize("name", FLEET)
def test_t4_mask_never_rejects_checker_pass_and_defers_light_displacement(name, record_property):
    from stowage.checker.in_port_evaluator import InPortEvaluator
    from stowage.checker.in_port_liquids import prepare_context
    from stowage.feasibility.in_port import in_port_screen_enabled, screen_in_port_prefix
    from stowage.linear import build_linear_model
    instance, plan = _prepared_manual_plan(name)
    _record_t4_profile(record_property, instance)
    with rule_freeze_v1(enabled=True):
        # A stale qualification is a failed dependency, never a passing gate-off
        # deferral. Establish it before evaluating hundreds of physical states.
        try:
            model = build_linear_model(instance)
            assert in_port_screen_enabled(), "D64 checker capability/cost-pruning gates are not enabled"
        except Exception as error:
            record_property("dependency_error", f"{type(error).__name__}: {error}")
            record_property("k", "not evaluated: mask qualification unavailable")
            record_property("first_failure", json.dumps(dict(
                stage="mask_qualification", error=f"{type(error).__name__}: {error}",
                k=None, state_index=None, margins={})))
            raise
        builder = InPortStateBuilder(instance)
        ships = [builder.state.ship]
        ships.extend(builder.push(move, move_index=index).ship
                     for index, move in enumerate(plan.moves))
        departure, problems = full._ballast(instance, plan, ships[-1])
        assert not problems
        context = prepare_context(instance, departure)
        transferred = [context.transferred_ship(ship, phase="arrival")[0] for ship in ships]
        with InPortEvaluator(instance, context, workers=1, warm_start=False) as evaluator:
            physical = evaluator.evaluate(transferred)
        complete = full._in_port(instance, plan)
        _record_t4_replay(record_property, complete, plan)
        low_states = 0
        for index, (ship, report) in enumerate(zip(transferred, physical.reports, strict=True)):
            screened = screen_in_port_prefix(instance, ship, model=model)
            if report.ran and not report.error and not report.violations:
                if screened.status == "reject":
                    record_property("first_failure", json.dumps(dict(
                        stage="mask_necessity", state_index=index, k=complete.metrics["k"],
                        margins=report.margins, reason=screened.reason), sort_keys=True))
                assert screened.status != "reject", (index, screened, report.model_dump())
            if report.metrics["displacement_kg"] < model.band.displacement_min_kg:
                low_states += 1
                if screened.status != "undecided":
                    record_property("first_failure", json.dumps(dict(
                        stage="mask_light_domain", state_index=index, k=complete.metrics["k"],
                        margins=report.margins, reason=screened.reason), sort_keys=True))
                assert screened.status == "undecided", (index, screened)
        assert low_states, "T4 must exercise initial states below the calibrated displacement"


def test_t4_waiting_status_is_explicit(record_property):
    """A fast status signal is separate from the opt-in full-size assertion."""
    pending = find_spec("stowage.vessel.fleet_berth") is None
    record_property("T4_status", "WAITING on generator; NOT passing" if pending else
                    "production initializer available; T4 requires an admitted full-size run")
    if pending:
        pytest.skip("WAITING on generator berth-trim + 50% HEEL initializer; T4 NOT passing")


def _assert_complete_replay_passes(report):
    assert report.ran, report.model_dump()
    assert report.error is None, report.model_dump()
    assert not report.violations, report.model_dump()


def test_replay_integration_entrypoint_exists():
    # Red-first: the old checker has no every-lift in-port replay entry point.
    assert callable(getattr(full, "_in_port", None))


@pytest.mark.parametrize("field", ["max_draught_m", "max_crane_height_tiers", "quay_crane", "yard", "sources"])
def test_t4_uses_unchanged_production_port_instead_of_fixture_limits(monkeypatch, field):
    """A tiny recipe isolates instance construction from expensive fleet physics."""
    from stowage.vessel.fleet_manual import manual_port
    from stowage.vessel.fleet_voyage import build_voyage_policy
    vessel = build_instance().vessel
    vessel.voyage_policy = build_voyage_policy(vessel.tanks,
        parameter_source="synthetic port-construction regression", vessel_id=vessel.id)
    recipe = dict(condition="C1", cargo_cells=[(vessel.cells[0].id, 14_000.)],
        boxes=1, departure_tank_masses_kg={tank.id: 0. for tank in vessel.tanks})
    monkeypatch.setitem(globals(), "load_vessel", lambda name: vessel)
    monkeypatch.setitem(globals(), "load_ship_params", lambda name: {})
    monkeypatch.setitem(globals(), "standard_manual_conditions", lambda *args: [recipe])
    with rule_freeze_v1(enabled=True):
        expected = manual_port("THLCH")
        instance, plan = manual_empty_ship_plan("feeder_500", {})
    assert getattr(instance.port, field) == getattr(expected, field)
    assert instance.port.yard.id == expected.yard.id
    assert all(move.src.startswith(expected.yard.blocks[0].id)
               for move in plan.moves if move.kind == "load")


def test_t7_mask_fsm_does_not_exceed_candidate_independent_pass_one(monkeypatch):
    """Rev 3.1 A.3: the screen cannot charge prospective worked tanks early.

    Exact synthetic rectangular solids isolate the FSM contract. Only the
    context's geometry lookup is supplied; no qualification gate or physics
    maximum is patched, and no linear model is accepted/requalified here.
    """
    from stowage.checker import stability
    from stowage.checker.in_port_liquids import prepare_context
    from stowage.checker.stability_loading import loading_condition
    from stowage.checker.stability_sounding import maximum_fsm, sounding
    from stowage.feasibility.in_port import _mandatory_fsm
    from stowage.model import TankPair
    from stowage.vessel.tanks import TankGeometry

    instance = build_instance()
    instance.vessel.tank_pairs = [TankPair(id="explicit-heel",
        port_tank="WB-P", starboard_tank="WB-S")]
    fuel = next(tank for tank in instance.vessel.tanks if tank.id == "FO-1")
    instance.vessel.tanks.append(fuel.model_copy(deep=True, update={
        "id": "candidate-center", "kind": "ballast", "adjustable": True,
        "heated": False, "content_density_kg_m3": 1025.}))
    params = {"tanks": []}
    maxima = {}
    for tank in instance.vessel.tanks:
        y = tank.table[0].tcg_m
        record = dict(id=tank.id, geometry=dict(x_min_m=10., x_max_m=20.,
            y_min_m=y-3., y_max_m=y+3., z_min_m=0., z_max_m=5.))
        params["tanks"].append(record)
        solid = TankGeometry.from_params(record)
        tank.capacity_m3 = solid.capacity_m3
        tank.table = [sounding(solid, volume) for volume in
                      (0., .5*solid.capacity_m3, solid.capacity_m3)]
        maxima[tank.id] = maximum_fsm(solid)[0]*tank.content_density_kg_m3
        instance.onboard.tank_masses_kg[tank.id] = (
            0. if tank.id == "candidate-center" else
            .5*solid.capacity_m3*tank.content_density_kg_m3)
    monkeypatch.setattr(stability, "_context", lambda _vessel: (None, params))
    departure = instance.onboard.model_copy(deep=True)
    departure.tank_masses_kg["candidate-center"] = 10_250.
    context = prepare_context(instance, departure)
    compulsory = {"WB-P", "WB-S", "FO-1"}
    assert context.max_fsm_tank_ids == compulsory
    assert context.for_phase("arrival").max_fsm_tank_ids == compulsory
    assert context.for_phase("departure").max_fsm_tank_ids == compulsory | {"candidate-center"}

    actual = loading_condition(instance, instance.onboard, ship_params=params,
        precomputed_soundings=True, small_tank_exemption=True)
    assert not actual.errors
    pass_one = context.for_phase("arrival").correct_loading(actual)
    assert pass_one.fsm_kgm == pytest.approx(sum(maxima[tid] for tid in compulsory))
    # Deliberately upward-enclosed model metadata exercises the screen's cap
    # at exact geometric maxima, rather than coincidentally matching its input.
    ids = tuple(tank.id for tank in instance.vessel.tanks)
    model = SimpleNamespace(tank_ids=ids, _reference_params=params,
        tank_fsm_max_kgm=[1.001*maxima[tid] for tid in ids])
    charge = _mandatory_fsm(model, instance)
    assert charge > 0., "The fixture must exercise a nonzero compulsory charge"
    assert charge == pytest.approx(sum(maxima[tid] for tid in compulsory))
    assert charge <= pass_one.fsm_kgm
    model.tank_fsm_max_kgm[ids.index("candidate-center")] *= 1000.
    assert _mandatory_fsm(model, instance) == charge
    unchanged = prepare_context(instance, instance.onboard)
    assert unchanged.max_fsm_tank_ids == context.max_fsm_tank_ids
    assert unchanged.correct_loading(actual).fsm_kgm == pass_one.fsm_kgm


@pytest.mark.slow
def test_t3_real_midbay_list_is_corrected_only_with_prefilled_heel():
    """Real single-bay port-side lifts distinguish transfer from empty authority.

    ASSUMPTION: this isolated T3 starts with 50% fore-peak ballast and 9.7%
    consumables, as in the rev-3 probe. It is not generator/T4 evidence.
    """
    from stowage.checker.in_port_evaluator import InPortEvaluator
    from stowage.checker.in_port_liquids import prepare_context
    with rule_freeze_v1(enabled=True):
        vessel = load_vessel("feeder_500")
        pair = vessel.tank_pairs[0]
        heel_ids = {pair.port_tank, pair.starboard_tank}
        arrival = {tank.id: ((.5 if tank.id in heel_ids or tank.id.startswith("FPT-")
                             else 0.) if tank.adjustable else .097)
                   *tank.capacity_m3*tank.content_density_kg_m3 for tank in vessel.tanks}
        instance, plan = manual_empty_ship_plan("feeder_500", arrival)
        cells = {cell.id: cell for cell in instance.vessel.cells}
        stacks = {stack.id: stack for stack in instance.vessel.stacks}
        candidates = [move for move in plan.moves if move.kind == "load"
            and cells[move.dst].tcg_m > 0]
        bay = min({int(move.dst[:3]) for move in candidates}, key=lambda bay: min(
            abs(cells[move.dst].lcg_m-.485*vessel.hull.lpp_m)
            for move in candidates if int(move.dst[:3]) == bay))
        builder = InPortStateBuilder(instance)
        for move in plan.moves:
            if move.kind == "hatch_cover_off":
                builder.push(move)
        states = []
        closed = False
        for move in candidates:
            if int(move.dst[:3]) == bay:
                if stacks[cells[move.dst].stack_id].on_deck and not closed:
                    for event in plan.moves:
                        if event.kind == "hatch_cover_on":
                            builder.push(event)
                    closed = True
                states.append(builder.push(move).ship)
        assert len(states) > 1
        context = prepare_context(instance, instance.onboard)
        corrected = [context.transferred_ship(ship)[0] for ship in states]
        with InPortEvaluator(instance, context) as evaluator:
            raw = evaluator.evaluate(states).reports
        with InPortEvaluator(instance, context) as evaluator:
            transferred = evaluator.evaluate(corrected).reports
        witnesses = [index for index, (before, after) in enumerate(zip(raw, transferred, strict=True))
            if before.ran and after.ran and before.margins["list_rad"] < 0
            and not after.error and not after.violations]
        assert witnesses, [(a.margins, b.margins, b.error)
                           for a, b in zip(raw, transferred, strict=True)]
        index = witnesses[0]
        empty = instance.model_copy(deep=True)
        for tid in heel_ids:
            empty.onboard.tank_masses_kg[tid] = 0.
        empty_context = prepare_context(empty, empty.onboard)
        without, metadata = empty_context.transferred_ship(states[index])
        assert metadata["heel_authority_kgm"] == metadata["heel_transfer_kg"] == 0.
        with InPortEvaluator(empty, empty_context) as evaluator:
            report = evaluator.evaluate([without]).reports[0]
        assert report.ran and report.margins["list_rad"] < 0, report.model_dump()
        assert any(v.rule_id == "port.stability.cargo_operations" and "list_rad" in v.message
                   for v in report.violations), report.model_dump()


def saved_pilot(run_id):
    """Reviewed small plans replayed on a freshly built production tiny slice.

    The saved cargo and move order are retained. The schema-1.8 hull/background
    has stale parameter hashes, so the current production builder reconstructs
    the background using that manifest's actual booking mass. No source hash or
    canonical qualification pin is changed. This is not original pilot pass
    evidence and is not T4's empty-ship series.
    """
    run = ROOT/"experiments/solver-diag-1028/pilot-d58/runs/exact"/run_id
    if not (run/"result.json").is_file():
        pytest.skip(f"Saved diagnostic pilot {run_id} is not installed; no acceptance claim")
    meta = json.loads((run/"result.json").read_text())
    source = Path(meta["input"])
    if not source.is_file():
        source = ROOT/"experiments"/str(source).split("/experiments/", 1)[-1]
    instance = Instance.model_validate_json(source.read_text())
    plan = MovePlan.model_validate_json((run/"final.plan.json").read_text())
    vessel_name = instance.vessel.id.split(".", 1)[0]
    from stowage.generate.random_yard import _tiny_region
    from stowage.vessel.fleet_slices import recondition_slice
    from stowage.vessel.fleet_conditions import departure_tank_masses
    booking_mass = sum(box.gross_mass_kg for box in instance.containers
                       if box.id in instance.load_list)
    instance.vessel = recondition_slice(_tiny_region(load_vessel(vessel_name)),
        expected_load_mass_kg=booking_mass, seed=instance.seed)
    instance.onboard.vessel_id = instance.vessel.id
    instance.onboard.tank_masses_kg = departure_tank_masses(instance.vessel)
    # The fixed arrival HEEL total is the D64 policy for newly created states;
    # it does not claim the old generator already emitted that pre-fill.
    tanks = {tank.id: tank for tank in instance.vessel.tanks}
    for pair in instance.vessel.tank_pairs:
        for tid in (pair.port_tank, pair.starboard_tank):
            tank = tanks[tid]
            mass = .5*tank.capacity_m3*tank.content_density_kg_m3
            instance.onboard.tank_masses_kg[tid] = mass
            if plan.ballast is not None:
                plan.ballast.tank_masses_kg[tid] = mass
    assert len(instance.vessel.tank_pairs) == 1
    return instance, plan


@pytest.mark.slow
@pytest.mark.parametrize("run_id,name", list(zip(("000", "008", "018"), FLEET)))
def test_real_pilot_every_lift_warm_and_cold_verdicts(run_id, name, record_property):
    """Every state, including open hatches, keeps the cold physical verdict."""
    from stowage.checker.in_port_evaluator import InPortEvaluator
    from stowage.checker.in_port_liquids import prepare_context
    with rule_freeze_v1(enabled=True):
        instance, plan = saved_pilot(run_id)
        assert instance.vessel.id.startswith(name)
        builder = InPortStateBuilder(instance)
        states = [builder.state.ship]
        for index, move in enumerate(plan.moves):
            states.append(builder.push(move, move_index=index).ship)
        assert any(ship.covers_off for ship in states), "Pilot must exercise open hatches"
        departure, problems = full._ballast(instance, plan, states[-1])
        assert not problems
        context = prepare_context(instance, departure)
        transferred = [context.transferred_ship(ship, phase="arrival")[0] for ship in states]
        with InPortEvaluator(instance, context, workers=1, warm_start=False) as evaluator:
            cold = evaluator.evaluate(transferred)
        with InPortEvaluator(instance, context, workers=1, warm_start=True) as evaluator:
            warm = evaluator.evaluate(transferred)
        assert len(cold.reports) == len(warm.reports) == len(plan.moves)+1
        for index, (expected, got) in enumerate(zip(cold.reports, warm.reports, strict=True)):
            assert expected.ran and got.ran, (index, expected.model_dump(), got.model_dump())
            assert expected.error is None and got.error is None
            signature = lambda report: [(v.rule_id, v.severity, v.unit) for v in report.violations]
            assert signature(expected) == signature(got), (index, expected.model_dump(), got.model_dump())
            assert expected.margins.keys() == got.margins.keys()
            assert {key: value >= 0 for key, value in expected.margins.items()} == {
                key: value >= 0 for key, value in got.margins.items()}, index
            # These are continuation validation bands, never allowances on a
            # physical limit; verdicts and margin signs must still agree above.
            for key, cold_margin in expected.margins.items():
                if key == "gm_m":
                    band = max(.01, .002*abs(expected.metrics["gm_m"]))
                elif "draught" in key or "trim" in key:
                    band = .02
                elif key.endswith("_rad"):
                    band = math.radians(.2)
                else:
                    pytest.fail(f"Undeclared warm/cold validation band for margin {key}")
                delta = abs(got.margins[key]-cold_margin)
                assert delta < band, (index, key, cold_margin, got.margins[key], band)
            if states[index].covers_off:
                assert "open_hatch_immersion_angle_rad" in got.margins
        expected_k = next((index for index, report in enumerate(cold.reports)
                           if report.violations), len(states))
        result = full._in_port(instance, plan)
        _assert_complete_replay_passes(result)
        assert result.metrics["state_count"] == len(states)
        assert result.metrics["k_arr"] == expected_k
        record_property("k", result.metrics["k_arr"])
        record_property("state_count", len(states))


@pytest.mark.slow
def test_real_pilot_sampled_strength_failure_cannot_pass_acceptance(monkeypatch):
    """The old state-count/k assertions alone would accept this injected failure."""
    from stowage.checker import in_port
    from stowage.model import PartReport, Violation
    with rule_freeze_v1(enabled=True):
        instance, plan = saved_pilot("000")
        seen = []

        def broken_strength(instance, ship, *, call_index, **kwargs):
            seen.append(ship)
            return PartReport(name="strength", violations=[Violation(
                rule_id="iacs.ur_s11.longitudinal_strength", severity="hard",
                call_index=call_index, measured=2., limit=1., unit="ratio",
                message="Injected sampled harbour-strength failure")])

        monkeypatch.setattr(in_port, "check_strength", broken_strength)
        report = full._in_port(instance, plan)
        assert seen, "The injected sample must actually be evaluated"
        assert report.metrics["state_count"] == len(plan.moves)+1
        assert report.metrics["k_arr"] == len(plan.moves)+1
        assert any("Injected sampled harbour-strength failure" in v.message
                   for v in report.violations)
        with pytest.raises(AssertionError):
            _assert_complete_replay_passes(report)


@pytest.mark.slow
def test_t8_real_mixed_discharge_load_and_restow_checks_complete_replay():
    """Real fleet physics, with a restow after a load requiring sampled P9."""
    from stowage.model import Discharge, RestowOff, RestowOn
    with rule_freeze_v1(enabled=True):
        instance, original = saved_pilot("000")
        builder = InPortStateBuilder(instance)
        # Reached physical arrival: two hold boxes and the first deck box.
        for index, move in enumerate(original.moves[:5]):
            builder.push(move, move_index=index)
        world = builder.state
        unload, through, deck, new_load = [move for move in original.moves if move.kind == "load"]
        boxes = [box.model_copy(update={"pod": instance.port.locode})
                 if box.id == unload.container_id else box for box in instance.containers]
        payload = instance.model_dump(mode="python")
        payload.update(stage="B", containers=boxes, onboard=world.ship, yard=world.yard,
            load_list=[new_load.container_id], discharge_list=[unload.container_id])
        instance = Instance.model_validate(payload)
        cover = next(move.cover_id for move in original.moves if move.kind == "hatch_cover_off")
        moves = [
            RestowOff(call_index=0, container_id=deck.container_id, src=deck.dst),
            HatchCoverOff(call_index=0, cover_id=cover),
            RestowOff(call_index=0, container_id=through.container_id, src=through.dst),
            Discharge(call_index=0, container_id=unload.container_id,
                      src=unload.dst, dst=unload.src),
            RestowOn(call_index=0, container_id=through.container_id, dst=unload.dst),
            HatchCoverOn(call_index=0, cover_id=cover),
            new_load,
            RestowOn(call_index=0, container_id=deck.container_id, dst=deck.dst),
        ]
        plan = MovePlan(id=f"{original.id}.mixed", instance_id=instance.id,
            call_index=0, moves=moves, ballast=original.ballast)
        builder = InPortStateBuilder(instance)
        states = [builder.state.ship]
        states.extend(builder.push(move, move_index=index).ship
                      for index, move in enumerate(moves))
        assert len(states[4].placements) == 0
        assert {placement.container_id for placement in states[-1].placements} == {
            through.container_id, deck.container_id, new_load.container_id}
        report = full._in_port(instance, plan)
        _assert_complete_replay_passes(report)
        assert report.metrics["state_count"] == len(moves)+1
        for index in range(len(states)):
            phase = "arrival" if index < report.metrics["k"] else "departure"
            assert f"state:{index}.{phase}.gm_m" in report.metrics
            assert f"state:{index}.{phase}.list_rad" in report.margins
        final_phase = "arrival" if len(moves) < report.metrics["k"] else "departure"
        assert f"state:{len(moves)}.{final_phase}.static.stack_mass_kg" in report.margins


def test_t4_generator_hook_runs_with_same_activation_as_replay(monkeypatch):
    from types import SimpleNamespace
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.vessel.fleet_voyage import build_voyage_policy
    monkeypatch.setitem(globals(), 'find_spec', lambda name: object())
    vessel = build_instance().vessel
    vessel.voyage_policy = build_voyage_policy(vessel.tanks,
        parameter_source="synthetic activation regression", vessel_id=vessel.id)
    monkeypatch.setitem(globals(), 'load_vessel', lambda name: vessel)
    def initialize(instance):
        assert rule_freeze_v1_enabled()
        return instance.model_copy(update={"onboard": instance.onboard.model_copy(
            update={"tank_masses_kg": {"witness": 1.}})})
    monkeypatch.setitem(globals(), 'import_module', lambda name: SimpleNamespace(
        prepare_arrival=initialize))
    with rule_freeze_v1(enabled=False):
        assert _generator_berth_arrival('feeder_500') == {'witness': 1.}
        assert not rule_freeze_v1_enabled()


@pytest.mark.parametrize('name', FLEET)
def test_t4_arrival_probe_does_not_pump_the_full_plan_ballast(monkeypatch, name):
    """The separate s0 certificate is not the real cargo-operation programme.

    Its zero lifts cannot provide the full plan's pumping window. The real
    plan, including its original departure fills, must still be checked next.
    """
    from stowage.model import PartReport, Violation
    tanks = [SimpleNamespace(id=tid, adjustable=True, capacity_m3=100.,
                             content_density_kg_m3=1000.) for tid in ('P','S','DB')]
    instance = SimpleNamespace(id=f'T4.{name}',
        vessel=SimpleNamespace(tanks=tanks,
            tank_pairs=[SimpleNamespace(port_tank='P',starboard_tank='S')],
            hull=SimpleNamespace(lpp_m=100.)),
        onboard=SimpleNamespace(tank_masses_kg={'P':50000.,'S':50000.,'DB':99000.}),
        port=SimpleNamespace(archetype='production',max_draught_m=None,
            max_crane_height_tiers=8,sources=[]))
    plan = MovePlan(id=f'{instance.id}.full',instance_id=instance.id,call_index=0,
        ballast=BallastCondition(tank_masses_kg={'P':50000.,'S':50000.,'DB':0.},source='planner'))
    plan.moves = [SimpleNamespace(kind='load')]*450
    seen=[]
    def replay(candidate_instance,candidate_plan,**kwargs):
        seen.append(candidate_plan)
        pumping = not candidate_plan.moves and (
            candidate_plan.ballast.tank_masses_kg != instance.onboard.tank_masses_kg)
        return PartReport(name='in_port',metrics={'k':float(len(candidate_plan.moves)+1),
            'state_count':float(len(candidate_plan.moves)+1),'state:0.arrival.trim_m':0.},
            violations=[Violation(rule_id='port.ballast.transfer_rate',severity='hard',call_index=0,
                message='changeover_time: zero-lift probe cannot empty DB')] if pumping else [])
    monkeypatch.setitem(globals(),'_prepared_manual_plan',lambda _: (instance,plan))
    monkeypatch.setattr(full,'_in_port',replay)
    test_t4_full_size_empty_ship_every_lift(name,lambda *_:None)
    assert len(seen)==2
    assert seen[0].ballast.tank_masses_kg == instance.onboard.tank_masses_kg
    assert seen[1] is plan and seen[1].ballast.tank_masses_kg['DB']==0.

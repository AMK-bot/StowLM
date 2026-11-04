"""D58: real fleet slice backgrounds are legal at every declared D57 sample."""
from __future__ import annotations

import pytest
from statistics import pstdev
from dataclasses import replace
from unittest.mock import patch

from stowage.checker import check, check_lashing, departure_condition
from stowage.checker import visibility
from stowage.generate.background_acceptance import background_instance, evaluate_background
from stowage.generate.cargo_limits import MAX_GENERATED_GROSS_MASS_KG
from stowage.generate.random_voyage import build_voyage_spec
from stowage.generate.random_yard import _tiny_region
from stowage.model import Instance, MovePlan
from stowage.model.plan import BallastCondition
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import ShipState
from stowage.model.yard import YardState
from stowage.ports.profiles import load_port
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_conditions import departure_tank_masses, slice_condition
from stowage.vessel.fleet_slices import recondition_slice, slice_preset
from stowage.solver.ballast_arrival import arrival_condition, mid_voyage_condition


def _background(vessel_name: str, rung: str, *, booking_mass_override=None, condition_seed_override=None) -> Instance:
    parent = load_vessel(vessel_name)
    vessel = _tiny_region(parent) if rung == "tiny" else slice_preset(parent, rung)
    # As in both generators, only the final selected geometry and an explicit
    # booking mass define an emitted condition. Unbooked presets are diagnostics.
    medium_mass = {"feeder_500": 1_917_238.3810685035,
                   "feeder_800": 3_734_513.4862812967,
                   "feeder_1700": 6_914_017.2082863245}
    booking_mass = medium_mass[vessel_name] if rung == "medium" else {"tiny": 50_000., "small": 250_000.}[rung]
    seed = 1 if rung == "medium" else 7
    if booking_mass_override is not None:
        booking_mass = booking_mass_override
    if condition_seed_override is not None:
        seed = condition_seed_override
    vessel = recondition_slice(vessel, expected_load_mass_kg=booking_mass, seed=seed)
    port = load_port("laem_chabang_like")
    # The same synthetic berth at both calls isolates the D57 consumable change;
    # it does not invent water densities or relax the berth limits.
    rotation = Rotation(service="background-acceptance", calls=[
        PortCall(locode=port.locode, archetype=port.archetype, index=index)
        for index in (0, 1)])
    return Instance(id=f"background.{vessel_name}.{rung}", stage="A", size_class=rung,
        seed=seed, vessel=vessel, rotation=rotation, port=port, call_index=0,
        yard=YardState(yard_id=port.yard.id), containers=[], load_list=[],
        onboard=ShipState(vessel_id=vessel.id, tank_masses_kg=departure_tank_masses(vessel)),
        voyage=build_voyage_spec(rotation, 0, port, seed=seed))


def _assert_stored_background_weight_order(instance: Instance) -> None:
    """D61 E2: compare actual serialized kg, on every physical support column."""
    stored = Instance.model_validate_json(instance.model_dump_json())
    occupied = {cid: box for load in stored.vessel.fixed_loads
                for box in load.cargo_boxes or [] for cid in box.cell_ids}
    comparisons = 0
    for stack in stored.vessel.fixed_load_stacks:
        column = [(cid, occupied[cid]) for cid in stack.cell_ids if cid in occupied]
        for (lower_cid, lower), (upper_cid, upper) in zip(column, column[1:]):
            # A long box appears in each supporting odd-bay column: each of
            # its two lower supports must independently carry at least its kg.
            assert upper.gross_mass_kg <= lower.gross_mass_kg, (
                stack.id, lower_cid, upper_cid, lower.gross_mass_kg,
                upper.gross_mass_kg, upper.gross_mass_kg-lower.gross_mass_kg)
            comparisons += 1
    assert comparisons > 0


@pytest.mark.parametrize("vessel_name", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("rung", ["tiny", "small", "medium"])
def test_background_passes_every_checker_part_at_all_d57_states(vessel_name, rung):
    instance = _background(vessel_name, rung)
    _assert_stored_background_weight_order(instance)
    # The emitted D42 records are actual occupied boxes: each must retain an
    # empty-container tare, and deck masses must have a material spread rather
    # than differ only through floating-point residue at a common cap.
    from stowage.conventions import parse_slot
    boxes = [box for load in instance.vessel.fixed_loads for box in load.cargo_boxes or []]
    assert boxes and min(box.gross_mass_kg for box in boxes) >= 2200.
    assert max(box.gross_mass_kg for box in boxes) <= MAX_GENERATED_GROSS_MASS_KG
    deck = [box.gross_mass_kg for box in boxes if parse_slot(box.cell_ids[0]).on_deck]
    assert len(deck) > 1 and pstdev(deck) > 1., deck
    original = instance.model_dump_json()
    result = evaluate_background(instance, time_limit_s=10.)
    assert instance.model_dump_json() == original
    assert result.accepted, (result.finisher_status, result.reasons)
    assert result.finisher_status in {"ok", "kept"}
    assert set(result.states) == {"departure", "mid_voyage", "arrival"}
    assert all(instance.vessel.hull.beam_m/state["draught_mean_m"] < 3.5
               for state in result.states.values())
    if rung == "medium":
        adjustable = {tank.id for tank in instance.vessel.tanks if tank.adjustable}
        booking_ballast = sum(mass for key, mass in instance.onboard.tank_masses_kg.items() if key in adjustable)
        witness_ballast = sum(mass for key, mass in result.witness_tank_masses_kg.items() if key in adjustable)
        assert witness_ballast > booking_ballast
        condition = slice_condition(instance.vessel)
        budget = condition["booking_departure_budget"]
        assert budget["within_displacement_limit"]
        assert not budget["empty_witness_ballast_included"]
        assert not budget["actual_plan_certificate"]
        booked_mass = (instance.vessel.hull.lightship_mass_kg
                       + sum(load.mass_kg for load in instance.vessel.fixed_loads)
                       + sum(instance.onboard.tank_masses_kg.values())
                       + condition["expected_load_mass_kg"])
        assert booked_mass <= instance.vessel.hull.max_displacement_kg + 1e-5
        assert booked_mass <= condition["design_displacement_kg"] + 1e-5
        assert condition["booking_reference_ballast_sizing"]["status"] == "sized"

    # Independently replay the returned ballast witness through the public full
    # checker, rather than trusting the gate's boolean or cached metric summary.
    background = background_instance(instance)
    adjustable = {tank.id for tank in instance.vessel.tanks if tank.adjustable}
    plan = MovePlan(id="empty-accepted-background", instance_id=background.id,
        call_index=background.call_index, moves=[], ballast=BallastCondition(
            tank_masses_kg={key: value for key, value in result.witness_tank_masses_kg.items()
                            if key in adjustable}, source="finisher"))
    report = check(background, plan)
    assert report.passed, report.violations
    assert all(part.ran and not part.error and not part.violations for part in report.parts.values())
    condition_parts = {key: part for key, part in report.parts.items() if key.startswith("condition:")}
    for phase in ("departure", "mid_voyage", "arrival"):
        parts = [part for part in condition_parts.values() if f"condition.phase: {phase}" in part.metrics]
        assert len(parts) >= 4, (phase, condition_parts.keys())
        assert all(not part.violations and part.ran and not part.error for part in parts)
    arrival = next(part for key, part in condition_parts.items()
                   if key.endswith(":stability") and "condition.phase: arrival" in part.metrics)
    coefficients = {"area_0_30_mrad": .009, "area_0_40_mrad": .016,
                    "area_30_40_mrad": .006, "gz_at_or_above_30_m": .033,
                    "gz_max_m": .042, "area_0_flood_mrad": .029}
    form_factor = arrival.metrics["containership_form_factor"]
    # MSC.267(85), Part B 2.3.2.1--.5: actual area/lever over its Code threshold.
    assert min(arrival.metrics[key] * form_factor / coefficient
               for key, coefficient in coefficients.items()) >= 1.05
    # A separate D56 stress diagnostic raises only the securing motion input to
    # the declared design GM. Actual draught, trim, cargo and tank geometry stay
    # as checked above. This hypothetical envelope is not an actual ship state.
    ship = departure_condition(background, plan)
    conditions = [(background, ship), mid_voyage_condition(background, ship),
                  arrival_condition(background, ship)]
    actual_loading_condition = visibility._loading_condition
    design_gm = background.vessel.hull.max_gm_lashing_m
    def design_loading_condition(condition_instance, condition_ship):
        return replace(actual_loading_condition(condition_instance, condition_ship), gm_m=design_gm)
    for condition_instance, condition_ship in conditions:
        with patch.object(visibility, "_loading_condition", side_effect=design_loading_condition):
            securing = check_lashing(condition_instance, condition_ship, call_index=condition_instance.call_index)
        assert securing.ran and not securing.error, securing.error
        assert not securing.violations, securing.violations
        assert securing.metrics["gm_m"] == design_gm


def test_medium_witness_near_empty_tanks_are_exactly_empty_and_fully_checkable():
    # The actual random-medium500 seed5 draw previously left ~2.45e-12kg in
    # each APT. A nonzero fraction with a cancelled geometric volume produced
    # undefined liquid CGs, and the real checker correctly rejected the draw.
    instance = _background("feeder_500", "medium",
        booking_mass_override=1_938_714.641122642,
        condition_seed_override=16_180_926_462_959_460_045)
    original = instance.model_dump_json()
    result = evaluate_background(instance, use_cache=False)
    assert result.accepted, (result.finisher_status, result.reasons)
    assert instance.model_dump_json() == original
    assert result.witness_tank_masses_kg["APT-P"] == 0.
    assert result.witness_tank_masses_kg["APT-S"] == 0.
    background = background_instance(instance)
    adjustable = {tank.id for tank in background.vessel.tanks if tank.adjustable}
    plan = MovePlan(id="empty-endpoint-regression", instance_id=background.id,
        call_index=background.call_index, moves=[], ballast=BallastCondition(
            tank_masses_kg={key: value for key, value in result.witness_tank_masses_kg.items()
                            if key in adjustable}, source="finisher"))
    report = check(background, plan)
    assert report.passed, report.violations
    assert all(part.ran and not part.error and not part.violations for part in report.parts.values())
    assert set(result.states) == {"departure", "mid_voyage", "arrival"}
    assert result.states["arrival"]["b23_ratio"] >= 1.05


def test_gate_rejects_forged_overweight_background_with_real_checker():
    """A recorded ballast recipe cannot certify an overloaded fixed stack."""
    instance = _background('feeder_500', 'tiny')
    vessel = instance.vessel
    stacks = {stack.id: stack for stack in vessel.fixed_load_stacks}
    index, load = next((index, load) for index, load in enumerate(vessel.fixed_loads)
                       if load.kind == 'cargo' and load.cargo_boxes and load.silhouette is None)
    box = load.cargo_boxes[0]
    rating = stacks[box.stack_id].max_mass_20_kg or stacks[box.stack_id].max_mass_kg
    # Start from the full declared rating, rather than assuming the allocator
    # happened to saturate this particular stack in its legal source condition.
    stack_mass = sum(item.gross_mass_kg for point in vessel.fixed_loads
                     for item in point.cargo_boxes or [] if item.stack_id == box.stack_id)
    extra = rating-stack_mass+1000.
    forged_box = box.model_copy(update={'gross_mass_kg': box.gross_mass_kg+extra})
    forged_load = load.model_copy(update={
        'mass_kg': load.mass_kg+extra, 'cargo_boxes': [forged_box, *load.cargo_boxes[1:]]})
    loads = [*vessel.fixed_loads]
    loads[index] = forged_load
    forged = instance.model_copy(update={'vessel': vessel.model_copy(update={'fixed_loads': loads})})

    # A short repair budget is sufficient for this negative test: ballast
    # cannot change the independently checked physical cargo-stack rating.
    result = evaluate_background(forged, time_limit_s=.01, use_cache=False)
    assert not result.accepted
    assert any('expert.physical.stack_weight' in reason for reason in result.reasons), result.reasons

"""Synthetic fixture variants for catalogue geometry and weight rules.

The copied invalid states deliberately bypass model validation: the checker must
return tagged violations even when another caller supplies a bad stow.
"""

import math

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.model import Placement
from tests.checker.test_rules_fixtures import build_instance


def clean_instance(*, layover=False):
    instance = build_instance(layover=layover)
    instance.containers = [
        box.model_copy(update=dict(iso_type="22G1", length_ft=20, height_m=2.591,
                                   tare_kg=2200, gross_mass_kg=12000, reefer=False,
                                   reefer_setpoint_c=None, dg=None, special_stowage=[],
                                   oog=None, pod="THBKK"))
        for box in instance.containers
    ]
    # D25 provisional class thresholds come from the catalogue, not fixtures.
    instance.rule_overrides = {}
    return instance


def stow(instance, *slots):
    return instance.onboard.model_copy(update={
        "placements": [Placement(slot=slot, container_id=box.id)
                       for box, slot in zip(instance.containers, slots)]
    })


def checked(instance, ship):
    report = check_static_rules(instance, ship, call_index=0)
    assert report.ran
    assert all(v.call_index == 0 for v in report.violations)
    return report


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_slot_occupancy(failing):
    instance = clean_instance()
    ship = stow(instance, "0010102", "0010102" if failing else "0030102")
    assert ("expert.physical.slot_occupancy" in {v.rule_id for v in checked(instance, ship).violations}) is failing


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_size_fit(failing):
    instance = clean_instance()
    instance.vessel.cells[0].accepts_20 = not failing
    ship = stow(instance, instance.vessel.cells[0].id)
    assert ("expert.physical.size_fit" in {v.rule_id for v in checked(instance, ship).violations}) is failing


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_no_20_on_40(failing):
    instance = clean_instance()
    instance.containers[0] = instance.containers[0].model_copy(update={"length_ft": 40, "iso_type": "42G1"})
    ship = stow(instance, "0020102", "0010104" if failing else "0010202")
    assert ("expert.physical.no_20_on_40" in {v.rule_id for v in checked(instance, ship).violations}) is failing


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_stack_weight(failing):
    instance = clean_instance()
    stack = next(s for s in instance.vessel.stacks if s.id == "00101H")
    stack.max_mass_kg = 23999 if failing else 24000
    ship = stow(instance, "0010102", "0010104")
    report = checked(instance, ship)
    assert ("expert.physical.stack_weight" in {v.rule_id for v in report.violations}) is failing
    assert report.margins["stack_mass_kg"] == (-1 if failing else 0)


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_tier_height(failing):
    instance = clean_instance()
    stack = next(s for s in instance.vessel.stacks if s.id == "00101H")
    stack.max_height_m = 5.181 if failing else 5.182
    ship = stow(instance, "0010102", "0010104")
    report = checked(instance, ship)
    assert ("expert.physical.tier_height" in {v.rule_id for v in report.violations}) is failing
    assert report.margins["stack_height_m"] == pytest.approx(-0.001 if failing else 0)


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_reefer_plug(failing):
    instance = clean_instance()
    instance.containers[0].reefer = True
    ship = stow(instance, "0010102" if failing else "0010182")
    assert ("expert.physical.reefer_plug" in {v.rule_id for v in checked(instance, ship).violations}) is failing


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_e6_heavy_under_deck(failing):
    instance = clean_instance()
    instance.containers[0].gross_mass_kg = 22000
    ship = stow(instance, "0010182" if failing else "0010102")
    assert ("expert.e6.heavy_under_deck" in {v.rule_id for v in checked(instance, ship).violations}) is failing


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_e1_heavy_bottom(failing):
    instance = clean_instance()
    instance.containers[0].gross_mass_kg = 12000 if failing else 22000
    instance.containers[1].gross_mass_kg = 22000 if failing else 12000
    ship = stow(instance, "0010102", "0010104")
    assert ("expert.e1.heavy_bottom" in {v.rule_id for v in checked(instance, ship).violations}) is failing


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_e2_weight_order(failing):
    instance = clean_instance()
    instance.containers[1].gross_mass_kg = 20000 if failing else 19999
    ship = stow(instance, "0010102", "0010104")
    assert ("expert.e2.weight_order" in {v.rule_id for v in checked(instance, ship).violations}) is failing


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_verified_gross_mass(failing):
    instance = clean_instance()
    instance.containers[0].gross_mass_kg = 0 if failing else 12000
    ship = stow(instance, "0010102")
    assert ("solas.vi2.verified_gross_mass" in {v.rule_id for v in checked(instance, ship).violations}) is failing


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_bottom_up(failing):
    instance = clean_instance()
    ship = stow(instance, "0010104" if failing else "0010102")
    assert ("expert.moves.bottom_up" in {v.rule_id for v in checked(instance, ship).violations}) is failing


@pytest.mark.parametrize("rule", ["expert.e1.heavy_bottom", "expert.e2.weight_order"])
@pytest.mark.parametrize("unchanged", [False, True], ids=["moved_hard", "unchanged_tolerated"])
def test_weight_order_inherited_only_at_unchanged_arrival_slots(rule, unchanged):
    instance = clean_instance(layover=True)
    # Arrival boxes already form this inversion. New inversions remain hard.
    lower, upper = instance.containers[-2:]
    lower.gross_mass_kg, upper.gross_mass_kg = 12000, 22000
    ship = instance.onboard.model_copy(deep=True)
    if not unchanged:
        ship.placements = [p.model_copy(update={"slot": p.slot[:3] + "02" + p.slot[5:]})
                           for p in ship.placements]
    violations = [v for v in checked(instance, ship).violations if v.rule_id == rule]
    assert violations
    assert all(v.severity == ("tolerated" if unchanged else "hard") for v in violations)
    assert all(v.inherited is unchanged for v in violations)


@pytest.mark.parametrize("rule", ["expert.e6.heavy_under_deck", "expert.e1.heavy_bottom", "expert.e2.weight_order"])
def test_weight_policy_defaults_do_not_require_instance_overrides(rule):
    instance = clean_instance()
    instance.rule_overrides.pop(rule, None)
    ship = stow(instance, "0010182", "0010184")
    violations = checked(instance, ship).violations
    assert not any(v.rule_id == "checker.missing_vessel_data" and rule in v.message for v in violations)


@pytest.mark.parametrize("mass", [math.nan, math.inf, -1])
def test_nonfinite_or_negative_vgm_does_not_raise(mass):
    instance = clean_instance()
    instance.containers[0].gross_mass_kg = mass
    violations = checked(instance, stow(instance, "0010102")).violations
    assert any(v.rule_id == "solas.vi2.verified_gross_mass" and v.severity == "hard" for v in violations)


def test_40ft_mass_shared_between_supporting_stacks():
    instance = clean_instance()
    instance.containers[0] = instance.containers[0].model_copy(update={"length_ft": 40, "iso_type": "42G1", "gross_mass_kg": 24000})
    for stack in instance.vessel.stacks:
        stack.max_mass_kg = 12000
    report = checked(instance, stow(instance, "0020102"))
    assert not any(v.rule_id == "expert.physical.stack_weight" for v in report.violations)
    assert report.margins["stack_mass_kg"] == 0


@pytest.mark.parametrize("length", [20, 40])
@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_loading_manual_size_specific_stack_limit(length, failing):
    instance = clean_instance()
    instance.containers[0] = instance.containers[0].model_copy(update={
        "length_ft": length, "iso_type": "22G1" if length == 20 else "42G1",
        "gross_mass_kg": 24000,
    })
    share = 24000 if length == 20 else 12000
    for stack in instance.vessel.stacks:
        setattr(stack, f"max_mass_{length}_kg", share - 1 if failing else share)
    report = checked(instance, stow(instance, "0010102" if length == 20 else "0020102"))
    assert ("expert.physical.stack_weight" in {v.rule_id for v in report.violations}) is failing


def test_incremental_placement_checks_candidate_without_mutating_state():
    instance = clean_instance()
    ship = stow(instance, "0010102")
    original = ship.model_dump()
    candidate = instance.containers[1]
    bad = placement_violations(instance, ship, candidate, "0010102", call_index=0)
    good = placement_violations(instance, ship, candidate, "0010104", call_index=0)
    assert any(v.rule_id == "expert.physical.slot_occupancy" for v in bad)
    assert not any(v.severity == "hard" for v in good)
    assert ship.model_dump() == original


@pytest.mark.parametrize("hatch", [False, True], ids=["stack", "hatch"])
def test_future_overstow_permits_accounted_legal_restows(hatch):
    instance = clean_instance()
    instance.containers[0].pod, instance.containers[1].pod = "THBKK", "THSGZ"
    ship = stow(instance, "0010102", "0010182" if hatch else "0010104")
    violations = checked(instance, ship).violations
    assert not any(v.rule_id in {"expert.rotation.no_overstow", "expert.rotation.hatch_overstow"}
                   and v.severity == "hard" for v in violations)


def test_missing_discharge_call_is_hard_data_violation():
    instance = clean_instance()
    instance.containers[0].pod = "SGSIN"
    assert any(v.rule_id == "checker.missing_vessel_data" and v.severity == "hard"
               for v in checked(instance, stow(instance, "0010102")).violations)


@pytest.mark.parametrize("missing", ["container", "cell"])
def test_unknown_placement_reference_returns_hard_data_violation(missing):
    instance = clean_instance()
    ship = stow(instance, "0990102" if missing == "cell" else "0010102")
    if missing == "container":
        instance.containers = instance.containers[1:]
    assert any(v.rule_id == "checker.missing_vessel_data" and v.severity == "hard"
               for v in checked(instance, ship).violations)


@pytest.mark.parametrize("plugged", [None, "0010102", "0030102"], ids=["none", "aft", "forward"])
def test_forty_foot_reefer_needs_either_supporting_cell_plug(plugged):
    instance = clean_instance()
    instance.containers[0] = instance.containers[0].model_copy(update={
        "length_ft": 40, "iso_type": "42R1", "reefer": True,
    })
    for cell in instance.vessel.cells:
        cell.reefer_plug = cell.id == plugged
    violations = checked(instance, stow(instance, "0020102")).violations
    assert any(v.rule_id == "expert.physical.reefer_plug" for v in violations) is (plugged is None)


@pytest.mark.parametrize("moved", [False, True], ids=["unchanged", "moved"])
def test_inherited_stack_mass_safety_limit_remains_hard(moved):
    instance = clean_instance(layover=True)
    ship = instance.onboard.model_copy(deep=True)
    if moved:
        ship.placements = [p.model_copy(update={"slot": p.slot[:3] + "02" + p.slot[5:]})
                           for p in ship.placements]
    for stack in instance.vessel.stacks:
        stack.max_mass_kg = 20000
    violations = [v for v in checked(instance, ship).violations
                  if v.rule_id == "expert.physical.stack_weight"]
    assert violations
    assert all(v.severity == "hard" for v in violations)


def test_nonadjacent_same_class_masses_have_no_weight_order_violation():
    instance = clean_instance()
    stack = next(s for s in instance.vessel.stacks if s.id == "00101H")
    cell = next(c for c in instance.vessel.cells if c.id == "0010104")
    instance.vessel.cells.append(cell.model_copy(update={"id": "0010106", "vcg_base_m": 7.182}))
    stack.cell_ids.append("0010106")
    stack.max_height_m = 9
    for box, mass in zip(instance.containers, [12000, 13000, 14000]):
        box.gross_mass_kg = mass
    report = checked(instance, stow(instance, "0010102", "0010104", "0010106"))
    violations = [v for v in report.violations if v.rule_id == "expert.e2.weight_order"]
    assert not violations


@pytest.mark.parametrize("rule", ["expert.e1.heavy_bottom", "expert.e2.weight_order"])
def test_new_order_inversion_cannot_hide_behind_inherited_minimum(rule):
    instance = clean_instance(layover=True)
    stack = next(s for s in instance.vessel.stacks if s.id == "00101H")
    cell = next(c for c in instance.vessel.cells if c.id == "0010104")
    instance.vessel.cells.append(cell.model_copy(update={"id": "0010106", "vcg_base_m": 7.182}))
    stack.cell_ids.append("0010106")
    stack.max_height_m = 9
    lower, upper = instance.containers[-2:]
    lower.gross_mass_kg, upper.gross_mass_kg = 12000, 22000
    instance.onboard.placements[1] = instance.onboard.placements[1].model_copy(update={"slot": "0010106"})
    ship = instance.onboard.model_copy(deep=True)
    new_box = instance.containers[0]
    new_box.gross_mass_kg = 13000
    ship.placements.append(Placement(slot="0010104", container_id=new_box.id))
    violations = [v for v in checked(instance, ship).violations if v.rule_id == rule]
    assert any(v.severity == "hard" and new_box.id in v.containers for v in violations)


@pytest.mark.parametrize("slot", ["0000102", "0010103", "bad-slot"])
def test_malformed_slot_returns_hard_data_violation(slot):
    instance = clean_instance()
    ship = stow(instance, "0010102")
    ship.placements[0] = ship.placements[0].model_copy(update={"slot": slot})
    assert any(v.rule_id == "checker.missing_vessel_data" and v.severity == "hard"
               for v in checked(instance, ship).violations)

"""D25 provisional class policy, using synthetic fixture-builder cargo.

VGM class boundaries, rather than raw mass differences or supplied class labels,
control E1/E2. The catalogue records the pending expert questions and assumptions.
"""
import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.model import Placement
from tests.checker.test_rules_fixtures import build_instance

E1 = "expert.e1.heavy_bottom"
E2 = "expert.e2.weight_order"


def weight_case(lower, upper, overrides=None):
    instance = build_instance()
    instance.rule_overrides = overrides or {}
    instance.containers = [
        box.model_copy(update={
            "iso_type": "22G1", "length_ft": 20, "height_m": 2.591,
            "tare_kg": 2200, "gross_mass_kg": mass, "reefer": False,
            "reefer_setpoint_c": None, "dg": None, "special_stowage": [],
            "oog": None, "pod": "THBKK",
        })
        for box, mass in zip(instance.containers[:2], (lower, upper))
    ]
    ship = instance.onboard.model_copy(update={"placements": [
        Placement(container_id=instance.containers[0].id, slot="0010102"),
        Placement(container_id=instance.containers[1].id, slot="0010104"),
    ]})
    return instance, ship


def weight_ids(instance, ship, incremental):
    if incremental:
        partial = ship.model_copy(update={"placements": ship.placements[:1]})
        violations = placement_violations(instance, partial, instance.containers[1],
                                          "0010104", call_index=0)
    else:
        violations = check_static_rules(instance, ship, call_index=0).violations
    assert not any(v.rule_id == "checker.missing_vessel_data" for v in violations)
    return {v.rule_id for v in violations}


@pytest.mark.parametrize("incremental", [False, True], ids=["departure", "placement"])
@pytest.mark.parametrize("lower,upper,e1,e2", [
    (9999, 10000, False, True),
    (10000, 9999, False, False),
    (19999, 20000, True, True),
    (20000, 19999, False, False),
    (2200, 9999, False, False),
    (10000, 19999, False, False),
    (20000, 30000, False, False),
    (30000, 20000, False, False),
])
def test_d25_class_boundaries_and_same_class_order(incremental, lower, upper, e1, e2):
    instance, ship = weight_case(lower, upper)
    ids = weight_ids(instance, ship, incremental)
    assert (E1 in ids) is e1
    assert (E2 in ids) is e2


@pytest.mark.parametrize("incremental", [False, True], ids=["departure", "placement"])
def test_d25_weight_class_label_cannot_override_vgm(incremental):
    instance, ship = weight_case(9999, 20000)
    instance.containers[0].weight_class = "H"
    instance.containers[1].weight_class = "L"
    assert {E1, E2} <= weight_ids(instance, ship, incremental)
    instance.containers[0].gross_mass_kg = 22000
    instance.containers[1].gross_mass_kg = 12000
    assert not {E1, E2} & weight_ids(instance, ship, incremental)


@pytest.mark.parametrize("incremental", [False, True], ids=["departure", "placement"])
@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_d25_e1_heavy_threshold_override_precedes_default(incremental, failing):
    instance, ship = weight_case(14000, 15000 if failing else 14999,
                               {E1: {"heavy_threshold": {"value": 15000}}})
    assert (E1 in weight_ids(instance, ship, incremental)) is failing


@pytest.mark.parametrize("incremental", [False, True], ids=["departure", "placement"])
@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_d25_e2_class_threshold_overrides_precede_defaults(incremental, failing):
    instance, ship = weight_case(7999, 8000 if failing else 7999,
                               {E2: {"medium_min_kg": {"value": 8000},
                                     "heavy_min_kg": {"value": 16000}}})
    assert (E2 in weight_ids(instance, ship, incremental)) is failing


@pytest.mark.parametrize("incremental", [False, True], ids=["departure", "placement"])
@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_d25_e2_heavy_class_override_precedes_default(incremental, failing):
    instance, ship = weight_case(15999, 16000 if failing else 15999,
                               {E2: {"medium_min_kg": 8000, "heavy_min_kg": 16000}})
    assert (E2 in weight_ids(instance, ship, incremental)) is failing


@pytest.mark.parametrize("incremental", [False, True], ids=["departure", "placement"])
@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_d25_e2_tolerance_is_in_class_ranks(incremental, failing):
    # A one-class override permits L -> M irrespective of their kg difference.
    instance, ship = weight_case(2200, 20000 if failing else 19999,
                               {E2: {"tolerance": 1}})
    assert (E2 in weight_ids(instance, ship, incremental)) is failing


@pytest.mark.parametrize("incremental", [False, True], ids=["departure", "placement"])
@pytest.mark.parametrize("overrides", [
    {E1: {"heavy_threshold": None}},
    {E2: {"medium_min_kg": 20000, "heavy_min_kg": 10000}},
    {E2: {"medium_min_kg": float("nan")}},
    {E2: {"tolerance": 0.5}},
])
def test_d25_invalid_policy_overrides_fail_closed(incremental, overrides):
    instance, ship = weight_case(12000, 22000, overrides)
    if incremental:
        partial = ship.model_copy(update={"placements": ship.placements[:1]})
        violations = placement_violations(instance, partial, instance.containers[1],
                                          "0010104", call_index=0)
    else:
        violations = check_static_rules(instance, ship, call_index=0).violations
    assert any(v.rule_id == "checker.missing_vessel_data" and v.severity == "hard"
               for v in violations)

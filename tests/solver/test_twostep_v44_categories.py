"""D73 booking-type boundaries with demo rules OFF; no physics certification."""
from time import monotonic

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import Container, Instance
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1, rule_freeze_v1_enabled
from stowage.solver import twostep, twostep_terminal
from tests.solver.test_twostep import PhysicalOnlyOracle, interchangeable_instance


@pytest.fixture(autouse=True)
def demo_rules():
    with rule_freeze_v1(enabled=False):
        yield


def typed_instance(rung="tiny"):
    data = interchangeable_instance().model_dump()
    # Both boxes have the same POD, dimensions, mass class and special flags.
    # The buried dry box is booked below the accessible open-top box. If the
    # category loses ISO type, both terminal policies can swap them and avoid
    # a yard lift by putting the wrong equipment type into the lower booking.
    data["size_class"] = rung
    data["containers"][0]["iso_type"] = "22G1"
    data["containers"][1]["iso_type"] = "22U1"
    data["containers"] = [Container.model_validate(b).model_dump() for b in data["containers"]]
    return Instance.model_validate(data)


def test_demo_categories_distinguish_validated_dry_and_open_top_equipment():
    instance = typed_instance()
    dry, open_top = instance.containers
    assert rule_freeze_v1_enabled() is False
    assert dry.iso_type == "22G1" and open_top.iso_type == "22U1"
    assert dry.model_dump(exclude={"id", "iso_type"}) == open_top.model_dump(exclude={"id", "iso_type"})
    assert twostep.category_of(dry, instance) != twostep.category_of(open_top, instance)


def test_type_boundary_preserves_same_type_weight_class_substitution():
    instance = typed_instance()
    dry = instance.containers[0]
    other = Container.model_validate({**dry.model_dump(), "gross_mass_kg": dry.gross_mass_kg + 1.})
    assert twostep.category_of(dry, instance) == twostep.category_of(other, instance)


@pytest.mark.parametrize("rung", ["small", "tiny"], ids=["fast-terminal", "cp-terminal"])
def test_terminal_cannot_swap_equipment_types_to_save_a_yard_lift(rung):
    instance = typed_instance(rung)
    dry, open_top = instance.containers
    lower, upper = "0010182", "0010184"
    actions = (Place(dry.id, lower), Place(open_top.id, upper))
    prestow = twostep.PreStowPlan(actions=actions, slots=(
        twostep.SlotCategory(lower, twostep.category_of(dry, instance), dry.id),
        twostep.SlotCategory(upper, twostep.category_of(open_top, instance), open_top.id)))
    plan, metrics, status, message = twostep_terminal.terminal_plan(
        instance, prestow, deadline=monotonic() + 3., seed=0, engine_factory=PhysicalOnlyOracle)
    assert status == "ok", message
    selected = get_action_space("boxslot").from_plan(instance, plan)
    types = {b.id: b.iso_type for b in instance.containers}
    assert {a.slot: types[a.box] for a in selected} == {lower: "22G1", upper: "22U1"}
    replay = apply_plan(instance, plan)
    assert replay.legal and replay.count.loads == 2
    assert replay.count.relocations > 0  # the cheaper cross-type swap is forbidden
    if rung == "small":
        assert metrics["terminal_cp_solves"] == 0
        assert metrics["terminal_fast_attempts"] == 2
    else:
        assert metrics["terminal_cp_solves"] > 0

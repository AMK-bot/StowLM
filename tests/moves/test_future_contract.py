"""Activated future-call screens preserve D15's explicitly abstract lift policy."""
from __future__ import annotations

import pytest

from stowage.model import Instance, MovePlan
from stowage.model.cargo import DangerousGoods
from stowage.model.fixtures import build_instance
from stowage.model.ship import Placement
from stowage.moves import IllegalMoveError, apply_plan, count_moves, project_future_moves
from stowage.rules.catalogue import rule_freeze_v1


def _future(*slots):
    instance = build_instance()
    instance.containers = instance.containers[1:]
    instance.load_list = []
    instance.discharge_list = []
    instance.yard.stacks = []
    for box in instance.containers:
        box.dg = None
        box.pod = "THSGZ"
    instance.containers[0].pod = "THLCH"
    instance.rotation.calls[1].locode = "THLCH"
    instance.rotation.calls[1].archetype = "laem_chabang_like"
    instance.onboard.placements = [Placement(slot=slot, container_id=box.id)
                                     for box, slot in zip(instance.containers, slots)]
    return instance


def _class(box, primary, *, subsidiary=()):
    box.dg = DangerousGoods(un_number="9999", imdg_class=primary, stowage_category="A",
                            subsidiary_risks=list(subsidiary))


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("lifts", [1, 2, 4])
def test_future_crossing_is_d15_off_on_cost_with_call_cap(enabled, lifts):
    instance = _future("0010182", "0010282", "0010284")
    instance.objective.future_restow_lifts_per_box = lifts
    before = instance.model_dump_json()
    with rule_freeze_v1(enabled=enabled):
        future = project_future_moves(instance, instance.onboard, from_call=0)
    # Resolved profile H=8, but this call explicitly caps H at two. The top
    # quay-side box must leave; the lower one is below H and stays untouched.
    assert (future[0].restowed_boxes, future[0].restow_lifts) == (
        (1, lifts) if enabled else (0, 0))
    assert instance.model_dump_json() == before


@pytest.mark.parametrize("enabled", [False, True])
def test_future_productive_blocker_is_discharged_first_without_restow(enabled):
    instance = _future("0010182", "0010282", "0010284")
    instance.containers[2].pod = "THLCH"
    with rule_freeze_v1(enabled=enabled):
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert future[0].restowed_boxes == future[0].restow_lifts == 0


def test_future_long_lift_checks_the_other_twenty_foot_half():
    instance = _future("0020182", "0030282", "0030284")
    instance.containers[0].length_ft = 40
    with rule_freeze_v1():
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert (future[0].restowed_boxes, future[0].restow_lifts) == (1, 2)


def test_future_cover_and_hold_paths_deduplicate_crossing_blocker():
    instance = _future("0010102", "0010282", "0010284")
    with rule_freeze_v1():
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert (future[0].restowed_boxes, future[0].restow_lifts, future[0].hatch_cover_lifts) == (1, 2, 2)


@pytest.mark.parametrize("enabled", [False, True])
def test_future_height_screens_required_lift(enabled):
    instance = _future("0010184", "0010182")
    instance.rotation.calls[1].max_on_deck_tiers = 1
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            with pytest.raises(IllegalMoveError) as caught:
                project_future_moves(instance, instance.onboard, from_call=0)
            violation = caught.value.violation
            assert (violation.rule_id, violation.call_index, violation.measured, violation.limit) == (
                "port.e3.quay_height", 1, 2, 1)
        else:
            assert project_future_moves(instance, instance.onboard, from_call=0)[0].restow_lifts == 0


def test_future_untouched_high_stack_is_not_a_required_lift():
    instance = _future("0010282", "0010182", "0010184")
    instance.rotation.calls[1].max_on_deck_tiers = 1
    with rule_freeze_v1():
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert future[0].restow_lifts == 0


def test_future_environment_profile_skips_placeholder_height():
    instance = _future("0010184", "0010282", "0010284")
    instance.containers[0].pod = "VNSGN"
    call = instance.rotation.calls[1]
    call.locode, call.archetype, call.max_on_deck_tiers = "VNSGN", "cat_lai_like", 1
    with rule_freeze_v1():
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert future[0].restow_lifts == 0


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("restow", [False, True])
def test_future_refusal_applies_to_productive_and_restow_lifts(enabled, restow):
    instance = _future("0010182", "0010184") if restow else _future("0010182")
    box = instance.containers[1 if restow else 0]
    _class(box, "7")
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            with pytest.raises(IllegalMoveError) as caught:
                project_future_moves(instance, instance.onboard, from_call=0)
            assert caught.value.violation.rule_id == "port.dg.refused_class"
            assert caught.value.violation.call_index == 1
            assert caught.value.violation.containers == [box.id]
        else:
            assert project_future_moves(instance, instance.onboard, from_call=0)[0].restowed_boxes == int(restow)


def test_future_refused_box_can_pass_through_without_handling():
    instance = _future("0010282", "0010182")
    _class(instance.containers[1], "7")
    with rule_freeze_v1():
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert future[0].restow_lifts == 0


def test_future_subsidiary_risk_is_not_refused():
    instance = _future("0010182")
    _class(instance.containers[0], "3", subsidiary=["7"])
    with rule_freeze_v1():
        assert project_future_moves(instance, instance.onboard, from_call=0)[0].restow_lifts == 0


def test_future_direct_delivery_has_no_yard_or_restow_lifts():
    instance = _future("0010282")
    _class(instance.containers[0], "1.4S")
    # The marker is current-port-derived, not reused as a future-port policy.
    # This fixture's current port has no direct-delivery rule; the later
    # resolved Laem Chabang profile derives direct delivery for class 1.
    with rule_freeze_v1():
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert future[0].restowed_boxes == future[0].restow_lifts == future[0].hatch_cover_lifts == 0


def _direct_blocker(kind):
    if kind == "crossing":
        instance = _future("0010182", "0010282", "0010284")
        blocker = instance.containers[2]
    else:
        instance = _future("0010102", "0010182") if kind == "cover" else _future("0010182", "0010184")
        blocker = instance.containers[1]
    return instance, blocker


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("kind", ["vertical", "cover", "crossing"])
@pytest.mark.parametrize("primary", ["1", "1.4S"])
def test_future_direct_blocker_off_on_policy_is_explicitly_unsupported(enabled, kind, primary):
    instance, blocker = _direct_blocker(kind)
    _class(blocker, primary)
    assert not blocker.direct_delivery  # Current port has no direct-delivery policy.
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            with pytest.raises(IllegalMoveError) as caught:
                project_future_moves(instance, instance.onboard, from_call=0)
            violation = caught.value.violation
            assert (violation.rule_id, violation.call_index, violation.containers) == (
                "port.dg.direct_delivery", 1, [blocker.id])
            assert all(word in violation.message.lower() for word in ("unsupported", "shift", "policy"))
        else:
            future = project_future_moves(instance, instance.onboard, from_call=0)
            assert (future[0].restowed_boxes, future[0].restow_lifts) == (
                (0, 0) if kind == "crossing" else (1, 2))


def test_future_productive_direct_crossing_blocker_uses_truck_discharge():
    instance, blocker = _direct_blocker("crossing")
    _class(blocker, "1.4S")
    blocker.pod = "THLCH"
    with rule_freeze_v1():
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert future[0].restowed_boxes == future[0].restow_lifts == 0


def test_future_direct_blocker_honors_resolved_profile_exemption(monkeypatch):
    from stowage.moves import _projection

    instance, blocker = _direct_blocker("vertical")
    _class(blocker, "1.4S")
    resolve = _projection.resolve_port_call_profile

    def exempt_profile(call, *, current_port=None):
        profile = resolve(call, current_port=current_port)
        if profile is not None and call.index == 1:
            profile = profile.model_copy(deep=True)
            ref = next(ref for ref in profile.port_rules if ref.rule_id == "port.dg.direct_delivery")
            ref.params["exempt_divisions"] = ["1.4S"]
        return profile

    monkeypatch.setattr(_projection, "resolve_port_call_profile", exempt_profile)
    with rule_freeze_v1():
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert (future[0].restowed_boxes, future[0].restow_lifts) == (1, 2)


@pytest.mark.parametrize("inherited_marker", [False, True])
def test_future_subsidiary_class_one_does_not_trigger_direct_blocker_policy(inherited_marker):
    instance, blocker = _direct_blocker("vertical")
    _class(blocker, "3", subsidiary=["1"])
    # Even a stale current-call marker cannot replace the future primary-class
    # lookup. Projection does not treat that marker as a later-port policy.
    blocker.direct_delivery = inherited_marker
    with rule_freeze_v1():
        future = project_future_moves(instance, instance.onboard, from_call=0)
    assert (future[0].restowed_boxes, future[0].restow_lifts) == (1, 2)


def test_future_unsupported_direct_blocker_has_diagnostic_and_no_count_objective():
    instance, blocker = _direct_blocker("cover")
    _class(blocker, "1.4S")
    plan = MovePlan(id="future.direct.unsupported", instance_id=instance.id, call_index=0, moves=[])
    with rule_freeze_v1():
        result = apply_plan(instance, plan)
        assert not result.legal
        assert result.count.future == []
        assert result.count.total_lifts_now == 0
        assert any(v.rule_id == "port.dg.direct_delivery" and v.call_index == 1
                   and v.containers == [blocker.id] and "unsupported" in v.message.lower()
                   for v in result.violations)
        with pytest.raises(ValueError, match="no objective value.*port.dg.direct_delivery"):
            count_moves(instance, plan)


def test_future_refusal_is_reported_by_apply_plan_without_raising():
    instance = _future("0010182")
    _class(instance.containers[0], "7")
    with rule_freeze_v1():
        result = apply_plan(instance, MovePlan(id="future.refused", instance_id=instance.id,
                                              call_index=0, moves=[]))
    assert not result.legal
    assert any(v.rule_id == "port.dg.refused_class" and v.call_index == 1 for v in result.violations)
    assert result.count.total_lifts_now == 0


def _fixed_future(*, on_required_cover=False):
    instance = _future("0010102" if on_required_cover else "0010182")
    payload = instance.model_dump()
    vessel = payload["vessel"]
    for field in ("cells", "stacks"):
        vessel[f"fixed_load_{field}"] = [item for item in vessel[field] if item["id"][3:5] == "02"]
        vessel[field] = [item for item in vessel[field] if item["id"][3:5] == "01"]
    vessel["fixed_load_hatch_covers"] = [cover for cover in vessel["hatch_covers"]
                                        if cover["id"].endswith("-2")]
    vessel["hatch_covers"] = [cover for cover in vessel["hatch_covers"] if cover["id"].endswith("-1")]
    if on_required_cover:
        vessel["fixed_load_hatch_covers"][0]["id"] = "HC-02-1"
        for stack in vessel["fixed_load_stacks"]:
            stack["hatch_cover_id"] = "HC-02-1"
    boxes = [dict(cell_ids=[f"00102{tier}"], stack_id="00102D", tier=tier,
                  length_ft=20, height_m=2.591, gross_mass_kg=10000.0,
                  x_m=46.9, y_m=1.3, z_m=9.0 + index * 2.591 + 2.591 / 2)
             for index, tier in enumerate([82] if on_required_cover else [82, 84])]
    vessel["fixed_loads"] = [dict(kind="cargo", cargo_boxes=boxes,
                                  mass_kg=sum(box["gross_mass_kg"] for box in boxes),
                                  x_m=46.9, y_m=1.3,
                                  z_m=sum(box["z_m"] for box in boxes) / len(boxes))]
    return Instance.model_validate(payload)


@pytest.mark.parametrize("enabled", [False, True])
def test_future_fixed_background_crossing_is_unremovable(enabled):
    instance = _fixed_future()
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            with pytest.raises(IllegalMoveError) as caught:
                project_future_moves(instance, instance.onboard, from_call=0)
            assert caught.value.violation.rule_id == "port.e3.quay_crossing"
            assert caught.value.violation.call_index == 1
        else:
            assert project_future_moves(instance, instance.onboard, from_call=0)[0].restow_lifts == 0


def test_future_fixed_background_below_height_does_not_block_crossing():
    instance = _fixed_future()
    instance.rotation.calls[1].max_on_deck_tiers = 3
    with rule_freeze_v1():
        assert project_future_moves(instance, instance.onboard, from_call=0)[0].restow_lifts == 0


@pytest.mark.parametrize("enabled", [False, True])
def test_future_fixed_background_on_required_cover_is_unremovable(enabled):
    instance = _fixed_future(on_required_cover=True)
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            with pytest.raises(IllegalMoveError) as caught:
                project_future_moves(instance, instance.onboard, from_call=0)
            assert caught.value.violation.rule_id == "expert.moves.hatch_clear"
            assert caught.value.violation.call_index == 1
        else:
            assert project_future_moves(instance, instance.onboard, from_call=0)[0].hatch_cover_lifts == 2

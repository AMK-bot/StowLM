"""C5/A3/A4: primary-class port refusal and one-lift truck delivery."""
from __future__ import annotations

import pytest

from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import (
    Discharge, GateIn, GateOut, Load, Relocate, RestowOff, RestowOn, RestowShift,
)
from stowage.moves._port_rules import port_move_violation, refused_primary_class
from stowage.moves.engine import IllegalMoveError, apply_move, apply_plan, count_moves, initial_state
from stowage.rules.catalogue import rule_freeze_v1


def _instance(*, primary="1.4S", direct=True, onboard=False, refused=False):
    payload = build_instance().model_dump(mode="json")
    box = payload["containers"][1]  # 20-foot box, initially at B-001-01-01.
    box["dg"] = dict(un_number="0004", imdg_class=primary, stowage_category="01")
    box["direct_delivery"] = direct
    payload["port"]["port_rules"] = [
        dict(rule_id="port.dg.direct_delivery", params={"classes": ["1"]}),
        *([dict(rule_id="port.dg.refused_class", params={"classes": ["7"]})] if refused else []),
    ]
    if direct or onboard:
        payload["yard"]["stacks"] = [s for s in payload["yard"]["stacks"] if box["id"] not in s["boxes"]]
    if onboard:
        payload["load_list"].remove(box["id"])
        payload["onboard"]["placements"] = [dict(slot="0010182", container_id=box["id"])]
        box["pod"] = payload["port"]["locode"]
        payload["discharge_list"] = [box["id"]]
    return Instance.model_validate(payload), box["id"]


def _plan(instance, *moves):
    return MovePlan(id="port.dg.test", instance_id=instance.id, call_index=0, moves=list(moves))


def _illegal(instance, move, rule):
    state = initial_state(instance)
    before = state.model_dump_json()
    with pytest.raises(IllegalMoveError) as error:
        apply_move(instance, state, move, move_index=4)
    assert error.value.violation.rule_id == rule
    assert error.value.violation.move_index == 4
    assert state.model_dump_json() == before


@pytest.mark.parametrize("onboard", [False, True])
def test_direct_delivery_productive_lift_has_no_yard_rehandle(onboard):
    with rule_freeze_v1(enabled=True):
        instance, cid = _instance(onboard=onboard)
        move = (Discharge(call_index=0, container_id=cid, src="0010182", dst="truck") if onboard
                else Load(call_index=0, container_id=cid, src="truck", dst="0010182"))
        plan = _plan(instance, move)
        result = apply_plan(instance, plan)
        assert result.legal, result.violations
        assert result.count.total_lifts_now == 1
        assert result.count.relocations == result.count.restow_lifts == 0
        assert result.count.loads == int(not onboard)
        assert result.count.discharges == int(onboard)
        assert result.final_state.yard == instance.yard
        assert count_moves(instance, plan) == result.count
        assert all(f.restow_lifts == 0 for f in result.count.future)


def test_direct_load_cannot_be_repeated_from_an_ever_available_truck():
    with rule_freeze_v1(enabled=True):
        instance, cid = _instance()
        plan = _plan(instance,
                     Load(call_index=0, container_id=cid, src="truck", dst="0010182"),
                     Load(call_index=0, container_id=cid, src="truck", dst="0030182"))
        result = apply_plan(instance, plan)
        assert not result.legal
        assert result.count.total_lifts_now == result.count.loads == 1
        assert len(result.final_state.ship.placements) == 1


@pytest.mark.parametrize("kind", ["load", "discharge", "relocate", "gate_in", "gate_out"])
def test_direct_delivery_forbids_every_yard_endpoint(kind):
    with rule_freeze_v1(enabled=True):
        instance, cid = _instance(onboard=kind == "discharge")
        move = {
            "load": lambda: Load(call_index=0, container_id=cid, src="B-001-01-01", dst="0010182"),
            "discharge": lambda: Discharge(call_index=0, container_id=cid, src="0010182", dst="B-002-01-01"),
            "relocate": lambda: Relocate(call_index=0, container_id=cid, src="B-001-01-01", dst="B-002-01-01"),
            "gate_in": lambda: GateIn(call_index=0, container_id=cid, dst="B-002-01-01"),
            "gate_out": lambda: GateOut(call_index=0, container_id=cid, src="B-001-01-01"),
        }[kind]()
        _illegal(instance, move, "port.dg.direct_delivery")


@pytest.mark.parametrize("onboard", [False, True])
def test_truck_endpoint_requires_direct_delivery(onboard):
    with rule_freeze_v1(enabled=True):
        instance, cid = _instance(primary="3", direct=False, onboard=onboard)
        move = (Discharge(call_index=0, container_id=cid, src="0010182", dst="truck") if onboard
                else Load(call_index=0, container_id=cid, src="truck", dst="0010182"))
        _illegal(instance, move, "port.dg.direct_delivery")


@pytest.mark.parametrize("kind", ["restow_off", "restow_on"])
def test_direct_delivery_cannot_enter_or_leave_quay_storage(kind):
    with rule_freeze_v1(enabled=True):
        instance, cid = _instance(onboard=True)
        move = (RestowOff(call_index=0, container_id=cid, src="0010182") if kind == "restow_off"
                else RestowOn(call_index=0, container_id=cid, dst="0030182"))
        _illegal(instance, move, "port.dg.direct_delivery")


@pytest.mark.parametrize("enabled", [False, True])
def test_activation_preserves_legacy_class_one_yard_load(enabled):
    with rule_freeze_v1(enabled=enabled):
        instance, cid = _instance(direct=enabled)
        move = Load(call_index=0, container_id=cid, src="B-001-01-01", dst="0010182")
        if enabled:
            _illegal(instance, move, "port.dg.direct_delivery")
        else:
            assert apply_plan(instance, _plan(instance, move)).legal


@pytest.mark.parametrize("enabled", [False, True])
def test_refused_class_blocks_current_load_only_when_activated(enabled):
    with rule_freeze_v1(enabled=enabled):
        instance, cid = _instance(primary="7", direct=False, refused=True)
        move = Load(call_index=0, container_id=cid, src="B-001-01-01", dst="0010182")
        if enabled:
            _illegal(instance, move, "port.dg.refused_class")
        else:
            assert apply_plan(instance, _plan(instance, move)).legal


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("kind", ["discharge", "restow_off", "restow_on", "restow_shift"])
def test_refusal_applies_to_every_ship_handling_operation(enabled, kind):
    with rule_freeze_v1(enabled=enabled):
        instance, cid = _instance(primary="7", direct=False, onboard=True, refused=True)
        move = {
            "discharge": lambda: Discharge(call_index=0, container_id=cid, src="0010182", dst="B-002-01-01"),
            "restow_off": lambda: RestowOff(call_index=0, container_id=cid, src="0010182"),
            "restow_on": lambda: RestowOn(call_index=0, container_id=cid, dst="0030182"),
            "restow_shift": lambda: RestowShift(call_index=0, container_id=cid, src="0010182", dst="0030182"),
        }[kind]()
        if enabled:
            _illegal(instance, move, "port.dg.refused_class")
        else:
            assert port_move_violation(instance, move) is None


@pytest.mark.parametrize("secondary", ["subsidiary", "additional"])
def test_subsidiary_or_additional_class_does_not_trigger_c5(secondary):
    with rule_freeze_v1(enabled=True):
        instance, cid = _instance(primary="3", direct=False, refused=True)
        box = next(box for box in instance.containers if box.id == cid)
        if secondary == "subsidiary":
            box.dg.subsidiary_risks = ["1", "7"]
        else:
            box.dg.additional = [box.dg.model_copy(update={"imdg_class": "7"}),
                                 box.dg.model_copy(update={"imdg_class": "1.4S"})]
        assert not refused_primary_class(instance.port, box.dg.imdg_class)
        move = Load(call_index=0, container_id=cid, src="B-001-01-01", dst="0010182")
        assert apply_plan(instance, _plan(instance, move)).legal


@pytest.mark.parametrize("enabled", [False, True])
def test_later_destination_refusal_is_checked_at_pol(enabled):
    with rule_freeze_v1(enabled=enabled):
        instance, cid = _instance(primary="7", direct=False)
        payload = instance.model_dump(mode="json")
        payload["rotation"]["calls"].append(dict(locode="THLCH", index=3, archetype="laem_chabang_like"))
        next(box for box in payload["containers"] if box["id"] == cid)["pod"] = "THLCH"
        instance = Instance.model_validate(payload)
        move = Load(call_index=0, container_id=cid, src="B-001-01-01", dst="0010182")
        if enabled:
            _illegal(instance, move, "port.dg.refused_class")
        else:
            assert apply_plan(instance, _plan(instance, move)).legal


def test_refusal_does_not_apply_to_unhandled_through_cargo():
    with rule_freeze_v1(enabled=True):
        instance, cid = _instance(primary="7", direct=False, refused=True)
        payload = instance.model_dump(mode="json")
        payload["load_list"].remove(cid)
        payload["yard"]["stacks"] = [s for s in payload["yard"]["stacks"] if cid not in s["boxes"]]
        payload["onboard"]["placements"] = [dict(slot="0010182", container_id=cid)]
        instance = Instance.model_validate(payload)
        assert apply_plan(instance, _plan(instance)).legal

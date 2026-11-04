"""Active crane sequencing must leave every open cover reachable."""
from dataclasses import asdict

import pytest

from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load
from stowage.model.ship import Placement, ShipState, WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves import apply_move
from stowage.ports import load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import ShipCall, simulate_port


def _scenario():
    instance = build_instance()
    instance.containers = instance.containers[2:4]
    for box in instance.containers:
        box.special_stowage = []
        box.oog = None
    instance.load_list = [box.id for box in instance.containers]
    instance.discharge_list = []
    instance.onboard = ShipState(vessel_id=instance.vessel.id)
    instance.yard.stacks = [YardStack(key=f"A-00{index + 1}-01", length_ft=20, boxes=[box.id])
                            for index, box in enumerate(instance.containers)]
    instance.port.max_crane_height_tiers = 2
    instance.port.quay_crane.max_on_deck_tiers = instance.port.yard_max_tiers = 2
    for equipment in instance.port.equipment:
        equipment.stack_tiers = 2
    for block in instance.port.yard.blocks:
        block.max_tiers = 2
    for cell in instance.vessel.cells:
        cell.accepts_20 = cell.id in {"0010282", "0010284"}
    return instance


def _run(instance):
    instance = Instance.model_validate(instance.model_dump())
    call = ShipCall("cover-test", instance, 1000, 900,
                    {cid: 0 for cid in instance.load_list})
    result = simulate_port([call], load_simulator_params("laem_chabang_like"), seed=0, noise=0)
    state = WorldState(call_index=0, ship=instance.onboard,
                       yard=YardState(yard_id=instance.port.yard.id))
    for record in result.moves:
        state = apply_move(instance, state, record.move)
    assert state.ship == result.final_ships[call.id]
    return result


def _remove_far_deck(instance):
    removed = {stack.id for stack in instance.vessel.stacks if stack.on_deck and stack.row == 1}
    instance.vessel.stacks = [stack for stack in instance.vessel.stacks if stack.id not in removed]
    instance.vessel.cells = [cell for cell in instance.vessel.cells if cell.stack_id not in removed]
    instance.vessel.hatch_covers[0].deck_stack_ids = []


def test_incompatible_hold_candidates_never_open_a_cover():
    with rule_freeze_v1():
        instance = _scenario()
        _remove_far_deck(instance)
        result = _run(instance)
        assert not result.unserved_loads
        assert not result.final_ships["cover-test"].covers_off
        assert all(record.move.kind not in {"hatch_cover_on", "hatch_cover_off"}
                   for record in result.moves)


def test_open_far_cover_closes_before_near_stack_reaches_H():
    with rule_freeze_v1():
        instance = _scenario()
        _remove_far_deck(instance)
        bottom, pending = instance.load_list
        instance.load_list = [pending]
        instance.onboard.placements = [Placement(container_id=bottom, slot="0010282")]
        instance.onboard.covers_off = {"HC-02-1": "quay"}
        instance.yard.stacks = instance.yard.stacks[1:]
        result = _run(instance)
        assert not result.unserved_loads
        moves = [record.move for record in result.moves]
        closed_at = next(index for index, move in enumerate(moves)
                         if move.kind == "hatch_cover_on" and move.cover_id == "HC-02-1")
        loaded_at = next(index for index, move in enumerate(moves) if isinstance(move, Load))
        assert closed_at < loaded_at
        assert not result.final_ships["cover-test"].covers_off


def test_initially_trapped_cover_is_reported_without_a_crash():
    with rule_freeze_v1():
        instance = _scenario()
        instance.onboard.placements = [Placement(container_id=cid, slot=f"00102{82 + 2 * index}")
                                          for index, cid in enumerate(instance.load_list)]
        instance.load_list = []
        instance.yard.stacks = []
        instance.onboard.covers_off = {"HC-02-1": "quay"}
        result = _run(instance)
        assert result.final_ships["cover-test"].covers_off == {"HC-02-1": "quay"}
        assert any("HC-02-1" in diagnostic and "port.e3.quay_crossing" in diagnostic
                   for diagnostic in result.cover_diagnostics)


@pytest.mark.parametrize("escape", [None, "permanent_cargo", "above_H"])
def test_far_load_precedes_near_H_load_using_shared_pending_rows(monkeypatch, escape):
    import stowage.moves

    shared = stowage.moves.pending_lift_rows
    checked = []

    def record(*args, **kwargs):
        result = shared(*args, **kwargs)
        checked.append(result)
        return result

    monkeypatch.setattr(stowage.moves, "pending_lift_rows", record)
    with rule_freeze_v1():
        instance = _scenario()
        near, far = instance.containers
        bottom = build_instance().containers[0].model_copy(update={
            "length_ft": 20, "iso_type": "22G1", "height_m": 2.591,
            "reefer": False, "reefer_setpoint_c": None})
        instance.containers.append(bottom)
        instance.onboard.placements = [Placement(container_id=bottom.id, slot="0010282")]
        far.length_ft, far.iso_type = 40, "42G1"
        instance.yard.stacks[1].length_ft = 40
        for cell in instance.vessel.cells:
            cell.accepts_40 = cell.id in {"0010182", "0030182"}
            if escape == "permanent_cargo" and cell.id in {"0010282", "0030282"}:
                cell.accepts_40 = True
        if escape == "above_H":
            for sid in ("00102D", "00302D"):
                stack = next(stack for stack in instance.vessel.stacks if stack.id == sid)
                original = next(cell for cell in instance.vessel.cells if cell.id == stack.cell_ids[-1])
                high = original.model_copy(update={"id": original.id[:-2] + "86", "accepts_40": True,
                                                   "accepts_20": False,
                                                   "vcg_base_m": original.vcg_base_m + 2.591})
                instance.vessel.cells.append(high)
                stack.cell_ids.append(high.id)
        result = _run(instance)
        loaded = [record.move.container_id for record in result.moves if isinstance(record.move, Load)]
        assert loaded == [far.id, near.id]
        assert (1,) in checked
        assert not result.unserved_loads


@pytest.mark.parametrize("enabled", [False, True])
def test_cover_diagnostics_does_not_change_serialized_result_fields(enabled):
    with rule_freeze_v1(enabled=enabled):
        result = _run(_scenario())
        before = asdict(result)
        assert result.cover_diagnostics == []
        assert asdict(result) == before
        assert "cover_diagnostics" not in before


def test_unserved_far_export_does_not_keep_reachable_near_export_waiting():
    with rule_freeze_v1():
        instance = _scenario()
        near, far = instance.containers
        bottom = build_instance().containers[0].model_copy(update={
            "length_ft": 20, "iso_type": "22G1", "height_m": 2.591,
            "reefer": False, "reefer_setpoint_c": None})
        instance.containers.append(bottom)
        instance.onboard.placements = [Placement(container_id=bottom.id, slot="0010282")]
        far.length_ft, far.iso_type = 40, "42R1"
        far.reefer, far.reefer_setpoint_c = True, -18
        instance.yard.stacks[1].length_ft = 40
        for cell in instance.vessel.cells:
            cell.accepts_40 = cell.id in {"0010182", "0030182"}
        for block in instance.port.yard.blocks:
            block.reefer_rows = []
        result = _run(instance)
        assert result.unserved_loads == [far.id]
        assert [record.move.container_id for record in result.moves
                if isinstance(record.move, Load)] == [near.id]

"""Simulator gate lifts must never substitute for counted planning rehandles."""
import pytest

from stowage.bounds import lower_bound
from stowage.checker.full import check
from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import GateIn, GateOut, Load, Relocate
from stowage.moves import apply_move, apply_plan, count_moves, initial_state


def gate_shortcut():
    instance = build_instance()
    forty, a, b, c = [box.id for box in instance.containers]
    instance.yard.stacks = [
        instance.yard.stacks[0].model_copy(update={"key": "A-003-01", "length_ft": 20,
                                                   "boxes": [a, b, c]}),
        next(s for s in instance.yard.stacks if s.length_ft == 40),
    ]
    for box in instance.containers:
        box.pod = instance.rotation.calls[-1].locode
    instance = Instance.model_validate(instance.model_dump())
    moves = [
        GateOut(call_index=0, container_id=c, src="A-003-01-03"),
        GateIn(call_index=0, container_id=c, dst="A-004-01-01"),
        GateOut(call_index=0, container_id=b, src="A-003-01-02"),
        GateIn(call_index=0, container_id=b, dst="A-004-01-02"),
        Load(call_index=0, container_id=a, src="A-003-01-01", dst="0010182"),
        Load(call_index=0, container_id=b, src="A-004-01-02", dst="0030182"),
        Load(call_index=0, container_id=c, src="A-004-01-01", dst="0010282"),
        Load(call_index=0, container_id=forty, src="A-001-01-01", dst="0020184"),
    ]
    return instance, MovePlan(id="gate-shortcut", instance_id=instance.id, call_index=0, moves=moves)


def test_fully_loaded_gate_shortcut_is_rejected_by_plan_and_checker():
    instance, plan = gate_shortcut()
    # Individual simulator events remain physically valid, even though a planning
    # caller cannot use them to remove blockers without paying for relocation.
    state = initial_state(instance)
    for move in plan.moves:
        state = apply_move(instance, state, move)
    assert {p.container_id for p in state.ship.placements} == set(instance.load_list)
    replay = apply_plan(instance, plan)
    assert not replay.legal
    assert replay.executed == replay.count.gate_moves == 0
    assert replay.violations[0].move_index == 0
    assert "simulator" in replay.violations[0].message.lower()
    with pytest.raises(ValueError, match="illegal plan"):
        count_moves(instance, plan)
    report = check(instance, plan)
    assert not report.passed
    assert any(v.move_index == 0 and "simulator" in v.message.lower()
               for v in report.parts["moves"].violations)


def test_checker_moves_part_rejects_gate_shortcut():
    instance, plan = gate_shortcut()
    report = check(instance, plan)
    assert any(v.move_index == 0 and "simulator" in v.message.lower()
               for v in report.parts["moves"].violations)


def test_physically_valid_gate_in_is_still_simulator_only():
    instance, shortcut = gate_shortcut()
    cid = shortcut.moves[0].container_id
    next(s for s in instance.yard.stacks if cid in s.boxes).boxes.remove(cid)
    instance.load_list.remove(cid)
    instance = Instance.model_validate(instance.model_dump())
    arrival = shortcut.moves[1]
    state = initial_state(instance)
    before = state.model_dump_json()
    assert apply_move(instance, state, arrival) != state
    plan = shortcut.model_copy(update={"moves": [arrival]})
    result = apply_plan(instance, plan)
    assert not result.legal
    assert result.count.gate_moves == result.executed == 0
    assert result.final_state.model_dump_json() == before
    assert "simulator" in result.violations[0].message.lower()


@pytest.mark.parametrize("gate_index", [0, 1])
def test_gate_rejection_is_atomic_and_diagnostics_continue(gate_index):
    instance, shortcut = gate_shortcut()
    c = shortcut.moves[0].container_id
    valid = Relocate(call_index=0, container_id=c, src="A-003-01-03", dst="A-004-01-01")
    plan = shortcut.model_copy(update={"moves": [shortcut.moves[gate_index], valid]})
    result = apply_plan(instance, plan, stop_at_first=False)
    assert not result.legal
    assert result.executed == 2  # diagnostic contract counts processed moves
    assert result.count.relocations == 1
    assert result.count.gate_moves == 0
    assert len(result.violations) == 1
    assert result.violations[0].move_index == 0
    assert any(s.key == "A-004-01" and s.boxes == [c] for s in result.final_state.yard.stacks)


def test_real_relocations_keep_all_loads_and_charge_two_rehandles():
    instance, shortcut = gate_shortcut()
    c, b = shortcut.moves[0].container_id, shortcut.moves[2].container_id
    plan = shortcut.model_copy(update={"moves": [
        Relocate(call_index=0, container_id=c, src="A-003-01-03", dst="A-004-01-01"),
        Relocate(call_index=0, container_id=b, src="A-003-01-02", dst="A-004-01-02"),
        *shortcut.moves[4:],
    ]})
    replay = apply_plan(instance, plan)
    assert replay.legal, replay.violations
    assert replay.count.loads == 4
    assert replay.count.relocations == replay.count.weighted_nonproductive == 2
    assert replay.count.total_lifts_now == 6


def test_nonload_blocker_gate_exit_cannot_undercut_lower_bound():
    instance, shortcut = gate_shortcut()
    instance.load_list = [shortcut.moves[4].container_id]
    instance.objective.relocate = 2
    instance = Instance.model_validate(instance.model_dump())
    bound = lower_bound(instance)
    assert bound.relocations == 2
    assert bound.weighted == 4
    shortcut.moves = [shortcut.moves[0], shortcut.moves[2], shortcut.moves[4]]
    replay = apply_plan(instance, shortcut)
    assert not replay.legal
    assert replay.executed == 0
    with pytest.raises(ValueError, match="illegal plan"):
        count_moves(instance, shortcut)

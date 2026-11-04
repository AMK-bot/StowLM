"""Independent engine-only retrieval search; expansion never constructs its witnesses.

The finite search moves any top yard box to any of four non-overlapping ground
positions, then tries the target's complete load to a fixed bottom deck slot.
It explores states until a shortest complete retrieval is found (not a prefix).
This qualifies physical action representation, not full departure feasibility.
"""
from __future__ import annotations

from collections import Counter, deque
from itertools import permutations, product

import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import BoxSlotSpace, Place, Relocate as RelocateAction, box_moves
from stowage.model import Instance, MovePlan
from stowage.model.moves import Load, Relocate
from stowage.model.yard import YardStack
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1


def _case(length, layout, *, seed=71):
    """Layout is four stacks of box indexes, in ground-key order."""
    n_boxes = sum(map(len, layout))
    instance = tiny_instance(seed, n_load=1, n_other=n_boxes - 1, p40=0, max_tiers=2)
    instance.port = load_port("laem_chabang_like")
    block = instance.port.yard.blocks[0]
    instance.port.yard.blocks = [block]
    block.n_bays, block.n_rows, block.max_tiers = (2 if length == 20 else 4), 2, 2
    instance.port.yard_max_tiers = 2
    block.reefer_rows = []
    for equipment in instance.port.equipment:
        equipment.stack_tiers = 2
    bays = (1, 2) if length == 20 else (1, 3)
    ground = tuple(f"{block.id}-{bay:03d}-{row:02d}" for bay in bays for row in (1, 2))
    ids = [box.id for box in instance.containers]
    for box in instance.containers:
        box.length_ft = length
        box.iso_type = {20: "22G1", 40: "42G1", 45: "L2G1"}[length]
    instance.yard.yard_id = instance.port.yard.id
    instance.yard.stacks = [
        YardStack(key=key, length_ft=length, boxes=[ids[i] for i in indexes])
        for key, indexes in zip(ground, layout, strict=True) if indexes
    ]
    instance.id = f"independent_{length}_{'_'.join(''.join(map(str, s)) or 'x' for s in layout)}"
    return Instance.model_validate(instance.model_dump()), ground, ("0010182" if length == 20 else "0020182")


def _layouts():
    """All 96 ordered, labelled 3-box states in four capacity-two stacks."""
    layouts = set()
    for assignment in product(range(4), repeat=3):
        for ordering in permutations(range(3)):
            layout = tuple(tuple(i for i in ordering if assignment[i] == key) for key in range(4))
            if max(map(len, layout)) <= 2:
                layouts.add(layout)
    return sorted(layouts)


def _search(instance, ground, slot):
    """BFS using only model moves and apply_move; no action/expander heuristics.

    Every top box, including the target, may relocate. No search-depth cutoff is
    imposed: visited yard configurations exhaust this bounded ground domain.
    Descending destination order deliberately disagrees with normal tie breaks.
    """
    target = instance.load_list[0]
    start = initial_state(instance)
    queue = deque([(start, [])])
    seen = {_yard_key(start)}
    counts = Counter()
    while queue:
        state, prefix = queue.popleft()
        counts["searched_states"] += 1
        stacks = {s.key: s.boxes for s in state.yard.stacks}
        for key, boxes in sorted(stacks.items()):
            if boxes[-1] != target:
                continue
            move = Load(call_index=instance.call_index, container_id=target,
                        src=f"{key}-{len(boxes):02d}", dst=slot)
            counts["load_attempts"] += 1
            try:
                final = apply_move(instance, state, move)
            except IllegalMoveError as error:
                counts[f"rejected:{error.violation.rule_id}"] += 1
            else:
                assert any(p.container_id == target and p.slot == slot for p in final.ship.placements)
                return prefix + [move], counts
        for source, boxes in sorted(stacks.items()):
            for destination in reversed(ground):
                if destination == source:
                    continue
                move = Relocate(call_index=instance.call_index, container_id=boxes[-1],
                                src=f"{source}-{len(boxes):02d}",
                                dst=f"{destination}-{len(stacks.get(destination, [])) + 1:02d}")
                counts["relocation_attempts"] += 1
                try:
                    after = apply_move(instance, state, move)
                except IllegalMoveError as error:
                    counts[f"rejected:{error.violation.rule_id}"] += 1
                    continue
                counts["legal_relocation_edges"] += 1
                fingerprint = _yard_key(after)
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    queue.append((after, prefix + [move]))
    return None, counts


def _yard_key(state):
    return tuple((s.key, s.length_ft, tuple(s.boxes)) for s in state.yard.stacks)


def _compare(instance, slot, witness):
    """Distinguish default Place failure from loss of action-space representation."""
    counts, default_failures, representation_failures = Counter(), [], []
    foreign = MovePlan(id=f"{instance.id}.engine", instance_id=instance.id,
                       call_index=instance.call_index, moves=witness)
    accepted = apply_plan(instance, foreign)
    assert accepted.legal, (instance.id, accepted.violations)
    assert accepted.count.loads == 1
    assert accepted.count.relocations == len(witness) - 1
    assert accepted.count.total_lifts_now == accepted.executed == len(witness)
    assert not accepted.final_state.ship.covers_off
    assert not accepted.final_state.yard.quay_buffer
    assert {p.container_id for p in accepted.final_state.ship.placements} == set(instance.load_list)
    try:
        default = Expander(instance).step(Place(instance.load_list[0], slot))
    except ValueError as error:
        default_failures.append((instance.id, str(error), [m.model_dump() for m in witness]))
    else:
        counts["default_places"] += 1
        counts["nondefault_witnesses"] += default != witness
    actions = [RelocateAction(m.container_id, m.dst.rsplit("-", 1)[0])
               if m.kind == "relocate" else Place(m.container_id, m.dst) for m in witness]
    try:
        ex = Expander(instance)
        reproduced = [move for action in actions for move in ex.step(action)]
        assert reproduced == witness
        space = BoxSlotSpace()
        assert box_moves(space.expand(instance, space.from_plan(instance, foreign)).moves) == witness
    except (ValueError, AssertionError) as error:
        representation_failures.append((instance.id, str(error), [m.model_dump() for m in witness]))
    else:
        counts["represented_witnesses"] += 1
    return counts, default_failures, representation_failures


@pytest.mark.parametrize("length", [20, 40, 45])
def test_engine_only_search_agrees_on_complete_tiny_retrievals(length, record_property):
    counts, default_failures, representation_failures = Counter(), [], []
    layouts = _layouts()
    assert len(layouts) == 96
    # Four-box additions require both a vertical dig-out and crossing clearance.
    layouts += [((2, 3), (0, 1), (), ()), ((), (), (2, 3), (0, 1))]
    with rule_freeze_v1():
        for layout in layouts:
            instance, ground, slot = _case(length, layout)
            witness, searched = _search(instance, ground, slot)
            counts.update(searched)
            assert witness is not None, (instance.id, searched)
            counts["completed_loads"] += 1
            counts["witness_moves"] += len(witness)
            counts["witness_relocations"] += len(witness) - 1
            counts["max_retrieval_depth"] = max(counts["max_retrieval_depth"], len(witness))
            compared, defaults, representation = _compare(instance, slot, witness)
            counts.update(compared)
            default_failures.extend(defaults)
            representation_failures.extend(representation)
    record_property("independent_search", dict(counts))
    record_property("default_false_rejects", default_failures)
    record_property("unrepresentable_witnesses", representation_failures)
    print(f"independent length={length}: {dict(counts)}; default_false_rejects={default_failures}; "
          f"unrepresentable={representation_failures}")
    assert counts["completed_loads"] == counts["represented_witnesses"] == 98
    assert counts["legal_relocation_edges"] > 0 and counts["nondefault_witnesses"] > 0
    assert counts["max_retrieval_depth"] >= 3
    assert default_failures == [], f"engine witnesses refused by default Place: {default_failures}"
    assert representation_failures == [], f"engine witnesses lost by action space: {representation_failures}"


def test_independent_search_never_calls_expander(monkeypatch):
    instance, ground, slot = _case(20, ((2, 3), (0, 1), (), ()))

    def forbidden(*args, **kwargs):
        pytest.fail("independent search used the expander")

    for name in ("__init__", "step", "can_expand", "yard_target"):
        monkeypatch.setattr(Expander, name, forbidden)
    with rule_freeze_v1():
        witness, counts = _search(instance, ground, slot)
    assert witness is not None and len(witness) >= 3
    assert counts["legal_relocation_edges"] > 1


def test_agreement_harness_detects_default_false_reject_with_representable_witness(monkeypatch):
    instance, ground, slot = _case(20, ((), (0, 1), (), ()))
    original = Expander.step

    def too_strict(ex, action):
        if isinstance(action, Place) and any(action.box in stack[:-1] for stack in ex.yard.values()):
            raise ValueError("injected overly strict dig-out")
        return original(ex, action)

    with rule_freeze_v1():
        witness, _ = _search(instance, ground, slot)
        assert witness is not None
        monkeypatch.setattr(Expander, "step", too_strict)
        counts, default_failures, representation_failures = _compare(instance, slot, witness)
    assert len(default_failures) == 1
    assert "injected overly strict dig-out" in default_failures[0][1]
    assert counts["represented_witnesses"] == 1 and representation_failures == []

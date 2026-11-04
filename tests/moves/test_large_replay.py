"""A thousand distinct containers exercise index scaling, not a repeated small state."""
from time import perf_counter

from stowage.conventions import format_slot, make_container_id, stack_id
from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load
from stowage.moves import apply_plan


def thousand_box_plan():
    instance = build_instance()
    template_box = next(box for box in instance.containers if box.length_ft == 20)
    template_cell = instance.vessel.cells[0]
    template_stack = next(s for s in instance.vessel.stacks if s.on_deck)
    boxes, cells, stacks, yard, moves = [], [], [], [], []
    for index in range(1000):
        cid = make_container_id("QZXU", 10000 + index)
        bay, row = 2 * (index // 4) + 1, index % 4 + 1
        slot = format_slot(bay, row, 82)
        sid = stack_id(bay, row, True)
        boxes.append(template_box.model_copy(update={"id": cid}))
        cells.append(template_cell.model_copy(update={"id": slot, "stack_id": sid}))
        stacks.append(template_stack.model_copy(update={
            "id": sid, "bay": bay, "row": row, "cell_ids": [slot], "hatch_cover_id": None}))
        key = f"A-{index // 50 + 1:03d}-{index % 50 + 1:02d}"
        yard.append(dict(key=key, length_ft=20, boxes=[cid]))
        moves.append(Load(call_index=0, container_id=cid, src=key + "-01", dst=slot))
    payload = instance.model_dump()
    payload["containers"] = boxes
    payload["load_list"] = [box.id for box in boxes]
    payload["vessel"].update(cells=cells, stacks=stacks, hatch_covers=[], nominal_teu=1000)
    payload["yard"]["stacks"] = yard
    for block in payload["port"]["yard"]["blocks"]:
        block.update(n_bays=20, n_rows=50)
    instance = Instance.model_validate(payload)
    return instance, MovePlan(id="thousand", instance_id=instance.id, call_index=0, moves=moves)


def test_thousand_distinct_boxes_replay_records_timing(record_property):
    instance, plan = thousand_box_plan()
    start = perf_counter()
    result = apply_plan(instance, plan)
    elapsed = perf_counter() - start
    assert result.legal, result.violations
    assert result.count.loads == result.executed == len(result.final_state.ship.placements) == 1000
    assert not result.final_state.yard.stacks
    # Host load must not turn an otherwise correct replay into a test failure.
    record_property("replay_1000_distinct_boxes_ms", elapsed * 1000)

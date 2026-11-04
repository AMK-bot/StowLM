"""Compact deterministic, outcome-free input to every zero-shot proposer arm."""
from __future__ import annotations

from collections import Counter, defaultdict
import json

from experiments.mvp.strategy.headroom.layout import BlockModel, booking_category
from stowage.conventions import cells_of_slot, parse_yard


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _counts(values):
    return ",".join(f"{key}:{value}" for key, value in sorted(Counter(values).items())) or "-"


def summary(instance, model=None):
    """Summarize under the exact rule epoch used by the headroom evaluator."""
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=False):
        return _summary(instance, model)


def _summary(instance, model=None):
    """Public facts only; no source plans, search scores, per-box IDs or labels.

    Capacity is physical 20-foot footprint cells, not guaranteed usable slots.
    Available cells subtract retained through cargo after mandatory discharge.
    Yard spread records new load-list cargo by POD and yard block; buried_above
    counts all boxes above each such box, so one blocker can be counted twice.
    """
    model = model or BlockModel.from_instance(instance)
    boxes = {box.id: box for box in instance.containers}
    loads = set(instance.load_list)
    cargo = defaultdict(list)
    for cid in sorted(loads):
        cargo[booking_category(boxes[cid], instance)].append(boxes[cid])
    retained = set()
    retained_ports = defaultdict(Counter)
    discharged = set(instance.discharge_list)
    for placement in instance.onboard.placements:
        if placement.container_id in discharged:
            continue
        for cell in cells_of_slot(placement.slot):
            retained.add(cell)
            retained_ports[model.cell_to_block[cell]][boxes[placement.container_id].pod] += 1
    cells = {cell.id: cell for cell in instance.vessel.cells}
    calls = instance.rotation.calls[instance.call_index:]
    rotation = ">".join(call.locode for call in calls)
    lines = [
        f"CALL vessel={instance.vessel.id} size={instance.size_class} loads={len(loads)} rotation={rotation}",
        "CARGO POD/class n=boxes f=footprint-cells len=feet:boxes rf=reefers dg=DG oog=OOG kg=min:max:sum",
    ]
    for (pod, weight), group in sorted(cargo.items()):
        masses = [box.gross_mass_kg for box in group]
        lines.append(
            f"{pod}/{weight} n={len(group)} f={sum(1 if b.length_ft == 20 else 2 for b in group)} "
            f"len={_counts(b.length_ft for b in group)} rf={sum(bool(b.reefer) for b in group)} "
            f"dg={sum(b.dg is not None for b in group)} oog={sum(b.oog is not None for b in group)} "
            f"kg={min(masses):g}:{max(masses):g}:{sum(masses):g}"
        )
    lines.append("BLOCK cap/free=20ft-cells after discharge accept20/40/45=free accepting cells plugs=free plugged cells retained=POD:cells")
    for block in sorted(model.blocks, key=lambda item: item.id):
        free = [cells[cid] for cid in block.cell_ids if cid not in retained]
        accepts = "/".join(str(sum(bool(getattr(cell, f"accepts_{length}")) for cell in free)) for length in (20, 40, 45))
        ports = ",".join(f"{pod}:{n}" for pod, n in sorted(retained_ports[block.id].items())) or "-"
        lines.append(f"{block.id} cap={len(block.cell_ids)} free={len(free)} accept={accepts} "
                     f"plugs={sum(bool(cell.reefer_plug) for cell in free)} retained={ports}")
    yard = defaultdict(lambda: dict(n=0, stacks=set(), top=0, buried_above=0))
    located = set()
    for stack in instance.yard.stacks:
        yard_block = parse_yard(stack.key + "-01").block
        for index, cid in enumerate(stack.boxes):
            if cid not in loads:
                continue
            located.add(cid)
            row = yard[boxes[cid].pod, yard_block]
            row["n"] += 1
            row["stacks"].add(stack.key)
            row["top"] += index == len(stack.boxes) - 1
            row["buried_above"] += len(stack.boxes) - 1 - index
    lines.append("YARD POD/block n=load-boxes s=distinct-stacks top=currently-accessible above=buried_above (not a relocation bound)")
    for (pod, block), row in sorted(yard.items()):
        lines.append(f"{pod}/{block} n={row['n']} s={len(row['stacks'])} top={row['top']} above={row['buried_above']}")
    buffer = set(instance.yard.quay_buffer)
    for pod in sorted({box.pod for group in cargo.values() for box in group}):
        other = [boxes[cid] for cid in sorted(loads - located) if boxes[cid].pod == pod]
        if other:
            lines.append(f"{pod}/off-stack buffer={sum(box.id in buffer for box in other)} "
                         f"direct={sum(bool(box.direct_delivery) for box in other)}")
    objective = instance.objective.model_dump() if hasattr(instance.objective, "model_dump") else vars(instance.objective)
    lines.extend([
        "RULES Permissions apply only to new load-list POD/D25-VGM-class bookings; cached weight labels are ignored.",
        "A block is its physical hatch cover crossed with hold/deck; fixed decks have fixed:<bay> IDs.",
        "20ft uses one cell; 40/45ft uses paired cells and needs permission in EVERY touched block. Permissions can overlap across categories.",
        "Through cargo/restoration is unrestricted. Capacity/free/accept counts are necessary information, not a packing or feasibility certificate.",
        "Software enforces length/reefer fit, bottom-up support, weight order, stack/cover/strength limits, DG/OOG, stability, ballast, terminal order and all other active rules.",
        "Minimize full-checker weighted nonproductive moves (yard relocations, restows and hatch lifts, including voyage projection); lower is better. Objective=" + _json(objective),
    ])
    if instance.rule_overrides:
        lines.append("RULE_OVERRIDES " + _json(instance.rule_overrides))
    if instance.plan_constraints:
        lines.append("PLAN_CONSTRAINTS " + _json([item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in instance.plan_constraints]))
    return "\n".join(lines) + "\n"


def build_prompt(instance, model=None):
    """The same zero-shot prompt, with no demonstrations or search feedback."""
    return (
        "Propose one block-permission layout for this ship call. Software fills slots, sequences moves, finishes and checks it.\n"
        + summary(instance, model)
        + "OUTPUT exactly LAYOUT 1 on the first line, then one row for EVERY CARGO POD/class:\n"
        + "POD/class = block-id,block-id\n"
        + "Use only listed block IDs; each row needs at least one block. No duplicate rows/blocks, wildcards, aliases, comments, prose, code fences, blank lines or extra spaces. "
        + "Use one space on either side of = and no spaces around commas. Rows/blocks may be in any order. One final newline is optional. "
        + "Return only this text. Invalid output wastes this proposal try. No repair or permission widening is allowed.\n"
    )

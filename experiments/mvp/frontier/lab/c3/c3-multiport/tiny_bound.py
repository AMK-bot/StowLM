"""Exhaustive global D15 relaxation for six 20-foot loads in six cells.

Every complete legal departure stow occurs among the 6! bijections. Relaxing
unary restrictions, yard/loading order, physics, and crane paths only enlarges
that family. We minimise graph_model's fixed-coordinate top/hatch closure over
the whole family, rather than claiming a fixed-stow certificate is map-wide.
No planner, replay, native checker, or numerical solver is called.
"""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from itertools import permutations
from math import factorial, isfinite, nextafter

from graph_model import assignment_bound


def _shape(instance):
    """Refuse partial occupancy, spanning loads, and unsupported geometries."""
    from stowage.conventions import cells_of_slot, parse_slot
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    if rule_freeze_v1_enabled():
        raise ValueError('tiny certificate is scoped to legacy D15 projection')
    loads = sorted(instance.load_list)
    cells = instance.vessel.cells
    if len(loads) != 6 or len(set(loads)) != 6 or len(cells) != 6:
        raise ValueError('requires exactly six distinct loads and six decision cells')
    if instance.onboard.placements or instance.onboard.covers_off:
        raise ValueError('requires empty arrival and initially closed covers')
    boxes = {box.id: box for box in instance.containers}
    if any(b not in boxes or boxes[b].length_ft != 20 for b in loads):
        raise ValueError('requires six single-cell 20-foot loads')
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    if len(stacks) != len(instance.vessel.stacks):
        raise ValueError('duplicate stack identifiers')
    slots, tiers = [], defaultdict(list)
    for cell in cells:
        if cells_of_slot(cell.id) != (cell.id,) or cell.stack_id not in stacks:
            raise ValueError('requires singleton cells with known stacks')
        slot, stack = parse_slot(cell.id), stacks[cell.stack_id]
        if slot.on_deck != stack.on_deck:
            raise ValueError('cell/stack deck mismatch')
        tiers[cell.stack_id].append(slot.tier)
        slots.append(cell.id)
    if len(set(slots)) != 6:
        raise ValueError('requires six distinct cells')
    for sid, levels in tiers.items():
        stack = stacks[sid]
        if set(stack.cell_ids) != {c.id for c in cells if c.stack_id == sid}:
            raise ValueError('partial stack catalog is outside certificate scope')
        levels.sort()
        # With every cell filled, this is exactly the no-floating shape check.
        # Physical heights and support capacities remain relaxed.
        if levels != list(range(levels[0], levels[0] + 2 * len(levels), 2)):
            raise ValueError('noncontiguous tiers are outside certificate scope')
        if not stack.on_deck and levels[0] != 2:
            raise ValueError('hold stack must start at tier 02')
        if stack.on_deck and levels[0] not in (80, 82):
            raise ValueError('deck stack must start at tier 80 or 82')
        if not stack.on_deck and stack.hatch_cover_id is None:
            raise ValueError('hold stack must have a hatch cover')
    future = {call.locode for call in instance.rotation.calls[instance.call_index + 1:]}
    if any(boxes[b].pod not in future for b in loads):
        raise ValueError('every load must have a future discharge call')
    covers = {cover.id for cover in instance.vessel.hatch_covers}
    if any(stack.hatch_cover_id is not None and stack.hatch_cover_id not in covers
           for sid, stack in stacks.items() if sid in tiers):
        raise ValueError('unknown hatch cover')
    used = {stacks[sid].hatch_cover_id for sid in tiers if not stacks[sid].on_deck}
    return loads, sorted(slots), 2 * len(used)


def _weight(value):
    if not isfinite(float(value)) or value < 0:
        raise ValueError('objective weights must be finite and nonnegative')
    return Fraction(str(value))


def _down_float(value):
    result = float(value)
    return nextafter(result, float('-inf')) if Fraction.from_float(result) > value else result


def tiny_global_bound(instance):
    """Return exhaustive restow and joint hatch/restow lower-bound certificates.

    The individual minima can have different witnesses; use the joint minimum
    when combining costs. The bound concerns D15's departure-coordinate
    projection, not an optimal adaptive future relocation policy. In legacy
    mode top/hatch closure is exact; crane crossings are a relaxation generally.
    """
    loads, slots, hatch_now = _shape(instance)
    w = instance.objective
    now_weight = _weight(w.hatch_cover)
    later_weight = _weight(w.future_hatch_cover)
    restow_weight = _weight(w.future_restow)
    if not isinstance(w.future_restow_lifts_per_box, int) or w.future_restow_lifts_per_box < 1:
        raise ValueError('future restow lifts per box must be a positive integer')
    best_restow = best_hatch = best_joint = None
    restow_witness = joint_witness = joint_parts = None
    enumerated = 0
    for ordering in permutations(loads):
        assignment = dict(zip(ordering, slots))
        bound = assignment_bound(instance, assignment)
        restow = bound['restow_lifts']
        hatch = bound['hatch_later']
        joint = hatch_now * now_weight + hatch * later_weight + restow * restow_weight
        enumerated += 1
        if best_restow is None or restow < best_restow:
            best_restow, restow_witness = restow, assignment
        best_hatch = hatch if best_hatch is None else min(best_hatch, hatch)
        if best_joint is None or joint < best_joint:
            best_joint, joint_witness = joint, assignment
            joint_parts = dict(hatch_now=hatch_now, hatch_later=hatch,
                               restow_later=restow, restowed_boxes=bound['restowed_boxes'])
    assert enumerated == factorial(6)
    return dict(
        scope='global six-cell complete-stow D15 relaxation; physics, unary, loading and crane paths relaxed',
        proved=True, enumeration_complete=True, assignments_enumerated=enumerated,
        assignments_expected=factorial(6), minimum_restow_lifts=best_restow,
        minimum_restowed_boxes=best_restow // w.future_restow_lifts_per_box,
        minimum_hatch_later=best_hatch, mandatory_hatch_now=hatch_now,
        minimum_joint_weighted=_down_float(best_joint),
        minimum_joint_weighted_exact=str(best_joint),
        restow_witness=restow_witness, joint_witness=joint_witness,
        joint_witness_parts=joint_parts,
        caution='separate minima need not share a witness; witnesses need not be physically legal')


if __name__ == '__main__':
    import argparse
    import json
    from stowage.io import load_instance

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance')
    args = parser.parse_args()
    print(json.dumps(tiny_global_bound(load_instance(args.instance)), indent=2))

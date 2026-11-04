"""Pure visual placement differences; never a checker or binding certificate."""
from __future__ import annotations

import hashlib
import json

from llm.instructions.apply import _class_matches, _dg_classes
from llm.instructions.schema import DgKeepAway
from stowage.conventions import cells_of_slot, parse_slot


def _signature(moves):
    payload = [move.model_dump(mode='json') for move in moves]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def final_positions(instance, plan):
    """Apply only ship membership/destinations in sequence, without legality claims.

    Initial onboard placements include eventual discharges until their discharge
    move occurs. Restow-off removes a placement, and restow-on/shift replaces it.
    Yard, gate, and cover moves do not alter ship placements.
    """
    known = {box.id for box in instance.containers}
    cells = {cell.id for cell in instance.vessel.cells}
    positions = {p.container_id: p.slot for p in instance.onboard.placements}
    diagnostics = []
    if plan.instance_id != instance.id:
        diagnostics.append('plan instance_id differs from instance')
    for index, move in enumerate(plan.moves):
        kind = move.kind
        if kind not in ('load', 'restow_on', 'restow_shift', 'discharge', 'restow_off'):
            continue
        bid = move.container_id
        if bid not in known:
            diagnostics.append(f'move {index}: unknown container {bid}')
            continue
        if kind in ('discharge', 'restow_off'):
            if bid not in positions:
                diagnostics.append(f'move {index}: {kind} container absent from ship: {bid}')
            positions.pop(bid, None)
            continue
        try:
            footprint = cells_of_slot(move.dst)
        except (ValueError, TypeError):
            diagnostics.append(f'move {index}: invalid ship destination for {bid}')
            continue
        if any(cell not in cells for cell in footprint):
            diagnostics.append(f'move {index}: destination outside vessel for {bid}')
            continue
        positions[bid] = move.dst
    return positions, diagnostics


def _bays(slot):
    return sorted({parse_slot(cell).bay for cell in cells_of_slot(slot)}) if slot is not None else []


def _row(box, before, after, *, intended_class):
    first, last = before.get(box.id), after.get(box.id)
    classes = sorted(_dg_classes(box))
    comparable = first is not None and last is not None
    first_bays, last_bays = _bays(first), _bays(last)
    delta = (parse_slot(last).bay-parse_slot(first).bay)/2 if comparable else None
    return dict(container_id=box.id, length_ft=box.length_ft, dg_classes=classes,
                matches_intended_dg=bool(intended_class is not None and _class_matches(intended_class, classes)),
                before_slot=first, after_slot=last, comparable=comparable,
                final_placement_changed=bool(comparable and first != last),
                physical_bay_changed=bool(comparable and first_bays != last_bays),
                before_physical_bays=first_bays, after_physical_bays=last_bays,
                signed_bay_displacement_20ft=delta,
                absolute_bay_displacement_20ft=abs(delta) if delta is not None else None)


def build_visual_delta(instance, before_plan, after_plan, intended_answer):
    """Compare typed plans and the independent intended answer, without solving.

    An even slot's footprint is its two odd physical bays. Bay displacement uses
    the address centre, in 20-foot bay pitches; it is not a distance in metres.
    A changed order, hatch lift, or ballast setting alone cannot establish visible
    loaded-box placement change. Full checker and semantic audits are separate.
    """
    boxes = {box.id: box for box in instance.containers}
    before, before_errors = final_positions(instance, before_plan)
    after, after_errors = final_positions(instance, after_plan)
    intended_class = intended_answer.imdg_class if isinstance(intended_answer, DgKeepAway) else None
    unknown_load = sorted(set(instance.load_list)-boxes.keys())
    loads = [_row(boxes[bid], before, after, intended_class=intended_class)
             for bid in sorted(set(instance.load_list)) if bid in boxes]
    retained_ids = {p.container_id for p in instance.onboard.placements}-set(instance.discharge_list)
    retained = [_row(boxes[bid], before, after, intended_class=intended_class)
                for bid in sorted(retained_ids) if bid in boxes]
    changed = [row for row in loads if row['final_placement_changed']]
    matched = [row for row in loads if row['matches_intended_dg']]
    matched_retained = [row for row in retained if row['matches_intended_dg']]
    matched_changed = [row for row in matched if row['final_placement_changed']]
    matched_bay_changed = [row for row in matched if row['physical_bay_changed']]
    first_signature, last_signature = _signature(before_plan.moves), _signature(after_plan.moves)
    first_container = _signature([m for m in before_plan.moves if hasattr(m, 'container_id')])
    last_container = _signature([m for m in after_plan.moves if hasattr(m, 'container_id')])
    missing_before = sorted(bid for bid in instance.load_list if bid not in before)
    missing_after = sorted(bid for bid in instance.load_list if bid not in after)
    before_bays = sorted({bay for row in matched for bay in row['before_physical_bays']})
    after_bays = sorted({bay for row in matched for bay in row['after_physical_bays']})
    load_before_bays = sorted({bay for row in loads for bay in row['before_physical_bays']})
    load_after_bays = sorted({bay for row in loads for bay in row['after_physical_bays']})
    diagnostics = dict(before=before_errors, after=after_errors, unknown_load_ids=unknown_load)
    candidate = bool(first_container != last_container and changed and not
                     (before_errors or after_errors or unknown_load or missing_before or missing_after))
    return dict(schema_version='hero-visual-delta/1', instance_id=instance.id,
                checker_called=False, legality_verified=False, instruction_compliance_verified=False,
                scope='placement arithmetic only; independent full checker and instruction audit required',
                before_move_signature=first_signature, after_move_signature=last_signature,
                move_signature_changed=first_signature != last_signature,
                before_container_move_signature=first_container, after_container_move_signature=last_container,
                container_move_signature_changed=first_container != last_container,
                signatures_changed=first_container != last_container,
                changed_load_placements_count=len(changed), changed_load_placements=changed,
                load_placements=loads, retained_placements=retained,
                load_before_physical_bays=load_before_bays, load_after_physical_bays=load_after_bays,
                load_before_distinct_bay_count=len(load_before_bays),
                load_after_distinct_bay_count=len(load_after_bays),
                load_total_bay_displacement_20ft=sum(row['absolute_bay_displacement_20ft'] or 0 for row in loads),
                missing_load_before=missing_before, missing_load_after=missing_after,
                intended_dg_class=intended_class, matched_dg_load_boxes=matched,
                matched_dg_retained_boxes=matched_retained,
                matched_dg_active_count=len(matched)+len(matched_retained),
                matched_dg_changed_load_placements_count=len(matched_changed),
                matched_dg_changed_physical_bay_count=len(matched_bay_changed),
                matched_dg_before_physical_bays=before_bays, matched_dg_after_physical_bays=after_bays,
                matched_dg_before_distinct_bay_count=len(before_bays),
                matched_dg_after_distinct_bay_count=len(after_bays),
                matched_dg_total_bay_displacement_20ft=sum(row['absolute_bay_displacement_20ft'] or 0 for row in matched),
                visible_binding_candidate=candidate, diagnostics=diagnostics)

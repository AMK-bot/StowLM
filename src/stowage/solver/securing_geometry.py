"""Cheap necessary support geometry from the checker's securing design.

The checker rejects 20-foot boxes above long boxes, mixed 20/long hold
columns, and paired supports whose actual roof heights differ by over 1e-6 m.
These facts depend on cargo geometry, not ballast. Testing them before search
expands a branch avoids layouts no ballast finisher can rescue. This is only
a necessary screen: the full checker still owns loads, GM, background evidence
and the final decision. It does not replace C1/C2 or D55 in the public mask.

Source: checker/lashing.py:_load_graph and checker/visibility.py:
_all_cargo_geometry; IRS-G-DES-19 §3.6.4.5 (mixed hold stacks require special
consideration). We apply exactly the checker's existing geometry/tolerance.
"""
from __future__ import annotations

from stowage.actions.space import Place
from stowage.conventions import cells_of_slot, parse_slot


def level_four_tiebreak(instance, ship) -> tuple[float, float]:
    """D11 preferences after move cost, using the checker's exact metrics.

    Missing soft-policy data is unknown (infinity), never a measured zero.
    Hard legality remains the feasibility engine's and full checker's decision.
    """
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    if not rule_freeze_v1_enabled():
        return 0., 0.
    from stowage.checker.rules_weight import weight_soft_metrics
    from stowage.checker.rules_special import killed_slots

    heavy = weight_soft_metrics(instance, ship).get("heavy_on_deck_count", float("inf"))
    return heavy, float(len(killed_slots(instance, ship)))


def securing_geometry_violation(instance, expander, action, *, include_existing=False) -> str | None:
    """Return a definite unsupported-placement reason, otherwise unknown/None.

    Call after ordinary oracle legality. Prefix screening is restricted to hold
    loads after every required discharge has left the ship: those lower supports
    are irreversible within the engine's Place/Discharge action set. Deck cargo
    may be restowed by later hold access, and discharge blockers may move, so
    those prefixes defer. ``include_existing=True`` instead checks one
    final-state placement; call it only after cargo completion. Missing geometry
    defers to the checker.
    """
    if not isinstance(action, Place):
        return None
    if not include_existing:
        if expander.where.get(action.box, (None,))[0] == "ship":
            return None
        if parse_slot(action.slot).on_deck:
            return None
        if any(expander.where.get(cid, (None,))[0] == "ship" for cid in instance.discharge_list):
            return None
    box = expander.boxes.get(action.box)
    if box is None:
        return None
    try:
        footprint = cells_of_slot(action.slot)
        targets = [expander.cells[cid] for cid in footprint]
        columns = [(expander.stacks[cell.stack_id], expander.cell_pos[cell.id][1])
                   for cell in targets]
    except (KeyError, ValueError):
        return None
    # Replay in the checker's (tier, slot) order. A long box has one bottom
    # (its first physical support) and sets *both* column tops to the same roof.
    # Independent column sums preserve small base offsets and can falsely
    # reject a later overlapping long slot even when the checker accepts it.
    tops = {sid: stack.base_z_m for sid, stack in expander.stacks.items()}
    try:
        before = (parse_slot(action.slot).tier, action.slot)
        records = sorted((parse_slot(slot).tier, slot, cid)
                         for cid, (location, slot) in expander.where.items()
                         if location == "ship" and cid != action.box)
        for tier, slot, cid in records:
            if (tier, slot) >= before:
                break
            lower_box = expander.boxes[cid]
            physical = [expander.cells[cell] for cell in cells_of_slot(slot)]
            bottom = tops[physical[0].stack_id]
            height = lower_box.height_m + (
                lower_box.oog.over_height_m if lower_box.oog else 0.0)
            top = bottom + height
            for cell in physical:
                tops[cell.stack_id] = top
        roofs = [tops[stack.id] for stack, _ in columns]
    except (KeyError, ValueError):
        return None
    parents = []
    for stack, at in columns:
        parent = expander.occ.get(stack.cell_ids[at - 1]) if at else None
        parents.append(expander.boxes.get(parent))
    supported = [parent for parent in parents if parent is not None]
    if box.length_ft == 20 and any(parent.length_ft != 20 for parent in supported):
        return "20-foot box over a 40/45-foot box"
    if not parse_slot(action.slot).on_deck and any(
            (box.length_ft == 20) != (parent.length_ft == 20) for parent in supported):
        return "mixed 20/long hold stacks require IRS special consideration"
    if len(roofs) == 2 and max(roofs) - min(roofs) > 1e-6:
        return "paired cargo supports must have equal actual height"
    return None


def securing_state_violations(instance, expander) -> tuple[str, ...]:
    """Definite geometry failures of a completed decision-cargo arrangement."""
    reasons = set()
    for cid, (location, slot) in expander.where.items():
        if location == "ship":
            reason = securing_geometry_violation(
                instance, expander, Place(cid, slot), include_existing=True)
            if reason is not None:
                reasons.add(reason)
    return tuple(sorted(reasons))

"""Decision evidence for retained-start contribution, independent of ancestry.

The caller must obtain a full-checker pass before treating a replayed plan as a
checked final plan. This module does not solve, alter, finish, or repair plans.
"""
from __future__ import annotations

MEASURE = 'load-list-box-slot-retention-v1'


def plan_decisions(instance, plan):
    """Replay actual moves and return departure box-to-slot choices.

    Encoded ``MovePlan.actions`` can be absent or stale, so it is never used as
    final-plan evidence. Fixed/onboard boxes are retained here; a contribution
    measure can explicitly choose its decision universe without losing evidence.
    """
    from stowage.moves import apply_plan

    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise ValueError('retained-decision evidence requires a legal final-plan replay')
    return dict(sorted((p.container_id, p.slot) for p in replay.final_state.ship.placements))


def decision_contribution(seed_origin, load_boxes, seed_decisions, final_decisions):
    """Logic review P0.1: retained exact box-slot pairs, divided by |load list|.

    This is decision evidence, not an H1 decision: unique retained pairs require
    the fresh none arm, and presence additionally requires the correct origin.
    """
    boxes = sorted(load_boxes)
    if len(boxes) != len(set(boxes)):
        raise ValueError('load-list boxes must be distinct')
    seed = {box: seed_decisions[box] for box in boxes if box in seed_decisions}
    final = {box: final_decisions[box] for box in boxes if box in final_decisions}
    retained = [dict(box=box, slot=seed[box]) for box in boxes
                if box in seed and box in final and final[box] == seed[box]]
    return dict(measure=MEASURE, seed_origin=seed_origin, load_boxes=boxes,
                seed_decisions=seed, final_decisions=final, retained_pairs=retained,
                retained_count=len(retained), total_load_boxes=len(boxes),
                retained_fraction=len(retained)/len(boxes) if boxes else 0.)


def for_plan(instance, plan, seed_origin, seed_decisions):
    """Build retained-decision evidence from the actual checked final moves."""
    return decision_contribution(seed_origin, instance.load_list, seed_decisions,
                                 plan_decisions(instance, plan))

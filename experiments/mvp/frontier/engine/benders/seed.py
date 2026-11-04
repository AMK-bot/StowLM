# Copied from experiments/mvp/frontier/lab/c3/c2-benders/construct.py:_seed; engine pure-page guard added.
"""Bind an in-memory legal stow to complete pure page patterns."""
from .cuts import _pure_selection


def seed_patterns(model, plan):
    from stowage.moves import apply_plan
    from stowage.model import MovePlan
    if hasattr(plan, 'plan'):
        plan = plan.plan
    if isinstance(plan, dict):
        plan = MovePlan.model_validate(plan)
    replay = apply_plan(model.p.instance, plan)
    if not replay.legal:
        return None
    T = model.T
    assignments = {T.slot_index[q.slot]: T.box_index[q.container_id]
                   for q in replay.final_state.ship.placements
                   if q.container_id in model.p.instance.load_list}
    chosen, covered = [], set()
    for k, page in enumerate(model.pages):
        pairs = [(tier, assignments[s]) for tier, s in enumerate(page.slots) if s in assignments]
        if not pairs or any(b in covered for _, b in pairs):
            continue
        if [tier for tier, _ in pairs] != list(range(len(pairs))):
            continue
        boxes = tuple(b for _, b in pairs)
        model.add(k, boxes)
        if (k, boxes) in model.pool:
            chosen.append(model.pool[k, boxes])
            covered.update(boxes)
    return chosen if _pure_selection(model, chosen) and all(model.legal(model.pages[model.columns[k].page], model.columns[k].boxes) for k in chosen) else None


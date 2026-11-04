"""Time-capped exact repair windows, with original-instance admission checks.

Large neighbourhood search frees a related group and searches its alternatives
while retaining earlier commitments (Shaw, 1998, doi:10.1007/3-540-49481-2_30).
Here hatch, neighbouring-bay, POD and yard-block groups choose where to cut an
action sequence. The window includes its entire dependent suffix: moving one
support or yard retrieval can invalidate later actions outside the named group.
Tiny manifests also free the whole plan, so their first greedy commitments do
not constrain the exact yardstick. The public 4.6 A* solver searches each window
with one worker and its own hard deadline. A* prioritizes paid cost plus a lower
bound on remaining cost (Hart et al., 1968, doi:10.1109/TSSC.1968.300136).

Residual snapshots are search inputs, not new physics: their inherited-box
context can differ from the original call. Consequently every stitched result
must replay through the ORIGINAL public feasibility engine, exact move engine
and full checker. Only strictly cheaper original D11 costs are accepted. No
residual optimality certificate is promoted to a global optimality claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.conventions import cells_of_slot, parse_slot
from stowage.moves import apply_move, apply_plan, initial_state
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.solver.securing_geometry import level_four_tiebreak


@dataclass(frozen=True)
class ExactWindow:
    kind: str
    cut: int
    boxes: int


def exact_windows(instance, actions, *, max_boxes=16, whole_plan_boxes=16):
    """Return deterministic, distinct dependency-closed group windows.

    The whole-plan window is first on tiny/small manifests. Otherwise smaller
    suffixes come first, making a bounded exact call useful on larger ships.
    """
    actions = tuple(actions)
    boxes = {b.id: b for b in instance.containers}
    yards = {cid: s.key.split("-")[0] for s in instance.yard.stacks for cid in s.boxes}
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    first, members = {}, {}
    for index, action in enumerate(actions):
        groups = [("pod", boxes[action.box].pod), ("yard", yards.get(action.box, action.box))]
        if isinstance(action, Place):
            # A 40-foot action and its neighbouring 20-foot cells share a pair.
            bay = min(parse_slot(cell).bay for cell in cells_of_slot(action.slot))
            groups.append(("bay_pair", (bay - 1) // 4))
            for cell in cells_of_slot(action.slot):
                cover = stacks[cells[cell].stack_id].hatch_cover_id
                if cover is not None:
                    groups.append(("hatch", cover))
        for group in groups:
            first.setdefault(group, index)
            members.setdefault(group, []).append(index)
    windows, seen = [], set()
    if len(set(a.box for a in actions)) <= whole_plan_boxes and actions:
        windows.append(ExactWindow("whole_plan", 0, len(set(a.box for a in actions))))
        seen.add(0)
    # If a whole group has too many downstream dependencies, free its latest
    # bounded retrieval segment. This still closes the complete suffix; no
    # subsequent action outside that segment is incorrectly treated as fixed.
    cuts = []
    for group, cut in first.items():
        if len(set(a.box for a in actions[cut:])) > max_boxes:
            cut = next((i for i in members[group]
                        if len(set(a.box for a in actions[i:])) <= max_boxes), cut)
        cuts.append((group[0], cut))
    candidates = sorted((len(set(a.box for a in actions[cut:])), -cut, kind)
                        for kind, cut in cuts)
    for count, negative_cut, kind in candidates:
        cut = -negative_cut
        if cut not in seen and count <= max_boxes:
            windows.append(ExactWindow(kind, cut, count))
            seen.add(cut)
    return windows


def _improves(instance, value, ship, previous, previous_ship):
    """A soft preference can break equal lift costs, never buy extra moves."""
    if rule_freeze_v1_enabled():
        return (value, *level_four_tiebreak(instance, ship)) < (
            previous, *level_four_tiebreak(instance, previous_ship))
    return value < previous - 1e-9


def repair_exact_window(instance, plan, window, *, time_limit_s, max_nodes,
                        seed, feasibility_config, engine_factory):
    """Return (cheaper checker-passing plan or None, structured diagnostics).

    The caller bounds total LNS time; the exact public API enforces its own
    hard per-window budget. Native validation calls after it are indivisible.
    """
    from stowage.checker import check
    from stowage.solver.exact import solve_exact

    started, space = monotonic(), get_action_space("boxslot")
    actions = tuple(space.from_plan(instance, plan))
    prefix = actions[:window.cut]
    diagnostic = {"kind": window.kind, "cut": window.cut, "boxes": window.boxes,
                  "budget_s": time_limit_s, "accepted": False, "num_workers": 1}
    state, ex = initial_state(instance), Expander(instance)
    for action in prefix:
        for move in ex.step(action):
            state = apply_move(instance, state, move)
    aboard = {p.container_id for p in state.ship.placements}
    residual = instance if not prefix else instance.model_copy(update={
        "yard": state.yard, "onboard": state.ship,
        "load_list": sorted(set(instance.load_list) - aboard),
        "discharge_list": sorted(set(instance.discharge_list) & aboard)})
    remaining = time_limit_s - (monotonic() - started)
    if remaining <= 0:
        diagnostic.update(status="TIME_LIMIT", elapsed_s=monotonic()-started)
        return None, diagnostic
    outcome = solve_exact(residual, time_limit_s=remaining, max_nodes=max_nodes,
                          seed=seed, num_workers=1, feasibility_config=feasibility_config)
    diagnostic.update(status=outcome.status, exact_objective=outcome.objective,
                      exact_lower_bound=outcome.lower_bound, expanded_nodes=outcome.expanded_nodes)
    if outcome.plan is None:
        diagnostic["elapsed_s"] = monotonic()-started
        return None, diagnostic
    candidate = space.expand(instance, (*prefix, *space.from_plan(residual, outcome.plan)))
    candidate = candidate.model_copy(update={"ballast": outcome.plan.ballast,
        "provenance": candidate.provenance.merge(plan.provenance, outcome.plan.provenance)})
    replay = apply_plan(instance, candidate)
    baseline = apply_plan(instance, plan)
    diagnostic.update(objective_before=baseline.count.weighted_nonproductive,
                      candidate_objective=replay.count.weighted_nonproductive)
    if not replay.legal or not baseline.legal:
        diagnostic["rejection"] = "original move replay"
    elif not _improves(instance, replay.count.weighted_nonproductive, replay.final_state.ship,
                       baseline.count.weighted_nonproductive, baseline.final_state.ship):
        diagnostic["rejection"] = "not strictly better"
    else:
        engine = engine_factory()
        try:
            for action in space.from_plan(instance, candidate):
                engine.push(action)
        except ValueError as exc:
            diagnostic.update(rejection="original feasibility replay", error=str(exc),
                              elapsed_s=monotonic()-started)
            return None, diagnostic
        if not engine.is_complete():
            diagnostic["rejection"] = "original manifest incomplete"
        else:
            diagnostic["original_mask_verified"] = True
            report = check(instance, candidate)
            diagnostic["original_checker_passed"] = report.passed
            if report.passed:
                if rule_freeze_v1_enabled():
                    diagnostic["level_four"] = list(level_four_tiebreak(instance, replay.final_state.ship))
                diagnostic.update(accepted=True, objective=replay.count.weighted_nonproductive,
                                  elapsed_s=monotonic()-started)
                return candidate, diagnostic
            diagnostic["rejection"] = "original full checker"
    diagnostic["elapsed_s"] = monotonic()-started
    return None, diagnostic

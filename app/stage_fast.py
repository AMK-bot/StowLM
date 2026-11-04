"""Lite replay for very large plans (the full-ship hero view), shaped like ``app.backend.build_replay``.

Why: ``build_replay`` materialises the full world state for every frame and recomputes the prefix move count with
``apply_plan`` per frame (quadratic). Measured 29 Oct on the 1 486-lift full-ship plan: ``apply_plan`` once = 0.07 s,
incremental engine stepping = 0.05 s, ``state()`` = 4 ms and ``project_future_moves`` = 3 ms per call, so the
per-frame path costs minutes while the stage page only needs, per lift, the move and the running lift counts.

What this does:
* legality: one public ``stowage.moves.apply_plan`` call (the engine's own verdict, stop at the first illegal lift);
  the step loop then walks the same engine (``Replay``, the class ``apply_plan`` uses) over the legal prefix and the
  final state is cross-checked against ``apply_plan``'s;
* counts: running current-call lift counts per kind exactly as ``apply_plan`` tallies them;
* projected later-port lifts (``project_future_moves``) only at ~``PROJECTION_KEYFRAMES`` evenly spaced frames plus
  the last one. APPROXIMATION: other steps carry ``future = null`` (the contract's ``projected_later_*`` are then
  null; the UI holds the last known value and must not present it as measured at that step).
* frames: full state only for frame 0 and the final frame; other frames have ``state = None``. Yard stack keys
  reached in between are listed in ``yard_keys`` for the geometry.
* checker: the full checker is run only when asked (``run_checker=True``); otherwise the payload is the unbound one
  and the plan is NOT approved. Nothing here issues a verdict: ``app.backend.certify_replay`` is the same binding,
  validation, provenance and ``PlannerResult.shippable`` decision as the regular replay; only counts and frames differ.
"""
from __future__ import annotations

from typing import Any

from stowage.model import Instance, MovePlan, PlannerResult

LITE_MIN_LIFTS = 600          # create_server switches to the lite replay above this many lifts
PROJECTION_KEYFRAMES = 40
_FIELDS = {"load": "loads", "discharge": "discharges", "relocate": "relocations", "restow_shift": "restow_lifts",
           "restow_off": "restow_lifts", "restow_on": "restow_lifts", "hatch_cover_off": "hatch_cover_lifts",
           "hatch_cover_on": "hatch_cover_lifts", "gate_in": "gate_moves", "gate_out": "gate_moves"}


def _count_dump(instance: Instance, counts: dict, future) -> dict:
    w = instance.objective
    out = dict(counts)
    if future is None:
        out.update(future=None, weighted_nonproductive=None)
    else:
        out["future"] = [f.model_dump(mode="json") for f in future]
        out["weighted_nonproductive"] = (w.relocate * counts["relocations"] + w.restow * counts["restow_lifts"]
                                         + w.hatch_cover * counts["hatch_cover_lifts"]
                                         + w.future_restow * sum(f.restow_lifts for f in future)
                                         + w.future_hatch_cover * sum(f.hatch_cover_lifts for f in future))
    out["total_lifts_now"] = sum(counts[k] for k in ("loads", "discharges", "relocations", "restow_lifts",
                                                     "hatch_cover_lifts"))
    return out


def build_lite_replay(instance: Instance, plan: MovePlan, *, planner_result: PlannerResult | None = None,
                      run_checker: bool = False) -> dict[str, Any]:
    from stowage.moves import IllegalMoveError, apply_plan, initial_state, project_future_moves
    from stowage.moves._physical import Replay
    from app.backend import certify_replay, require_plan_binding, vessel_display_label
    from app import check_cache
    from app.checker import build_checker_payload, unbound_payload

    require_plan_binding(instance, plan, planner_result)
    result = apply_plan(instance, plan)
    n = result.executed
    keyframes = {round(i * n / PROJECTION_KEYFRAMES) for i in range(PROJECTION_KEYFRAMES + 1)} | {0, n}
    start = initial_state(instance)
    replay = Replay(instance, start)
    counts = {k: 0 for k in ("loads", "discharges", "relocations", "restow_lifts", "hatch_cover_lifts", "gate_moves")}
    yard_keys = {s.key: s.length_ft for s in start.yard.stacks}
    lengths = {c.id: c.length_ft for c in instance.containers}

    def future_at(state):
        try:
            return project_future_moves(instance, state.ship, from_call=instance.call_index)
        except IllegalMoveError:
            return []  # as apply_plan: the rejection is in result.violations

    frames = [{"state": start.model_dump(mode="json"), "move": None,
               "counts": _count_dump(instance, counts, future_at(start)), "highlight": None}]
    for index, move in enumerate(plan.moves[:n]):
        replay.move(move, move_index=index)
        counts[_FIELDS[move.kind]] += 1
        if move.kind == "relocate" and isinstance(move.dst, str):
            # Relocation targets are yard addresses (BLOCK-BBB-RR-TT); geometry lists stacks (BLOCK-BBB-RR).
            yard_keys.setdefault(_stack_key(move.dst), lengths.get(move.container_id))
        k = index + 1
        state = replay.state() if k in keyframes else None
        frames.append({"state": None, "move": move.model_dump(mode="json"),
                       "counts": _count_dump(instance, counts, future_at(state) if state is not None else None),
                       "highlight": None})
    final = replay.state()
    if final.model_dump(mode="json") != result.final_state.model_dump(mode="json"):
        raise ValueError("lite replay diverged from apply_plan's final state")  # never show an unverified state
    frames[-1]["state"] = final.model_dump(mode="json")
    # apply_plan's own count (executed lifts + projection on the reached ship) is authoritative for the last frame.
    frames[-1]["counts"] = {**result.count.model_dump(mode="json"), "total_lifts_now": result.count.total_lifts_now}
    checker = (build_checker_payload(instance, plan, evaluated_frame=n) if run_checker else
               unbound_payload(n, "full checker not run (lite replay)"))
    checker, candidate, shipping = certify_replay(instance, plan, planner_result, checker, n)
    checker["check_source"] = check_cache.last_source() if run_checker else None
    loaded = {p.container_id for p in final.ship.placements}
    shut_out = sorted(set(instance.load_list) - loaded)
    # Both already block through the full report; kept as explicit reasons, they can only narrow the approval.
    extra = []
    if not result.legal:
        extra.append(f"engine replay stopped at lift {n + 1} of {len(plan.moves)}"
                     if n < len(plan.moves) else "plan leaves departure conditions unmet")
    if shut_out:
        extra.append(f"{len(shut_out)} load-list boxes not aboard")
    approved = bool(shipping["shippable"] and not extra)
    blockers = [*shipping["shipping_blockers"], *extra]
    if not approved and not blockers:
        blockers = ["Full clean shipping evidence is incomplete"]
    notice = ("Lite replay (large plan): every lift replayed by the move engine; "
              + ("full checker passed." if approved else "; ".join(blockers) + ". Not approved."))
    metadata = {**shipping, "vessel_label": vessel_display_label(instance),
                "label": planner_result.planner if planner_result else "Supplied plan",
                "planner": planner_result.planner if planner_result else None,
                "shippable": approved, "departure_approved": approved, "physical_replay_legal": result.legal,
                "shipping_blockers": blockers, "planned_moves": len(plan.moves),
                "executed_moves": n, "shut_out": shut_out, "notice": notice, "lite_replay": True,
                "projection_keyframes": sorted(keyframes)}
    return {"instance": instance.model_dump(mode="json"), "plan": plan.model_dump(mode="json"), "frames": frames,
            "planner_result": candidate.model_dump(mode="json"),
            "checker": checker, "metadata": metadata, "yard_keys": yard_keys,
            "violations": [v.model_dump(mode="json") for v in result.violations]}


def _stack_key(address: str) -> str:
    """Stack key of a yard address (``A1-012-03-02`` -> ``A1-012-03``); a stack key is returned unchanged."""
    from stowage.conventions import parse_yard, yard_stack_key
    try:
        a = parse_yard(address)
    except ValueError:
        return address
    return yard_stack_key(a.block, a.bay, a.row)

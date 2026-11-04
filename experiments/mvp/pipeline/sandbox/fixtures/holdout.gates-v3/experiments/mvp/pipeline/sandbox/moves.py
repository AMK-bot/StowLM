"""Pure drag proposals, preliminary screens and checker-gated move verdicts."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Callable

from experiments.mvp.pipeline.contract.schema import MoveVerdict
from stowage.conventions import cells_of_slot, parse_slot
from stowage.model import Instance, MovePlan, ShipState
from stowage.model.moves import RestowOff, RestowOn, RestowShift
from stowage.moves import apply_plan


@dataclass
class PreparedMove:
    instance: Instance
    base_plan: MovePlan
    plan: MovePlan
    ship: ShipState
    baseline: object
    replay: object
    box_id: str
    target_slot: str
    swapped_box: str | None


def violation_to_dict(violation) -> dict:
    """Keep the checker's values, including null for nonnumeric rules."""
    row = violation.model_dump(mode="json") if hasattr(violation, "model_dump") else dict(violation)
    return {"rule_id": row["rule_id"], "message": row["message"],
            "measured": row.get("measured"), "limit": row.get("limit"),
            "boxes": row.get("containers", row.get("boxes", [])),
            "unit": row.get("unit"), "severity": row.get("severity", "hard"),
            "slots": row.get("slots", []), "move_index": row.get("move_index")}


def broken_from_report(report) -> list[dict]:
    rows = report.violations if hasattr(report, "violations") else report.get("violations", [])
    return _unique([violation_to_dict(v) for v in rows])


def _unique(rows: list[dict]) -> list[dict]:
    import json
    seen, result = set(), []
    for row in rows:
        key = json.dumps(row, sort_keys=True, allow_nan=False)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def prepare_move(instance, plan, box_id: str, target_slot: str) -> PreparedMove:
    """Build the requested final stow and a crane proposal without mutating inputs.

    Existing loading destinations are changed rather than running a planner. An
    occupied slot swaps its box to the dragged box's old slot. For arrival cargo,
    explicit shift/off/on operations are appended; replay decides accessibility.
    """
    instance = Instance.model_validate(instance) if isinstance(instance, dict) else instance
    plan = MovePlan.model_validate(plan) if isinstance(plan, dict) else plan
    if not isinstance(instance, Instance) or not isinstance(plan, MovePlan):
        raise ValueError("instance and plan must be model values or JSON objects")
    parse_slot(target_slot)
    baseline = apply_plan(instance, plan)
    if baseline.executed != len(plan.moves):
        raise ValueError("the starting plan must replay completely")
    placements = baseline.final_state.ship.placements
    source = next((p for p in placements if p.container_id == box_id), None)
    if source is None:
        raise ValueError(f"box {box_id!r} is not stowed in the starting plan")
    # A long box may be addressed by its even bay while the clicked cell is odd.
    target_cells = set(cells_of_slot(target_slot))
    occupants = [p for p in placements if p.container_id != box_id
                 and set(cells_of_slot(p.slot)) & target_cells]
    if len(occupants) > 1:
        # The target remains a diagnostic proposal. Moving one box cannot swap
        # two independent occupants; the static checker reports the overlap.
        occupant = next((p for p in occupants if p.slot == target_slot), None)
    else:
        occupant = occupants[0] if occupants else None
    mapping = {box_id: (source.slot, target_slot)}
    if occupant:
        mapping[occupant.container_id] = (occupant.slot, source.slot)
    desired = [p.model_copy(update={"slot": mapping[p.container_id][1]})
               if p.container_id in mapping else p.model_copy(deep=True) for p in placements]
    ship = baseline.final_state.ship.model_copy(deep=True, update={"placements": sorted(desired, key=lambda p: p.slot)})
    from stowage.checker.full import _ballast
    ship, _ = _ballast(instance, plan, ship)
    remapped, changed = [], set()
    # Remap only references to each box's final slot, preserving yard locations.
    for move in plan.moves:
        cid = getattr(move, "container_id", None)
        updates = {}
        if cid in mapping:
            old, new = mapping[cid]
            for side in ("src", "dst"):
                if getattr(move, side, None) == old:
                    updates[side] = new
                    if side == "dst":
                        changed.add(cid)
        remapped.append(move.model_copy(update=updates, deep=True))
    if source.slot != target_slot and not changed:
        if occupant:
            remapped.extend([
                RestowOff(call_index=plan.call_index, container_id=occupant.container_id, src=occupant.slot),
                RestowShift(call_index=plan.call_index, container_id=box_id, src=source.slot, dst=target_slot),
                RestowOn(call_index=plan.call_index, container_id=occupant.container_id, dst=source.slot),
            ])
        else:
            remapped.append(RestowShift(call_index=plan.call_index, container_id=box_id,
                                        src=source.slot, dst=target_slot))
    elif source.slot != target_slot:
        missing = [cid for cid in mapping if cid not in changed]
        remapped = [RestowOff(call_index=plan.call_index, container_id=cid, src=mapping[cid][0])
                    for cid in missing] + remapped
        remapped.extend(RestowOn(call_index=plan.call_index, container_id=cid, dst=mapping[cid][1])
                        for cid in missing)
    candidate = plan.model_copy(deep=True, update={"id": plan.id + ".sandbox", "moves": remapped,
                                                  "actions": None, "action_space": None})
    replay = apply_plan(instance, candidate)
    return PreparedMove(instance, plan, candidate, ship, baseline, replay,
                        box_id, target_slot, occupant.container_id if occupant else None)


def move_plan(instance, plan, box_id: str, target_slot: str) -> MovePlan:
    """Return the proposed plan; only a full passing verdict permits accepting it."""
    return prepare_move(instance, plan, box_id, target_slot).plan


def _counts(prepared: PreparedMove) -> dict:
    base, replay = prepared.baseline, prepared.replay
    complete = replay.legal and replay.executed == len(prepared.plan.moves)
    before, after = base.count, replay.count
    later = lambda count: sum(f.restow_lifts + f.hatch_cover_lifts for f in count.future)
    return {"extra_lifts": after.total_lifts_now - before.total_lifts_now if complete else None,
            "lift_counts": {"baseline_now": before.total_lifts_now,
                            "candidate_now": after.total_lifts_now if complete else None,
                            "projected_later_delta": later(after) - later(before) if complete else None,
                            "source": "stowage.moves.apply_plan",
                            "projection_note": "Fixed-stow later-port scenario; no future loads or certified future sequence."},
            "replay_legal": replay.legal, "replay_executed": replay.executed,
            "candidate_moves": len(prepared.plan.moves), "swapped_box": prepared.swapped_box}


def _frame_steps(gauges, prepared):
    gauges["n_lifts"] = len(prepared.plan.moves)
    for frame in gauges.get("frames", []):
        frame["step"] = len(prepared.plan.moves)


def _screen(prepared: PreparedMove, started: float) -> MoveVerdict:
    from .physics import fast_check
    broken, gauges = fast_check(prepared.instance, prepared.ship)
    broken = _unique([*broken, *[violation_to_dict(v) for v in prepared.replay.violations]])
    securing_clear = True
    visibility_clear = True
    if not any(v.get("severity", "hard") == "hard" for v in broken):
        from .securing import screen_securing
        securing_broken, securing = screen_securing(prepared.instance, prepared.ship)
        broken = _unique([*broken, *securing_broken])
        securing_clear = securing.get("passed") is True
        gauges["securing_screen"] = securing
        gauges["note"] = (gauges.get("note", "").replace("lashing, ", "") + " " + securing["note"]).strip()
        if securing_clear and not any(v.get("severity", "hard") == "hard" for v in broken):
            from .visibility import screen_visibility
            visibility_broken, visibility = screen_visibility(prepared.instance, prepared.ship)
            broken = _unique([*broken, *visibility_broken])
            visibility_clear = visibility.get("passed") is True
            gauges["visibility_screen"] = visibility
            gauges["note"] = (gauges["note"].replace("visibility or ", "").replace(
                "all other full-checker parts", "remaining full-checker parts") + " " + visibility["note"])
    counts = _counts(prepared)
    extra = counts.pop("extra_lifts")
    gauges.update(counts)
    gauges.update(label="fast screen, full check running", full_check_pending=True)
    _frame_steps(gauges, prepared)
    return MoveVerdict(legal=securing_clear and visibility_clear and not any(v.get("severity", "hard") == "hard" for v in broken),
                       screen="fast", broken=broken, gauges=gauges, extra_lifts=extra,
                       wall_s=perf_counter() - started)


def _invalid(error: Exception, screen: str, started: float) -> MoveVerdict:
    # Invalid UI input is not a made-up checker rule or a safety diagnosis.
    return MoveVerdict(legal=False, screen=screen, broken=[], extra_lifts=None,
                       gauges={"error": str(error), "input_error": True,
                               "label": "Invalid drag request", "full_check_pending": False},
                       wall_s=perf_counter() - started)


def fast_screen(instance, plan, box_id: str, target_slot: str) -> MoveVerdict:
    started = perf_counter()
    try:
        return _screen(prepare_move(instance, plan, box_id, target_slot), started)
    except (ValueError, TypeError) as error:
        return _invalid(error, "fast", started)


def _same_placements(left, right) -> bool:
    return {(p.container_id, p.slot) for p in left.placements} == {(p.container_id, p.slot) for p in right.placements}


def _snapshot(prepared: PreparedMove):
    """Full-check the requested stow if crane replay stopped before reaching it.

    This is diagnostic evidence only: the original proposal's replay failure is
    retained and forbids a legal verdict. Model copies intentionally allow
    illegal placement geometry to reach the independent static checker.
    """
    aboard = {p.container_id for p in prepared.ship.placements}
    yard = prepared.instance.yard.model_copy(deep=True)
    yard.stacks = [s.model_copy(update={"boxes": [cid for cid in s.boxes if cid not in aboard]})
                   for s in yard.stacks if any(cid not in aboard for cid in s.boxes)]
    yard.quay_buffer = [cid for cid in yard.quay_buffer if cid not in aboard]
    snapshot = prepared.instance.model_copy(deep=True, update={"onboard": prepared.ship,
                        "yard": yard, "load_list": [], "discharge_list": [], "stage": "A"})
    plan = prepared.plan.model_copy(update={"moves": [], "ballast": prepared.base_plan.ballast,
                                            "actions": None, "action_space": None})
    return snapshot, plan


def try_move(instance, plan, box_id: str, target_slot: str, *,
             on_fast: Callable[[MoveVerdict], None] | None = None) -> MoveVerdict:
    """Emit a labelled screen, then return the independent full checker verdict."""
    started = perf_counter()
    try:
        prepared = prepare_move(instance, plan, box_id, target_slot)
    except (ValueError, TypeError) as error:
        return _invalid(error, "full", started)
    fast = _screen(prepared, started)
    if on_fast:
        on_fast(fast)
    from stowage.checker import check
    from .physics import full_gauges
    reached = _same_placements(prepared.replay.final_state.ship, prepared.ship)
    if reached:
        report = check(prepared.instance, prepared.plan)
        broken = [dict(row, source="proposal_replay") for row in broken_from_report(report)]
    else:
        diagnostic_instance, diagnostic_plan = _snapshot(prepared)
        report = check(diagnostic_instance, diagnostic_plan)
        # This is the full checker's own move/replay gate. A rejected crane
        # sequence cannot pass; diagnose its requested stow once rather than
        # repeating all numerical physics on the stopped prefix too.
        broken = _unique([*[dict(violation_to_dict(v), source="proposal_replay")
                            for v in prepared.replay.violations],
                          *[dict(row, source="requested_stow_snapshot")
                            for row in broken_from_report(report)]])
    gauges = full_gauges(prepared.instance, prepared.ship, report)
    counts = _counts(prepared)
    extra = counts.pop("extra_lifts")
    gauges.update(counts)
    gauges.update(label="Full checker", full_check_pending=False,
                  diagnostic_only=not reached, fast_screen_wall_s=fast.wall_s,
                  fast_screen_legal=fast.legal)
    _frame_steps(gauges, prepared)
    return MoveVerdict(legal=bool(report.passed and reached and prepared.replay.legal), screen="full",
                       broken=broken, gauges=gauges, extra_lifts=extra, wall_s=perf_counter() - started)

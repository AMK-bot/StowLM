"""Checker-gated fallback by bounded prefix reconstruction (plan 5.7).

A prefix is the part of a sequence before a cut. Removing the whole suffix
also removes later decisions that may rely on a support, yard access or hatch
opening created at the cut. This deliberately conservative dependency closure
uses the same destroy-and-rebuild idea as large neighbourhood search; see
Pisinger and Ropke (2018), https://orbit.dtu.dk/en/publications/large-neighborhood-search/.
The scalable solver supplies candidates; only finalize plus the full checker
can accept one. Widening probes are followed by descending refinement of skipped
longer prefixes. This preserves the longest *found* prefix within the budget,
not a proof of maximal preservation after a heuristic timeout.

Metrics prefixed ``repair_`` describe this invocation: actions kept/removed/
added are multiset counts of canonical actions; changed = removed + added;
move_count_delta counts current-call lifts against the input (not a partial
checker replay). All fallback attempts, including failures, set repaired=True.
Only final candidate metrics describe the repaired plan; original metrics are
archived exclusively in repair_original_metrics. Unrepresentable input action
counts are None; stored action fallbacks are labelled unverified. Action diffs
use the canonical codec.
Cargo reconstruction can still fail visibility or lashing; re-ballasting is
never treated as evidence that a cargo-side violation was fixed.
Deadlines are cooperative: an individual checker/oracle call cannot be
interrupted, and a candidate verified after the deadline is rejected.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
from time import monotonic

from stowage.actions import get_action_space
from stowage.conventions import cells_of_slot
from stowage.frozen import current_provenance, frozen
from stowage.model import CheckReport, Instance, PlannerResult
from stowage.rules.catalogue import rule_freeze_v1_enabled

REPAIR_VERSION = "2.0"


def _stack_keys(slot: str) -> set[str]:
    """Physical columns, including both halves of a 40/45-foot slot."""
    try:
        return {c[:5] + ("D" if int(c[5:]) >= 80 else "H") for c in cells_of_slot(slot)}
    except (ValueError, TypeError):
        return set()


def _cut(instance, plan, actions, report, space):
    """First implicated action; dropping its suffix closes every dependency."""
    if report is None:
        return len(actions)
    hard = [v for v in report.violations if v.severity == "hard"]
    boxes = {b for v in hard for b in v.containers}
    stacks = {s for v in hard for slot in v.slots for s in _stack_keys(slot)}
    yards = {y.rsplit("-", 1)[0] for v in hard for y in v.yard_addresses}
    indices = {v.move_index for v in hard if v.move_index is not None}
    ex = space.expander(instance)
    offset = 0
    for i, action in enumerate(actions):
        if action.box in boxes or _stack_keys(getattr(action, "slot", "")) & stacks:
            return i
        try:
            moves = ex.step(action)
        except (ValueError, KeyError, TypeError):
            return i
        for j, move in enumerate(moves):
            if offset + j in indices or getattr(move, "container_id", None) in boxes:
                return i
            for field in ("src", "dst"):
                address = getattr(move, field, "")
                if _stack_keys(address) & stacks or address.rsplit("-", 1)[0] in yards:
                    return i
        offset += len(moves)
    # Global physics/ballast failures have no local reference. First retry all
    # actions with fresh ballast, then release the suffix if that still fails.
    return len(actions)


def _prefix_lengths(length):
    """Fine cuts first, then widen to reach the empty prefix in bounded probes."""
    yield length
    removed = 0
    while length:
        removed += 1
        length = max(0, length - (1 if removed <= 8 else 2 ** min(removed - 8, 30)))
        yield length


@frozen("solver.repair", owner="5.7", implemented=True)
def repair(
    instance: Instance, result: PlannerResult, report: CheckReport | None, *, time_limit_s: float = 60.0, seed: int = 0
) -> PlannerResult:
    """Frozen production entry point; diagnostics live in the internal helper.

    Default feasibility, finalization and full-checker acceptance are unchanged.
    """
    return _repair(instance, result, report, time_limit_s=time_limit_s, seed=seed)


def _repair(
    instance: Instance, result: PlannerResult, report: CheckReport | None, *, time_limit_s: float = 60.0, seed: int = 0,
    feasibility_config=None,
) -> PlannerResult:
    """Return a new PlannerResult with ``repaired=True`` and ``repair_note`` describing the change (actions kept,
    actions replaced). ``report`` = the checker's verdict on ``result.plan`` (None for a dead-end partial plan).
    The returned plan passes ``checker.check`` with bound evidence; the surrounding pipeline rechecks it.
    Repair never bypasses the checker. If repair fails, status is
    ``error`` / ``timeout`` and the caller reports the instance as failed (counts against the pass rate)."""
    from stowage.checker import check
    from stowage.planner import finalize
    from stowage.solver.scalable import ScalableConfig, complete

    started = monotonic()
    provenance = current_provenance(created_by="solver.repair", seed=seed).merge_nested(
        instance, result, report, parent_ids=[instance.id])
    provenance = provenance.model_copy(update={"versions": {**provenance.versions, "solver.repair": REPAIR_VERSION}})
    space = get_action_space("boxslot")
    # Recover move-only inputs even on early returns. Stored actions are only
    # an explicitly unverified fallback when malformed lifts cannot be parsed.
    accounting = "canonical"
    try:
        actions = space.from_plan(instance, result.plan) if result.plan is not None else []
    except (ValueError, KeyError, TypeError):
        try:
            if not result.plan.actions:
                raise ValueError("No recoverable stored actions")
            actions = [space.decode(a) for a in result.plan.actions]
            accounting = "stored_unverified"
        except (ValueError, KeyError, TypeError):
            actions = []
            accounting = "unavailable"
    attempts = 0
    last_error = "No candidate passed finalization and the full checker"
    input_moves = len(result.plan.moves) if result.plan is not None else 0
    original_metrics = deepcopy(result.metrics)
    last_attempt_metrics = {}
    best = None
    activated = rule_freeze_v1_enabled()
    rule_failures = Counter()

    def finish(candidate=None, checked=None, status="error"):
        nonlocal provenance
        success = candidate is not None and checked is not None and checked.passed and candidate.status == "ok"
        output = space.from_plan(instance, candidate.plan) if success else []
        old, new = Counter(actions), Counter(output)
        kept = sum((old & new).values())
        removed_actions = [space.encode(a) for a in (old - new).elements()]
        added_actions = [space.encode(a) for a in (new - old).elements()]
        removed, added = len(removed_actions), len(added_actions)
        prefix = 0
        for a, b in zip(actions, output):
            if a != b:
                break
            prefix += 1
        elapsed = monotonic() - started
        # The input's objective, gap and certificate belong to its old moves.
        # Retain those claims only in the archive, including on failed repair.
        metrics = {**(candidate.metrics if success else last_attempt_metrics),
                   "repair_original_metrics": original_metrics,
                   "repair_last_attempt_metrics": deepcopy(last_attempt_metrics),
                   "repair_version": REPAIR_VERSION, "repair_attempts": attempts,
                   "repair_action_accounting": accounting,
                   "repair_input_actions": len(actions), "repair_actions_kept": kept,
                   "repair_actions_removed": removed, "repair_actions_added": added,
                   "repair_actions_changed": removed + added, "repair_prefix_length": prefix,
                   "repair_kept_action_fraction": kept / len(actions) if actions else (1.0 if success else 0.0),
                   "repair_move_count_delta": len(candidate.plan.moves) - input_moves if success else None,
                   "repair_time_s": elapsed, "repair_succeeded": success,
                   "repair_checker_passed": success, "fallback_needed": True,
                   "repair_strategy": "prefix_reconstruction_and_reballast",
                   "repair_cargo_changed": bool(removed or added) if success and accounting != "unavailable" else None,
                   "repair_removed_actions": removed_actions, "repair_added_actions": added_actions}
        if activated:
            metrics["repair_rule_failures"] = dict(sorted(rule_failures.items()))
            from stowage.feasibility import FeasibilityConfig

            config = feasibility_config or FeasibilityConfig()
            metrics["repair_feasibility_config"] = {
                "stability_margin_scale": config.stability_margin_scale,
                "lookahead": config.lookahead, "extra": dict(config.extra)}
            metrics["repair_configured_mask_replay_passed"] = success
            metrics["repair_mask_replay_passed"] = success and config == FeasibilityConfig()
        note = (f"Repair {'succeeded' if success else 'failed'}: kept {kept}/{len(actions)} actions; "
                f"removed {removed}, added {added}; {attempts} attempts in {elapsed:.3f}s.")
        if accounting == "unavailable":
            for key in ("repair_input_actions", "repair_actions_kept", "repair_actions_removed",
                        "repair_actions_added", "repair_actions_changed", "repair_prefix_length",
                        "repair_kept_action_fraction", "repair_removed_actions", "repair_added_actions"):
                metrics[key] = None
            note = f"Repair {'succeeded' if success else 'failed'}: input action accounting unavailable; {attempts} attempts in {elapsed:.3f}s."
        elif accounting == "stored_unverified":
            note += " Input counts use unverified stored actions."
        if success and accounting != "unavailable":
            note += (f" Cargo actions removed={removed_actions}, added={added_actions}."
                     if removed or added else " Cargo actions unchanged; ballast refreshed.")
        # The checked plan must remain byte-for-byte unchanged: checker content
        # bindings include its provenance. Only the result gains report lineage.
        output_result = result.model_copy(update={"status": "ok" if success else status,
            "plan": candidate.plan if success else None,
            "later_plans": [], "repaired": True, "repair_note": note, "metrics": metrics,
            "message": note if success else last_error, "provenance": provenance})
        if success:
            output_result._record_full_check(instance, checked)
        output_result.metrics["shippable"] = output_result.shippable
        return output_result

    if not math.isfinite(time_limit_s) or time_limit_s < 0:
        last_error = "time_limit_s must be finite and nonnegative"
        return finish()
    deadline = started + time_limit_s
    if time_limit_s == 0:
        last_error = "No repair time available"
        return finish(status="timeout")
    try:
        cut = _cut(instance, result.plan, actions, report, space)
        lengths = list(_prefix_lengths(cut))
        probed = set()
        refining = False
        for index, length in enumerate(lengths):
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            attempts += 1
            probed.add(length)
            # Reserve time for ballast/checking and ensure one bad prefix cannot
            # consume the entire budget. All search runs on one worker.
            allocation = remaining / min(4, len(lengths) - index)
            candidate = complete(instance, actions[:length], seed=seed,
                time_limit_s=allocation * 0.6,
                config=ScalableConfig(max_neighborhoods=0, backtrack_limit=0, **(
                    {"feasibility_config": feasibility_config} if feasibility_config is not None else {})))
            last_attempt_metrics = deepcopy(candidate.metrics)
            provenance = provenance.merge_nested(candidate)
            if candidate.status != "ok" or candidate.plan is None:
                last_error = candidate.message or last_error
                continue
            actual = space.from_plan(instance, candidate.plan)
            if actual[:length] != actions[:length]:
                last_error = "Solver discarded part of the requested prefix"
                continue
            if activated:
                # Replay the entire reconstruction against the public mask.
                # Keep this hook at the acceptance boundary so future in-port
                # screens automatically apply to repair as well as search.
                from stowage.feasibility import make_feasibility_engine

                engine = make_feasibility_engine(instance, space, **(
                    {"config": feasibility_config} if feasibility_config is not None else {}))
                for action in actual:
                    if monotonic() >= deadline:
                        last_error = "Repair time expired during configured mask replay"
                        break
                    diagnose = getattr(engine, "screen_diagnostics", lambda: {})
                    before_rejected = diagnose().get("rejected", {})
                    if not engine.is_legal(action):
                        diagnostics = diagnose()
                        rejected = diagnostics.get("rejected", {})
                        rules = [rule for rule, count in rejected.items()
                                 if count > before_rejected.get(rule, 0)]
                        dead = engine.dead_end()
                        rules = rules or diagnostics.get("dead_end_rule_ids", []) or (dead.rule_ids if dead else ())
                        rule_failures.update(rules or ["feasibility.replay_rejected"])
                        last_error = "Repair mask replay rejected: " + ", ".join(rules or ["unknown rule"])
                        break
                    engine.push(action)
                else:
                    if engine.is_complete():
                        engine = None
                    else:
                        rule_failures.update(["objective.all_loaded"])
                        last_error = "Repair mask replay did not complete every required lift"
                if engine is not None:
                    continue
            candidate = candidate.model_copy(update={"plan": candidate.plan.model_copy(update={"ballast": None})})
            if monotonic() >= deadline:
                break
            candidate = finalize(instance, candidate, time_limit_s=min(allocation * 0.4, deadline - monotonic()))
            last_attempt_metrics = deepcopy(candidate.metrics)
            provenance = provenance.merge_nested(candidate)
            if candidate.plan is None or monotonic() >= deadline:
                continue
            candidate = candidate.model_copy(update={"plan": candidate.plan.model_copy(update={
                "provenance": candidate.plan.provenance.merge(provenance)})})
            checked = check(instance, candidate.plan)
            if activated and not checked.passed:
                rule_failures.update(v.rule_id for v in checked.violations if v.severity == "hard")
            provenance = provenance.merge_nested(checked)
            if candidate.status == "ok" and checked.passed and monotonic() <= deadline:
                if refining:
                    return finish(candidate, checked)
                best = candidate, checked
                # Heuristic failures are not infeasibility proofs. Test every
                # skipped longer prefix in descending order with remaining time;
                # no monotonic-success assumption or binary search is sound here.
                skipped = [n for n in range(cut, length, -1) if n not in probed]
                if not skipped:
                    return finish(candidate, checked)
                lengths[index + 1:] = skipped
                refining = True
                continue
            last_error = candidate.message or "; ".join(v.rule_id for v in checked.violations) or last_error
    except Exception as exc:  # Planner contract: failure is a result, never an unchecked candidate.
        last_error = f"Repair failed: {type(exc).__name__}: {exc}"
        if best is not None:
            return finish(*best)
        return finish(status="timeout" if monotonic() >= deadline else "error")
    if best is not None:
        return finish(*best)
    return finish(status="timeout" if monotonic() >= deadline else "error")

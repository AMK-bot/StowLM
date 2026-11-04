"""D199 canonical placement labels and fresh, guarded CPU admission.

No stored checker verdict admits a label. The input is an already finished plan;
ballast is preserved and the exact canonical output is checked again. Ordering
only chooses among the plan's final box/slot pairs, never another placement.
"""
from __future__ import annotations

import hashlib
import json
import math
from time import monotonic


CANONICAL_VERSION = "stowlm9-final-placement-v1"


class LabelError(ValueError):
    """A plan cannot be represented as a deterministic one-placement label."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def score_plan(instance, plan):
    """Measure the exact move-engine objective; illegal/nonfinite costs cannot win."""
    from stowage.moves import apply_plan

    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise LabelError("illegal source plan: " + "; ".join(v.message for v in replay.violations))
    score = float(replay.count.weighted_nonproductive)
    if not math.isfinite(score) or score < 0:
        raise LabelError("nonfinite or negative measured move cost")
    return score


def _action_key(action):
    # Bottom-up, all holds before decks; exact numeric addresses and box ID break
    # ties. This key depends on final placement, not teacher move/action order.
    slot = action.slot
    return (int(slot[-2:]), int(slot[:3]), int(slot[3:5]), action.box)


def _order_fixed(engine, candidates, *, max_nodes=5000):
    """Lexicographically first complete legal ordering of fixed placements.

    Deterministic bounded backtracking changes order only, never slots or boxes.
    The node budget, rather than elapsed time, keeps identical inputs identical.
    A budget exhaustion is reported and excluded, never repaired or admitted.
    """
    candidates = tuple(sorted(candidates, key=_action_key))
    nodes = 0

    def visit(remaining):
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise LabelError(f"canonical order search exceeded {max_nodes} nodes")
        if not remaining:
            return [] if engine.is_complete() else None
        for index, action in enumerate(remaining):
            if not engine.is_legal(action):
                continue
            engine.push(action)
            tail = visit(remaining[:index] + remaining[index + 1:])
            engine.pop()
            if tail is not None:
                return [action, *tail]
        return None

    result = visit(candidates)
    if result is None:
        raise LabelError("no legal complete one-placement ordering for final stow")
    return result, nodes


def canonicalize(instance, plan, *, max_nodes=5000):
    """Return ``(canonical MovePlan, diagnostic dict)`` or raise LabelError.

    Scope is current-call load-only maps, as in the MVP training corpus. A map
    needing discharges or movements of pre-existing cargo is explicitly rejected.
    The layered mask independently replays the resulting label in ``certify``.
    """
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    from stowage.feasibility import make_feasibility_engine
    from stowage.frozen import require_clean
    from stowage.moves import apply_plan
    from llm.stowlm.sft_data import d79_engine_config

    require_clean(instance, plan, what="D199 canonical label input")
    if instance.plan_calls or instance.discharge_list or instance.onboard.placements:
        raise LabelError("one-placement labels require current-call load-only maps with no onboard cargo")
    raw_moves = score_plan(instance, plan)
    replay = apply_plan(instance, plan)
    final = {p.container_id: p.slot for p in replay.final_state.ship.placements}
    required = set(instance.load_list)
    if len(required) != len(instance.load_list) or set(final) != required:
        raise LabelError("final stow does not contain exactly one placement per load-list box")
    candidates = [Place(box, final[box]) for box in sorted(required)]
    space = get_action_space()
    engine = make_feasibility_engine(instance, space, config=d79_engine_config())
    ordered, nodes = _order_fixed(engine, candidates, max_nodes=max_nodes)
    canonical = space.expand(instance, ordered)
    canonical = canonical.model_copy(update={
        "id": instance.id + "." + CANONICAL_VERSION,
        "ballast": plan.ballast,
        "provenance": canonical.provenance.merge(plan.provenance),
    })
    encoded = [space.encode(action) for action in ordered]
    # Expansion's canonical lift parser must not introduce additional actions.
    if canonical.actions != encoded or space.from_plan(instance, canonical) != ordered:
        raise LabelError("expanded label needs actions beyond one placement per box")
    canonical_final = apply_plan(instance, canonical)
    if (not canonical_final.legal or
            {p.container_id: p.slot for p in canonical_final.final_state.ship.placements} != final):
        raise LabelError("canonical expansion changed final stow or failed physical replay")
    return canonical, dict(version=CANONICAL_VERSION, actions=len(ordered), boxes=len(required),
        one_placement_per_box=True, deterministic=True, order="tier,bay,row,box; first complete legal permutation",
        search_nodes=nodes, max_search_nodes=max_nodes, placements_sha256=_digest(sorted(final.items())),
        actions_sha256=_digest(encoded), raw_weighted_moves=raw_moves,
        canonical_weighted_moves=score_plan(instance, canonical))


def certify(instance, canonical_plan, expected_epoch, *, check_timeout_s=300.):
    """Return ``(fresh record or None, diagnostics)`` with no finisher or repair.

    Mandatory expected epoch is the full outcome manifest (not just its hash).
    A passing record has plan/report bound to the exact same input, current guarded
    child-check acknowledgement, layered token replay, and matching measured cost.
    """
    from llm.exit import outcome_epoch
    from llm.exit.checker_watchdog import run_full_check
    from llm.exit.selection import mask_replay
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    from stowage.checker import report_matches_inputs
    from stowage.frozen import require_clean

    started = monotonic()
    info = dict(passed=False, stage="canonical_admission")
    try:
        if not isinstance(expected_epoch, dict) or not expected_epoch:
            raise LabelError("a nonempty expected outcome epoch manifest is required")
        outcome_epoch.assert_current(expected_epoch)
        require_clean(instance, canonical_plan, what="D199 canonical label")
        space = get_action_space()
        actions = space.from_plan(instance, canonical_plan)
        if (not all(isinstance(a, Place) for a in actions) or len(actions) != len(instance.load_list)
                or {a.box for a in actions} != set(instance.load_list)
                or canonical_plan.actions != [space.encode(a) for a in actions]):
            raise LabelError("label is not exactly one stored Place per load-list box")
        moves = score_plan(instance, canonical_plan)
        checked = run_full_check(instance, canonical_plan, timeout_s=check_timeout_s,
                                 expected_outcome_epoch=expected_epoch)
        info["checker_watchdog"] = checked.details()
        if not checked.available:
            info.update(unavailable=True, unavailable_reason=checked.reason)
            return None, info
        report = checked.report
        info["checker_passed"] = report.passed
        if report.passed is not True or not report_matches_inputs(report, instance, canonical_plan):
            raise LabelError("full checker failed or input/plan binding differs")
        require_clean(instance, canonical_plan, report, what="D199 checker-approved label")
        if report.count is None or moves != report.count.weighted_nonproductive:
            raise LabelError("checker and physical replay weighted costs disagree")
        replay = mask_replay(instance, canonical_plan)
        info["mask"] = replay
        if replay.get("passed") is not True or replay.get("actions_expected") != len(actions):
            raise LabelError("complete layered mask replay did not pass")
        outcome_epoch.assert_current(expected_epoch)
        info.update(passed=True, weighted_moves=moves)
        return dict(plan=canonical_plan.model_dump(mode="json"), report=report.model_dump(mode="json"),
                    weighted_moves=moves, mask=replay, outcome_epoch=outcome_epoch.epoch_id(expected_epoch),
                    checker_watchdog=checked.details(), canonical_version=CANONICAL_VERSION,
                    actions_sha256=_digest(canonical_plan.actions)), info
    except Exception as error:
        info["error"] = f"{type(error).__name__}: {error}"
        return None, info
    finally:
        info["wall_s"] = monotonic() - started


def choose_cheapest(stream, baseline):
    """Choose freshly certified records; a tie deterministically keeps best-d172."""
    passing = []
    for priority, record in ((0, baseline), (1, stream)):
        if record is None:
            continue
        value = record.get("weighted_moves")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise LabelError("candidate has invalid checked objective")
        if not (record.get("report", {}).get("passed") is True and record.get("mask", {}).get("passed") is True):
            raise LabelError("candidate is missing passing checker/mask evidence")
        passing.append((value, priority, record))
    return min(passing, key=lambda item: item[:2])[2] if passing else None

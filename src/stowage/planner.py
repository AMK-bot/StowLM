"""Planner protocol (frozen interface, plan 0.9).

Every planner — exact solver (4.6), scalable solver (4.7), two-step baseline (4.8), StowLM (masked Glimmer + LoRA),
Muse Spark zero-shot (5.5) — implements ``Planner``. The pipeline around it is fixed:

    result = planner.plan(instance, seed=s)              # MovePlan in the 3.8 action space, expanded to moves
    result = finalize(instance, result)                  # D75 lift order (switch on), then 4.2 ballast unless set
    report = stowage.checker.check(instance, result.plan)  # the judge
    if not report.passed: result = repair(...)           # 5.7; re-checked; fallback counted

``run_planner`` does exactly this and is what the eval harness (5.2), gold-plan writer (6.1) and demo (6.5) call.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from stowage.frozen import frozen
from stowage.model import CheckReport, Instance, PlannerResult


@runtime_checkable
class Planner(Protocol):
    """A planner.

    Attributes:
        name: stable id (``"solver.exact"``, ``"solver.scalable"``, ``"baseline.twostep"``, ``"stowlm"``,
            ``"stowlm.base"``, ``"spark.zeroshot"``).
        version: implementation version (solver version, adapter id + step, model id).
        sets_ballast: True if ``plan`` returns ``plan.ballast`` set (``source="planner"``); otherwise ``finalize``
            adds it via 4.2.
        deterministic: True if same instance + seed + versions → identical plan.
    """

    name: str
    version: str
    sets_ballast: bool
    deterministic: bool

    def plan(self, instance: Instance, *, seed: int = 0, time_limit_s: float | None = None) -> PlannerResult:
        """Plan ``instance.call_index``. Never raises for a hard instance: returns a status instead
        (``dead_end`` / ``infeasible`` / ``timeout`` / ``error`` with the best partial plan). ``provenance`` must be
        ``current_provenance(...).merge(instance.provenance)`` so stub taint propagates."""
        ...


@frozen("planner.finalize", owner="4.2", implemented=True)
def finalize(instance: Instance, result: PlannerResult, *, time_limit_s: float = 30.0) -> PlannerResult:
    """Add departure ballast via ``stowage.solver.ballast.finish_ballast`` when ``result.status == "ok"`` and
    ``result.plan.ballast is None``; otherwise return ``result`` unchanged. If no ballast is found the status becomes
    ``infeasible`` (proven for this fixed stow only, not for the instance) or ``error`` (not found), with
    ``message`` from the BallastOutcome and
    ``metrics["ballast_status"]`` = the BallastOutcome status in every case. Activated ``not_found`` also retains
    ``metrics["not_found_cause"]`` so callers can distinguish unknowns, completed rejections and timeouts.
    Merges provenance. A planner that
    declared ``sets_ballast=True`` but returned ``ballast=None`` is a planner bug: ``finalize`` is only told the
    result, so ``run_planner`` (which knows the planner) sets status ``error`` with that message instead of
    finishing it. Direct callers must enforce that declaration themselves before calling ``finalize``; this
    function cannot infer it from the result's planner name.

    With rule-freeze-v1 on, an ``ok`` plan first goes through the D75 shared lift resequencer
    (``stowage.planner_resequence``), including plans whose planner set its own ballast, so every caller (run_planner,
    gold, best-of-k selection, rewards, repair, rechecks) sees the same order before the finisher. Its report is
    ``metrics["resequence"]``. Switch off: no resequencing and no metric (legacy behaviour)."""
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    if rule_freeze_v1_enabled() and result.status == "ok" and result.plan is not None:
        result = _resequenced(instance, result, time_limit_s=min(30.0, time_limit_s))
    if result.status != "ok" or result.plan is None or result.plan.ballast is not None:
        return result

    from stowage.frozen import current_provenance
    from stowage.solver.ballast import finish_ballast

    outcome = finish_ballast(
        instance, result.plan, time_limit_s=time_limit_s, seed=result.seed if result.seed is not None else 0
    )
    provenance = current_provenance(created_by="planner.finalize", seed=result.seed).merge(
        instance.provenance, result.provenance, result.plan.provenance, outcome.provenance,
        parent_ids=[instance.id, result.plan.id],
    )
    updates = {
        "metrics": {**result.metrics, "ballast_status": outcome.status},
        "provenance": provenance,
    }
    cause = getattr(outcome, "not_found_cause", None)
    if outcome.status == "not_found" and cause is not None:
        # Preserve the finisher's separate search/acceptance/prepass verdict
        # for reward callers; elapsed time cannot distinguish those clocks.
        updates["metrics"]["not_found_cause"] = cause
    if outcome.status in ("ok", "kept") and outcome.plan is not None and outcome.plan.ballast is not None:
        updates["plan"] = outcome.plan.model_copy(
            update={"provenance": outcome.plan.provenance.merge(provenance)}
        )
        updates["provenance"] = provenance.merge(outcome.plan.provenance)
    else:
        updates["status"] = "infeasible" if outcome.status == "infeasible_proven" else "error"
        updates["message"] = outcome.proof or (
            "No ballast exists for this stow (other stows are not excluded)."
            if outcome.status == "infeasible_proven" else
            "Ballast not found within the time limit; infeasibility has not been proved."
        )
        if outcome.status == "not_found" and cause is not None:
            updates["message"] = f"Ballast not found ({cause}); infeasibility has not been proved."
    return result.model_copy(update=updates)


def _resequenced(instance: Instance, result: PlannerResult, *, time_limit_s: float) -> PlannerResult:
    """D75 step of ``finalize``: same moves, possibly a new lift order; never raises."""
    from stowage import planner_resequence

    try:
        outcome = planner_resequence.resequence(instance, result.plan, time_limit_s=time_limit_s)
        report = dict(outcome.report)
        plan = outcome.plan if outcome.applied else result.plan
    except Exception as exc:  # the resequencer is fail-safe; this guards a replaced implementation
        report = {"applied": False, "lifts_moved": 0,
                  "reason": f"Resequencing failed: {type(exc).__name__}: {exc}"}
        plan = result.plan
    return result.model_copy(update={"plan": plan, "metrics": {**result.metrics, "resequence": report}})


@frozen("planner.run_planner", owner="5.2", implemented=True)
def run_planner(
    planner: Planner,
    instance: Instance,
    *,
    seed: int = 0,
    time_limit_s: float | None = None,
    allow_repair: bool = True,
) -> tuple[PlannerResult, CheckReport | None]:
    """plan → finalize → check → (repair → check) as in the module docstring. Returns the final result and the
    checker report on its current plan (None if no plan exists). ``result.shippable`` requires a full passing
    checker verdict for every carried plan and clean input/component provenance; ``metrics["shippable"]`` is its
    compatibility snapshot. Later-call plans fail closed until sequential replay/checking is implemented.
    ``result.repaired`` marks the fallback. Never ships an unchecked or stub-tainted plan."""
    from time import monotonic

    from stowage.checker import check
    from stowage.frozen import FROZEN, _artifact_stubs, current_provenance
    from stowage.model import Provenance, Violation
    from stowage.solver.repair import repair

    started = monotonic()
    timings = {}
    provenance = current_provenance(created_by="planner.run_planner", seed=seed).merge(
        instance.provenance, Provenance(stubs=_artifact_stubs(instance)), parent_ids=[instance.id]
    )
    phase = monotonic()
    try:
        result = planner.plan(instance, seed=seed, time_limit_s=time_limit_s)
    except Exception as exc:
        result = PlannerResult(
            status="error", planner=planner.name, planner_version=planner.version,
            seed=seed, message=f"Planning failed: {type(exc).__name__}: {exc}",
            wall_time_s=monotonic() - phase, provenance=provenance,
        )
    timings["planning_s"] = monotonic() - phase
    provenance = provenance.merge(result.provenance, Provenance(stubs=_artifact_stubs(result)))
    if result.plan is not None:
        provenance = provenance.merge(result.plan.provenance, parent_ids=[result.plan.id])
    result = result.model_copy(update={"provenance": provenance})
    ballast_bug = (planner.sets_ballast and result.status == "ok"
                   and result.plan is not None and result.plan.ballast is None)
    phase = monotonic()
    if ballast_bug:
        result = result.model_copy(update={
            "status": "error", "message": "Planner declares sets_ballast=True but returned ballast=None."
        })
    else:
        try:
            result = finalize(instance, result, **(
                {"time_limit_s": time_limit_s} if time_limit_s is not None else {}
            ))
        except Exception as exc:
            result = result.model_copy(update={
                "status": "error", "message": f"Finalization failed: {type(exc).__name__}: {exc}"
            })
    timings["finalize_s"] = monotonic() - phase

    def prepare_for_check(candidate):
        """Freeze all candidate lineage before the checker hashes its inputs."""
        nonlocal provenance
        parent_ids = [instance.id]
        if candidate.plan is not None:
            parent_ids.append(candidate.plan.id)
        provenance = provenance.merge_nested(candidate, instance, parent_ids=parent_ids).merge(
            current_provenance(created_by="planner.run_planner", seed=seed),
            Provenance(stubs=_artifact_stubs(candidate, instance)),
        )
        updates = {"provenance": provenance}
        if candidate.plan is not None:
            updates["plan"] = candidate.plan.model_copy(update={
                "provenance": candidate.plan.provenance.merge(provenance)
            })
        return candidate.model_copy(update=updates)

    def checked(candidate):
        if candidate.plan is None:
            return None
        try:
            verdict = check(instance, candidate.plan)
        except Exception as exc:
            verdict = CheckReport(passed=False, provenance=provenance, violations=[Violation(
                rule_id="checker.internal_error", severity="hard", call_index=instance.call_index,
                message=f"Checker failed: {type(exc).__name__}: {exc}",
            )])
        # The declaration applies to every replacement, including an unchanged or failed repair.
        if planner.sets_ballast and candidate.plan.ballast is None:
            verdict = verdict.model_copy(update={"passed": False, "violations": [
                *verdict.violations, Violation(
                    rule_id="checker.invalid_ballast", severity="hard", call_index=instance.call_index,
                    message="Planner declares sets_ballast=True but returned ballast=None.",
                )
            ]})
        # An omitted future call is incomplete too. Sequential checking is not
        # implemented yet, even when the planner leaves later_plans empty.
        if instance.plan_calls:
            verdict = verdict.model_copy(update={"passed": False, "violations": [
                *verdict.violations, Violation(
                    rule_id="checker.unchecked_plan_calls", severity="hard", call_index=instance.call_index,
                    message="Required later calls have no sequential full-checker coverage.",
                )
            ]})
        return verdict

    phase = monotonic()
    result = prepare_for_check(result)
    report = checked(result)
    timings["check_s"] = monotonic() - phase
    fallback_needed = report is None or not report.passed
    repair_status = "not_needed"
    timings["repair_s"] = 0.0
    timings["recheck_s"] = 0.0
    if fallback_needed:
        if not allow_repair:
            repair_status = "disabled"
        elif not FROZEN["solver.repair"].implemented:
            repair_status = "unavailable"
            result = result.model_copy(update={"repair_note": "repair unavailable"})
        else:
            phase = monotonic()
            original = result
            # Snapshot before repair can mutate or replace either input. Prior
            # report hashes are historical lineage, never the final binding.
            provenance = provenance.merge_nested(original, report).merge(
                Provenance(stubs=_artifact_stubs(original, report))
            )
            try:
                result = repair(instance, result, report, seed=seed, **(
                    {"time_limit_s": time_limit_s} if time_limit_s is not None else {}
                ))
                repair_status = "attempted"
            except Exception as exc:
                result = original.model_copy(update={
                    "status": "error", "repair_note": f"Repair failed: {type(exc).__name__}: {exc}"
                })
                repair_status = "error"
            timings["repair_s"] = monotonic() - phase
            phase = monotonic()
            result = prepare_for_check(result)
            report = checked(result)
            timings["recheck_s"] = monotonic() - phase
    if report is not None:
        provenance = provenance.merge(report.provenance, Provenance(stubs=_artifact_stubs(report)))
    provenance = provenance.merge(result.provenance, Provenance(stubs=_artifact_stubs(result)))
    # Only the result envelope gains the final report's lineage. Mutating the
    # checked plan would invalidate its content binding; merging older hashes
    # into the final report would also create conflicting direct bindings.
    timings["pipeline_s"] = monotonic() - started
    result = result.model_copy(update={
        "provenance": provenance,
        "metrics": {**result.metrics, "pipeline_timing": timings,
                    "fallback_needed": fallback_needed, "repair_status": repair_status},
    })
    if report is not None:
        result._record_full_check(instance, report)
    result.metrics["shippable"] = result.shippable
    return result, report

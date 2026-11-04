"""Native greedy then reserved-budget constructor, with early deck geometry.

Only internal search slices change. Each candidate retains its original native
geometry/E6 checks, terminal assignment, ballast and full-checker obligations.
"""
from __future__ import annotations

from dataclasses import replace
import math
from time import monotonic

VERSION = "scale-carrier-5-direct-constructor-oog-first"


def deck_geometry_screen(original, metrics):
    """Add exact deck support geometry as a conservative search restriction.

    Native membership/action sets are untouched. The checker's geometry cannot
    be cured by ballast. A future ship restow could repair a bad prefix, so
    screening it can exclude a repairable branch: this is explicitly bounded
    constructive search, not an infeasibility certificate. No completed plan
    bypasses the native final geometry/E6 validation.
    """
    from stowage.actions.space import Place
    from stowage.conventions import parse_slot

    def screen(instance, ex, action, *, include_existing=False):
        reason = original(instance, ex, action, include_existing=include_existing)
        if reason or include_existing or not isinstance(action, Place) or not parse_slot(action.slot).on_deck:
            return reason
        metrics["deck_geometry_probes"] = metrics.get("deck_geometry_probes", 0) + 1
        reason = original(instance, ex, action, include_existing=True)
        if reason:
            metrics["deck_geometry_rejections"] = metrics.get("deck_geometry_rejections", 0) + 1
            counts = metrics.setdefault("deck_geometry_reasons", {})
            counts[reason] = counts.get(reason, 0) + 1
        return reason

    return screen


class Installation:
    def __init__(self, module, original, replacement):
        self.module, self.original, self.replacement = module, original, replacement

    def restore(self):
        if self.module._preplan is self.replacement:
            self.module._preplan = self.original

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.restore()


def install(*, greedy_budget_s=60., greedy_fraction=.4, early_deck_geometry=True):
    """Patch the loaded frozen runtime's empty-ship small/medium carrier only."""
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.solver import twostep
    from stowage.solver.twostep_construct import construct_preplan
    from reservations_v5 import constructor_search
    if (not math.isfinite(greedy_budget_s) or greedy_budget_s < 0
            or not 0 <= greedy_fraction <= 1):
        raise ValueError("invalid carrier stage budgets")
    original = twostep._preplan

    def preplan(instance, *, beam_width, deadline, feasibility_config=None, seed=0):
        if (rule_freeze_v1_enabled() or not twostep._use_fast_prestow(instance)
                or instance.onboard.placements):
            return original(instance, beam_width=beam_width, deadline=deadline,
                            feasibility_config=feasibility_config, seed=seed)
        started = monotonic()
        stages = {}
        metrics = {"carrier_version": VERSION, "carrier_stages": stages,
                   "preplan_search_method": VERSION, "preplan_method": VERSION,
                   "preplan_constructor_fallback_used": False,
                   "carrier_budget_policy": {"greedy_cap_s": greedy_budget_s,
                       "greedy_fraction": greedy_fraction, "cp_omitted": True,
                       "shared_deadline": deadline},
                   "carrier_early_deck_geometry": bool(early_deck_geometry),
                   "carrier_geometry_screen_scope": "constructive_search_only_no_native_mask_mutation"}

        def finish(candidate, stage):
            metrics["carrier_total_time_s"] = monotonic() - started
            metrics["carrier_selected_stage"] = stage
            metrics["carrier_deadline_overrun_s"] = max(0., monotonic() - deadline)
            combined = dict(candidate.search_metrics or {})
            combined.update(metrics)
            return replace(candidate, search_metrics=combined)

        def expired(message):
            return finish(twostep.PreStowPlan(status="timeout", message=message), "none")

        if started >= deadline:
            return expired("Carrier deadline expired before setup")
        canonical = twostep._canonical_instance(instance)
        config = feasibility_config or FeasibilityConfig(extra={"global_screen": 0})
        metrics["effective_global_screen"] = config.extra.get("global_screen", 1)

        stage_started = monotonic()
        greedy_deadline = min(deadline, stage_started + min(greedy_budget_s,
                              max(0., deadline - stage_started) * greedy_fraction))
        greedy = twostep.PreStowPlan(status="timeout", message="No extended-greedy time remains")
        row = stages["extended_greedy"] = {"budget_s": max(0., greedy_deadline - stage_started)}
        original_geometry = twostep.securing_geometry_violation
        replacement_geometry = None
        try:
            if monotonic() < greedy_deadline:
                engine = twostep.make_feasibility_engine(canonical, get_action_space("boxslot"), config=config)
                if monotonic() < greedy_deadline:
                    if early_deck_geometry:
                        replacement_geometry = deck_geometry_screen(original_geometry, row)
                        twostep.securing_geometry_violation = replacement_geometry
                    greedy = twostep._fast_prestow(canonical, engine, beam_width=beam_width,
                                                   deadline=greedy_deadline)
        except Exception as exc:
            greedy = twostep.PreStowPlan(status="error", message=f"Extended greedy {type(exc).__name__}: {exc}")
        finally:
            if replacement_geometry is not None and twostep.securing_geometry_violation is replacement_geometry:
                twostep.securing_geometry_violation = original_geometry
        row.update(status=greedy.status, message=greedy.message, time_s=monotonic() - stage_started,
                   late=monotonic() >= greedy_deadline)
        if greedy.status == "ok" and row["late"]:
            greedy = twostep.PreStowPlan(status="timeout", message="Extended greedy candidate exceeded its slice")
            row["status"] = greedy.status
        metrics.update(preplan_greedy_primary_status=greedy.status,
                       preplan_greedy_primary_message=greedy.message,
                       preplan_greedy_primary_time_s=row["time_s"],
                       preplan_greedy_primary_budget_s=row["budget_s"])
        if greedy.status == "ok":
            return finish(greedy, "extended_greedy")
        if monotonic() >= deadline:
            return expired("Extended greedy exhausted the shared carrier deadline")

        # Call the native constructor directly: repeating _preplan would spend
        # another ten-second greedy slice rather than use the remaining budget.
        stage_started = monotonic()
        metrics["preplan_constructor_fallback_used"] = True
        row = stages["constructor"] = {"budget_s": max(0., deadline - stage_started)}
        try:
            with constructor_search():
                candidate, detail = construct_preplan(canonical, deadline=deadline, seed=seed,
                                                       feasibility_config=config)
        except Exception as exc:
            candidate = twostep.PreStowPlan(status="error", message=f"Constructor {type(exc).__name__}: {exc}")
            detail = {}
        row.update(status=candidate.status, message=candidate.message,
                   time_s=monotonic() - stage_started, late=monotonic() >= deadline,
                   metrics=detail)
        metrics.update(detail)
        if candidate.status == "ok" and row["late"]:
            candidate = twostep.PreStowPlan(status="timeout", message="Constructor candidate exceeded carrier deadline")
            row["status"] = candidate.status
        metrics.update(preplan_constructor_status=candidate.status,
                       preplan_constructor_message=candidate.message,
                       preplan_constructor_fallback_time_s=row["time_s"])
        return finish(candidate, "constructor" if candidate.status == "ok" else "none")

    twostep._preplan = preplan
    return Installation(twostep, original, preplan)

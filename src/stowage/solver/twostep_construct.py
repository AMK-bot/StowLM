"""Cargo-only constructor candidate for the two-step baseline.

APPROXIMATION: reuse the scalable constructor's bounded MRV search (try the
most restricted cargo first), with singleton canonical yard stacks instead of
the actual terminal yard. Search family is shared; information available to
the carrier is different. This supports an information-ablation comparison,
not an independent-algorithm claim. Search is truncated, rules are not relaxed.

The shared constructor screens its actions through its feasibility oracle.
This adapter validates the returned physical plan, inventory, securing geometry
and exact E6 departure condition without rebuilding all native masks. It only
returns a carrier category proposal: D73 terminal assignment, ballast and the
full checker remain mandatory.

MRV means choosing cargo with the fewest available placements first; see
Russell and Norvig, Artificial Intelligence: A Modern Approach, ch. 6, as in
the shared scalable solver's search documentation.
"""
from __future__ import annotations

from time import monotonic, perf_counter

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.checker.rules import e6_completion_possible
from stowage.feasibility import FeasibilityConfig
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.solver.scalable import ScalableConfig, ScalableSolver
from stowage.solver.securing_geometry import securing_state_violations


def construct_preplan(canonical, *, deadline, seed=0, feasibility_config=None):
    """Return ``(PreStowPlan, metrics)`` from canonical input only.

    ``deadline`` owns search and validation together. Solver calls are
    indivisible; any overrun is measured, with no late candidate accepted.
    Explicit ``feasibility_config`` is passed through unchanged. None selects
    the D79 C2-OFF configuration for this small/medium carrier path.
    """
    # Late import permits future integration without a module-import cycle.
    from stowage.solver.twostep import PreStowPlan, SlotCategory, category_of

    start = perf_counter()
    metrics = dict(
        preplan_search_method="canonical_constructor_mrv_v4",
        constructor_status="not_started", constructor_time_s=0.,
        constructor_validation_time_s=0., carrier_validation_status="not_started",
        carrier_validation_failures=[], constructor_shared_search_family=True,
        constructor_yard_information="canonical_singleton_stacks",
        constructor_full_checker_run=False, constructor_ballast_finisher_run=False,
        constructor_repair_fallback=False)

    def outcome(status, message=None, *, slots=(), actions=()):
        metrics["constructor_total_time_s"] = perf_counter() - start
        metrics["constructor_deadline_overrun_s"] = max(0., monotonic() - deadline)
        return PreStowPlan(slots=slots, actions=actions, status=status, message=message), metrics

    if monotonic() >= deadline:
        return outcome("timeout", "Canonical constructor budget expired before setup")
    if (canonical.port.yard.id != "twostep-canonical"
            or canonical.yard.yard_id != "twostep-canonical"
            or any(len(stack.boxes) > 1 for stack in canonical.yard.stacks)):
        return outcome("error", "Canonical constructor requires the yard-blind singleton instance")
    if rule_freeze_v1_enabled():
        return outcome("error", "Canonical constructor candidate requires rule-freeze OFF")
    if feasibility_config is None:
        feasibility_config = FeasibilityConfig(extra={"global_screen": 0})
    metrics["effective_global_screen"] = feasibility_config.extra.get("global_screen", 1)

    try:
        solver = ScalableSolver(ScalableConfig(
            constructor_v4=True, max_neighborhoods=0, feasibility_config=feasibility_config))
        remaining = deadline - monotonic()
        if remaining <= 0:
            return outcome("timeout", "Canonical constructor budget expired during setup")
        metrics["constructor_requested_time_s"] = remaining
        stage = perf_counter()
        try:
            raw = solver.plan(canonical, seed=seed, time_limit_s=remaining)
        finally:
            metrics["constructor_time_s"] = perf_counter() - stage
        metrics.update(constructor_status=raw.status, constructor_message=raw.message,
                       constructor_planner_version=raw.planner_version,
                       constructor_metrics=raw.metrics)
        if monotonic() >= deadline:
            return outcome("timeout", "Canonical constructor exceeded its cargo-only deadline")
        if raw.status != "ok" or raw.plan is None:
            return outcome(raw.status if raw.status != "ok" else "error",
                           raw.message or "Canonical constructor returned no complete plan")

        stage = perf_counter()
        metrics["carrier_validation_status"] = "running"
        try:
            physical = apply_plan(canonical, raw.plan)
            reasons = []
            if not physical.legal:
                reasons.append("Canonical constructor plan failed physical move replay")
                metrics["carrier_physical_rule_ids"] = sorted({v.rule_id for v in physical.violations})
            ship = physical.final_state.ship
            positions = {p.container_id: p.slot for p in ship.placements}
            missing = sorted(set(canonical.load_list) - positions.keys())
            undischarged = sorted(set(canonical.discharge_list) & positions.keys())
            retained = {p.container_id for p in canonical.onboard.placements} - set(canonical.discharge_list)
            missing_retained = sorted(retained - positions.keys())
            unexpected = sorted(positions.keys() - (set(canonical.load_list) | retained))
            metrics.update(carrier_missing_loads=missing, carrier_undischarged=undischarged,
                           carrier_missing_retained=missing_retained,
                           carrier_unexpected_cargo=unexpected, carrier_physical_replay_legal=physical.legal)
            if missing:
                reasons.append(f"Missing load-list cargo: {missing}")
            if undischarged:
                reasons.append(f"Undischarged cargo: {undischarged}")
            if missing_retained:
                reasons.append(f"Missing retained cargo: {missing_retained}")
            if unexpected:
                reasons.append(f"Unexpected departure cargo: {unexpected}")
            if physical.final_state.yard.quay_buffer:
                reasons.append("Canonical quay buffer is not empty")
            if monotonic() >= deadline:
                metrics["carrier_validation_status"] = "timeout"
                return outcome("timeout", "Canonical carrier physical replay exhausted its deadline")
            if not reasons:
                space = get_action_space("boxslot")
                actions = space.from_plan(canonical, raw.plan)
                expander = Expander(canonical)
                for action in actions:
                    if monotonic() >= deadline:
                        metrics["carrier_validation_status"] = "timeout"
                        return outcome("timeout", "Canonical carrier action conversion exhausted its deadline")
                    expander.step(action)
                expander.finish()
                reasons.extend(securing_state_violations(canonical, expander))
                if not e6_completion_possible(canonical, ship, (), call_index=canonical.call_index):
                    reasons.append("Exact E6 departure completion check failed")
                final_positions = {cid: loc[1] for cid, loc in expander.where.items() if loc[0] == "ship"}
                if final_positions != positions:
                    reasons.append("Action conversion changed the departure inventory or slots")
            if monotonic() >= deadline:
                metrics["carrier_validation_status"] = "timeout"
                return outcome("timeout", "Canonical carrier validation exceeded its deadline")
            metrics["carrier_validation_failures"] = reasons
            if reasons:
                metrics["carrier_validation_status"] = "rejected"
                return outcome("dead_end", "; ".join(reasons))
            boxes = {b.id: b for b in canonical.containers}
            slots = tuple(SlotCategory(positions[cid], category_of(boxes[cid], canonical), cid)
                          for cid in sorted(canonical.load_list))
            places = tuple(a for a in actions if isinstance(a, Place) and a.box in canonical.load_list)
            metrics["carrier_validation_status"] = "ok"
            return outcome("ok", slots=slots, actions=places)
        finally:
            metrics["constructor_validation_time_s"] = perf_counter() - stage
    except Exception as exc:
        if metrics["carrier_validation_status"] == "running":
            metrics["carrier_validation_status"] = "error"
        return outcome("error", f"Canonical constructor {type(exc).__name__}: {exc}")

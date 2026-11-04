"""D68 instance facts from the union of checked methods and reference plans.

"Binding" means a passing plan is near a constraint's limit (Hillier &
Lieberman, Introduction to Operations Research, sensitivity analysis). The
10% threshold below is a reporting convention, not a new checker tolerance.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import math


BINDING_THRESHOLD = 1.10
BINDING_CRITERION = "best passing plan: tightest measured stability ratio across D57 states <= 1.10 (chosen threshold)"


def _physical_witness(record: Mapping) -> bool:
    from eval.harness_metrics import _checked_passed

    # A validated complete solver incumbent can witness physical feasibility
    # after its search deadline, even though the pipeline did not ship it.
    return (_checked_passed(record) and
            (record.get("shippable") is not False or record.get("reference_validated") is True))


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def stability_facts(record: Mapping | None) -> dict:
    """Use measured limits for active Code criteria, across every D57 state.

    Positive minimum criteria use achieved/required; the wind-heel maximum uses
    allowed/actual. Binary applicability flags are excluded: a passing flag is
    not evidence of a physically tight constraint. Missing limits stay unknown.
    """
    out = {"tightest_ratio": None, "binding": None, "criterion": BINDING_CRITERION,
           "binding_threshold": BINDING_THRESHOLD, "tightest_state": None,
           "tightest_criterion": None, "n_measured_criteria": 0,
           "n_measured_states": 0, "gm_margin_m": None}
    if record is None or not _physical_witness(record):
        return out
    ratios, gm_margins, states = [], [], set()
    for name, part in (record.get("report") or {}).get("parts", {}).items():
        if not name.startswith("condition:") or not name.endswith(":stability"):
            continue
        values, margins = part.get("metrics", {}), part.get("margins", {})
        phase = next((key.split(": ", 1)[1] for key in values if key.startswith("condition.phase: ")), name)
        gm = _number(margins.get("gm_m"))
        if gm is not None:
            gm_margins.append(gm)
        criteria = []
        if values.get("criteria_part_b_2_3_active") != 0:
            criteria.extend((key, f"containership_{key}") for key in (
                "area_0_30_mrad", "area_0_40_mrad", "area_30_40_mrad",
                "gz_at_or_above_30_m", "gz_max_m", "area_0_flood_mrad"))
        if values.get("criteria_part_a_2_2_1_to_3_active") != 0:
            criteria.extend((key, key) for key in (
                "area_0_30_mrad", "area_0_40_mrad", "area_30_40_mrad",
                "gz_at_or_above_30_m", "angle_gz_max_rad"))
        if values.get("criteria_initial_gm_active") != 0:
            criteria.append(("gm_m", "gm_m"))
        if values.get("criteria_weather_active") != 0:
            criteria.append(("weather_ratio", "weather_ratio"))
        for value_key, margin_key in criteria:
            achieved, margin = _number(values.get(value_key)), _number(margins.get(margin_key))
            limit = achieved - margin if achieved is not None and margin is not None else None
            if limit is not None and limit > 0:
                ratios.append((achieved / limit, phase, margin_key))
                states.add(phase)
        # Downflooding is an angular minimum; the checker stores its exact
        # clearance from 30 degrees instead of a duplicate measured angle.
        for margin_key, active in (("containership_downflooding_30_margin_rad", "criteria_part_b_2_3_active"),
                                   ("downflooding_30_margin_rad", "criteria_part_a_2_2_1_to_3_active")):
            margin = _number(margins.get(margin_key))
            if values.get(active) != 0 and margin is not None:
                ratios.append(((math.radians(30) + margin) / math.radians(30), phase, margin_key))
                states.add(phase)
        if values.get("criteria_weather_active") != 0:
            heel = _number(values.get("weather_phi0_rad"))
            edge = _number(values.get("weather_deck_edge_rad"))
            clearance = _number(margins.get("weather_steady_heel_margin_rad"))
            if heel is not None and heel > 0 and edge is not None and clearance is not None:
                # Clearance is allowed-minus-actual after any stricter policy.
                allowed = heel + clearance
                if allowed > 0:
                    ratios.append((allowed / heel, phase, "weather_steady_heel"))
                    states.add(phase)
    if ratios:
        ratio, phase, criterion = min(ratios)
        out.update(tightest_ratio=ratio, binding=ratio <= BINDING_THRESHOLD or math.isclose(ratio, BINDING_THRESHOLD, rel_tol=0., abs_tol=1e-12),
                   tightest_state=phase, tightest_criterion=criterion,
                   n_measured_criteria=len(ratios), n_measured_states=len(states))
    out["gm_margin_m"] = min(gm_margins) if gm_margins else None
    return out


def _groups(record_groups):
    if isinstance(record_groups, Mapping):
        yield from record_groups.items()
    else:
        for index, group in enumerate(record_groups):
            if isinstance(group, Mapping) and ("spec_key" in group or "instance_id" in group):
                yield "unspecified", [group]
            else:
                yield str(index), group


def build_instance_facts(record_groups: Mapping[str, Iterable[Mapping]] | Iterable) -> dict:
    """Build one spec ledger from all methods, gold and full solver references.

    A method failure never proves instance infeasibility. A validated physical
    certificate may supply ``proven-infeasible``; any checked passing plan is a
    feasibility witness, including solver-repaired plans. The lowest weighted
    nonproductive-lift witness determines stability facts once for all methods.
    """
    collected = {}
    for method, records in _groups(record_groups):
        for raw in records:
            record = dict(raw)
            key = record.get("spec_key") or record.get("instance_id")
            if key is None:
                continue
            collected.setdefault(str(key), []).append((str(method), record))
            reference = record.get("reference_solver")
            reference_method = reference.get("method") if isinstance(reference, Mapping) else None
            if isinstance(reference, Mapping) and isinstance(reference.get("witness"), Mapping):
                reference = reference["witness"]
            if isinstance(reference, Mapping) and "report" in reference:
                identity = {field: record.get(field) for field in ("spec_key", "instance_id", "input_sha256", "rung", "split")}
                ref = {**identity, **reference}
                if (ref.get("spec_key") or ref.get("instance_id")) != key:
                    raise ValueError("reference spec_key differs from its instance ledger key")
                reference_method = (reference_method or reference.get("method")
                                    or (reference.get("result") or {}).get("planner") or "reference_solver")
                collected[str(key)].append((str(reference_method), ref))
    facts = {}
    for key, candidates in collected.items():
        hashes = {record["input_sha256"] for _, record in candidates if record.get("input_sha256")}
        if len(hashes) > 1:
            raise ValueError(f"mismatched input_sha256 for instance fact {key}")
        witnesses = [(method, record) for method, record in candidates if _physical_witness(record)]
        statuses = {record.get("method_feasibility_status", record.get("feasibility_status")) for _, record in candidates}
        status = ("feasible-witnessed" if witnesses else "proven-infeasible" if "proven-infeasible" in statuses
                  else "generation-rejected" if "generation-rejected" in statuses else "unknown")
        def rank(candidate):
            method, record = candidate
            objective = _number((record.get("report") or {}).get("count", {}).get("weighted_nonproductive"))
            return (objective if objective is not None else math.inf, method,
                    str(((record.get("result") or {}).get("plan") or {}).get("id", "")))
        best = min(witnesses, key=rank) if witnesses else None
        best_record = best[1] if best else None
        facts[key] = {"spec_key": key, "input_sha256": next(iter(hashes), None),
                      "feasibility_status": status, "witness_methods": sorted({method for method, _ in witnesses}),
                      "best_passing_plan": ({"method": best[0], "weighted_nonproductive": _number(
                          (best_record.get("report") or {}).get("count", {}).get("weighted_nonproductive")),
                          "plan_id": ((best_record.get("result") or {}).get("plan") or {}).get("id")}
                          if best else None), "stability": stability_facts(best_record)}
    return facts


def apply_instance_facts(records: Iterable[Mapping], facts: Mapping) -> list[dict]:
    """Join the same immutable spec facts into each method's allocated rows."""
    out = []
    for record in records:
        key = str(record.get("spec_key") or record.get("instance_id"))
        if key not in facts:
            raise ValueError(f"instance fact missing for {key}")
        fact = facts[key]
        if (record.get("input_sha256") and fact.get("input_sha256")
                and record["input_sha256"] != fact["input_sha256"]):
            raise ValueError(f"mismatched input_sha256 when applying instance fact {key}")
        out.append({**record, "method_feasibility_status": record.get("method_feasibility_status", record.get("feasibility_status", "unknown")),
                    "instance_facts": deepcopy(fact), "feasibility_status": fact["feasibility_status"]})
    return out

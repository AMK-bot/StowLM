"""D11 evaluation summaries and paired uncertainty, without ranking illegal plans.

A lift is one crane operation; later-port restows and cover lifts come from the
checker projection, not from counting today's plan text. D11 compares hard-rule
feasibility, shut-outs, weighted nonproductive lifts, then soft-rule/margin ties
in that order (docs/architecture.md and docs/evaluation.md). Objective summaries
and gaps require passing reports and a shipping verdict when recorded; attempted
counts remain separately visible.
Missing bounds, references and margins stay missing, never zero.

Paired bootstrap follows Efron & Tibshirani, *An Introduction to the Bootstrap*
(1993), chapters 5 and 13: sample instance pairs with replacement, recompute the
mean difference, and take percentile endpoints. Pairing removes variation due to
instance difficulty; it does not establish generalisation beyond this set.
"""
from __future__ import annotations

import math
import random
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from statistics import fmean
from typing import Any

from eval.yard_metrics import (AVOIDABLE_YARD_METRIC, congestion_mixing, mixing_share,
                               mixing_stratum, yard_rehandles)
from eval.sample_reporting import aggregate_sample_reporting, is_spark_record
from eval.plan_diagnostics import summarise_plan_diagnostics

MOVE_MEAN_POPULATION = "over this method's passing plans; not comparable across rows"


def _release_pin(rows: list[Mapping]) -> dict:
    """Retain one complete release pin; legacy entirely unlabelled rows stay valid."""
    pins = set()
    for row in rows:
        version, digest = row.get('split_version'), row.get('split_entries_sha256')
        if version is None and digest is None:
            if row.get('test_family') == 'dg_stress' or _dict(row.get('spec')).get('cargo_profile') == 'dg_stress':
                raise ValueError('DG-stress reporting requires a complete split release pin')
            pins.add(None)
        elif not all(isinstance(value, str) and value.strip() for value in (version, digest)):
            raise ValueError('incomplete split release version/digest pin')
        else:
            pins.add((version, digest))
    if len(pins) > 1:
        raise ValueError('mixed or partially labelled split releases cannot be pooled')
    pin = next(iter(pins), None)
    if pin is None:
        return {}
    result = dict(split_version=pin[0], split_entries_sha256=pin[1])
    if pin[0].startswith('dg-stress'):
        from eval.dg_coverage import CLAIM_LABEL, stress_release_policy
        stress_release_policy(pin[0], pin[1])
        result['claim_label'] = CLAIM_LABEL
    return result


def _release_label(summary: Mapping) -> str:
    version = summary.get('split_version')
    if not version:
        return ''
    label = f" [release {version}; entries {summary.get('split_entries_sha256') or 'unknown'}]"
    if str(version).startswith('dg-stress'):
        from eval.dg_coverage import CLAIM_LABEL, stress_release_policy
        stress_release_policy(version, summary.get('split_entries_sha256'))
        label += f" [{CLAIM_LABEL}]"
    return label


def _dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _stats(values: Iterable[float | None]) -> dict:
    vals = [v for v in values if v is not None]
    return {"n": len(vals), "sum": sum(vals) if vals else None,
            "mean": fmean(vals) if vals else None,
            "min": min(vals) if vals else None, "max": max(vals) if vals else None}


def _report(record: Mapping) -> dict:
    return _dict(record.get("report"))


def _count(record: Mapping) -> dict:
    return _dict(_report(record).get("count"))


def _checked_passed(record: Mapping) -> bool:
    report = _report(record)
    return (record.get("report_binding_valid") is not False
            and report.get("passed") is True and not _checker_timeout(record)
            and not internal_error_causes(record)
            and not any(v.get("severity") == "hard" for v in report.get("violations", []))
            and all(_part_evaluated(_dict(part))
                    and not any(v.get("severity") == "hard" for v in _dict(part).get("violations", []))
                    for part in report.get("parts", {}).values()))


def _passed(record: Mapping, population: str = "own") -> bool:
    if population not in ("own", "shipped"):
        raise ValueError("population must be 'own' or 'shipped'")
    return (_checked_passed(record) and not (is_spark_record(record) and _dict(record.get('result')).get('repaired')) and
            (not _dict(record.get("result")).get("repaired") if population == "own"
             else record.get("shippable", True) is True))


def _part_evaluated(part: Mapping) -> bool:
    return (part.get("ran", True) is True and not part.get("error")
            and not any(v.get('rule_id') == 'checker.internal_error' for v in part.get('violations', [])))


def _checker_timeout(record: Mapping) -> bool:
    return any(_dict(_dict(part).get("metrics")).get("time_budget_exceeded") == 1
               for part in _report(record).get("parts", {}).values())


def internal_error_causes(record: Mapping) -> list[str]:
    """Count failed calculations by recorded cause; replay context stays in the raw report."""
    causes, part_errors = set(), set()
    for name, raw in _report(record).get('parts', {}).items():
        part = _dict(raw)
        errors = [v for v in part.get('violations', []) if v.get('rule_id') == 'checker.internal_error']
        part_errors.update(str(v.get('message', 'checker.internal_error')) for v in errors)
        if _dict(part.get('metrics')).get('time_budget_exceeded') == 1:
            continue
        messages = [str(v.get('message', part.get('error') or 'calculation unavailable')) for v in errors]
        if not messages and part.get('error') and not any(
                v.get('rule_id') == 'checker.missing_vessel_data' for v in part.get('violations', [])):
            messages = [str(part['error'])]
        for message in messages:
            message = re.sub(r'^\[in_port [^]]+\]\s*', '', message)
            message = re.sub(r'^state=[^:]+:\s*', '', message)
            causes.add(f'{name}: {message}')
    for violation in _report(record).get('violations', []):
        if violation.get('rule_id') == 'checker.internal_error':
            message = str(violation.get('message', 'checker.internal_error'))
            if message not in part_errors:
                causes.add(f'report: {message}')
    return sorted(causes)


def mask_time_limit(record: Mapping) -> bool:
    """Read the masked planner's explicit deadline flag, including an original repaired draft."""
    result = _dict(record.get('result'))
    metrics = _dict(result.get('metrics'))
    original = _dict(metrics.get('repair_original_metrics'))
    for source in (metrics, original):
        if source.get('mask_time_limit') is True or source.get('mask_time_limit_exceeded') is True:
            return True
        name = str(record.get('planner') or record.get('method') or result.get('planner') or '').lower()
        if source.get('deadline_exceeded') is True and ('stowlm' in name or 'mask_version' in source):
            return True
    return False


def checker_outcome(record: Mapping) -> str:
    """A checker infrastructure failure is never evidence of infeasibility."""
    if record.get("allocation_only"):
        return "generation_rejected" if record.get("feasibility_status") == "generation-rejected" else "not_evaluated"
    parts = [_dict(part) for part in _report(record).get("parts", {}).values()]
    unrun_rules = {v.get("rule_id") for part in parts if not _part_evaluated(part)
                   for v in part.get("violations", [])}
    if (any(_part_evaluated(part) and any(v.get("severity") == "hard" for v in part.get("violations", []))
            for part in parts) or any(v.get("severity") == "hard" and v.get("rule_id") not in unrun_rules
                and v.get("rule_id") not in ("checker.internal_error", "checker.missing_vessel_data")
                for v in _report(record).get("violations", []))):
        return "failed_check"
    if _checker_timeout(record):
        return "checker_timeout"
    if internal_error_causes(record):
        return 'unevaluated_internal_error'
    if mask_time_limit(record) and not _checked_passed(record):
        return 'mask_time_limit'
    if not _report(record):
        return "missing_report"
    if any(not _part_evaluated(_dict(part)) for part in _report(record).get("parts", {}).values()):
        return "unevaluated"
    return "passed" if _checked_passed(record) else "failed_check"


def global_screen_status(record: Mapping) -> str | None:
    """Read the engine diagnostic preserved by the producer, without guessing."""
    result = _dict(record.get("result"))
    metrics = _dict(result.get("metrics"))
    value = record.get("mask_global_screen", metrics.get("global_screen", metrics.get("mask_global_screen")))
    if value is None:
        value = _dict(metrics.get("diagnostics", result.get("diagnostics"))).get("global_screen")
    return str(value) if value is not None else None


def _mask_unavailable(record: Mapping) -> bool:
    return (global_screen_status(record) or "").startswith("unavailable")


def arrival_stability(record: Mapping) -> dict:
    """Final-plan arrival B 2.3 minimum achieved/required ratio and GM margin.

    The six minima are IS Code B 2.3.2.1–5 coefficients divided by C; measured
    checker margins recover overridden limits, so policy is never silently reset.
    GM margin is the checker's operational-loading ``gm_m`` margin.
    """
    missing = {"b2_3_ratio": None, "gm_margin_m": None}
    if record.get("report_binding_valid") is False:
        return missing
    arrival_id = _dict(_dict(_dict(record.get("instance")).get("voyage")).get("arrival")).get("id")
    for name, raw in _report(record).get("parts", {}).items():
        part = _dict(raw)
        values, margins = _dict(part.get("metrics")), _dict(part.get("margins"))
        if (not name.startswith("condition:") or not name.endswith(":stability")
                or not ("condition.phase: arrival" in values or name == f"condition:{arrival_id}:stability")
                or not _part_evaluated(part)):
            continue
        if values.get("criteria_part_b_2_3_active") == 0:
            return {"b2_3_ratio": None, "gm_margin_m": _number(margins.get("gm_m"))}
        criteria = ("area_0_30_mrad", "area_0_40_mrad", "area_30_40_mrad",
                    "gz_at_or_above_30_m", "gz_max_m", "area_0_flood_mrad")
        ratios = []
        for key in criteria:
            measured = _number(values.get(key))
            margin = _number(margins.get(f"containership_{key}"))
            required = measured - margin if measured is not None and margin is not None else None
            ratios.append(measured / required if measured is not None and required is not None and required > 0 else None)
        gm = _number(margins.get("gm_m"))
        return {"b2_3_ratio": min(ratios) if all(v is not None for v in ratios) else None,
                "gm_margin_m": gm}
    return missing


def wilson_interval(successes: int, total: int) -> list[float] | None:
    """Wilson 95% score interval bounds a binomial pass rate (Wilson 1927; Agresti & Coull 1998)."""
    if not total:
        return None
    z = 1.959963984540054
    p, z2 = successes / total, z * z
    centre = (p + z2 / (2 * total)) / (1 + z2 / total)
    half = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / (1 + z2 / total)
    return [max(0., centre - half), min(1., centre + half)]


def _stability_summary(rows: list[dict]) -> dict:
    from eval.instance_facts import BINDING_CRITERION, build_instance_facts

    # Consolidated runs supply the union over all methods. Standalone summaries
    # may only describe the witnesses provided to this call; label that scope.
    facts = {str(row.get("spec_key") or row.get("instance_id")): row["instance_facts"]
             for row in rows if row.get("instance_facts")}
    local = build_instance_facts({"available_records": rows}) if len(facts) < len(rows) else {}
    for key, fact in local.items():
        facts.setdefault(key, fact)
    values = [fact["stability"] for fact in facts.values()]
    measured = [value for value in values if value["binding"] is not None]
    n_binding = sum(value["binding"] is True for value in measured)
    return {"n_instances": len(facts), "n_measured": len(measured), "n_binding": n_binding,
            "binding_share": n_binding / len(measured) if measured else None,
            "n_unknown": len(facts) - len(measured), "criterion": BINDING_CRITERION,
            "facts_scope": "all supplied methods and references" if rows and all(row.get("instance_facts") for row in rows)
                           else "available records only; consolidate methods before family claims"}


def _allocation_summary(rows: list[dict], population: str = "own") -> dict:
    if rows and all(is_spark_record(row) for row in rows):
        population = 'own'
    release = _release_pin(rows)
    if str(release.get('split_version', '')).startswith('dg-stress'):
        from eval.dg_coverage import coverage_summary
        release['dg_coverage'] = coverage_summary(rows)
    statuses = ("feasible-witnessed", "proven-infeasible", "unknown", "generation-rejected")
    counts = Counter(row.get("feasibility_status") or ("feasible-witnessed" if _checked_passed(row) else "unknown")
                     for row in rows)
    if counts.keys() - set(statuses):
        raise ValueError("unrecognised allocated-spec feasibility_status")
    attempted = [row for row in rows if not row.get("allocation_only")]
    n_attempted, n = len(attempted), len(rows)
    own = sum(_passed(row) for row in attempted)
    shipped = sum(_passed(row, "shipped") for row in attempted)
    passed = own if population == "own" else shipped
    rejected = sum(bool(row.get("allocation_only")) and row.get("feasibility_status") == "generation-rejected" for row in rows)
    not_attempted = n - n_attempted - rejected
    fallback_events = [_fallback(row) for row in attempted]
    fallback_count = sum(event is True for event in fallback_events)
    fallback_unknown = sum(event is None for event in fallback_events)
    n_repaired = sum(_dict(row.get("result")).get("repaired") is True for row in attempted)
    outcomes = Counter(checker_outcome(row) for row in rows)
    internal = Counter(cause for row in attempted for cause in internal_error_causes(row))
    infrastructure = {'checker_timeout', 'unevaluated', 'unevaluated_internal_error', 'mask_time_limit'}
    method_failed = sum(not _passed(row, population) and checker_outcome(row) not in infrastructure
        and not (mask_time_limit(row) and checker_outcome(row) != 'failed_check') for row in attempted)
    return {**release, "yard_rehandles": _yard_summary(rows, population),
            'plan_diagnostics': summarise_plan_diagnostics(rows, population=population),
            'sample_reporting': aggregate_sample_reporting(rows, population=population),
            "n_allocated": len(rows), "n_instances": sum(not row.get("allocation_only") for row in rows),
            "n_emitted": sum(not row.get('allocation_only') or row.get('emitted') is True for row in rows),
            "population": population, "n_passed": passed, "pass_rate": passed / len(rows) if rows else None,
            "pass_rate_ci95": wilson_interval(passed, len(rows)),
            "n_own_passed": own, "n_shipped_passed": shipped,
            "own_pass_rate": own / n if n else None, "shipped_pass_rate": shipped / n if n else None,
            "own_pass_rate_ci95": wilson_interval(own, n), "shipped_pass_rate_ci95": wilson_interval(shipped, n),
            "n_attempted": n_attempted, "n_generation_rejected": rejected, "n_not_attempted": not_attempted,
            "n_method_failed": method_failed,
            "fallback_count": fallback_count, "fallback_unknown_count": fallback_unknown,
            "fallback_rate": fallback_count / n_attempted if n_attempted and not fallback_unknown else None,
            "n_repaired": n_repaired, "repair_rate": n_repaired / n_attempted if n_attempted else None,
            "attempted_pass_rate": passed / n_attempted if n_attempted else None,
            "attempted_pass_rate_ci95": wilson_interval(passed, n_attempted),
            "own_attempted_pass_rate": own / n_attempted if n_attempted else None,
            "shipped_attempted_pass_rate": shipped / n_attempted if n_attempted else None,
            "own_attempted_pass_rate_ci95": wilson_interval(own, n_attempted),
            "shipped_attempted_pass_rate_ci95": wilson_interval(shipped, n_attempted),
            "n_known_feasible_failed": sum((row.get("feasibility_status") == "feasible-witnessed" or _checked_passed(row))
                                            and not _passed(row, population) for row in attempted),
            "feasibility_status_counts": {status: counts[status] for status in statuses},
            "feasibility_certificate_note": (
                "no accepted physical infeasibility certificate supplied; the proven-infeasible bucket stays empty"
                if not counts["proven-infeasible"] else
                "proven-infeasible requires an accepted physical-instance or physical-relaxation certificate"),
            "checker_outcome_counts": dict(sorted(outcomes.items())),
            "n_checker_timeout": outcomes["checker_timeout"], "n_failed_check": outcomes["failed_check"],
            'n_internal_error': sum(bool(internal_error_causes(row)) for row in attempted),
            'internal_error_causes': dict(sorted(internal.items())),
            'n_mask_time_limit': sum(mask_time_limit(row) for row in attempted),
            "n_unevaluated": outcomes["unevaluated"] + outcomes['unevaluated_internal_error'], "stability": _stability_summary(rows)}


def _fallback(record: Mapping) -> bool | None:
    result = _dict(record.get("result"))
    metrics = _dict(result.get("metrics"))
    if result.get("repaired"):
        return True
    flags = [value for value in (record.get("fallback_needed"), metrics.get("fallback_needed"))
             if isinstance(value, bool)]
    return any(flags) if flags else None


def _objective_eligible(record: Mapping, population: str = "own") -> bool:
    # New harness records carry the pipeline's private-evidence shipping verdict.
    # Historical records have only the report verdict; preserve their readability.
    return _passed(record, population) and ("shippable" not in record or record["shippable"] is True)


def _yard_summary(records, population='own'):
    measured = [yard_rehandles(row) for row in records
                if not row.get('allocation_only') and _objective_eligible(row, population)]
    return {key: _stats(row[key] for row in measured) for key in ('total', 'lower_bound', 'avoidable')} | {
        'n_no_certificate': sum(row['lower_bound'] == 0 for row in measured),
        'n_missing_lower_bound': sum(row['lower_bound'] is None for row in measured),
        'definition': 'avoidable yard rehandles = total yard rehandles - yard lower bound',
        'population': f'{population} passing plans',
    }


def voyage_conditions(record: Mapping) -> dict:
    """Expose D50's two endpoint verdicts without inferring absent evidence.

    The full checker stores endpoint parts under ``condition:<id>:<part>``;
    IDs may themselves contain colons. Identity labels are preserved from the
    report, with the declared voyage identity available even if checking failed
    before a part could run. A condition passes only if both physics parts ran
    without an error or hard violation. This reports evidence, not a new check.
    """
    spec = _dict(_dict(record.get("instance")).get("voyage"))
    out = {}
    for phase in ("departure", "arrival"):
        identity = _dict(spec.get(phase))
        out[phase] = {"condition_id": identity.get("id"), "phase": phase,
                      **{key: identity.get(key) for key in (
                          "call_index", "locode", "environment_id", "density_id", "water_density_kg_m3")},
                      "passed": None, "parts": {name: {"passed": None, "ran": False,
                                                        "error": None, "hard_rule_ids": []}
                                                 for name in ("stability", "strength")}}
    if record.get("report_binding_valid") is False:
        return out
    for key, raw_part in _report(record).get("parts", {}).items():
        if not key.startswith("condition:") or ":" not in key[len("condition:"):]:
            continue
        identity, name = key[len("condition:"):].rsplit(":", 1)
        if name not in ("stability", "strength"):
            continue
        part = _dict(raw_part)
        metrics = _dict(part.get("metrics"))
        labels = {field: next((k[len(f"condition.{field}: "):] for k in metrics
                                if k.startswith(f"condition.{field}: ")), None)
                  for field in ("id", "phase", "locode", "environment_id", "density_id")}
        phase = labels["phase"]
        if phase not in out:
            phase = next((p for p, value in out.items() if value["condition_id"] == identity), None)
        if phase not in out:
            continue
        condition = out[phase]
        condition["condition_id"] = identity
        for field in ("locode", "environment_id", "density_id"):
            if labels[field] is not None:
                condition[field] = labels[field]
        for field in ("call_index", "water_density_kg_m3"):
            field_value = _number(metrics.get(f"condition.{field}"))
            if field_value is not None:
                condition[field] = field_value
        rules = sorted({v.get("rule_id", "unknown") for v in part.get("violations", [])
                        if v.get("severity") == "hard"})
        ran, error = part.get("ran", True) is True, part.get("error")
        evaluated = _part_evaluated(part)
        condition["parts"][name] = {"passed": evaluated and not rules,
                                     "ran": ran, "error": error, "evaluated": evaluated,
                                     "hard_rule_ids": rules if evaluated else [],
                                     "unevaluated_rule_ids": [] if evaluated else rules or ["checker.internal_error"]}
    for condition in out.values():
        verdicts = [part["passed"] for part in condition["parts"].values()]
        condition["passed"] = False if False in verdicts else (True if all(v is True for v in verdicts) else None)
    return out


def _voyage_summary(rows: list[dict]) -> dict:
    conditions = [voyage_conditions(row) for row in rows]
    out = {}
    for phase in ("departure", "arrival"):
        values = [value[phase] for value in conditions]
        evaluated = sum(value["passed"] is not None and not any(
            part.get("evaluated") is False for part in value["parts"].values()) for value in values)
        passed = sum(value["passed"] is True for value in values)
        failed_parts = Counter(name for value in values for name, part in value["parts"].items()
                               if part["passed"] is False and part.get("evaluated") is not False)
        unevaluated_parts = Counter(name for value in values for name, part in value["parts"].items()
                                   if part.get("evaluated") is False)
        failed_rules = Counter(rule for value in values for part in value["parts"].values()
                              for rule in part["hard_rule_ids"])
        unevaluated = Counter(rule for value in values for part in value["parts"].values()
                              for rule in part.get("unevaluated_rule_ids", []))
        out[phase] = {"n_instances": len(rows), "n_evaluated": evaluated, "n_passed": passed,
                      "n_failed": evaluated - passed, "n_missing": len(rows) - evaluated,
                      "pass_rate": passed / len(rows) if evaluated else None,
                      "failed_parts": dict(sorted(failed_parts.items())),
                      "unevaluated_parts": dict(sorted(unevaluated_parts.items())),
                      "n_unevaluated": sum(any(part.get("evaluated") is False
                                                for part in value["parts"].values()) for value in values),
                      "hard_rule_counts": dict(sorted(failed_rules.items())),
                      "unevaluated": dict(sorted(unevaluated.items()))}
    return out


def _sample_counts(record: Mapping) -> tuple[int | None, int | None]:
    metrics = _dict(_dict(record.get("result")).get("metrics"))
    def count(value):
        numeric = _number(value)
        return int(numeric) if numeric is not None and numeric >= 0 and numeric.is_integer() else None
    dead = count(metrics.get("n_dead_end", metrics.get("mask_dead_ends")))
    samples = count(metrics.get("n_samples"))
    # A sample rate needs a known population, not an assumed one-sample run.
    if dead is None or samples is None or dead > samples:
        samples = None
    return dead, samples


def _lifts(count: Mapping) -> dict:
    fields = ("loads", "discharges", "relocations", "restow_lifts", "hatch_cover_lifts", "gate_moves")
    out = {key: _number(count.get(key, 0)) for key in fields}
    future = [_dict(v) for v in count.get("future", [])]
    out["future_restow_lifts"] = sum(v.get("restow_lifts", 0) for v in future)
    out["future_hatch_cover_lifts"] = sum(v.get("hatch_cover_lifts", 0) for v in future)
    out["future_restowed_boxes"] = sum(v.get("restowed_boxes", 0) for v in future)
    out["total_lifts_now"] = sum(out[k] for k in fields if k != "gate_moves")
    out["nonproductive_lifts_now"] = sum(out[k] for k in ("relocations", "restow_lifts", "hatch_cover_lifts"))
    out["nonproductive_lifts_including_projection"] = out["nonproductive_lifts_now"] + out["future_restow_lifts"] + out["future_hatch_cover_lifts"]
    out["weighted_nonproductive"] = _number(count.get("weighted_nonproductive"))
    return out


def _reference(value: Any) -> float | None:
    numeric = _number(value)
    if numeric is not None:
        return numeric
    data = _dict(value)
    if "report" in data:
        return _number(_count(data).get("weighted_nonproductive")) if _passed(data) else None
    return _number(data.get("weighted_nonproductive", data.get("weighted")))


def _gap(records: list[dict], field: str) -> dict:
    absolute, relative = [], []
    for record in records:
        objective = _number(_count(record).get("weighted_nonproductive"))
        reference = _reference(record.get(field))
        if objective is None or reference is None:
            continue
        absolute.append(objective - reference)
        # A positive objective above a zero bound has an undefined relative gap.
        relative.append((objective - reference) / reference if reference else (0.0 if objective == 0 else None))
    return {"absolute": _stats(absolute), "relative": _stats(relative),
            "definition": "(weighted_nonproductive - reference) / reference; zero/zero = 0; positive/zero = null"}


def _paired_solver_gap(records: list[dict], population: str = "own") -> dict:
    """Compare each plan to its attached, input-matched solver reference.

    The harness validates attached solver values before aggregation. Historical
    numeric references retain that contract; complete reference records also
    retain their checker/shipping verdicts and input identity checks.
    """
    first, second = [], []
    identity_fields = ("input_sha256", "spec_key", "split", "rung", "split_version", "split_entries_sha256")
    for index, record in enumerate(records):
        # Aggregation also reads historical records without unique instance IDs;
        # pair each attached reference to its own row, retaining input identities.
        pair_id = f"{index}:{record.get('instance_id', 'unidentified')}"
        first.append({**record, "instance_id": pair_id})
        raw = record.get("reference_solver")
        reference = _dict(raw)
        if isinstance(reference.get("witness"), Mapping):
            reference = _dict(reference["witness"])
        if "report" in reference:
            if ("instance_id" in reference and "instance_id" in record
                    and reference["instance_id"] != record["instance_id"]):
                raise ValueError("mismatched instance_id in attached solver reference")
            second.append({**{key: record.get(key) for key in identity_fields},
                           **reference, "instance_id": pair_id})
        else:
            value = _reference(raw)
            second.append({**{key: record.get(key) for key in identity_fields},
                           "instance_id": pair_id, "shippable": value is not None,
                           "report": {"passed": value is not None, "count": {
                               "weighted_nonproductive": value}} if value is not None else None})
    comparison = paired_comparison(first, second, population=population)
    pairs = comparison["per_instance"]
    absolute = [pair["difference"] for pair in pairs]
    relative = [pair["difference"] / pair["second"] if pair["second"] else
                0. if pair["difference"] == 0 else None
                for pair in pairs if pair["difference"] is not None]
    return {"absolute": _stats(absolute), "relative": _stats(relative),
            "definition": "paired (weighted_nonproductive - solver reference) / solver reference; zero/zero = 0; positive/zero = null",
            "source": "paired_comparison", "n_pairs": comparison["n_pairs"],
            "n_excluded": comparison["n_excluded"], "comparison": comparison}


def aggregate_results(records: Iterable[Mapping], *, method: str | None = None,
                      split: str | None = None, tiny_run: bool = False,
                      population: str = "own") -> dict:
    """Summarise every attempt; objective/gap/margins require eligible checked passes.

    Pass rates use all allocated specs, including ``allocation_only`` records;
    operational rates use attempted instances. A missing report has unknown
    shut-outs. Failed/partial move counts are
    retained under ``attempted_moves`` and excluded from objective comparisons.
    Fallback is the recorded need for repair, including unsuccessful repair;
    absent repair-event evidence leaves that rate unknown rather than inferring
    it from the final pass rate. Shipping verdicts are distinct from report passes.
    """
    if population not in ("own", "shipped"):
        raise ValueError("population must be 'own' or 'shipped'")
    allocated_rows = [dict(r) for r in records]
    if method:
        for row in allocated_rows:
            row.setdefault('method', method)
    if allocated_rows and all(is_spark_record(row) for row in allocated_rows):
        population = 'own'
    _release_pin(allocated_rows)  # Reject release mixing before derived comparisons.
    rows = [r for r in allocated_rows if not r.get("allocation_only")]
    passed = [r for r in rows if _passed(r, population)]
    eligible = [r for r in rows if _objective_eligible(r, population)]
    known_counts = [r for r in rows if _count(r)]
    checked_counts = [r for r in eligible if _count(r)]
    weighted_types = {"relocations": "relocate", "restow_lifts": "restow",
                      "hatch_cover_lifts": "hatch_cover", "future_restow_lifts": "future_restow",
                      "future_hatch_cover_lifts": "future_hatch_cover"}
    weighted_by_type = {key: [] for key in weighted_types}
    for r in checked_counts:
        weights = _dict(_dict(r.get("instance")).get("objective"))
        counts = _lifts(_count(r))
        for key, weight_name in weighted_types.items():
            weight = _number(weights.get(weight_name))
            weighted_by_type[key].append(counts[key] * weight if weight is not None else None)
    n = len(rows)
    def moves(population):
        counts = [_lifts(_count(r)) for r in population]
        keys = _lifts({}).keys()
        return {k: _stats(c[k] for c in counts) for k in keys} | {
            AVOIDABLE_YARD_METRIC: _stats(yard_rehandles(r)['avoidable'] for r in population)}
    severity, hard_rules, unevaluated = Counter(), Counter(), Counter()
    for r in rows:
        unrun_rules, evaluated_rules = set(), set()
        for part in _report(r).get("parts", {}).values():
            rules = {v.get("rule_id", "unknown") for v in _dict(part).get("violations", [])
                     if v.get("severity") == "hard"}
            if _part_evaluated(_dict(part)):
                evaluated_rules.update(rules)
            else:
                unrun_rules.update(rules or {"checker.internal_error"})
        if any(v.get('rule_id') == 'checker.internal_error' for v in _report(r).get('violations', [])):
            unrun_rules.add('checker.internal_error')
        unevaluated.update(unrun_rules)
        evaluated_rules.update(v.get("rule_id", "unknown") for v in _report(r).get("violations", [])
                               if v.get("severity") == "hard" and v.get("rule_id", "unknown") not in unrun_rules)
        hard_rules.update(evaluated_rules)
        severity.update(v.get("severity", "unknown") for v in _report(r).get("violations", [])
                        if v.get("rule_id", "unknown") not in unrun_rules
                        or v.get("rule_id", "unknown") in evaluated_rules)
    margins, physics = {}, {}
    for r in eligible:
        for name, part in _report(r).get("parts", {}).items():
            for k, v in part.get("margins", {}).items():
                margins.setdefault(f"{name}.{k}", []).append(_number(v))
            for k in ("gm_m", "max_sf_ratio", "max_bm_ratio"):
                if k in part.get("metrics", {}):
                    physics.setdefault(f"{name}.{k}", []).append(_number(part["metrics"][k]))
    provenance = []
    for r in rows:
        provenance.extend([_dict(r.get("provenance")), _dict(_dict(r.get("result")).get("provenance")),
                           _dict(_report(r).get("provenance")),
                           _dict(_dict(_dict(r.get("result")).get("plan")).get("provenance"))])
    stubs = {k: v for p in provenance for k, v in p.get("stubs", {}).items()}
    versions: dict[str, set] = {}
    for p in provenance:
        for k, v in p.get("versions", {}).items():
            versions.setdefault(k, set()).add(v)
        for k, values in p.get("input_versions", {}).items():
            versions.setdefault(k, set()).update(values)
    timings = [_dict(r.get("timing")) for r in rows]
    fallback_events = [_fallback(r) for r in rows]
    fallback_count = sum(event is True for event in fallback_events)
    fallback_unknown = sum(event is None for event in fallback_events)
    repaired = sum(_dict(r.get("result")).get("repaired") is True for r in rows)
    mask_unavailable = sum(_mask_unavailable(r) for r in rows)
    mask_observed = [r for r in rows if not _mask_unavailable(r) and not mask_time_limit(r)]
    mask_available_count = len(mask_observed)
    dead_samples = [_sample_counts(r) for r in mask_observed]
    known_sample_total = sum(samples for _, samples in dead_samples if samples is not None)
    known_sample_dead = sum(dead for dead, samples in dead_samples if samples is not None)
    shipping_rows = [r for r in rows if "shippable" in r]
    references = [_dict(r.get("reference_solver")) for r in eligible if r.get("reference_solver") is not None]
    reference_metadata = {label: sorted({str(r.get(field, "unspecified")) for r in references})
                          for label, field in (("methods", "method"), ("proof_statuses", "proof_status"),
                                               ("optimality_scopes", "optimality_scope"))}
    result = {"n_instances": n, "n_passed": len(passed), "n_missing_reports": sum(not _report(r) for r in rows),
              "pass_rate": len(passed) / n if n else None,
              "n_shippable": sum(r["shippable"] is True for r in shipping_rows),
              "n_shipping_verdicts": len(shipping_rows),
              "shipping_rate": sum(r["shippable"] is True for r in shipping_rows) / len(shipping_rows) if shipping_rows else None,
              "shut_outs": _stats(len(_report(r).get("shut_out", [])) if _report(r) else None for r in rows),
              "fallback_count": fallback_count, "fallback_unknown_count": fallback_unknown,
              "fallback_rate": fallback_count / n if n and not fallback_unknown else None,
              "n_repaired": repaired, "repair_rate": repaired / n if n else None,
              "repair_prefix_share": _stats(_number(_dict(r.get("repair_prefix")).get("share"))
                                              for r in rows if _dict(r.get("result")).get("repaired")),
              "moves": moves(checked_counts), "attempted_moves": moves(known_counts),
              "moves_population": MOVE_MEAN_POPULATION,
              "weighted_nonproductive_by_type": {k: _stats(v) for k, v in weighted_by_type.items()},
              "objective_population": f"{population} passing plans; shipping verdict required when recorded",
              "gap_to_lower_bound": _gap(eligible, "lower_bound"),
              "gap_to_solver": _paired_solver_gap(allocated_rows, population),
              "solver_references": reference_metadata,
              "planning_time_s": _stats(_number(t.get("plan_s", t.get("planning_wall_time_s", _dict(r.get("result")).get("wall_time_s")))) for r, t in zip(rows, timings)),
              "pipeline_time_s": _stats(_number(t.get("total_s", t.get("pipeline_wall_time_s"))) for t in timings),
              "violation_counts": dict(sorted(severity.items())),
              "margins": {k: _stats(v) for k, v in sorted(margins.items())},
              "physics": {k: _stats(v) for k, v in sorted(physics.items())},
              "mask_dead_end_count": sum(dead or 0 for dead, _ in dead_samples),
              "mask_dead_end_rate": sum(bool(dead) for dead, _ in dead_samples) / mask_available_count if mask_available_count else None,
              "mask_dead_end_instance_rate": sum(bool(dead) for dead, _ in dead_samples) / mask_available_count if mask_available_count else None,
              "mask_sample_count": known_sample_total,
              "mask_dead_end_sample_rate": known_sample_dead / known_sample_total if known_sample_total else None,
              "dead_end_rate": sum(_dict(r.get("result")).get("status") == "dead_end"
                                   for r in mask_observed) / mask_available_count if mask_available_count else None,
              "mask_available_instance_count": mask_available_count,
              'mask_denominator_note': 'available, non-time-limited mask attempts only',
              "mask_unavailable_count": mask_unavailable,
              "mask_unavailable_rate": mask_unavailable / n if n else None,
              "hard_rule_counts": dict(sorted(hard_rules.items())),
              "unevaluated": dict(sorted(unevaluated.items())),
              "voyage_conditions": _voyage_summary(allocated_rows),
              "provenance": {"stubs": stubs, "versions": {k: sorted(v) for k, v in sorted(versions.items())},
                             "mixed_versions": any(len(v) > 1 for v in versions.values())},
              "tiny_run": tiny_run, "method": method, "split": split}
    result.update(_allocation_summary(allocated_rows, population))
    result["training_epoch_mismatch_count"] = sum(row.get("training_epoch_differs") is True for row in rows)
    result["training_epoch_known_count"] = sum(isinstance(row.get("training_epoch_differs"), bool) for row in rows)
    families: dict[str, list[dict]] = {}
    for row in allocated_rows:
        family = row.get("test_family") or _dict(row.get("spec")).get("family") or "unspecified"
        families.setdefault(str(family), []).append(row)
    result["training_lineage_unknown_count"] = sum(row.get("training_lineage_unknown") is True for row in rows)
    result["training_version_mismatches"] = sorted({key for row in rows for key in row.get("training_version_mismatches", [])})
    result["by_family"] = {family: _allocation_summary(family_rows, population)
                           for family, family_rows in sorted(families.items())}
    result["by_family_rung"] = {family: {
        rung: _allocation_summary([row for row in family_rows if str(row.get("rung", "unspecified")) == rung], population)
        for rung in sorted({str(row.get("rung", "unspecified")) for row in family_rows})}
        for family, family_rows in sorted(families.items())}
    result['by_rung'] = {rung: _allocation_summary(
        [row for row in allocated_rows if str(row.get('rung', 'unspecified')) == rung], population)
        for rung in sorted({str(row.get('rung', 'unspecified')) for row in allocated_rows})}
    ships = {}
    from stowage.conventions import canonical_vessel_id
    for row in allocated_rows:
        ship = _dict(row.get('instance')).get('vessel_class') or _dict(row.get('spec')).get('vessel') or 'unknown'
        ships.setdefault(canonical_vessel_id(str(ship)), []).append(row)
    result['by_ship'] = {ship: _allocation_summary(group, population) for ship, group in sorted(ships.items())}
    result['by_ship_rung'] = {ship: {rung: _allocation_summary(
        [row for row in group if row.get('rung', 'unspecified') == rung], population)
        for rung in sorted({row.get('rung', 'unspecified') for row in group})}
        for ship, group in sorted(ships.items())}
    mixing_groups = {}
    for row in allocated_rows:
        key, controls = mixing_stratum(row)
        group = mixing_groups.setdefault(key, {'provenance': controls, 'rows': []})
        group['rows'].append(row)
    result['by_congestion_mixing'] = {key: {
        **_allocation_summary(group['rows'], population), 'provenance': group['provenance'],
        'achieved_share': _stats(mixing_share((congestion_mixing(row) or {}).get('achieved_share'))
                                for row in group['rows'])}
        for key, group in sorted(mixing_groups.items())}
    if str(result.get('split_version', '')).startswith('dg-stress'):
        from eval.dg_coverage import pair_cohort, stress_release_policy
        policy = stress_release_policy(result['split_version'], result['split_entries_sha256'])
        cohorts = {}
        for row in allocated_rows:
            cohorts.setdefault(pair_cohort(row, policy), []).append(row)
        result['by_dg_stress_pair'] = {cohort: _allocation_summary(group, population)
                                       for cohort, group in sorted(cohorts.items())}
        result['by_dg_stress_pair_rung'] = {cohort: {
            rung: _allocation_summary([row for row in group if row.get('rung', 'unspecified') == rung], population)
            for rung in sorted({row.get('rung', 'unspecified') for row in group})}
            for cohort, group in sorted(cohorts.items())}
    return result


def render_row(summary: Mapping, *, method: str | None = None, instances: str | None = None,
               run: str = "", tiny_run: bool = False, date: str | None = None) -> str:
    """Render D11/D50 evidence, Wilson bounds and distinct infrastructure columns.

    A tainted row must be explicitly tiny-run. This check complements the
    harness's require_clean gate; it does not replace artifact validation.
    """
    tiny_run = tiny_run or bool(summary.get("tiny_run"))
    if _dict(summary.get("provenance")).get("stubs") and not tiny_run:
        raise ValueError("stub-tainted results require a clearly marked tiny-run row")
    def fmt(value):
        return "N/A" if value is None else f"{value:.3g}"
    def percent(value):
        return "N/A" if value is None else f"{100 * value:.1f}%"
    def mean(key):
        return _dict(_dict(summary.get("moves")).get(key)).get("mean")
    def gap(key):
        g = _dict(summary.get(key))
        if key == "gap_to_solver" and g.get("source") != "paired_comparison":
            return "N/A (paired comparison unavailable)"
        absolute, relative = _dict(g.get("absolute")), _dict(g.get("relative"))
        value = f"{fmt(absolute.get('mean'))} lifts; {percent(relative.get('mean'))}"
        value += (f" (n_pairs={g.get('n_pairs', 0)}; n_excluded={g.get('n_excluded', 0)})"
                  if key == "gap_to_solver" else
                  f" (n_relative={relative.get('n', 0)}; n_absolute={absolute.get('n', 0)}); {MOVE_MEAN_POPULATION}")
        if key == "gap_to_solver" and absolute.get("n", 0):
            reference = _dict(summary.get("solver_references"))
            value += "; reference " + ", ".join(reference.get("methods", ["unspecified"]))
            value += "; proof " + ", ".join(reference.get("proof_statuses", ["unspecified"]))
            value += "; scope " + ", ".join(reference.get("optimality_scopes", ["unspecified"]))
        return value
    def condition_cell(phase):
        condition = _dict(_dict(summary.get("voyage_conditions")).get(phase))
        return (f"{phase} {percent(condition.get('pass_rate'))} "
                f"({condition.get('n_passed', 0)}/{condition.get('n_instances', 0)}; "
                f"missing {condition.get('n_missing', 0)})")
    label = method or summary.get("method") or "unknown"
    label += _release_label(summary)
    sampling = _dict(summary.get('sample_reporting'))
    sample_k = sampling.get('k')
    k_label = (str(sample_k) if sample_k is not None else
               'mixed ' + ','.join(map(str, sampling['k_values'])) if len(sampling.get('k_values', [])) > 1 else 'unknown')
    label += f" [k={k_label} samples per instance]"
    spark = (is_spark_record({'method': method or summary.get('method') or ''})
             or sampling.get('selection_label') == 'own plans; one sample')
    if spark:
        label += ' [own plans only]'
    elif str(sampling.get('selection_label', '')).startswith('checker-selected'):
        label += f" [{sampling['selection_label']}; pass@1 {percent(sampling.get('pass_at_1_rate'))}; "
        label += f"unknown {sampling.get('pass_at_1_unknown_count', 0)}]"
    if summary.get("instance_facts_complete") is False:
        label += " [provisional instance facts; consolidate methods]"
    if summary.get("partial"):
        label += " [partial allocation]"
    if summary.get("training_lineage_unknown_count", 0):
        label += " [training lineage unknown]"
    if summary.get("training_version_mismatches"):
        label += " [training version mismatch: " + ", ".join(summary["training_version_mismatches"]) + "]"
    if summary.get("training_epoch_mismatch_count", 0):
        label += f" [training epoch ≠ evaluation epoch; n={summary['training_epoch_mismatch_count']}]"
    if tiny_run:
        label += " [TINY-RUN; unofficial]"
        stubs = _dict(_dict(summary.get("provenance")).get("stubs"))
        if stubs:
            label += " stubs: " + "; ".join(f"{name}: {reason}" for name, reason in sorted(stubs.items()))
    parts = [f"{summary.get('population', 'own')} population; {MOVE_MEAN_POPULATION}",
             f"{fmt(mean('total_lifts_now'))} now ({fmt(mean('weighted_nonproductive'))} weighted non-prod.)",
             f"yard total {fmt(mean('relocations'))}; avoidable yard rehandles {fmt(mean(AVOIDABLE_YARD_METRIC))} "
             f"(total - yard lower bound; n={_dict(_dict(summary.get('yard_rehandles')).get('avoidable')).get('n', 0)}; "
             f"no certificate {_dict(summary.get('yard_rehandles')).get('n_no_certificate', 0)}; "
             f"bound missing {_dict(summary.get('yard_rehandles')).get('n_missing_lower_bound', 0)}); "
             f"restow now/later {fmt(mean('restow_lifts'))}/{fmt(mean('future_restow_lifts'))}",
             f"covers now/later {fmt(mean('hatch_cover_lifts'))}/{fmt(mean('future_hatch_cover_lifts'))}",
             f"shut-outs total {fmt(_dict(summary.get('shut_outs')).get('sum'))}",
             f"soft/tolerated {_dict(summary.get('violation_counts')).get('soft', 0)}/{_dict(summary.get('violation_counts')).get('tolerated', 0)}"]
    for key, group in _dict(summary.get('by_congestion_mixing')).items():
        yard = _dict(group.get('yard_rehandles'))
        parts.append(f"congestion mixing {key}: {group.get('n_allocated', 0)} allocated; "
                     f"yard total {fmt(_dict(yard.get('total')).get('mean'))}; "
                     f"avoidable yard rehandles {fmt(_dict(yard.get('avoidable')).get('mean'))}; "
                     f"achieved share {percent(_dict(group.get('achieved_share')).get('mean'))}")
    margin_values = _dict(summary.get("margins"))
    if margin_values:
        parts.append("min margins " + ", ".join(f"{k}={fmt(_dict(v).get('min'))}" for k, v in sorted(margin_values.items())))
    physics_values = _dict(summary.get("physics"))
    if physics_values:
        parts.append("physics " + ", ".join(f"{k}={fmt(_dict(v).get('mean'))}" for k, v in sorted(physics_values.items())))
    stability = _dict(summary.get("stability"))
    parts.append(f"binding share {percent(stability.get('binding_share'))} "
                 f"({stability.get('n_binding', 0)}/{stability.get('n_measured', 0)} measured; "
                 f"unknown {stability.get('n_unknown', 0)})")
    parts.append(f"proven-infeasible {_dict(summary.get('feasibility_status_counts')).get('proven-infeasible', 0)}; "
                 f"{summary.get('feasibility_certificate_note', 'physical infeasibility certificate required')}")
    weighted_values = _dict(summary.get("weighted_nonproductive_by_type"))
    if any(_dict(v).get("n", 0) for v in weighted_values.values()):
        parts.append("weighted types " + ", ".join(f"{k}={fmt(_dict(v).get('mean'))}" for k, v in sorted(weighted_values.items())))
    diagnostics = _dict(summary.get('plan_diagnostics'))
    distributions = _dict(diagnostics.get('distributions'))
    delay = _dict(distributions.get('ballast_delay_h'))
    parts.insert(2, f"ballast_delay_h mean {fmt(delay.get('mean'))}; "
        f"min/max {fmt(delay.get('min'))}/{fmt(delay.get('max'))}; "
        f"n={delay.get('n', 0)} (informational; never a failure)")
    for key in ('abs_change_t', 'pumped_mass_t', 'lifts_after_k', 'heel_max_step_t',
                'pump_time_margin_h', 'pump_time_required_available_ratio', 'heel_ideal_limit_ratio'):
        values = _dict(distributions.get(key))
        parts.append(f"ballast_programme {key} mean {fmt(values.get('mean'))}; "
                     f"min/max {fmt(values.get('min'))}/{fmt(values.get('max'))}; n={values.get('n', 0)}")
    parts.append('ballast programme R2 failure share ' +
                 percent(_dict(diagnostics.get('r2_failure')).get('share')))
    resequencing = _dict(diagnostics.get('resequencing'))
    parts.append('resequencing ' + '; '.join(f"{key}={fmt(_dict(resequencing.get(key)).get('mean'))} "
        f"(n={_dict(resequencing.get(key)).get('n', 0)})" for key in ('lifts_moved', 'list_margin_before', 'list_margin_after')))
    def rate_cell(population, *, attempted=False, stats=summary):
        field = f"{population}_{'attempted_' if attempted else ''}pass_rate"
        interval = stats.get(f"{field}_ci95")
        value = f"{population} {percent(stats.get(field))}"
        if interval is not None:
            value += f" [Wilson 95% {percent(interval[0])}, {percent(interval[1])}]"
        return value
    pass_cell = (rate_cell("own") + "; " + rate_cell("shipped") + f" over {summary.get('n_allocated', 0)} allocated; "
                 + rate_cell("own", attempted=True) + "; " + rate_cell("shipped", attempted=True)
                 + f" over {summary.get('n_attempted', 0)} attempted; rejected {summary.get('n_generation_rejected', 0)}; "
                 + f"not attempted {summary.get('n_not_attempted', 0)}; method failed {summary.get('n_method_failed', 0)}")
    if spark:
        pass_cell = (rate_cell('own') + f" over {summary.get('n_allocated', 0)} allocated; " +
            rate_cell('own', attempted=True) + f" over {summary.get('n_attempted', 0)} attempted; " +
            f"rejected {summary.get('n_generation_rejected', 0)}; not attempted {summary.get('n_not_attempted', 0)}; " +
            f"method failed {summary.get('n_method_failed', 0)}")
    if str(summary.get('split_version', '')).startswith('dg-stress'):
        from eval.dg_coverage import render_coverage
        rung_rows = _dict(summary.get('by_rung')) or {str(summary.get('rung', 'selected rung')): summary}
        pass_cell = '; '.join(
            f"{rung}: {rate_cell('own', stats=stats)}; " +
            ('' if spark else rate_cell('shipped', stats=stats) + '; ') +
            f"over {stats.get('n_allocated', 0)} allocated; {stats.get('n_emitted', 0)} emitted; "
            f"{stats.get('n_attempted', 0)} attempted; {stats.get('n_not_attempted', 0)} not attempted; "
            f"{stats.get('n_method_failed', 0)} method failed; "
            + ', '.join(f"{status}={count}" for status, count in
                        _dict(stats.get('feasibility_status_counts')).items())
            for rung, stats in rung_rows.items())
        for rung, stats in rung_rows.items():
            if stats.get('dg_coverage'):
                parts.append(f"{rung}: {render_coverage(stats['dg_coverage'])}")
        for cohort, cohort_rungs in _dict(summary.get('by_dg_stress_pair_rung')).items():
            for rung, stats in cohort_rungs.items():
                parts.append(f"{rung} cohort {cohort}: {stats.get('n_allocated', 0)} allocated / "
                    f"{stats.get('n_emitted', 0)} emitted / {stats.get('n_generation_rejected', 0)} rejected; "
                    + ', '.join(f'{status}={count}' for status, count in stats['feasibility_status_counts'].items()))
    cells = [date or datetime.now(timezone.utc).date().isoformat(), label,
             instances or f"{summary.get('n_instances', 0)} {summary.get('split') or ''}".strip(),
             pass_cell, "; ".join(parts), gap("gap_to_lower_bound"), gap("gap_to_solver"),
             f"{fmt(_dict(summary.get('planning_time_s')).get('mean'))} s plan; {fmt(_dict(summary.get('pipeline_time_s')).get('mean'))} s pipeline",
             f"needed {percent(summary.get('fallback_rate'))}; repaired {percent(summary.get('repair_rate'))}",
             f"[run]({run})" if run else "N/A",
             condition_cell("departure"), condition_cell("arrival"),
             ", ".join(f"{phase}.{part}={count}" for phase, values in _dict(summary.get("voyage_conditions")).items()
                       for part, count in _dict(_dict(values).get("failed_parts")).items()) or "none recorded",
             f"checker timeout {summary.get('n_checker_timeout', 0)}",
             f"unevaluated {summary.get('n_unevaluated', 0)}; mask unavailable {summary.get('mask_unavailable_count', 0)}; "
             f"mask denominator {summary.get('mask_available_instance_count', 0)}",
             f"unevaluated (internal error) {summary.get('n_internal_error', 0)}; " +
             '; '.join(f'{cause}={count}' for cause, count in _dict(summary.get('internal_error_causes')).items()),
             f"mask time limit {summary.get('n_mask_time_limit', 0)}"]
    return "| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |"


def _table_header(columns: list[str]) -> str:
    return "| " + " | ".join(columns) + " |\n| " + " | ".join("---" for _ in columns) + " |"


def render_table_header() -> str:
    """Headers match render_row; method-specific means cannot rank methods."""
    return _table_header(["Date", "Method / split release / k samples per instance", "Instances", "Own / shipped passes over allocated and attempted (Wilson 95%); rejection / unattempted / failure counts",
        f"Moves (yard total / avoidable yard rehandles; {MOVE_MEAN_POPULATION})", f"Gap to lower bound ({MOVE_MEAN_POPULATION}; n_relative / n_absolute)",
        "Paired difference to solver (n_pairs; n_excluded)", "Time", "Fallback needed / repair rate", "Run",
        "Departure", "Arrival", "Evaluated failed parts", "Checker timeouts", "Unevaluated / mask unavailable",
        'Unevaluated (internal error) by cause', 'Mask time limit'])


def evaluation_table_header() -> str:
    """Compatibility name for the standalone evaluation table header."""
    return render_table_header()


def paired_table_header() -> str:
    """Header matches render_paired_comparison, including the paired population."""
    return _table_header(["Methods (first minus second) / split release", "Metric", "Paired difference (n_pairs; n_excluded)",
                          "Bootstrap interval", "Population"])


def render_paired_comparison(comparison: Mapping, *, first_method: str, second_method: str) -> str:
    """Print cross-method differences only from paired_comparison's matched plans."""
    if comparison.get("source") != "paired_comparison":
        raise ValueError("cross-method output requires paired_comparison results")
    def fmt(value):
        return "N/A" if value is None else f"{value:.3g}"
    interval = comparison.get("confidence_interval")
    interval_text = (f"{100 * comparison['confidence']:.1f}% [{fmt(interval[0])}, {fmt(interval[1])}]"
                     if interval is not None else "N/A")
    population = (f"both methods' passing plans ({comparison.get('population', 'own')} population)" if comparison.get("metric") in set(_lifts({})) | {AVOIDABLE_YARD_METRIC}
                  else "matched instances with observed values")
    directional = ""
    if comparison.get("small_sample"):
        sign = _dict(comparison.get("sign_test"))
        directional = (f"; wins/ties/losses={comparison.get('wins', 0)}/{comparison.get('ties', 0)}/{comparison.get('losses', 0)}"
                       f"; exact sign-test p={fmt(sign.get('p_value'))}")
        interval_text += "; small sample; bootstrap may under-cover"
    metric_label = comparison.get('metric', 'unknown')
    if metric_label == AVOIDABLE_YARD_METRIC:
        metric_label = f"avoidable yard rehandles (total - yard lower bound; no certificate {comparison.get('n_no_certificate', 0)} pairs)"
        for key, group in _dict(comparison.get('by_congestion_mixing')).items():
            population += (f"; congestion mixing {key}: difference {fmt(group.get('mean_difference'))} "
                           f"(n_pairs={group.get('n_pairs', 0)}; n_excluded={group.get('n_excluded', 0)})")
    cells = [f"{first_method} minus {second_method}" + _release_label(comparison), metric_label,
             f"{fmt(comparison.get('mean_difference'))} (n_pairs={comparison.get('n_pairs', 0)}; "
             f"n_excluded={comparison.get('n_excluded', 0)}){directional}", interval_text, population]
    budgets = _dict(comparison.get('sample_budgets'))
    for side in ('first', 'second'):
        budget = _dict(budgets.get(side))
        k = budget.get('k')
        cells[0] += f" [k({side})={k if k is not None else 'unknown'}]"
        if budget.get('population') == 'own' and (budget.get('selection_label') == 'own plans; one sample'
                or 'spark' in (first_method if side == 'first' else second_method).lower()):
            cells[4] += f'; {side} own plans only'
    return "| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in cells) + " |"


def _paired_interval(deltas, *, n_bootstrap, confidence, seed):
    if not deltas:
        return None
    rng = random.Random(seed)
    means = sorted(fmean(rng.choices(deltas, k=len(deltas))) for _ in range(n_bootstrap))
    def quantile(p):
        position = (len(means) - 1) * p
        lo = math.floor(position)
        hi = math.ceil(position)
        return means[lo] + (means[hi] - means[lo]) * (position - lo)
    tail = (1 - confidence) / 2
    return [quantile(tail), quantile(1 - tail)]


def paired_comparison(first: Iterable[Mapping], second: Iterable[Mapping], *,
                      metric: str = "weighted_nonproductive", n_bootstrap: int = 2000,
                      confidence: float = 0.95, seed: int = 0, population: str = "own") -> dict:
    """Return first-minus-second per-instance differences and paired bootstrap CI.

    Each replicate draws n matched instance indices with replacement and takes
    their mean difference; confidence bounds are interpolated percentile bounds.
    Negative differences favour the first planner for lift, time and fallback
    metrics. Own-plan comparisons exclude solver-repaired plans by default;
    ``population='shipped'`` includes repaired passing deliveries. Objective comparisons require both eligible checked passes;
    pass-rate differences remain separately visible for all pairs. Unmatched,
    duplicate or differently hashed instances raise rather than silently pairing.
    """
    allowed = set(_lifts({})) | {AVOIDABLE_YARD_METRIC, "pass_rate", "fallback_rate", "planning_time_s", "pipeline_time_s"}
    if metric not in allowed:
        raise ValueError(f"unsupported paired metric: {metric}")
    if n_bootstrap < 1 or not 0 < confidence < 1:
        raise ValueError("n_bootstrap must be positive and confidence in (0, 1)")
    if population not in ("own", "shipped"):
        raise ValueError("population must be 'own' or 'shipped'")
    def index(records):
        out = {}
        for r in records:
            key = r["instance_id"]
            if key in out:
                raise ValueError(f"duplicate instance_id: {key}")
            out[key] = r
        return out
    left, right = index(first), index(second)
    release = _release_pin([*left.values(), *right.values()])
    if left.keys() != right.keys():
        raise ValueError("paired comparison requires identical instance sets")
    def value(r):
        if metric == AVOIDABLE_YARD_METRIC:
            return yard_rehandles(r)['avoidable'] if _objective_eligible(r, population) else None
        if metric == "pass_rate":
            return float(_passed(r, population))
        if metric == "fallback_rate":
            event = _fallback(r)
            return float(event) if event is not None else None
        if metric in ("planning_time_s", "pipeline_time_s"):
            timing = _dict(r.get("timing"))
            keys = ("plan_s", "planning_wall_time_s") if metric == "planning_time_s" else ("total_s", "pipeline_wall_time_s")
            return _number(timing.get(keys[0], timing.get(keys[1])))
        return _lifts(_count(r)).get(metric) if _objective_eligible(r, population) and _count(r) else None
    differences = []
    for key in sorted(left):
        a, b = left[key], right[key]
        for identity in ("input_sha256", "spec_key", "split", "rung"):
            if a.get(identity) != b.get(identity):
                raise ValueError(f"mismatched {identity} for {key}")
        if metric == AVOIDABLE_YARD_METRIC:
            ab, bb = yard_rehandles(a)['lower_bound'], yard_rehandles(b)['lower_bound']
            if ab is not None and bb is not None and ab != bb:
                raise ValueError(f'mismatched yard lower bound for {key}')
        av, bv = value(a), value(b)
        differences.append({"instance_id": key, "first": av, "second": bv,
                            "difference": av - bv if av is not None and bv is not None else None,
                            "first_passed": _passed(a, population), "second_passed": _passed(b, population)})
    deltas = [r["difference"] for r in differences if r["difference"] is not None]
    ci = _paired_interval(deltas, n_bootstrap=n_bootstrap, confidence=confidence, seed=seed)
    # Lower costs win, while a higher pass rate wins. The exact two-sided
    # sign-test probability is invariant to this directional relabelling.
    direction = 1 if metric == "pass_rate" else -1
    wins, ties, losses = (sum(direction * d > 0 for d in deltas), sum(d == 0 for d in deltas),
                          sum(direction * d < 0 for d in deltas))
    non_ties = wins + losses
    # Exact two-sided binomial sign test under equal win/loss probability;
    # ties carry no direction evidence (NIST/SEMATECH e-Handbook, Sign Test).
    sign_test = ({"method": "exact two-sided sign test", "n_non_ties": non_ties,
                  "p_value": min(1., 2 * sum(math.comb(non_ties, k) for k in range(min(wins, losses) + 1))
                                   / 2 ** non_ties) if non_ties else None,
                  "ties_excluded": ties} if len(deltas) < 30 else None)
    yard_notes = ({'n_no_certificate': sum(
        pair['difference'] is not None and yard_rehandles(left[pair['instance_id']])['lower_bound'] == 0
        for pair in differences)} if metric == AVOIDABLE_YARD_METRIC else {})
    if metric == AVOIDABLE_YARD_METRIC:
        strata = {}
        for pair in differences:
            key = pair['instance_id']
            ak, ac = mixing_stratum(left[key])
            bk, bc = mixing_stratum(right[key])
            if ak != 'unknown' and bk != 'unknown' and ak != bk:
                raise ValueError(f'mismatched congestion-mixing provenance for {key}')
            group = strata.setdefault(ak if ak != 'unknown' else bk,
                                      {'provenance': ac if ak != 'unknown' else bc, 'pairs': []})
            group['pairs'].append(pair)
        yard_notes['by_congestion_mixing'] = {}
        for key, group in sorted(strata.items()):
            values = [pair['difference'] for pair in group['pairs'] if pair['difference'] is not None]
            yard_notes['by_congestion_mixing'][key] = {
                'source': 'paired_comparison', 'metric': metric, 'provenance': group['provenance'],
                'n_instances': len(group['pairs']), 'n_pairs': len(values),
                'n_excluded': len(group['pairs']) - len(values),
                'mean_difference': fmean(values) if values else None,
                'confidence_interval': _paired_interval(values, n_bootstrap=n_bootstrap, confidence=confidence, seed=seed),
                'confidence': confidence}
    return {**release, **yard_notes, "source": "paired_comparison", "metric": metric, "population": population,
            'sample_budgets': {'first': aggregate_sample_reporting(left.values(), population=population),
                               'second': aggregate_sample_reporting(right.values(), population=population)},
            "direction": "first_minus_second", "n_instances": len(left),
            "n_pairs": len(deltas), "n_excluded": len(left) - len(deltas),
            "wins": wins, "ties": ties, "losses": losses, "sign_test": sign_test,
            "small_sample": len(deltas) < 30,
            "mean_difference": fmean(deltas) if deltas else None,
            "confidence": confidence, "confidence_interval": ci,
            "bootstrap_samples": n_bootstrap, "seed": seed,
            "pass_rate_difference": (sum(_passed(r, population) for r in left.values()) - sum(_passed(r, population) for r in right.values())) / len(left) if left else None,
            "per_instance": differences}

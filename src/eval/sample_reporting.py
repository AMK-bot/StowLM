"""D76 sample budgets and first-sample rates, separate from verifier selection.

``sample_evidence`` is an optional JSON list of zero-based ``sample_index``,
``report``, ``report_binding_valid`` and ``repaired`` fields. A selected sibling's
report or the number of feasible siblings cannot establish sample zero's result.
The StowLM producer's explicit ``metrics.pass_at_1`` verdict is also supported;
its legacy false verdict without a complete evaluated report remains unresolved.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping


_REQUIRED_PARTS = {"moves", "rules", "stability", "strength", "visibility", "lashing", "constraints"}


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _positive_integer(value):
    return value if type(value) is int and value > 0 else None


def _draft_metrics(record):
    metrics = _mapping(_mapping(record.get("result")).get("metrics"))
    # Repair replaces the returned planner metrics but preserves the sampled
    # draft's evidence under this producer-owned key.
    original = _mapping(metrics.get("repair_original_metrics"))
    return original if original else metrics


def _name(record, planner=None):
    name = getattr(planner, "name", planner if isinstance(planner, str) else None)
    return str(name or record.get("planner") or record.get("method")
               or _mapping(record.get("result")).get("planner") or "unknown").lower()


def is_spark_record(record, planner=None):
    """Identify the one-response Spark baseline from its recorded planner identity."""
    return "spark" in _name(record, planner)


def _bound_verdict(sample):
    from eval.harness_metrics import checker_outcome

    if sample.get("report_binding_valid") is not True or sample.get("repaired") is True:
        return None
    report = _mapping(sample.get("report"))
    outcome = checker_outcome(sample)
    # A definitive evaluated failure stays a non-pass even if a different part
    # could not run. Unresolved numerical errors and timeouts remain unknown.
    if outcome == "failed_check":
        return False
    if not _REQUIRED_PARTS.issubset(_mapping(report.get("parts"))):
        return None
    if outcome == "passed" and report.get("count") is not None and not report.get("shut_out"):
        return True
    return None


def _first_sample(record, k, metrics):
    if record.get("allocation_only"):
        return False, "generation rejected" if record.get("feasibility_status") == "generation-rejected" else "not attempted"

    # Prefer full, input-bound per-sample evidence to a producer's compact flag.
    evidence = record.get("sample_evidence", metrics.get("sample_evidence"))
    if isinstance(evidence, list):
        first = [sample for sample in evidence if isinstance(sample, Mapping)
                 and type(sample.get("sample_index")) is int and sample["sample_index"] == 0]
        if len(first) == 1:
            verdict = _bound_verdict(first[0])
            return verdict, "measured" if verdict is not None else "unknown: first-sample checker verdict unresolved"
        if len(first) > 1:
            return None, "unknown: duplicate first-sample evidence"

    explicit = _mapping(metrics.get("pass_at_1"))
    if type(explicit.get("sample")) is int and explicit["sample"] == 0:
        if isinstance(explicit.get("report"), Mapping):
            verdict = _bound_verdict(explicit)
            return verdict, "measured" if verdict is not None else "unknown: first-sample checker verdict unresolved"
        if explicit.get("checker_passed") is None:
            return None, "unknown: first-sample checker verdict unresolved"
        if explicit.get("complete") is False:
            return False, "measured"
        # is_full_pass in StowLM establishes true only after full checking and
        # input binding. Its historical false conflates evaluated failure with
        # numerical/timeout/binding errors, so false alone is insufficient.
        if (explicit.get("complete") is True and explicit.get("checker_passed") is True
                and (explicit.get("checked_in_selection") is True or "extra_check_s" in explicit)):
            return True, "measured (producer full-checker verdict)"
        return None, "unknown: first-sample checker verdict unresolved"

    if k == 1 and not _mapping(record.get("result")).get("repaired"):
        verdict = _bound_verdict(record)
        if verdict is not None:
            return verdict, "measured"
    return None, "unknown: first-sample checker evidence missing"


def sample_reporting(record: Mapping, planner=None, parameters: Mapping | None = None) -> dict:
    """Describe actual k and independently checked sample zero; missing proof stays unknown."""
    metrics = _draft_metrics(record)
    parameters = _mapping(parameters)
    saved = _mapping(record.get("sample_reporting"))
    k = next((value for candidate in (parameters.get("n_samples"), getattr(planner, "n_samples", None),
                                     metrics.get("n_samples"), saved.get("k"))
              if (value := _positive_integer(candidate)) is not None), None)
    if k is None and getattr(planner, "deterministic", False) is True:
        k = 1  # A declared deterministic planner produces one plan per invocation.
    spark = is_spark_record(record, planner)
    if spark:
        k = 1  # SparkPlanner makes exactly one completion request per instance.
    verdict, status = _first_sample(record, k, metrics)
    name = _name(record, planner)
    label = ("own plans; one sample" if spark else
             f"checker-selected best of {k}" if "stowlm" in name and k is not None else
             "k unknown" if k is None else f"k={k}")
    return {"k": k, "k_status": "known" if k is not None else "unknown",
            "selection_label": label, "pass_at_1": verdict, "pass_at_1_status": status,
            "spark_own_only": spark}


def aggregate_sample_reporting(records: Iterable[Mapping], population: str = "own") -> dict:
    """Count sample-zero and selected passes over every allocated specification."""
    from eval.harness_metrics import _passed, wilson_interval

    if population not in ("own", "shipped"):
        raise ValueError("population must be 'own' or 'shipped'")
    rows = list(records)
    samples = [sample_reporting(record) for record in rows]
    spark_only = bool(rows) and all(sample["spark_own_only"] for sample in samples)
    if spark_only:
        population = "own"
    ks = sorted({sample["k"] for sample in samples if sample["k"] is not None})
    unknown_k = sum(sample["k"] is None for sample in samples)
    k = ks[0] if len(ks) == 1 and not unknown_k else None
    count = sum(sample["pass_at_1"] is True for sample in samples)
    unknown = sum(sample["pass_at_1"] is None for sample in samples)
    n = len(rows)
    selected = sum(not row.get("allocation_only") and _passed(row,
        "own" if sample["spark_own_only"] else population) for row, sample in zip(rows, samples))
    labels = {sample["selection_label"] for sample in samples}
    return {"k": k, "k_values": ks, "k_unknown_count": unknown_k,
            "n_allocated": n, "population": population,
            "selection_label": next(iter(labels)) if len(labels) == 1 else "mixed sample budgets",
            "pass_at_1_count": count, "pass_at_1_unknown_count": unknown,
            "pass_at_1_measured_count": n - unknown,
            "pass_at_1_rate": count / n if n and not unknown else None,
            "pass_at_1_ci95": wilson_interval(count, n) if not unknown else None,
            "pass_at_1_rate_bounds": [count / n, (count + unknown) / n] if n else None,
            "best_of_k_count": selected, "best_of_k_rate": selected / n if n else None,
            "best_of_k_ci95": wilson_interval(selected, n)}

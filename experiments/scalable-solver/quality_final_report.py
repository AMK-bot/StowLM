"""Rerunnable read-only aggregation of separate exact-window quality cohorts.

Only full-checker-passing departures contribute objective averages or paired
savings. C2-off checker witnesses remain diagnostic, never production gold.
Throughput uses every recorded construction/improvement/validation second,
including failed and currently checkpointed attempts. Independent exact-oracle
runtime is disclosed separately and excluded from that production denominator.
Generation failures stay in the attempted-case denominator; their generation
runtime is disclosed separately, since production consumes existing instances.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean

SCOPE = Path(__file__).resolve().parent
COHORTS = ("quality-final-tiny", "quality-final-v263-tiny", "quality-final-v263-tiny-long", "quality-posttrim-tiny", "quality-posttrim-small", "quality-posttrim-medium")
PINNED = ("quality-pinned-regression.json", "quality-pinned-strict.json", "quality-pinned-small-v263.json")


def avg(xs):
    return mean(xs) if xs else None


def gap(cost, bound):
    if cost is None or bound is None:
        return None
    return (cost-bound)/cost if cost else (0.0 if bound == 0 else None)


def excess(cost, optimum):
    if cost is None or optimum is None:
        return None
    return (cost-optimum)/optimum if optimum else (0.0 if cost == 0 else None)


def valid_cost(phase):
    # checked_cost is authoritative; older pinned records use explicit before/
    # after checker booleans, normalized by pinned() below.
    if phase.get("checker_passed") is True and phase.get("completed") is not False:
        return phase.get("checked_cost", phase.get("cost"))
    return None


def phases_seconds(row):
    return sum(row.get(phase, {}).get(name, 0) or 0
               for phase in ("construct", "improve") for name in ("search_s", "validation_s"))


def case(row, path):
    selection = row.get("selection", {})
    if selection.get("split") not in ("train", "val"):
        raise ValueError(f"Refusing non-train/val result {path}")
    bound = row.get("lower_bound", {}).get("weighted")
    exact = row.get("exact", {})
    # OPTIMAL alone is not sufficient: independent exact must also certify its
    # witness through the full checker. Domain/config remains attached.
    proved = exact.get("status") == "OPTIMAL" and exact.get("metrics", {}).get("checker_passed") is True
    optimum = exact.get("objective") if proved else None
    out = dict(path=str(path.relative_to(SCOPE)), input_sha256=row.get("input_sha256"),
        seed=selection.get("spec", {}).get("seed"), split=selection["split"],
        rung=selection.get("rung"), finished=row.get("finished", False),
        source_changed=row.get("source_changed_during_run"), source_before=row.get("identity", {}).get("source_sha256"),
        source_after=row.get("source_sha256_after"), solver_version=row.get("identity", {}).get("solver_version"),
        diagnostic_c2_off=row.get("identity", {}).get("diagnostic_c2_off"),
        generation_error=row.get("generation_error"), generation_status=row.get("generation_status"),
        counting_lower_bound=bound, independent_exact=dict(status=exact.get("status", "not_run_or_pending"),
            proved_optimum=proved, optimum=optimum, domain=exact.get("domain"),
            elapsed_s=exact.get("search_s", 0), lower_bound=exact.get("lower_bound")),
        production_phase_seconds=phases_seconds(row))
    for phase in ("construct", "improve"):
        data = row.get(phase, {})
        cost = valid_cost(data)
        out[phase] = dict(status=data.get("status", "not_run_or_pending"),
            completed=data.get("completed", False), checker_passed=data.get("checker_passed", False),
            approved=data.get("approved", False), valid_cost=cost,
            gap_to_lb=gap(cost, bound), relative_excess_vs_proved_exact=excess(cost, optimum),
            search_s=data.get("search_s", 0), validation_s=data.get("validation_s", 0),
            finalized_status=data.get("finalized_status"), ballast_status=data.get("ballast_status"),
            validation_error=data.get("validation_error"), error=data.get("error"),
            violation_rule_ids=sorted({v["rule_id"] for v in data.get("checker_violations", [])}),
            exact_root_status=data.get("metrics", {}).get("exact_root_status"),
            configured_engine_exact_objective=data.get("metrics", {}).get("exact_root_objective"),
            relative_excess_vs_configured_engine_root_proof=excess(cost, data.get("metrics", {}).get("exact_root_objective")) if data.get("metrics", {}).get("exact_root_status") == "OPTIMAL" else None,
            configured_engine_exact_lower_bound=data.get("metrics", {}).get("exact_root_lower_bound"),
            configured_engine_exact_production=data.get("metrics", {}).get("exact_root_production_engine"),
            configured_engine_exact_scope=data.get("metrics", {}).get("exact_root_optimality_scope"),
            verified_incumbent=data.get("metrics", {}).get("verified_incumbent"),
            beam_checker_rejections=data.get("metrics", {}).get("beam_checker_rejections", 0),
            beam_checker_rejection_rule_ids=data.get("metrics", {}).get("beam_checker_rejection_rule_ids", {}))
    out["checker_valid_pair"] = all(out[p]["valid_cost"] is not None for p in ("construct", "improve"))
    return out


def cohort(directory):
    cases = []
    raw = []
    for path in sorted((directory / "results").glob("*.json")):
        row = json.loads(path.read_text())
        if "selection" not in row:
            continue  # approved-plan envelopes are artifacts, not attempts
        raw.append(row)
        cases.append(case(row, path))
    manifest_path = directory / "manifest.json"
    expected = len(json.loads(manifest_path.read_text())["selections"]) if manifest_path.exists() else None
    pairs = [c for c in cases if c["checker_valid_pair"]]
    total = sum(c["production_phase_seconds"] for c in cases)
    passed_after = sum(c["improve"]["checker_passed"] for c in cases)
    approved = sum(c["improve"]["approved"] and c["diagnostic_c2_off"] is False for c in cases)
    out = dict(expected_cases=expected, recorded_attempts=len(cases),
        pending_unrecorded=max(0, expected-len(cases)) if expected is not None else None,
        unfinished_checkpoints=sum(not c["finished"] for c in cases),
        generation_failures=sum(c["generation_error"] is not None for c in cases),
        generation_failure_status_counts=dict(Counter(c["generation_status"] for c in cases if c["generation_error"])),
        source_changed_cases=sum(c["source_changed"] is True for c in cases),
        source_unchanged_cases=sum(c["source_changed"] is False for c in cases),
        source_change_unknown=sum(c["source_changed"] is None for c in cases),
        diagnostic_c2_off=any(c["diagnostic_c2_off"] for c in cases),
        independent_exact_proved_optima=sum(c["independent_exact"]["proved_optimum"] for c in cases),
        independent_exact_status_counts=dict(Counter(c["independent_exact"]["status"] for c in cases)),
        independent_exact_seconds_excluded=sum(c["independent_exact"]["elapsed_s"] or 0 for c in cases),
        generation_seconds_excluded=sum(r.get("generation_s", 0) for r in raw if r.get("generation_error")),
        production_core_seconds=total,
        throughput_denominator="Recorded one-core construct.search + construct.validation + improve.search + improve.validation seconds across ALL attempted cases, including failures/checkpoints; excludes generation, independent exact oracle, and unstarted work",
        improved_checker_passing_plans=passed_after, improved_production_approved_plans=approved,
        checker_passing_diagnostic_plans_per_core_hour=3600*passed_after/total if total else None,
        production_approved_plans_per_core_hour=3600*approved/total if total else None,
        checker_valid_pairs=dict(n=len(pairs),
            mean_before_cost=avg([c["construct"]["valid_cost"] for c in pairs]),
            mean_after_cost=avg([c["improve"]["valid_cost"] for c in pairs]),
            mean_construct_search_s=avg([c["construct"]["search_s"] for c in pairs]),
            mean_improve_search_s=avg([c["improve"]["search_s"] for c in pairs]),
            mean_construct_validation_s=avg([c["construct"]["validation_s"] for c in pairs]),
            mean_improve_validation_s=avg([c["improve"]["validation_s"] for c in pairs]),
            total_saved=sum(c["construct"]["valid_cost"]-c["improve"]["valid_cost"] for c in pairs),
            improved=sum(c["construct"]["valid_cost"]>c["improve"]["valid_cost"] for c in pairs),
            worsened=sum(c["construct"]["valid_cost"]<c["improve"]["valid_cost"] for c in pairs),
            mean_before_gap_to_lb=avg([c["construct"]["gap_to_lb"] for c in pairs if c["construct"]["gap_to_lb"] is not None]),
            mean_after_gap_to_lb=avg([c["improve"]["gap_to_lb"] for c in pairs if c["improve"]["gap_to_lb"] is not None])))
    for phase in ("construct", "improve"):
        valid = [c[phase] for c in cases if c[phase]["valid_cost"] is not None]
        exact = [c for c in valid if c["relative_excess_vs_proved_exact"] is not None]
        out[phase] = dict(status_counts=dict(Counter(c[phase]["status"] for c in cases)),
            completed=sum(c[phase]["completed"] for c in cases), checker_valid=len(valid),
            completion_rate_all_attempts=sum(c[phase]["completed"] for c in cases)/len(cases) if cases else None,
            checker_pass_rate_all_attempts=len(valid)/len(cases) if cases else None,
            mean_checker_valid_cost=avg([c["valid_cost"] for c in valid]),
            mean_checker_valid_gap_to_lb=avg([c["gap_to_lb"] for c in valid if c["gap_to_lb"] is not None]),
            mean_search_s_all_recorded_attempts=avg([c[phase]["search_s"] for c in cases]),
            mean_validation_s_all_recorded_attempts=avg([c[phase]["validation_s"] for c in cases]),
            exact_proved_and_checker_valid_comparisons=len(exact),
            mean_relative_excess_vs_proved_exact=avg([c["relative_excess_vs_proved_exact"] for c in exact]),
            violation_rule_counts=dict(Counter(rule for c in cases for rule in c[phase]["violation_rule_ids"])))
    out["evidence_label"] = ("exploratory/source-transition: one or more source hashes changed during measurement"
        if out["source_changed_cases"] else "stable recorded sources; prefer this cohort over source-transition results"
        if out["source_unchanged_cases"] else "source stability unrecorded")
    out["configured_engine_exact_root_status_counts"] = dict(Counter(c["improve"]["exact_root_status"] for c in cases if c["improve"]["exact_root_status"]))
    out["configured_engine_root_proof_note"] = "Optimizer-internal configured-engine proof; not counted as an independent exact comparison"
    out["cases"] = cases
    return out


def pinned(path):
    row = json.loads(path.read_text())
    split = row.get("selection", {}).get("split")
    if split is not None and split not in ("train", "val"):
        raise ValueError(f"Refusing non-train/val pinned result {path}")
    if split is None and not row.get("input_sha256"):
        raise ValueError(f"Pinned result without selection must pin input bytes: {path}")
    metrics = row.get("metrics", {})
    before = row.get("before_cost") if row.get("before_checker_passed") else None
    after = row.get("after_cost") if row.get("after_checker_passed") else None
    bound = row.get("lower_bound", metrics.get("lower_bound"))
    # This is the optimizer's own proof, not an independent comparison run.
    optimum = metrics.get("exact_root_objective") if metrics.get("exact_root_status") == "OPTIMAL" else None
    return dict(cohort="pinned regression; excluded from all fresh cohort statistics", input_sha256=row.get("input_sha256"),
        status=row.get("status"), message=row.get("message"), diagnostic_c2_off=row.get("diagnostic_c2_off"),
        selection=row.get("selection"), selection_split=split, selection_note="Explicit frozen train/val selection" if split else "This pinned follow-up artifact records input SHA but omits its frozen selection; excluded from fresh cohorts",
        version=row.get("version"), verified_incumbent=metrics.get("verified_incumbent"),
        beam_checker_attempts=metrics.get("beam_checker_attempts", 0),
        beam_checker_rejections=metrics.get("beam_checker_rejections", 0),
        beam_checker_rejection_rule_ids=metrics.get("beam_checker_rejection_rule_ids", {}),
        before_checker_passed=row.get("before_checker_passed", False),
        after_checker_passed=row.get("after_checker_passed", False),
        before_valid_cost=before, after_valid_cost=after,
        invalid_after_cost_excluded=row.get("after_cost") if not row.get("after_checker_passed") else None,
        checker_valid_pair=before is not None and after is not None,
        saved=before-after if before is not None and after is not None else None,
        lower_bound=bound, before_gap_to_lb=gap(before, bound), after_gap_to_lb=gap(after, bound),
        optimizer_root_proved_optimum=optimum,
        after_relative_excess_vs_optimizer_proof=excess(after, optimum),
        independent_exact_comparison=False, search_s=row.get("search_s"), total_s=row.get("total_s"),
        production_throughput=None,
        throughput_note="Pinned timings omit original construction, so no comparable full production throughput is inferred",
        source_changed=row.get("source_changed", row["source_before"] != row["source_after"] if row.get("source_before") and row.get("source_after") else None), source_before=row.get("source_before"), source_after=row.get("source_after"),
        violation_rule_ids=sorted({v["rule_id"] for v in row.get("violations", [])}),
        dead_end_rule_ids=metrics.get("dead_end_rule_ids", {}),
        approved=False if row.get("diagnostic_c2_off") else None)


def prior_reference_comparison(cohort):
    """Attach qualifications to the number even when copied out of its cohort."""
    references = [ref["after_relative_excess_to_prior_run_optimum"]
                  for item in cohort["cases"]
                  for ref in item.get("prior_same_input_exact_evidence", [])
                  if ref["after_relative_excess_to_prior_run_optimum"] is not None]
    return dict(n=len(references), mean_relative_excess=avg(references),
        worst_relative_excess=max(references) if references else None,
        diagnostic_c2_off=cohort["diagnostic_c2_off"],
        independent_proved_optima_this_revision=cohort["independent_exact_proved_optima"],
        production_quality_evidence=False,
        qualification="Same input bytes, earlier source-transition proof; not an independent proof under this source revision")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=SCOPE)
    parser.add_argument("--output", type=Path, default=SCOPE / "quality-final-report.json")
    args = parser.parse_args(argv)
    root, output = args.root.resolve(), args.output.resolve()
    if not root.is_relative_to(SCOPE) or not output.is_relative_to(SCOPE):
        parser.error("inputs and output must stay within scalable-solver experiment scope")
    out = dict(schema="scalable-exact-window-quality-v1", generated_utc=datetime.now(timezone.utc).isoformat(),
        qualification="Checker-passing C2-off departures are diagnostic, not production gold. Counting LB gaps and engine-domain exact-optimum gaps are distinct. Cohorts remain separate; invalid costs never enter objective averages.",
        cohorts={name: cohort(root/name) for name in COHORTS},
        pinned={name: pinned(root/name) if (root/name).exists() else dict(status="not_yet_recorded") for name in PINNED})
    old_cases = out["cohorts"]["quality-final-tiny"]["cases"]
    latest_cases = out["cohorts"]["quality-final-v263-tiny"]["cases"] + out["cohorts"]["quality-final-v263-tiny-long"]["cases"]
    for item in latest_cases:
        matches = [old for old in old_cases if old["input_sha256"] == item["input_sha256"]
                   and old["independent_exact"]["proved_optimum"]]
        item["prior_same_input_exact_evidence"] = [dict(path=old["path"],
            input_sha256=old["input_sha256"], optimum=old["independent_exact"]["optimum"],
            domain=old["independent_exact"]["domain"], source_changed=old["source_changed"],
            after_relative_excess_to_prior_run_optimum=excess(item["improve"]["valid_cost"], old["independent_exact"]["optimum"]),
            qualification="Prior-run same-input evidence; not a new independent proof for this source revision")
            for old in matches]
    for name in ("quality-final-v263-tiny", "quality-final-v263-tiny-long"):
        out["cohorts"][name]["prior_run_reference_comparison"] = prior_reference_comparison(out["cohorts"][name])
    output.write_text(json.dumps(out, indent=2, sort_keys=True, allow_nan=False)+"\n")
    for name, data in out["cohorts"].items():
        print(json.dumps(dict(cohort=name, attempted=data["recorded_attempts"], unfinished=data["unfinished_checkpoints"],
            pairs=data["checker_valid_pairs"], independent_proved=data["independent_exact_proved_optima"],
            production_s=data["production_core_seconds"], checker_diagnostic_per_hour=data["checker_passing_diagnostic_plans_per_core_hour"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Render measured classical references without promoting failed/timeout plans.

Usage: uv run python experiments/mvp/refs_tools/summarize_refs.py --rung tiny
Reads direct saved checkpoints and repairs stale journals; no planner/checker run.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.harness_metrics import aggregate_results, checker_outcome, paired_comparison

METHODS = ("twostep", "solver", "exact", "bounds")
ROOT = Path(__file__).resolve().parents[3]
FIELDS = ("relocations", "restow_lifts", "hatch_cover_lifts")


def read_json(path: Path):
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_records(folder: Path, rungs: set[str]) -> list[dict]:
    # Direct checkpoints commit first and are authoritative after interruption.
    # Archived pilots must never enter either the attempt population or journal.
    rows = [read_json(path) for path in sorted(folder.glob("instances/*/record.json"))]
    def indexed(values, source):
        out = {}
        for row in values:
            key = row["instance_id"]
            if key in out:
                raise ValueError(f"Duplicate instance IDs in {source}: {key}")
            out[key] = row
        return out
    checkpoints = indexed(rows, folder)
    journal = folder / "records.jsonl"
    if journal.exists():
        journal_rows = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        journal_index = indexed(journal_rows, journal)
        # Refuse to silently erase journal-only evidence if a checkpoint vanished.
        orphaned = journal_index.keys() - checkpoints.keys()
        if orphaned:
            raise ValueError(f"Journal attempts lack direct checkpoints in {folder}: {sorted(orphaned)}")
    else:
        journal_index = {}
    if journal_index != checkpoints:
        folder.mkdir(parents=True, exist_ok=True)
        temporary = journal.with_name(journal.name + f".{os.getpid()}.tmp")
        temporary.write_text("".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
                                     for row in rows))
        temporary.replace(journal)
    rows = [row for row in rows if row.get("rung") in rungs]
    for row in rows:
        if row.get("result") is not None and not isinstance(row.get("shippable"), bool):
            raise ValueError(f"Missing explicit shipping verdict: {folder}/{row['instance_id']}")
    return sorted(rows, key=lambda row: (row.get("rung", ""), row["instance_id"]))


def first_failure(row: dict) -> str | None:
    report = row.get("report") or {}
    violations = list(report.get("violations", []))
    for part in report.get("parts", {}).values():
        violations.extend(part.get("violations", []))
    return next((str(v.get("rule_id", "unknown")) for v in violations
                 if v.get("severity") == "hard"), None)


def counts(row: dict) -> dict:
    # Missing measured fields stay missing; a failure is never a zero-cost plan.
    count = (row.get("report") or {}).get("count") or {}
    out = {key: count.get(key) for key in FIELDS}
    future = count.get("future")
    for key in ("restow_lifts", "hatch_cover_lifts"):
        out[f"future_{key}"] = (sum(part[key] for part in future)
                                 if isinstance(future, list) and all(key in part for part in future)
                                 else None)
    out["weighted_nonproductive"] = count.get("weighted_nonproductive")
    return out


def eligible(row: dict) -> bool:
    return (checker_outcome(row) == "passed" and row.get("shippable") is True
            and not (row.get("result") or {}).get("repaired", False))


def pipeline_diagnostic(row: dict) -> dict:
    result = row.get("result") or {}
    metrics = result.get("metrics") or {}
    plan = result.get("plan")
    return {"instance_id": row["instance_id"], "status": result.get("status"),
            "ballast_status": metrics.get("ballast_status"),
            "ballast_present": isinstance(plan, dict) and plan.get("ballast") is not None,
            "not_found_cause": metrics.get("not_found_cause"),
            "message": result.get("message"),
            "reference_exclusion_reason": row.get("reference_exclusion_reason"),
            "relevant_source_changes": row.get("relevant_source_changes", [])}


def fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if 0 < abs(value) < 0.001:
            return f"{value:.6f}".rstrip("0").rstrip(".")
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
            *("| " + " | ".join(fmt(v) for v in row) + " |" for row in rows)]


def summarize(runs_root: Path, manifest_path: Path, rungs: set[str]) -> dict:
    manifest = read_json(manifest_path)
    expected = [row for row in manifest["files"]
                if row.get("split") == "test" and row.get("rung") in rungs
                and row.get("path", "").endswith(".json")]
    expected_hashes = {row["sha256"] for row in expected}
    if not expected:
        raise ValueError("No test instances selected in corpus manifest")
    records = {method: load_records(runs_root / f"test-{method}", rungs) for method in METHODS}
    for method, rows in records.items():
        for row in rows:
            if row.get("input_sha256") not in expected_hashes:
                raise ValueError(f"Unfrozen/unselected input in {method}: {row['instance_id']}")
    summaries, comparisons, pins = {}, {}, {}
    for method in METHODS:
        folder = runs_root / f"test-{method}"
        manifest_file = folder / "manifest.json"
        rows = records[method]
        source_pins = sorted({json.dumps(row.get("source_before", {}), sort_keys=True) for row in rows})
        pins[method] = {"manifest_sha256": digest(manifest_file) if manifest_file.exists() else None,
                        "manifest": read_json(manifest_file) if manifest_file.exists() else None,
                        "record_sha256": digest(folder / "records.jsonl") if (folder / "records.jsonl").exists() else None,
                       "source_before_variants": [json.loads(value) for value in source_pins],
                        "source_drift_instances": [row["instance_id"] for row in rows if row.get("source_drift")],
                        "reference_exclusions": [pipeline_diagnostic(row) for row in rows
                                                 if row.get("reference_exclusion_reason")]}
        if method == "bounds":
            continue
        summary = aggregate_results(rows, method=method, split="test", population="own")
        wanted = sum(row.get("rung") == "tiny" for row in expected) if method == "exact" else len(expected)
        summary.update(expected_instances=wanted, missing_instances=max(0, wanted - len(rows)),
                       checker_outcomes=dict(Counter(checker_outcome(row) for row in rows)),
                       first_failing_rules=dict(Counter(first_failure(row) for row in rows if first_failure(row))),
                       exact_labels=dict(Counter(row.get("exact_label", "unlabelled") for row in rows))
                       if method == "exact" else None,
                       pipeline_diagnostics=[pipeline_diagnostic(row) for row in rows],
                       objective_eligible_instances=[row["instance_id"] for row in rows if eligible(row)])
        summaries[method] = summary
    for first, second in itertools.combinations(METHODS[:3], 2):
        # Exact is tiny-only. Select the declared scope explicitly, never silently
        # intersect missing instance identities to improve comparison coverage.
        left, right = records[first], records[second]
        if "exact" in (first, second):
            left = [row for row in left if row.get("rung") == "tiny"]
            right = [row for row in right if row.get("rung") == "tiny"]
        try:
            comparisons[f"{first}_minus_{second}"] = paired_comparison(left, right, population="own")
        except ValueError as exc:
            comparisons[f"{first}_minus_{second}"] = {"unavailable": str(exc)}
    return {"created_at": datetime.now(timezone.utc).isoformat(), "rungs": sorted(rungs),
            "corpus_manifest": str(manifest_path), "corpus_manifest_sha256": digest(manifest_path),
            "expected_instances": len(expected), "expected_input_sha256": sorted(expected_hashes),
            "methods": summaries, "paired_comparisons": comparisons, "run_pins": pins,
            "records": records}


def markdown(summary: dict) -> str:
    lines = ["# Frozen test references", "", f"Measured rungs: {', '.join(summary['rungs'])}; "
             f"{summary['expected_instances']} frozen inputs. Generated {summary['created_at']}.", "",
             "Rule freeze OFF; C2 OFF (`FeasibilityConfig(extra={\"global_screen\": 0})`). "
             "The full checker gates plans. These are classical references, not StowLM results.", "",
             "Own-plan checker pass rates include fully checked timeout incumbents. Objective means and pairs "
             "require passing, unrepaired, explicitly shippable plans; checked timeout incumbents remain raw "
             "best-found counts below. Missing reports and failed runs stay in the attempt denominators. "
             "Move means from different passing populations must not be ranked; use the paired table.", ""]
    lines += ["A checker pass alone does not establish pipeline readiness. A finisher failure can leave a "
              "checked cargo plan without ballast; it remains nonshippable and its counts are diagnostics only. "
              "Finisher outcomes and pipeline messages are reported separately from hard-rule failures below.", ""]
    aggregate_rows = []
    for method, s in summary["methods"].items():
        aggregate_rows.append([method, f"{s['n_instances']}/{s['expected_instances']}", s["missing_instances"],
                               f"{s['n_passed']}/{s['n_instances']}", f"{s['n_shippable']}/{s['n_shipping_verdicts']}",
                               s["moves"]["weighted_nonproductive"]["n"], s["moves"]["weighted_nonproductive"]["mean"],
                               s["planning_time_s"]["sum"], s["pipeline_time_s"]["sum"],
                               json.dumps(s["checker_outcomes"], sort_keys=True)])
    lines += table(["Method", "Attempts/frozen", "Missing", "Own checker passes/attempts", "Shippable/verdicts",
                    "Objective n", "Mean weighted lifts", "Plan s total", "Pipeline s total", "Checker outcomes"], aggregate_rows)
    lines += ["", "## Paired weighted nonproductive lifts", "",
              "Differences are first minus second; negative favours the first. The bootstrap resamples matching "
              "instance pairs to quantify uncertainty within this test set (harness default: 2,000 samples, seed 0).", ""]
    paired_rows = []
    for name, p in summary["paired_comparisons"].items():
        paired_rows.append([name, p.get("n_pairs"), p.get("n_excluded"), p.get("mean_difference"),
                            p.get("confidence_interval"), p.get("unavailable")])
    lines += table(["Comparison", "Eligible pairs", "Excluded", "Mean difference", "95% interval", "Unavailable reason"], paired_rows)
    lines += ["", "## Every plan attempt", "",
              "R = yard rehandles; S = restow lifts; H = hatch-cover lifts. Now/later values come from the checker, "
              "including its voyage projection. All counts here are raw diagnostics; only rows with Objective=yes "
              "enter objective aggregates. A dash is missing, never an assumed zero.", ""]
    plan_rows = []
    for method in METHODS[:3]:
        for row in summary["records"][method]:
            c, timing = counts(row), row.get("timing") or {}
            result = row.get("result") or {}
            diagnostic = pipeline_diagnostic(row)
            plan_rows.append([row["instance_id"], method, row.get("exact_label") if method == "exact" else result.get("status"),
                              checker_outcome(row), first_failure(row), row.get("shippable"), eligible(row),
                              diagnostic["ballast_status"], diagnostic["ballast_present"],
                              c["relocations"], c["restow_lifts"], c["hatch_cover_lifts"],
                              c["future_restow_lifts"], c["future_hatch_cover_lifts"], c["weighted_nonproductive"],
                              timing.get("plan_s"), timing.get("total_s"), row.get("source_drift", False)])
    lines += table(["Instance", "Method", "Result/proof", "Checker", "First hard rule", "Shippable", "Objective",
                    "Ballast status", "Ballast present",
                    "R now", "S now", "H now", "S later", "H later", "Raw weighted", "Plan s", "Pipeline s", "Source drift"], plan_rows)
    lines += ["", "## Pipeline and reference exclusions", "",
              "The first hard-rule column above can be empty when finalization or source eligibility fails. "
              "The messages below explain every recorded nonshippable attempt, including ballast search "
              "failures that do not prove infeasibility.", ""]
    failures = []
    for method in METHODS[:3]:
        for row in summary["records"][method]:
            if row.get("shippable") is True:
                continue
            d = pipeline_diagnostic(row)
            failures.append([row["instance_id"], method, d["status"], d["ballast_status"],
                             d["ballast_present"], d["not_found_cause"], d["message"],
                             d["reference_exclusion_reason"], ", ".join(d["relevant_source_changes"])])
    lines += table(["Instance", "Method", "Status", "Ballast outcome", "Ballast present", "Not-found cause",
                    "Pipeline message", "Reference exclusion", "Relevant source changes"], failures)
    if not failures:
        lines += ["", "No recorded nonshippable attempts."]
    lines += ["", "Exact `optimal` means weighted-lift optimality in the public engine model only "
              "(`engine_model_weighted_lifts`); physical-domain optimality and continuous-margin optimality are "
              "not certified. `best-found (timeout)` is a validated incumbent without an optimality proof, and "
              "is excluded from shippable objective comparisons. Exact solve budget is at most 60 s; outer "
              "ballast/checker work can add pipeline time.", "", "## Lower bounds (no plans)", "",
              "A lower bound counts work every possible solution must pay. These rows have no ballast finisher "
              "or checker verdict because they contain no plan. Zero gives no informative relative-gap certificate. "
              "Disjoint lift types add; overlapping bounds use their maximum.", ""]
    bound_rows = []
    for row in summary["records"]["bounds"]:
        b = row.get("lower_bound") or {}
        bound_rows.append([row["instance_id"], *(b.get(key) for key in FIELDS), b.get("future_restow_lifts"),
                           b.get("future_hatch_cover_lifts"), b.get("weighted"), b.get("gap_informative"),
                           (row.get("timing") or {}).get("total_s")])
    lines += table(["Instance", "R now LB", "S now LB", "H now LB", "S later LB", "H later LB", "Weighted LB", "Informative", "Time s"], bound_rows)
    lines += ["", f"Bounds recorded: {len(bound_rows)}/{summary['expected_instances']}; these are not checker passes.",
              "", "## Pins and limitations", "",
              f"Corpus manifest SHA256: `{summary['corpus_manifest_sha256']}`. Full input hashes, per-run manifests, "
              "source fingerprints and D68 JSON aggregates are in [test-bounds/summary.json](test-bounds/summary.json).", ""]
    for method, pin in summary["run_pins"].items():
        lines.append(f"- {method}: manifest SHA256 `{pin['manifest_sha256']}`; "
                     f"{len(pin['source_before_variants'])} source fingerprint variant(s); "
                     f"{len(pin['source_drift_instances'])} instance(s) with source drift flagged; "
                     f"{len(pin['reference_exclusions'])} reference exclusion(s).")
    lines += ["", "Source drift is disclosed rather than attributed to a single source revision; inspect the saved "
              "fingerprints before release comparisons. This report reads stored checker evidence; it does not replay plans."]
    execution = summary.get("execution")
    if execution:
        actual = execution["actual"]
        lines += ["", f"Compute: estimated 55 minutes on up to 2 cores / 4 GB / no GPU; measured "
                  f"{actual['wall_s']/60:.3f} minutes including validation, reduced to one core after parallel work. "
                  "`nice -n 10` was invoked but the sandbox denied setpriority. "
                  "[Exact commands, compute actuals and verification](test-bounds/compute.json).", "",
                  "APPROXIMATION: C2 is OFF under D79; the full checker remains the gate. "
                  "Exact uses a 60-second requested search budget and 10,000-node cap; outer checking is separate.", "",
                  "Assumption: the [pinned runtime](../refs_tools/frozen-runtime/snapshot.json) uses the original "
                  "parameters and shared warm numerical caches. Cold relocated linear-boundary-policy path "
                  "identity remains unverified; no campaign failure was observed. "
                  "[Audit](../refs_tools/review.md). These are standalone MVP input-hash references, not the "
                  "historical official harness epoch. Small/medium await the leader's test-v2 go-ahead.", "",
                  "Verification: 17 harness-metrics tests passed; all 45 saved plan/report bindings and "
                  "15 bound records validated; all 90 frozen SHA256 entries and 259 snapshot hashes remained unchanged."]
    lines += ["", "References: [D68 aggregation and paired bootstrap](../../../src/eval/harness_metrics.py), "
              "[lower-bound validity](../../../src/stowage/bounds/__init__.py), "
              "[exact scope](../../../src/stowage/solver/exact.py), "
              "[planner finalization and checker gate](../../../src/stowage/planner.py).", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=ROOT / "experiments/mvp/runs")
    parser.add_argument("--corpus-manifest", type=Path, default=ROOT / "experiments/mvp/corpus/manifest.json")
    parser.add_argument("--rung", action="append", choices=("tiny", "small", "medium"))
    args = parser.parse_args()
    summary = summarize(args.runs_root, args.corpus_manifest, set(args.rung or ["tiny"]))
    destination = args.runs_root / "test-bounds"
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "compute.json").exists():
        summary["execution"] = read_json(destination / "compute.json")
    (destination / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n")
    output = args.runs_root / "test-references.md"
    output.write_text(markdown(summary))
    print(output)


if __name__ == "__main__":
    main()

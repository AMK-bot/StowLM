#!/usr/bin/env python3
"""Append TEST v2 small references without changing the accepted tiny report.

No planners are executed. Explicit --expected-count pins the released population;
a later amendment needs an explicit new count and release-report argument.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from eval.harness_metrics import aggregate_results, checker_outcome, paired_comparison

_spec = importlib.util.spec_from_file_location("tiny_reference_helpers", Path(__file__).with_name("summarize_refs.py"))
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
ROOT = Path(__file__).resolve().parents[3]
MARKER = b"<!-- BEGIN SMALL TEST V2 REFERENCES -->"
END_MARKER = b"<!-- END SMALL TEST V2 REFERENCES -->"
METHODS = ("solver", "bounds", "twostep")


def released_inputs(release_report: Path, manifest_path: Path, expected_count: int) -> list[dict]:
    release = helpers.read_json(release_report)
    hashes = {p: h for p, h in release["hashes"].items() if p.startswith("test/small/") and p.endswith(".json")}
    if len(hashes) != expected_count:
        raise ValueError(f"Release has {len(hashes)} small inputs, expected explicit count {expected_count}; acknowledge amendments explicitly")
    manifest = helpers.read_json(manifest_path)
    rows = {r["path"]: r for r in manifest["files"] if r["path"] in hashes}
    if set(rows) != set(hashes):
        raise ValueError("Release inputs missing from manifest")
    for path, sha in hashes.items():
        if rows[path]["sha256"] != sha:
            raise ValueError(f"Manifest/release hash mismatch: {path}")
        if helpers.digest(manifest_path.parent / path) != sha:
            raise ValueError(f"Frozen input changed: {path}")
    return [rows[p] for p in sorted(rows)]


def summarize(runs_root: Path, expected: list[dict], *, twostep_released: bool = False) -> dict:
    expected_by_hash = {r["sha256"]: r for r in expected}
    if len(expected_by_hash) != len(expected):
        raise ValueError("Duplicate frozen input hashes")
    records, summaries = {}, {}
    for method in METHODS:
        rows = helpers.load_records(runs_root / f"test-{method}-small-v2", {"small"})
        if method == "twostep" and rows and not twostep_released:
            raise ValueError("Two-step records exist but release was not explicitly acknowledged")
        seen = set()
        for row in rows:
            sha = row.get("input_sha256")
            if sha not in expected_by_hash:
                raise ValueError(f"Unselected input: {method}/{row['instance_id']}")
            if sha in seen:
                raise ValueError(f"Duplicate input hash: {method}/{sha}")
            seen.add(sha)
        records[method] = rows
        if method == "bounds":
            continue
        summary = aggregate_results(rows, method=method, split="test", population="own")
        summary.update(expected_instances=len(expected),
                       missing_paths=[r["path"] for r in expected if r["sha256"] not in seen],
                       status="waiting for leader go" if method == "twostep" and not twostep_released else "released")
        summaries[method] = summary
    comparison = {"unavailable": "Two-step waiting for leader go"}
    if twostep_released:
        if all(len(records[m]) == len(expected) for m in ("solver", "twostep")):
            try:
                comparison = paired_comparison(records["solver"], records["twostep"], population="own")
            except ValueError as exc:
                comparison = {"unavailable": str(exc)}
        else:
            comparison = {"unavailable": "Full matching released population not yet recorded"}
    return dict(expected=expected, records=records, methods=summaries, paired_comparison=comparison)


def markdown(summary: dict) -> str:
    n = len(summary["expected"])
    lines = ["## Small — frozen TEST v2", "", f"Released population: **{n} small instances**. Tiny rows above are preserved byte for byte. "
             "Exact remains tiny only. Two-step awaits its explicit leader go unless marked released below.", "",
             'APPROXIMATION: C2 OFF (`FeasibilityConfig(extra={"global_screen": 0})`); '
             '`STOWAGE_BALLAST_V4=1` is scoped to finalization only (D88). Every displayed plan requires the full checker gate. '
             'Bounds contain no plans and have no checker verdict.', "",
             'R = yard rehandles; S = restow lifts; H = hatch-cover lifts. All attempt counts include failed checkpoints. '
             'Missing/unattempted cases are listed explicitly and remain in the frozen-population denominator. '
             'Missing values are dashes, never assumed zero. Objective means use only passing, unrepaired, explicitly shippable plans; '
             'means from differing populations must not be ranked.', ""]
    aggregate_rows = []
    for method, s in summary["methods"].items():
        if s["status"] == "waiting for leader go":
            aggregate_rows.append([method, s["status"], f"0/{n}", n,
                                   None, None, None, None, None, None])
            continue
        aggregate_rows.append([method, s["status"], f"{s['n_instances']}/{n}", len(s["missing_paths"]),
                               f"{s['n_passed']}/{s['n_instances']}", f"{s['n_passed']}/{n}",
                               f"{s['n_shippable']}/{n}", s["moves"]["weighted_nonproductive"]["n"],
                               s["moves"]["weighted_nonproductive"]["mean"], s["pipeline_time_s"]["sum"]])
    lines += helpers.table(["Method", "Release", "Attempts/frozen", "Unattempted", "Checker passes/attempts",
                            "Checker passes/frozen", "Shippable/frozen", "Objective n", "Mean weighted", "Pipeline s total"], aggregate_rows)
    expected_by_hash = {r["sha256"]: r for r in summary["expected"]}
    plan_rows, failures, bound_rows, settings = [], [], [], []
    for expected in summary["expected"]:
        settings.append([expected["path"], expected.get("ship"), expected.get("family"),
                         expected.get("booking_slack"), expected.get("booking_mass_fraction"), expected["sha256"]])
    for method in ("solver", "twostep"):
        indexed = {r["input_sha256"]: r for r in summary["records"][method]}
        for expected in summary["expected"]:
            row = indexed.get(expected["sha256"])
            if row is None:
                status = "waiting for leader go" if summary["methods"][method]["status"] != "released" else "unattempted"
                plan_rows.append([expected["path"], method, status] + [None] * 14)
                continue
            c, d, timing = helpers.counts(row), helpers.pipeline_diagnostic(row), row.get("timing") or {}
            plan_rows.append([row["instance_id"], method, d["status"], checker_outcome(row), helpers.first_failure(row),
                              row.get("shippable"), helpers.eligible(row), d["ballast_status"], d["ballast_present"],
                              c["relocations"], c["restow_lifts"], c["hatch_cover_lifts"], c["future_restow_lifts"],
                              c["future_hatch_cover_lifts"], c["weighted_nonproductive"], timing.get("plan_s"), timing.get("total_s")])
            if not helpers.eligible(row):
                failures.append([row["instance_id"], method, checker_outcome(row), helpers.first_failure(row),
                                 d["ballast_status"], d["not_found_cause"], d["message"], d["reference_exclusion_reason"]])
    lines += ["", "### Every small plan attempt and unattempted case", ""]
    lines += helpers.table(["Instance/path", "Method", "Pipeline", "Checker", "First hard rule", "Ship", "Objective",
                            "Ballast status", "Ballast present", "R now", "S now", "H now", "S later", "H later",
                            "Weighted", "Plan s", "Pipeline s"], plan_rows)
    lines += ["", "### Failed or excluded small attempts", ""]
    lines += helpers.table(["Instance", "Method", "Checker", "First hard rule", "Ballast status", "Not-found cause", "Message", "Exclusion"], failures)
    if not failures:
        lines += ["", "No recorded failed or excluded attempts."]
    indexed = {r["input_sha256"]: r for r in summary["records"]["bounds"]}
    for expected in summary["expected"]:
        row = indexed.get(expected["sha256"], {})
        b = row.get("lower_bound") or {}
        bound_rows.append([row.get("instance_id", expected["path"]), "recorded" if row else "unattempted",
                           *(b.get(key) for key in helpers.FIELDS), b.get("future_restow_lifts"),
                           b.get("future_hatch_cover_lifts"), b.get("weighted"), (row.get("timing") or {}).get("total_s")])
    lines += ["", "### Small lower bounds", "", "A lower bound counts moves every solution must pay; zero does not certify a useful relative gap. "
              "[Bound definitions and validity](../../../src/stowage/bounds/__init__.py).", ""]
    lines += helpers.table(["Instance/path", "Status", "R now LB", "S now LB", "H now LB", "S later LB", "H later LB", "Weighted LB", "Time s"], bound_rows)
    lines += ["", f"Bounds recorded: {len(indexed)}/{n} (not checker passes).", "", "### Small paired comparison", ""]
    pair = summary["paired_comparison"]
    if "unavailable" in pair:
        lines += [pair["unavailable"] + "."]
    else:
        lines += ["Solver minus two-step; negative favours solver. The paired bootstrap resamples matching instances to estimate uncertainty within this test set "
                  "([D68 implementation and source](../../../src/eval/harness_metrics.py)).", ""]
        lines += helpers.table(["Eligible pairs", "Excluded", "Mean difference", "95% interval"],
                               [[pair.get(k) for k in ("n_pairs", "n_excluded", "mean_difference", "confidence_interval")]])
    lines += ["", "### Small frozen input settings", ""]
    lines += helpers.table(["Path", "Ship", "Family", "Booking slack", "Booking mass fraction", "SHA256"], settings)
    lines += ["", "Full D68 aggregates, run records and release pins: [small summary](test-bounds-small-v2/summary.json). "
              "This report reads saved full-checker evidence; it does not replay plans. Further v2.1 instances require an explicit release selection/count.", ""]
    if summary.get('execution'):
        execution = summary['execution']
        lines += ["### Small execution and verification", "",
                  f"Measured pilot: {execution['pilot']['timing']['total_s']:.3f} s. "
                  f"Remaining nine cases: {execution['wall_s']/60:.3f} min on up to two one-thread CPU workers "
                  "(12 min estimated; 30 min stage-cap ceiling), 4 GB reserved, no GPU. "
                  "`nice -n 10` was attempted but sandbox-denied. The recorded child RSS high-water mark is "
                  f"{execution['child_peak_rss_bytes']/1e9:.3f} GB; simultaneous aggregate RSS was not measured.", "",
                  "35 targeted tests passed. All ten saved input/report bindings, weighted objective arithmetic, "
                  "and absent/1/absent D88 stage environments validated. The approved tiny prefix is unchanged. "
                  "The original ten input hashes remain the selection despite live v2.1 metadata relabelling. "
                  "[Exact commands, failures and compute actuals](test-solver-small-v2/compute.json). "
                  "[Source/provenance audit](../refs_tools/review-small-v2.md).", "",
                  "Prepared baseline command (not executed; leader GO required):", "",
                  f"`{execution['baseline_command']}`", ""]
    return "\n".join(lines)


def append_section(report: Path, body: str, backup_dir: Path) -> str:
    original = report.read_bytes()
    if original.count(MARKER) > 1 or original.count(END_MARKER) > 1:
        raise ValueError("Duplicate small section markers")
    if MARKER in original:
        prefix, old_section = original.split(MARKER, 1)
        if END_MARKER not in old_section or old_section.split(END_MARKER, 1)[1].strip():
            raise ValueError("Small section is not a complete terminal section")
    elif END_MARKER in original:
        raise ValueError("Orphaned small end marker")
    else:
        prefix = original
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "tiny-report-prefix.md"
    digest = hashlib.sha256(prefix).hexdigest()
    if backup.exists() and backup.read_bytes() != prefix:
        raise ValueError("Accepted tiny prefix differs from preserved copy")
    backup.write_bytes(prefix)
    (backup_dir / "tiny-report-prefix.sha256").write_text(digest + "\n")
    report.write_bytes(prefix + MARKER + b"\n\n" + body.encode() + END_MARKER + b"\n")
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=ROOT / "experiments/mvp/runs")
    parser.add_argument("--release-report", type=Path, default=ROOT / "experiments/mvp/corpus_tools/EVAL-V2-REPORT.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / "experiments/mvp/corpus/manifest.json")
    parser.add_argument("--expected-count", type=int, default=10)
    parser.add_argument("--twostep-released", action="store_true")
    args = parser.parse_args()
    expected = released_inputs(args.release_report, args.manifest, args.expected_count)
    summary = summarize(args.runs_root, expected, twostep_released=args.twostep_released)
    compute = args.runs_root / 'test-solver-small-v2/compute.json'
    if compute.exists():
        summary['execution'] = helpers.read_json(compute)
    summary["release_report_sha256"] = helpers.digest(args.release_report)
    summary["manifest_sha256"] = helpers.digest(args.manifest)
    destination = args.runs_root / "test-bounds-small-v2"
    prefix_digest = append_section(args.runs_root / "test-references.md", markdown(summary), destination)
    summary["tiny_prefix_sha256"] = prefix_digest
    (destination / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"tiny_prefix_sha256": prefix_digest, "small_instances": len(expected)}))


if __name__ == "__main__":
    main()

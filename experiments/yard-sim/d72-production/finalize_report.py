"""Final audit/summary after BOTH cooperative executors finish; no new yards."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import statistics
import subprocess
import sys
from run_measurement import interval

HERE = Path(__file__).resolve().parent


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main():
    for script in ("verify_results.py", "update_report.py"):
        subprocess.run([sys.executable, str(HERE / script)], check=True)
    rows = [json.loads(line) for line in (HERE / "results.jsonl").read_text().splitlines()]
    measured = [row for row in rows if row["status"] == "measured"]
    assert all(arm["metrics"]["direct_delivery_exports"] == 0
               for row in measured for arm in row["arms"].values()), (
        "direct-delivery bypasses the snapshot retrieval hook: audit no-planner scope before reporting")
    cells = json.loads((HERE / "summary.json").read_text())
    model_epochs, runner_epochs, runtimes = set(), set(), set()
    harness_changes = []
    for row in rows:
        if "source_before" not in row:
            continue
        before, after = [json.loads((HERE / "manifests" / (row[key] + ".json")).read_text())["files"]
                         for key in ("source_before", "source_after")]
        physical = {key: value for key, value in before.items() if not key.startswith("experiment/")}
        model_epochs.add(digest(physical))
        runner_sha = before["experiment/run_measurement.py"]
        runner_epochs.add(runner_sha)
        archive = HERE / "runner-sources" / (runner_sha + ".py")
        assert archive.exists() and hashlib.sha256(archive.read_bytes()).hexdigest() == runner_sha
        changed = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
        assert not [key for key in changed if not key.startswith("experiment/")], "physical/input epoch changed inside a case"
        if changed:
            harness_changes.append({"spec": row["spec"], "paths": changed})
        runtimes.add(json.dumps(row["runtime"], sort_keys=True))
        assert "/source-snapshot/src/stowage/" in row["import_origins"]["instances"]
        assert "/source-snapshot/src/stowage/" in row["import_origins"]["simulator"]
    assert len(model_epochs) == len(runtimes) == 1, "do not pool different production/environment epochs"
    current_runtime = {"python": sys.version,
        "dependencies": {name: version(name) for name in ("numpy", "pydantic", "scipy")}}
    assert json.dumps(current_runtime, sort_keys=True) == next(iter(runtimes)), (
        "analysis runtime differs from the measured/cached worker environment")
    unexpected = [row for row in rows if row["status"] not in {"measured", "producer_or_measurement_refusal", "timeout"}
                  or (row["status"] == "producer_or_measurement_refusal"
                      and not row.get("error", "").startswith("ValueError: screening exhausted"))]
    positive = [cell for cell in cells if cell["cell"][3] > 0]
    precision = sum(bool(cell["shares"]["default"] and cell["shares"]["default"]["within_5pp"]) for cell in positive)
    effects = [cell["shares"]["default"]["mean"] for cell in positive if cell["shares"]["default"]]
    durations = [arm["yard_time_s"] for row in measured for arm in row["arms"].values()
                 if not arm["reused_identical_effective_policy"]]
    drift = json.loads((HERE / "source-drift.json").read_text())
    admissions = {}
    for noise in sorted({row["spec"]["noise"] for row in rows}):
        group = [row for row in measured if row["spec"]["noise"] == noise]
        admissions[str(noise)] = {"measured_candidates": len(group),
            "default_mean_target_present_fraction": statistics.mean(
                row["arms"]["default"]["metrics"]["exports_present"] / row["target_count"] for row in group) if group else None,
            "default_missing_targets": sum(row["arms"]["default"]["missing_required_yard_exports"] for row in group),
            "default_external_queue_all_calls": sum(row["arms"]["default"]["external_queue"] for row in group),
            "complete_yard_admission": {arm: sum(row["arms"][arm]["complete_yard_admission"] for row in group)
                for arm in ("off", "default", "old_n_coupled")}}
    present_cells = []
    for cell in cells:
        group = [row for row in measured if
            [row["spec"][key] for key in ("ship", "rung", "port", "noise")] == cell["cell"]]
        present_cells.append({"cell": cell["cell"], "shares": {arm: interval([
            row["arms"][arm]["metrics"]["foreign_blocked_present_export_share"] for row in group
            if row["arms"][arm]["metrics"]["exports_present"] > 0])
            for arm in ("off", "default", "old_n_coupled")}})
    summary = {"created_utc": datetime.now(timezone.utc).isoformat(), "requested_records": 768,
        "records": len(rows), "measured_candidates": len(measured), "statuses": dict(Counter(r["status"] for r in rows)),
        "measured_cells": sum(cell["measured"] > 0 for cell in cells),
        "positive_noise_cells": len(positive), "positive_noise_cells_with_descriptive_ci_half_width_le_5pp": precision,
        "positive_noise_mean_default_share_range": [min(effects), max(effects)] if effects else None,
        "model_input_environment_epochs": sorted(model_epochs), "runner_epochs": sorted(runner_epochs),
        "harness_only_mid_case_changes": harness_changes, "runtime": json.loads(next(iter(runtimes))),
        "workspace_changed_files": len(drift), "unexpected_errors": unexpected,
        "yard_admissions_by_noise": admissions,
        "yard_present_export_shares": present_cells,
        "positive_noise_present_only_cells_with_descriptive_ci_half_width_le_5pp": sum(
            bool(entry["shares"]["default"] and entry["shares"]["default"]["within_5pp"])
            for entry in present_cells if entry["cell"][3] > 0),
        "distinct_simulator_runs": len(durations),
        "yard_wall_s": {"min": min(durations), "median": statistics.median(durations),
                        "max": max(durations)} if durations else {},
        "peak_worker_rss_gib": max(row.get("peak_rss_gib", 0) for row in rows),
        "a8_over_limit_observations": sum(block["over_limit_stacks"] for row in measured
            for arm in row["arms"].values() for block in arm["blocks"]),
        "accepted_instance_qualification_performed": False}
    (HERE / "final-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    path = HERE / "report.md"
    detailed = path.read_text().replace("source-start epochs", "source-start content manifests (runner included)")
    detailed = detailed.replace("cases changed source while running",
        "cases changed tracked content while running (harness-only transitions audited above)")
    detailed = detailed.replace("Raw refusals stay in the denominator.",
        "Refusals remain in requested-seed accounting; share means/intervals use measured yards only.")
    overview = ["# D72 production study — final measured grid", "",
        f"**{len(rows)}/768 seed specifications completed; {len(measured)} produced paired yards across "
        f"{summary['measured_cells']}/96 cells.** Status counts: `{json.dumps(summary['statuses'], sort_keys=True)}`. "
        "Eight outer seeds/cell; producer work cap8 candidate attempts/seed. Refused or timed-out seeds are retained, "
        "not replaced. This finite work cap can reduce measured precision; it is not the producer's default attempt limit.", "",
        f"**Precision:** {precision}/72 positive-noise cells have an observed independent-yard t-interval "
        "half-width ≤5pp and nonzero sample variance. The remaining cells do **not** establish the requested ±5pp. "
        "These are descriptive per-cell intervals, not simultaneous or distribution-free guarantees. Noise0 follows "
        "the synthetic no-mixing guard; its zero-variance samples are not claimed as statistical calibration.", "",
        f"**A8/source:** {summary['a8_over_limit_observations']} over-limit observations. "
        f"One verified frozen producer/model/input/environment epoch; {len(runner_epochs)} archived harness revisions "
        f"(metadata/caching only), with {len(harness_changes)} harness-only mid-case transitions. "
        f"{len(drift)} source/input files now differ in the active workspace; compare `source-drift.json` and "
        "`source-snapshot.json` before the yield matrix. Final admission/arrival-ballast changes can change the cohort.", "",
        "**Scope:** genuine production-producer workloads and layouts, pre-final-screen snapshots. "
        "No planner, completion/C2/background qualification, source/test modification or claimed accepted-instance yield. "
        "The old sensitivity selects share=noise in the current implementation; it is a mapping sensitivity, "
        "not a historical-version replay.", "",
        "**Pre-freeze handoff:** none of the measured noise0 or noise.2 candidates had every required target "
        "export present at the snapshot. Default mean target presence was "
        f"{admissions['0.0']['default_mean_target_present_fraction']:.1%} / "
        f"{admissions['0.2']['default_mean_target_present_fraction']:.1%}, respectively. "
        "The frozen real producer rejects these candidates at `expert.physical.slot_occupancy` immediately "
        "after simulation (`generate/instances.py` required_yard check). All prescribed gates are before "
        "the cutoff and target arrival; queued storage arrivals, not snapshot-only load deferrals, account "
        "for missing targets. This is not proof that the producer's larger resampling budget can never "
        "find a usable candidate. It is an admission/cohort issue for the producer/yard owners and the yield "
        "matrix: do not treat these candidate-yard means as accepted-production calibration or close the "
        "regeneration gate on this evidence alone.", "",
        "**Yard admission:** counts below are complete storage admission of required target-call exports "
        "among the measured candidates; "
        "they are not final producer acceptance. At noise0 the synthetic separation policy queues boxes "
        "when no compatible separated destination remains, rather than mixing above pending exports. "
        "Partial yard admissions remain in the main outcome means; use the raw present-only shares "
        "and missing-export counts when interpreting those cells.", "",
        "| Noise | Measured candidates | Complete target admission off / default / old |",
        "|---:|---:|---|",
        *[f"| {noise} | {entry['measured_candidates']} | " +
          "/".join(str(entry['complete_yard_admission'][arm]) for arm in ("off", "default", "old_n_coupled")) + " |"
          for noise, entry in admissions.items()], "",
        "**Share interpretation:** configured mix share.25 selects eligible foreign placements; it does not "
        "promise that25% of exports are buried. Available compatible stacks, A8 crossing/cap checks and queueing "
        "also constrain placement. The reported outcome counts distinct target exports with at least one "
        "foreign box above; the raw records also retain selected/achieved foreign-placement counters and "
        "present-only export shares. The shared yard bound counts foreign blockers that must move, so it is "
        "not numerically interchangeable with the number of buried exports.", "",
        "**Timing/accounting:** per-arm `yard_time_s` is the original measured simulator call, including the "
        "producer's snapshot-only retrieval deferrals. Preparation/ship-building time is separate. OFF/default/old "
        "execute in that order; warm caches and shared-machine contention mean these are observed costs, not causal "
        "policy speedups. Cache-hit `process_wall_s` measures request delivery only. Prefetch overlap is never extra "
        "independent draws. Noise0's identical OFF/old arm is reused and labelled; default share.25 runs separately. "
        f"Peak measured worker RSS={summary['peak_worker_rss_gib']:.3f}GiB; max two one-thread workers, 5GB reserved.", "",
        "**Performance follow-up (static inference, not profiling attribution):** the frozen noise0 placement "
        "path classifies each candidate destination twice and calls the export-separation predicate, whose "
        "destination-stack lookup is linear. Positive-noise placement uses a prepared dictionary lookup. "
        "This is a plausible contributor to the observed noise0 scaling cost; shared-machine wall timings "
        "do not isolate its contribution. A future scoped optimization should preserve the exact separation "
        "predicate and engine checks while avoiding repeated classification/lookup. No production code was "
        "changed for this measurement. See frozen `src/stowage/yard/simulator.py` choose() and "
        "`src/stowage/yard/dig_out.py` preserves_export_separation().", "",
        f"**Unexpected measurement errors:** {len(unexpected)}; details in `final-summary.json`. "
        "A producer refusal is not a feasibility certificate or a yard-policy failure.", "",
        "**Rerun hygiene:** preserve this study directory. The runner resumes existing seed specifications; "
        "a new producer/source epoch needs a fresh study directory and source snapshot, not an append to "
        "these results. Validate the stored runtime before reusing any content cache: the current case key "
        "includes source/uv.lock but does not independently key every installed runtime version.", "",
        "## Detailed per-cell results and block-height distributions", ""]
    present_table = ["", "## Yard-present export shares (alternative denominator)", "",
        "Independent-yard means and descriptive95% t intervals use only exports physically present in the yard. "
        "An arm with no exports present is excluded from this alternative ratio, with usable counts shown; "
        "all candidates remain in the declared-export table above. These are the same seeded yards, not extra draws.", "",
        "| Ship | Rung | Port | Noise | Usable yards off/default/old | Buried % off/default/old | Default95% CI | Default ±5pp |",
        "|---|---|---|---:|---|---|---|---|"]
    for entry in present_cells:
        stats = entry["shares"]
        counts = "/".join(str(stats[arm]["n"] if stats[arm] else 0) for arm in stats)
        shares = "/".join(f"{100 * stats[arm]['mean']:.1f}" if stats[arm] else "—" for arm in stats)
        default = stats["default"]
        ci = "[" + ", ".join(f"{100 * value:.1f}" for value in default["ci95"]) + "]" if default and default["ci95"] else "unavailable"
        precise = "yes*" if default and default["within_5pp"] else "NO"
        present_table.append("| " + " | ".join(map(str, entry["cell"])) + f" | {counts} | {shares} | {ci} | {precise} |")
    path.write_text("\n".join(overview) + detailed + "\n".join(present_table) + "\n")
    print(json.dumps({key: summary[key] for key in ("records", "measured_candidates", "measured_cells",
        "positive_noise_cells_with_descriptive_ci_half_width_le_5pp", "a8_over_limit_observations",
        "peak_worker_rss_gib", "workspace_changed_files")}))
    if unexpected:
        raise SystemExit("unexpected errors require review; do not declare the study clean")


if __name__ == "__main__":
    main()

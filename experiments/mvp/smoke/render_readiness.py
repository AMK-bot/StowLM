"""Render measured MVP readiness artifacts without inventing missing stages."""
from __future__ import annotations

import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def completed(row):
    return row.get("completed", "wall_s" in row)


def failure_text(row):
    failure = row.get("first_failure", {})
    details = failure.get("rule") or failure.get("error") or row.get("error")
    if not details:
        details = next((stage.get("first_failing_rule") or stage.get("error")
                        for stage in row.get("stages", {}).values()
                        if not stage.get("passed") and (stage.get("first_failing_rule") or stage.get("error"))), None)
    return str(details or failure.get("stage") or row.get("blocked_at") or "stage failed")


def render(run_dir=None):
    run_dir = Path(run_dir) if run_dir else HERE / "run-20261029"
    historical = run_dir.name == "run-20261029" and (HERE / "v50-interruption.json").exists()
    rows = []
    for path in sorted(run_dir.glob("*/result.json")):
        rows.append(json.loads(path.read_text()))
    finished = [row for row in rows if completed(row)]
    def valid_pass(row):
        return (completed(row) and row.get("passed", False)
                and row.get("current_tree_evidence_valid",
                    row.get("source_sha256_start") == row.get("source_sha256_end")))
    passed = sum(valid_pass(row) for row in finished)
    lines = [
        "# MVP readiness: rule-freeze OFF",
        "",
        f"Rendered {datetime.now(timezone.utc).isoformat()}. Measured local synthetic diagnostics; no cloud or model inference.",
        "",
        f"{'Historical interrupted campaign' if historical else 'End-to-end campaign'}: **{passed}/{len(finished)} completed cases pass**; target 18 (two per ship × tiny/small/medium). "
        "A pass requires generation, bounded scalable construction, ballast finalization, full checker, and complete C2-off token-mask replay. "
        "Missing stages are shown as —; timeouts are inconclusive, not proofs of infeasibility.",
        "",
        "## Cache preparation",
        "",
        "The pre-restart default `uv run python -m stowage.cache_warm` "
        "exited **2**, **72.697 s** (three ship workers). All 15 linear profiles "
        "(three ships × tiny/small/medium/full/production-tiny) reject `production linear policy epoch is stale; requalify`. "
        "Stability caches and liquid qualification were reused. No linear requalification was performed (D78). "
        "[Exact command/result](smoke/cache-warm-summary.json), [raw output](smoke/cache-warm.log).",
        "",
        "**Required D79 configuration: `FeasibilityConfig(extra={\"global_screen\": 0})` in the MVP mask and scalable solver.** "
        "The full checker remains the acceptance gate. The assignment-MIP constructor still requires qualified linear physics; "
        "use `ScalableSolver` for gold with the explicit C2-off configuration.",
        "",
        "## End-to-end measurements",
        "",
        "Times are wall seconds. Constructor means `ScalableSolver(max_neighborhoods=0)`, not the linear assignment-MIP constructor. "
        "Inputs use the generator and opt-ins pinned in each case, Laem Chabang port parameters and deterministic seeds; no corpus files are edited.",
        "",
        "| Ship | Rung | Replicate | Boxes / cells | Generate | Construct | Ballast | Checker | Mask C2 off | Result / first failure |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    warm_path = HERE / "cache-warm-v54-c2off.json"
    if warm_path.exists():
        warm = json.loads(warm_path.read_text())
        lines[lines.index("## End-to-end measurements"):lines.index("## End-to-end measurements")] = [
            f"5.4a MVP command: `UV_CACHE_DIR=$PWD/.uv-cache uv run python -m stowage.cache_warm --c2-off --workers 1` "
            f"exited **{warm['returncode']}** in **{warm['wall_seconds']:.3f} s**. All 15 linear profiles are explicitly "
            "`not used: C2 off (D78/D79)`. Stability qualification remains required. "
            "[Measured result](smoke/cache-warm-v54-c2off.json); [raw output](smoke/cache-warm-v54-c2off.raw.log).", ""]
    if historical:
        lines.insert(6, "The leader stopped this campaign at 11:31 EDT. It has 15 completed cases, one interrupted case and two unstarted cases; "
                     "the unfinished fast suite was also killed. These are historical diagnostics, not 5.4b results. "
                     "5.4b is prepared and awaits the leader's settled 4.7 settings and go message. "
                     "[Interruption record](smoke/v50-interruption.json).")
        lines.insert(7, "")
    if not historical and rows:
        if any(row.get("diagnostic_only") for row in rows):
            primary = [row for row in finished if not row.get("diagnostic_only")]
            diagnostic = [row for row in finished if row.get("diagnostic_only")]
            lines[4] = (f"D83 tiny/small smoke: **{sum(valid_pass(row) for row in primary)}/{len(primary)} completed cases pass**. "
                        f"Medium diagnostic exploration: **{sum(valid_pass(row) for row in diagnostic)}/{len(diagnostic)} pass**. "
                        f"Overall {len(finished)}/18 cases completed. A pass requires generation, bounded default construction, "
                        "ballast finalization, the full checker, and complete C2-off token-mask replay. "
                        "Timeouts are inconclusive, not proofs of infeasibility.")
        overview = ["## Ship/rung outcomes", "",
                    "Passes require the full checker and complete C2-off replay. Medium D83 rows are diagnostic exploration; "
                    "they are reported separately from the tiny/small smoke population.", "",
                    "| Ship | Rung | Role | Pass / completed | First failing stage / rule or cause |",
                    "|---|---|---|---:|---|"]
        for ship in ("feeder_500", "feeder_800", "feeder_1700"):
            for rung in ("tiny", "small", "medium"):
                group = [row for row in rows if row["ship"] == ship and row["rung"] == rung]
                done = [row for row in group if completed(row)]
                if not group:
                    continue
                failures = sorted({str(row.get("first_failure", {}).get("stage") or row.get("blocked_at") or "unknown")
                                   + ": " + failure_text(row)
                                   for row in done if not valid_pass(row)})
                role = "diagnostic" if any(row.get("diagnostic_only") for row in group) else "smoke"
                cause = "; ".join(failures).replace("|", "\\|").replace("\n", " ") or "—"
                overview.append(f"| {ship} | {rung} | {role} | {sum(valid_pass(row) for row in done)} / {len(done)} | {cause} |")
        overview += [""]
        if any(row.get("diagnostic_only") for row in rows):
            for diagnostic, label in ((False, "Tiny/small smoke"), (True, "Medium diagnostic")):
                population = [row for row in finished if bool(row.get("diagnostic_only")) == diagnostic]
                overview += [f"{label}: **{sum(valid_pass(row) for row in population)}/{len(population)} completed cases pass**.", ""]
        index = lines.index("## End-to-end measurements")
        lines[index:index] = overview
    settings = sorted({json.dumps({key: row.get("request", {}).get(key)
                       for key in ("generator_family", "generator_options", "constructor_mode", "solver_s")}, sort_keys=True)
                       for row in rows if row.get("request")})
    if settings:
        lines[lines.index("| Ship | Rung | Replicate | Boxes / cells | Generate | Construct | Ballast | Checker | Mask C2 off | Result / first failure |"):
              lines.index("| Ship | Rung | Replicate | Boxes / cells | Generate | Construct | Ballast | Checker | Mask C2 off | Result / first failure |")] = [
                  "Measured case settings (not a new recommendation): " + "; ".join(f"`{value}`" for value in settings), ""]
    for row in rows:
        stages = row.get("stages", {})
        timings = [f"{stages[name]['wall_s']:.3f}" if "wall_s" in stages.get(name, {}) else "—"
                   for name in ("generation", "constructor", "ballast", "checker", "mask_c2_off")]
        if valid_pass(row):
            outcome = "PASS"
        elif not completed(row):
            outcome = "INTERRUPTED" if historical or row.get("interrupted") else "INCOMPLETE"
        else:
            outcome = failure_text(row)
        if (row.get("source_sha256_end") is not None
                and row.get("source_sha256_start") != row["source_sha256_end"]):
            outcome += "; source changed during case"
        outcome = outcome.replace("|", "\\|").replace("\n", " ")
        generation = stages.get("generation", {})
        lines.append(f"| {row['ship']} | {row['rung']} | {row['replicate']} | {generation.get('boxes', '—')} / {generation.get('cells', '—')} | "
                     + " | ".join(timings) + f" | {outcome} |")
    lines += ["", f"Saved inputs, plans, full reports and source fingerprints: `{run_dir}`."]
    extra = []
    for name in ("retry-heavy", "reduced-12small-24medium"):
        attempts = [json.loads(path.read_text()) for path in sorted((HERE / name).glob("*/result.json"))]
        if not attempts:
            continue
        extra += [(name, row) for row in attempts]
        lines += ["", f"## Separate diagnostic: {name}", "",
                  "These attempts are additional to the baseline denominator. Reduced booking quantity is an "
                  "**APPROXIMATION**, not evidence of default-volume solvability. Requested quantities, actual quantities "
                  "and decision cells are shown. Reused input time measures loading the saved input, not generation.", "",
                  "| Case | Booked / cells | Input | Construct s | Ballast s | Checker s | C2-off replay s | Result |",
                  "|---|---:|---|---:|---:|---:|---:|---|"]
        for row in attempts:
            stages = row.get("stages", {})
            generation = stages.get("generation", {})
            timings = [f"{stages[key]['wall_s']:.3f}" if "wall_s" in stages.get(key, {}) else "—"
                       for key in ("constructor", "ballast", "checker", "mask_c2_off")]
            mode = "saved input" if generation.get("resumed_input") else "fresh generation"
            if generation.get("historical_input"):
                mode += "; historical source"
            if row.get("approximation"):
                mode += f"; requested {row.get('requested_boxes')} boxes"
            if valid_pass(row):
                outcome = "PASS"
            elif not completed(row):
                outcome = "INTERRUPTED" if row.get("interrupted") else "INCOMPLETE"
            else:
                outcome = failure_text(row)
            outcome = str(outcome).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {row['case']} | {generation.get('boxes', '—')} / {generation.get('cells', '—')} | {mode} | "
                         + " | ".join(timings) + f" | {outcome} |")
        done = [row for row in attempts if completed(row)]
        lines += ["", f"Complete additional passes: {sum(valid_pass(row) for row in done)}/{len(done)}. "
                  f"[Invocation, source pins and per-case solver configuration](smoke/{name}/)."]
    lines += ["", "## MVP C2 choice", "",
              "C2 is OFF by D79 because the linear qualification is stale. No requalification was run (D78). "
              "An explicit `--compare-c2` smoke option may diagnose ON, but is not required for the chosen MVP setting. "
              "The complete full checker remains the plan gate.", "",
              "Latency is measured per completed action across production decoder token advances (milliseconds); "
              "engine setup is separate. p95 uses the nearest-rank definition. "
              "Failed prefixes are labelled; an empty population has no median or p95.", "",
              "| Case | C2 | Engine setup s | Actions replayed | Median ms/action | p95 ms/action | Complete replay |",
              "|---|---|---:|---:|---:|---:|---|"]
    for label, row in [("campaign", row) for row in rows] + extra:
        for mode in ("off", "on"):
            stage = row.get("stages", {}).get("mask_c2_" + mode)
            if stage is None:
                continue
            def number(key):
                return f"{stage[key]:.3f}" if key in stage else "—"
            complete = "PASS" if stage.get("passed") else str(stage.get("error") or stage.get("status") or "FAIL")
            complete = complete.replace("|", "\\|").replace("\n", " ")
            identifier = row.get("case") or f"{row.get('rung', '?')}-{row.get('ship', '?')}-{row.get('replicate', '?')}"
            lines.append(f"| {identifier} ({label}) | {mode} | {number('engine_setup_s')} | "
                         f"{stage.get('actions_completed', 0)}/{stage.get('actions_expected', '—')} | "
                         f"{number('action_ms_median')} | {number('action_ms_p95')} | {complete} |")
    lines += ["", "## Fast suite and OFF-path audit", ""]
    triaged_suite = HERE / "v54b-suite-summary.json"
    suite = triaged_suite if triaged_suite.exists() else HERE / "fast-suite-summary.json"
    if suite.exists():
        data = json.loads(suite.read_text())
        compact = {key: data[key] for key in (
            "status", "command", "counts", "complete", "expected_selected", "completed_selected",
            "missing_count", "wall_s", "root_selection_collected_utc", "failure_classification_counts",
            "marker_expression", "xfail", "xpass", "historical_exclusion_count", "started_utc",
        ) if key in data}
        lines += [f"[Exact suite counts, command, timing and failure classification](smoke/{suite.name}).",
                  ("Current opt-in MVP lane, after triage. Skipped counts include the explicitly shown xfail subset. "
                   "[Untriaged 5.4a baseline and source-coverage limits](smoke/v54-fast-report.md)."
                   if suite == triaged_suite else
                   "[Commands, fixes and compute report](smoke/v54-fast-report.md). "
                   "Counts cover selected node IDs across checkpoints and a final collection delta, not one frozen source tree. "
                   "The runtime skipped count includes xfails in older checkpoint telemetry; collection skips are separate."),
                  "", "```json", json.dumps(compact, indent=2), "```"]
        if (HERE / "v54b-suite-report.md").exists():
            lines += ["", "[Post-triage commands, classification and compute report](smoke/v54b-suite-report.md)."]
        if data.get("failures"):
            lines += ["", "| Failing node | Classification | Reason |", "|---|---|---|"]
            for item in data["failures"]:
                values = [str(item.get(key, "—")).replace("|", "\\|").replace("\n", " ")
                          for key in ("nodeid", "classification", "reason")]
                lines.append("| " + " | ".join(values) + " |")
    else:
        lines.append("5.4a's default-marker suite is running in bounded local subprocess shards. "
                     "Fetching xdist failed because PyPI DNS is unavailable. Counts pending; the pre-stop run was interrupted.")
    exclusions_path = HERE / "v54-triage-exclusions.json"
    if exclusions_path.exists():
        exclusions = json.loads(exclusions_path.read_text())
        lines += ["", "## Explicit MVP suite exclusions", "",
                  "These unchanged assertions are excluded only by the opt-in MVP triage command: "
                  "42 archived pre-v4 evidence cases and nine checks requiring the linear requalification cut by D78. "
                  "Exclusion is not a passing result. [Manifest and reviewed source identities](smoke/v54-triage-exclusions.json).",
                  "", "| Node | Marker | Reason |", "|---|---|---|"]
        for item in exclusions["cases"]:
            fields = [str(item[key]).replace("|", "\\|").replace("\n", " ")
                      for key in ("nodeid", "marker", "reason")]
            lines.append("| " + " | ".join(fields) + " |")
        lines += ["", "The ownership source map is repaired; the exact architecture table update is supplied in "
                  "[proposed rows](smoke/v54-ownership-proposed-rows.md), outside this edit fence. "
                  "The two manual C2 loading conditions are real production recipe regressions after the HEEL redesign "
                  "([root cause](smoke/v54-manual-root-cause.md)); C2 here is the condition label. "
                  "The yard legacy-byte fixture now freezes its original input source text; both original hashes and "
                  "all output assertions remain unchanged ([proof](smoke/v54-workload-byte-root-cause.json))."]
    lines += ["", "Audit: `finalize` calls the resequencer only under `rule_freeze_v1_enabled()`; "
              "the resequencer itself is a no-op OFF. Full checker, ballast finisher and feasibility in-port screening "
              "branch around D64/D74 work OFF. No OFF gating source fix was indicated by the initial audit.",
              "", "## Assumptions, approximations and limitations", "",
              "- **APPROXIMATION:** C2 screening is explicitly disabled because its empirical linear qualification is stale. "
              "This removes an early completion screen; it does not relax checker acceptance limits. "
              "C2 is an early test for whether remaining boxes could fit the ship's linear stability/strength bounds "
              "([implementation and limitations](../../src/stowage/feasibility/engine.py)).",
              "- **APPROXIMATION:** bounded scalable construction disables large-neighbourhood objective improvement. "
              "Smoke success measures readiness, not optimality or population yield. Exact checker limits are unchanged.",
              "- **Assumption:** two deterministic current-generator instances per cell are diagnostics, not the frozen evaluation set. "
              "The baseline 18-case campaign retains its failed cases; retries and reduced-booking cases must be reported separately. "
              "Generator rejection summaries are saved rather than every rejected candidate payload.",
              "- Source hashes before/after each case are retained. Concurrent edits can invalidate a shared-epoch interpretation; "
              "the version snapshot is timestamped. [Versions and catalogue identity](versions.md); [51 active OFF contracts](rules.json).",
              "- Existing synthetic-physics, D57 three-voyage-state sampling, lashing and restow approximations are documented "
              "in the version snapshot and source; this task adds no physical-rule relaxation.",
              "", "## Compute", "",
              "Cache pilot feeder500: 35.843 s, one core. Full cache estimate: three cores / 4 GB / no GPU / ≤120 s; "
              "actual 72.697 s, exit 2. Peak RAM was not instrumented; `ps` and time's kernel-stat query are sandbox-denied. "
              "Native numerical threads were capped at one. Other jobs have their own ledger/history entries; "
              "final measured smoke/suite costs are in their artifacts. No cloud spend, GPU run or foreign-process signal.", ""]
    if (HERE / "v54b-suite-source-epoch-failure.json").exists():
        index = lines.index("## Compute")
        lines[index:index] = [
            "- Later constructor/ballast source edits occurred during the suite, after the pinned smoke epoch. "
            "A generator CLI input-identity guard rejected a changing shard; coincident edits and hashes are "
            "[recorded separately](smoke/v54b-suite-source-epoch-failure.json). The exact changed binding at failure "
            "was not emitted. The smoke proves its recorded source epoch; it does not certify an unchanged later tree.", ""]
    if warm_path.exists():
        lines += [f"5.4a C2-off warm-up estimate: one CPU / 4 GB / no GPU / ≤120 s; actual "
                  f"{warm['wall_seconds']:.3f} s, exit {warm['returncode']}, max child RSS "
                  f"{warm['max_child_rss_bytes'] / 1024**3:.3f} GiB. "
                  "Suite allocation reduced from three cores to one under fleet priority; "
                  "[per-phase estimates and actuals](smoke/v54-fast-report.md).", ""]
    campaign_summary = run_dir / "summary.json"
    if campaign_summary.exists():
        measured = json.loads(campaign_summary.read_text())
        if "measured_invocation_to_last_output_seconds" in measured:
            lines += [f"D83 smoke estimate: three CPU / 9 GB / no GPU / ≤15 min; measured invocation-to-last-output "
                      f"{measured['measured_invocation_to_last_output_seconds']:.3f} s, largest worker peak RSS "
                      f"{measured['largest_worker_peak_rss_bytes'] / 1024**3:.3f} GiB. All recorded workers exited. "
                      "[Measurements and process closure](smoke/v54b-d83/summary.json); "
                      "[independent persisted-artifact audit](smoke/v54b-d83/audit.json).", ""]
    (HERE.parent / "readiness.md").write_text("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, help="Settled 5.4b output directory; defaults to historical pre-stop evidence")
    render(parser.parse_args().run_dir)

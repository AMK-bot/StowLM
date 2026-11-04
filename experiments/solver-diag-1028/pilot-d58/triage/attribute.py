"""Conservative D58 pilot attribution; no solver or physics is executed.

Run with uv run --offline --no-sync python <this file>. Inputs are the live
pilot runs/<solver>/<index>/result.json checkpoints and generation outcomes.
Unknown ballast searches are never promoted to infeasibility or a missed
feasible ballast without an independent passing witness.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
PILOT = HERE.parent
STATIC_CARGO_PREFIXES = ("imdg.", "expert.e2.", "expert.e6.",
                         "expert.physical.slot_occupancy", "expert.physical.size_fit",
                         "expert.physical.stack_weight", "expert.physical.tier_height")
ADMISSION_TERMS = ("epoch is stale", "model unavailable", "missing_vessel_data",
                   "qualification", "global screen is unavailable", "c2 unavailable")


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def classify(row, generation=None, *, finished=True):
    """Return a hypothesis with evidence/limits rather than inventing proofs."""
    generation = generation or {}
    metrics = row.get("solver_metrics", {}) or {}
    text = " ".join(str(row.get(key) or "") for key in
                    ("error", "solver_message", "final_message"))
    text += " " + json.dumps(metrics, sort_keys=True)
    rules = row.get("rules", []) or []
    budget, cap = row.get("budget_s"), row.get("cap_s")
    # dispatch.py admits the requested solver budget plus a nominal 90 s
    # pipeline allowance, clipping that OUTER cap to the campaign deadline.
    # A cap can exceed the planner budget and still truncate finalization.
    nominal_cap = budget + 90 if isinstance(budget, (int, float)) else None
    cap_shortened = (cap < nominal_cap - 1e-6
                     if isinstance(cap, (int, float)) and nominal_cap is not None else None)
    campaign_cutoff = bool(row.get("campaign_deadline") or (row.get("cap_expired") and cap_shortened))
    category, owner, evidence, fix = "unresolved", "solver/ballast owners", "", ""
    if row.get("source_drift") or row.get("external_source_drift"):
        category, owner = "source_transition", "campaign owner"
        evidence = "Source bytes changed during this attempt; observed outcome is diagnostic only."
        fix = "Repeat on the frozen source epoch."
    elif not finished:
        category, owner = "in_progress", "campaign owner"
        evidence = "Checkpoint is not a completed attempt."
        fix = "Wait for the worker's completion marker."
    elif campaign_cutoff and row.get("solver_status") not in ("timeout", "dead_end", "infeasible"):
        category, owner = "campaign_deadline", "campaign owner"
        evidence = "The campaign shortened the worker allocation before this measurement completed."
        fix = "Repeat with the full requested planner/finisher/check/replay allocation; do not label this a requested-budget solver timeout."
    elif row.get("cap_expired") and row.get("solver_status") not in ("timeout", "dead_end", "infeasible"):
        category, owner = "harness_budget", "campaign owner"
        evidence = "The nominal whole-worker cap interrupted the remaining pipeline measurement."
        fix = "Repeat with sufficient pipeline/check/replay wall time; retain the original solver outcome separately."
    elif row.get("clean_complete_pass") or all(row.get(k) for k in
                    ("checker_passed", "shippable", "mask_replay_passed")):
        category, owner = "pass", "none"
        evidence = "Full checker, shipping gate and final-action replay passed."
    elif any(term in text.lower() for term in ADMISSION_TERMS) or metrics.get("model_unavailable"):
        category, owner = "instance_or_dependency_admission", "generation/linear/feasibility owners"
        evidence = "The current production engine refused an input/dependency before a valid completion."
        fix = "Inspect required_data_violations and qualification diagnostics; regenerate or requalify current inputs."
    elif row.get("checker_passed") and row.get("mask_replay_passed") is False:
        category, owner = "mask_replay", "feasibility/action representation owners"
        evidence = "The exact checker passed this plan, but its final action sequence failed replay."
        fix = "Replay the recorded failing action/prefix; distinguish action encoding from mask rejection."
    elif row.get("checker_passed") and not row.get("shippable"):
        category, owner = "solver_status_or_provenance", "solver/planner protocol owners"
        evidence = "Checker passed; the shipping gate rejected solver status or provenance."
        fix = "Inspect final_status, timeout/checkpoint status, and final provenance binding."
    elif row.get("completion") != "complete" and row.get("solver_status") in ("timeout", "dead_end"):
        category, owner = "solver_search_yield", "solver owner"
        evidence = "The solver returned only a partial candidate; subsequent checker errors do not supply the missing completion."
        fix = "Improve construction/search using phase/depth/dead-end telemetry; preserve secondary checker errors separately."
    elif "checker.internal_error" in rules:
        category, owner = "checker_unavailable", "checker owner"
        evidence = "At least one checker part did not run or errored; physical infeasibility is unproved."
        fix = "Reproduce the recorded part error on this exact candidate."
    elif metrics.get("optimality_status") == "INFEASIBLE":
        category, owner = "solver_domain_no_solution", "solver/feasibility owners"
        evidence = "Exact search exhausted the configured engine-model domain; this is not a full physical-domain infeasibility proof."
        fix = "Inspect terminal checker rule counts and engine admission diagnostics; preserve the search-domain qualification."
    elif row.get("completion") != "complete":
        category, owner = "solver_search_yield", "solver owner"
        evidence = "No complete candidate was returned within the recorded search/node budget."
        fix = "Use phase/depth/dead-end telemetry to improve construction/search; no instance infeasibility follows."
    elif row.get("part_errors"):
        category, owner = "checker_unavailable", "checker owner"
        evidence = "A complete candidate has at least one checker part that did not run or errored."
        fix = "Reproduce the recorded part error on this exact candidate; physical infeasibility is unproved."
    elif row.get("ballast_miss_demonstrated"):
        category, owner = "ballast", "ballast owner"
        evidence = "An independent full-checker passing ballast witness exists for this identical fixed cargo stow."
        fix = "Add the verified witness and a regression of the missed search branch."
    elif rules and row.get("ballast_found"):
        category, owner = "solver_choice", "solver owner"
        evidence = "A complete candidate with finished/declared ballast failed the full checker."
        fix = "Screen the recorded cargo-side rejection during candidate search, or correct D57 finisher acceptance if physics failed."
    elif rules and row.get("ballast_status") in ("not_reached", None):
        category, owner = "solver_choice", "solver owner"
        evidence = "A complete cargo candidate was rejected before successful ballast finishing."
        fix = "Inspect the first hard rules and solver status; retain checker authority."
    elif row.get("ballast_status") in ("not_found", "infeasible_proven"):
        # Do not infer ballast independence from an unfamiliar rule family:
        # port air-draught, visibility and lashing can all change with tanks.
        static = [rule for rule in rules if rule.startswith(STATIC_CARGO_PREFIXES)]
        if static:
            category, owner = "solver_choice", "solver owner"
            evidence = "Complete cargo has ballast-independent hard-rule failures: " + ", ".join(static)
            fix = "Reject those cargo placements before choosing the incumbent; retain all hard rules."
        else:
            category, owner = "fixed_stow_ballast_unresolved", "solver and ballast owners"
            evidence = "Finisher did not certify this fixed cargo stow; no feasible ballast witness is recorded."
            fix = "Find an independently passing ballast for this identical stow before claiming a ballast miss; otherwise try different cargo placements."
    else:
        evidence = "The recorded outcome does not yet separate solver choice, ballast search and checker availability."
        fix = "Retain raw candidate/report and collect a same-stow passing witness or checker reproduction."
    return dict(category=category, owner=owner, evidence=evidence, proposed_action=fix,
                nominal_cap_s=nominal_cap, campaign_cap_shortened=cap_shortened,
                campaign_cutoff=campaign_cutoff,
                secondary_checker_unavailable=bool(row.get("part_errors") or "checker.internal_error" in rules),
                ballast_miss_demonstrated=bool(row.get("ballast_miss_demonstrated")),
                global_infeasibility_proved=False,
                accepted_background_witness=bool(generation.get("audit", {}).get("background_last", {}).get("accepted")))


def collect():
    outcomes = {path.stem: load(path) for path in (PILOT / "generation/outcomes").glob("*.json")}
    rows = []
    for run in sorted((PILOT / "runs").glob("*/*")):
        if not run.is_dir():
            continue
        row, done = load(run / "result.json"), load(run / "done.json")
        index = run.name.zfill(3)
        generation = outcomes.get(index, {})
        spec = generation.get("spec", done.get("requested_spec", {}))
        if not row:
            # Scheduling/CLI failures are not solver attempts or solver failure evidence.
            if done:
                rows.append(dict(index=index, solver=run.parent.name,
                    rung=spec.get("size"), ship=spec.get("vessel"), generator=spec.get("generator"),
                    category="harness_no_checkpoint", owner="campaign owner", counts_as_solver_attempt=False,
                    evidence=done.get("error", "Process exited without a solver checkpoint."),
                    source=str(run.relative_to(PILOT)), returncode=done.get("returncode")))
            continue
        item = dict(index=index, solver=row.get("solver", run.parent.name), rung=spec.get("size"),
            ship=spec.get("vessel", row.get("vessel")), generator=spec.get("generator"),
            counts_as_solver_attempt=True, finished=bool(done), source=str(run.relative_to(PILOT)),
            completion=row.get("completion"), ballast_status=row.get("ballast_status"),
            checker_passed=row.get("checker_passed"), shippable=row.get("shippable"),
            mask_replay_passed=row.get("mask_replay_passed"), solve_s=row.get("solve_s"),
            wall_s=row.get("wall_s"), rules=row.get("rules", []), states=row.get("states", {}),
            part_errors=row.get("part_errors", {}), mask_replay=row.get("mask_replay", {}),
            solver_message=row.get("solver_message"), final_message=row.get("final_message"),
            source_drift=row.get("source_drift", []))
        for name in ("budget_s", "cap_s", "ballast_budget_s", "cap_expired",
                     "interrupted_stage", "stage", "solver_status", "final_status"):
            if name in row:
                item[name] = row[name]
        metrics = row.get("solver_metrics", {}) or {}
        for name in ("terminal_failure_rule_counts", "terminal_failure_state_rule_counts",
                     "terminal_static_failure_rule_counts", "terminal_last_part_errors",
                     "securing_geometry_rejections", "preplan_status", "preplan_message",
                     "deepest_depth", "partial_loaded", "oracle_dead_end", "phase",
                     "termination", "optimality_status", "budget_overrun_s"):
            if name in metrics:
                item[name] = metrics[name]
        item.update(classify(row, generation, finished=bool(done)))
        rows.append(item)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        assert classify({"completion": "incomplete", "rules": ["iscode.b2_3"]})["category"] == "solver_search_yield"
        assert classify({"completion": "complete", "ballast_status": "not_found", "rules": ["iscode.b2_3"]})["category"] == "fixed_stow_ballast_unresolved"
        assert classify({"completion": "complete", "ballast_status": "not_found", "rules": ["imdg.stowage.3_2"]})["category"] == "solver_choice"
        assert classify({"checker_passed": True, "shippable": True, "mask_replay_passed": False})["category"] == "mask_replay"
        assert classify({"checker_passed": True, "shippable": False})["category"] == "solver_status_or_provenance"
        assert classify({"source_drift": ["a"], "clean_complete_pass": True})["category"] == "source_transition"
        assert classify({"completion": "complete", "ballast_miss_demonstrated": True})["category"] == "ballast"
        assert classify({"completion": "incomplete", "solver_message": "epoch is stale"})["category"] == "instance_or_dependency_admission"
        partial = classify({"completion": "incomplete", "solver_status": "timeout",
                            "rules": ["checker.internal_error"],
                            "part_errors": {"stability": "incomplete-plan consequence"}})
        assert partial["category"] == "solver_search_yield" and partial["secondary_checker_unavailable"]
        cutoff = classify({"completion": "incomplete", "budget_s": 300, "cap_s": 10,
                           "cap_expired": True, "interrupted_stage": "planning"})
        assert cutoff["category"] == "campaign_deadline" and cutoff["campaign_cutoff"]
        full_timeout = classify({"completion": "incomplete", "budget_s": 300, "cap_s": 350,
                           "cap_expired": True, "solver_status": "timeout", "solve_s": 300})
        assert full_timeout["category"] == "solver_search_yield" and full_timeout["campaign_cutoff"]
        finishing = classify({"completion": "complete", "budget_s": 300, "cap_s": 340,
                              "cap_expired": True, "solver_status": "ok", "interrupted_stage": "finishing"})
        assert (finishing["category"] == "campaign_deadline" and finishing["campaign_cap_shortened"]
                and finishing["nominal_cap_s"] == 390)
        nominal = classify({"completion": "complete", "budget_s": 300, "cap_s": 390,
                           "cap_expired": True, "solver_status": "ok", "interrupted_stage": "finishing"})
        assert nominal["category"] == "harness_budget" and not nominal["campaign_cap_shortened"]
        admitted = classify({"completion": "incomplete", "solver_status": "timeout", "budget_s": 300,
                             "cap_s": 300.424, "cap_expired": False})
        assert (admitted["category"] == "solver_search_yield" and admitted["campaign_cap_shortened"]
                and not admitted["campaign_cutoff"])
        print("14 attribution self-checks passed")
        return
    rows = collect()
    (HERE / "attribution.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    finished = [row for row in rows if row.get("counts_as_solver_attempt") and row.get("finished")]
    groups = defaultdict(list)
    for row in finished:
        groups[row["solver"], row["rung"], row["ship"]].append(row)
    summary = dict(recorded=len(rows), completed_solver_attempts=len(finished),
                   categories=dict(Counter(row["category"] for row in rows)), groups=[])
    generation_rows = []
    for path in sorted((PILOT / "generation/outcomes").glob("*.json")):
        outcome = load(path)
        spec = outcome.get("spec", {})
        error = outcome.get("error", "")
        if outcome.get("status") == "generated":
            category, owner = "generated", "none"
        elif "no legal footprint for sampled cargo" in error and outcome.get("index") in (40, 41, 43, 50, 52):
            category, owner = "generation_frozen_yard_capacity", "generation owner"
        elif "no legal footprint for sampled cargo" in error:
            category, owner = "generation_yard_footprint_rejection", "generation owner"
        elif "outer cap expired" in error and "background_acceptance.py" in outcome.get("traceback", ""):
            category, owner = "generation_background_gate_timeout", "checker/hydrostatics/generation performance owners"
        else:
            category, owner = "generation_unresolved", "generation owner"
        generation_rows.append(dict(index=outcome.get("index"), spec=spec,
            category=category, owner=owner, status=outcome.get("status"), error=error,
            elapsed_s=outcome.get("elapsed_s"), source_changed=outcome.get("source_changed"),
            counts_as_solver_attempt=False, physical_infeasibility_proved=False))
    (HERE / "generation-attribution.jsonl").write_text("".join(
        json.dumps(row, sort_keys=True) + "\n" for row in generation_rows))
    summary["generation_categories"] = dict(Counter(row["category"] for row in generation_rows))
    for key, group in sorted(groups.items()):
        times = [row["solve_s"] for row in group if isinstance(row.get("solve_s"), (int, float))]
        summary["groups"].append(dict(zip(("solver", "rung", "ship"), key), n=len(group),
            categories=dict(Counter(row["category"] for row in group)),
            median_s=statistics.median(times) if times else None, max_s=max(times) if times else None))
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "groups"}, sort_keys=True))


if __name__ == "__main__":
    main()

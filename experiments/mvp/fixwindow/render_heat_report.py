"""Render saved full-checker heat comparisons; never run numerical code.

Prepared as text while the campaign runs. Execute only after parent GO.
All outputs stay under fixwindow; reference cohorts and diagnostics stay distinct.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import statistics
import sys

sys.dont_write_bytecode = True
FENCE = Path(__file__).resolve().parent
ROOT = FENCE.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def object_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def save(path, data):
    path = Path(path).resolve()
    if not path.is_relative_to(FENCE):
        raise ValueError(f"Output escapes fixwindow: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)


def csv_text(rows, fields):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
                         for k, v in row.items()})
    return out.getvalue()


def cell(value):
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers, rows):
    return ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|",
            *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)]


def verdict(phase):
    if phase is None:
        return "missing"
    if phase.get("error") or not phase.get("completed") or not isinstance(phase.get("report"), dict):
        return "unavailable"
    return "PASS" if phase["report"].get("passed") is True else "FAIL"


def first_hard(phase):
    return next((v["rule_id"] for v in (phase or {}).get("report", {}).get("violations", [])
                 if v.get("severity") == "hard"), None)


def diff_reports(baseline, patched):
    if verdict(baseline) not in {"PASS", "FAIL"} or verdict(patched) not in {"PASS", "FAIL"}:
        return dict(direction="unavailable", removed=[], added=[])
    left = baseline["report"].get("violations", [])
    right = patched["report"].get("violations", [])
    before = Counter(json.dumps(v, sort_keys=True) for v in left)
    after = Counter(json.dumps(v, sort_keys=True) for v in right)
    removed = [json.loads(s) for s, n in (before-after).items() for _ in range(n)]
    added = [json.loads(s) for s, n in (after-before).items() for _ in range(n)]
    vb, vp = verdict(baseline), verdict(patched)
    direction = ("fail_to_pass" if (vb, vp) == ("FAIL", "PASS") else
                 "pass_to_fail" if (vb, vp) == ("PASS", "FAIL") else
                 "changed_same_verdict" if removed or added else "unchanged")
    return dict(direction=direction, removed=removed, added=added)


def is_e8(violation):
    return violation["rule_id"].startswith("expert.e8") or "expert.e8" in violation.get("message", "")


def render(args):
    inventory_path, output = args.inventory.resolve(), args.output.resolve()
    runtime_root = args.runtime_root.resolve()
    if not output.is_relative_to(FENCE):
        raise ValueError("Measurement/output directory must be within fixwindow")
    inventory = read(inventory_path)
    config_path = output / "config.json"
    config = read(config_path)
    config_sha = object_sha(config)
    findings = []
    def check(condition, label, key=None):
        if not condition:
            findings.append(dict(check=label, key=key))
    check(config["inventory_sha256"] == sha(inventory_path), "inventory configuration hash")
    check(config["native_threads"] == 1 and config["gpu"] is False and config["rule_freeze_v1"] is False,
          "single-core GPU-off rule-freeze-off configuration")
    driver_name = "heat_measure_snapshot.py" if config.get("runtime_root") else "heat_measure.py"
    check(config["driver_sha256"] == sha(FENCE / driver_name), "current measurement driver digest")
    check(runtime_root == Path(config.get("runtime_root", str(ROOT))).resolve(), "explicit checker runtime root")
    candidate_path = FENCE / "heat/src/stowage/checker/rules_special.py"
    check(candidate_path.is_file() and sha(candidate_path) == config["candidate_sha256"],
          "scratch candidate source digest")
    check(sha(runtime_root / "src/stowage/checker/rules_special.py") == config["baseline_special_sha256"],
          "runtime baseline heat source digest")
    current_source_mismatches = [name for name, digest in config["source_pins"].items()
        if not (runtime_root / name).is_file() or sha(runtime_root / name) != digest]
    check(not current_source_mismatches, "runtime source bytes remain pinned")
    live_source_mismatches = [name for name, digest in config["source_pins"].items()
        if not (ROOT / name).is_file() or sha(ROOT / name) != digest]
    live_checker_dependency_changes = [name for name in live_source_mismatches
        if not name.startswith(("src/stowage/solver/", "src/stowage/generate/"))]
    # Drift after a frozen measurement is disclosed, not silently rewritten into
    # the historical source-valid flags recorded at each checker completion.
    plan_rows, phases, changed_details = [], [], []
    cohort_stats = defaultdict(Counter)
    per_input = defaultdict(list)
    removed_rules, added_rules, non_e8_rules = Counter(), Counter(), Counter()
    denial_max_by_pid, peak_rss = {}, 0
    artifacts = {str(inventory_path): sha(inventory_path), str(config_path): sha(config_path)}
    artifacts[str(candidate_path)] = sha(candidate_path)
    expected_plan_keys = {p["key"] for p in inventory["plans"]}
    found_plan_keys = {p.parent.name for p in (output / "cases").glob("*/comparison.json")}
    check(found_plan_keys <= expected_plan_keys, "no unexpected comparisons")
    for item in inventory["plans"]:
        key, cohort = item["key"], item["cohort"]
        directory = output / "cases" / key
        stored_path = directory / "comparison.json"
        stored = read(stored_path) if stored_path.exists() else None
        baseline_path, patched_path = directory / "baseline.json", directory / "patched.json"
        baseline = read(baseline_path) if baseline_path.exists() else None
        patched = read(patched_path) if patched_path.exists() else None
        d = diff_reports(baseline, patched)
        check(stored is not None, "comparison checkpoint present", key)
        if stored:
            check(stored["config_sha256"] == config_sha, "comparison config identity", key)
            check(stored["comparison"]["direction"] == d["direction"], "independently recomputed direction", key)
            check(stored["input_sha256"] == item["input"]["sha256"], "comparison input identity", key)
            artifacts[str(stored_path)] = sha(stored_path)
        check(sha(item["input"]["path"]) == item["input"]["sha256"], "frozen input digest", key)
        check(sha(item["plan_path"]) == item["plan_sha256"], "private frozen plan digest", key)
        for phase_name, phase_path, phase in (("baseline", baseline_path, baseline), ("patched", patched_path, patched)):
            check(phase is not None, phase_name + " checkpoint present", key)
            if phase:
                check(phase.get("completed") is True, phase_name + " completion", key)
                check(phase.get("config_sha256") == config_sha, phase_name + " config identity", key)
                check(phase.get("source_pins_valid") is True, phase_name + " source pins at completion", key)
                if config.get("runtime_root") and verdict(phase) in {"PASS", "FAIL"}:
                    check(phase.get("all_stowage_imports_from_snapshot") is True,
                          phase_name + " snapshot-only production imports", key)
                check(phase.get("input_sha256") == item["input"]["sha256"] and phase.get("plan_sha256") == item["plan_sha256"],
                      phase_name + " frozen input/plan identity", key)
                artifacts[str(phase_path)] = sha(phase_path)
                if verdict(phase) in {"PASS", "FAIL"}:
                    check(phase["report"].get("provenance", {}).get("created_by") == "checker.check",
                          phase_name + " full checker provenance", key)
                rss = phase.get("process_peak_rss_bytes", 0)
                peak_rss = max(peak_rss, rss)
                pid = phase.get("pid")
                denial_max_by_pid[pid] = max(denial_max_by_pid.get(pid, 0), len(phase.get("blocked_outside_writes", [])))
            else:
                rss, pid = None, None
            part_rows = (phase or {}).get("report", {}).get("parts", {})
            phases.append(dict(key=key, cohort=cohort, phase=phase_name, verdict=verdict(phase),
                wall_s=(phase or {}).get("wall_s"), process_lifetime_peak_rss_bytes=rss, pid=pid,
                completed=(phase or {}).get("completed", False), source_pins_valid=(phase or {}).get("source_pins_valid"),
                phase_error=(phase or {}).get("error"), first_hard_rule=first_hard(phase),
                full_report_present=isinstance((phase or {}).get("report"), dict),
                checker_parts=len(part_rows), checker_parts_not_ran=sum(p.get("ran") is False for p in part_rows.values()),
                checker_part_errors=[dict(part=name, error=p["error"]) for name, p in part_rows.items() if p.get("error")],
                cumulative_blocked_write_attempts=len((phase or {}).get("blocked_outside_writes", []))))
        removed, added = d["removed"], d["added"]
        removed_rules.update(v["rule_id"] for v in removed)
        added_rules.update(v["rule_id"] for v in added)
        non_e8_rules.update(v["rule_id"] for v in removed+added if not is_e8(v))
        before_hard = [v for v in (baseline or {}).get("report", {}).get("violations", []) if v.get("severity") == "hard"]
        after_hard = [v for v in (patched or {}).get("report", {}).get("violations", []) if v.get("severity") == "hard"]
        row = dict(key=key, cohort=cohort, label=item["label"], role=item["role"],
            certified_reference_before_measurement=item["certified_full_plan"],
            instance_id=item["input"]["instance_id"], input_path=item["input"]["relative_path"],
            input_sha256=item["input"]["sha256"], plan_sha256=item["plan_sha256"],
            baseline=verdict(baseline), patched=verdict(patched), direction=d["direction"],
            baseline_first_hard=first_hard(baseline), patched_first_hard=first_hard(patched),
            baseline_hard_count=len(before_hard), patched_hard_count=len(after_hard),
            first_removed_rule=removed[0]["rule_id"] if removed else None,
            first_added_rule=added[0]["rule_id"] if added else None,
            removed_rules=dict(Counter(v["rule_id"] for v in removed)),
            added_rules=dict(Counter(v["rule_id"] for v in added)),
            removed_e8_count=sum(is_e8(v) for v in removed), added_e8_count=sum(is_e8(v) for v in added),
            changed_non_e8_rules=dict(Counter(v["rule_id"] for v in removed+added if not is_e8(v))),
            baseline_s=(baseline or {}).get("wall_s"), patched_s=(patched or {}).get("wall_s"),
            baseline_error=(baseline or {}).get("error"), patched_error=(patched or {}).get("error"))
        plan_rows.append(row)
        per_input[row["input_sha256"]].append(row)
        stats = cohort_stats[cohort]
        stats["expected"] += 1
        stats["baseline_" + row["baseline"]] += 1
        stats["patched_" + row["patched"]] += 1
        stats[d["direction"]] += 1
        if d["direction"] not in {"unchanged", "unavailable"}:
            changed_details.append(dict(key=key, cohort=cohort, input_sha256=row["input_sha256"],
                direction=d["direction"], removed_violations=removed, added_violations=added))
    input_rows = []
    for item in inventory["coverage"]:
        instance = item["input"]
        matched = per_input[instance["sha256"]]
        completed = [r for r in matched if r["baseline"] in {"PASS", "FAIL"} and r["patched"] in {"PASS", "FAIL"}]
        exact = [r for r in matched if r["cohort"] == "tiny_exact"]
        input_rows.append(dict(instance_id=instance["instance_id"], path=instance["relative_path"],
            sha256=instance["sha256"], rung=instance["rung"], ship=instance["ship"], family=instance["family"],
            expected_reference_plans=item["measurable_reference_plans"], measured_pairs=len(completed),
            official_reference_plans=item["measurable_official_plans"], official_blocker=item["official_blocker"],
            baseline_pass=sum(r["baseline"] == "PASS" for r in completed),
            patched_pass=sum(r["patched"] == "PASS" for r in completed),
            changed_reference_ids=[r["key"] for r in matched if r["direction"] not in {"unchanged", "unavailable"}],
            exact_reference_results=[dict(key=r["key"], baseline=r["baseline"], patched=r["patched"], direction=r["direction"]) for r in exact],
            unavailable_reference_ids=[r["key"] for r in matched if r["direction"] == "unavailable"]))
    pair_complete = sum(r["baseline"] in {"PASS", "FAIL"} and r["patched"] in {"PASS", "FAIL"} for r in plan_rows)
    stage_counts = Counter(p["verdict"] for p in phases)
    phase_error_count = sum(bool(p["phase_error"]) for p in phases)
    blocked_writes = sum(denial_max_by_pid.values())
    all_phase_seconds = sum(p["wall_s"] for p in phases if p["wall_s"] is not None)
    summary = dict(rendered_utc=datetime.now(timezone.utc).isoformat(),
        complete=pair_complete==len(inventory["plans"]) and not findings,
        expected_pairs=len(inventory["plans"]), persisted_full_checker_pairs=pair_complete,
        expected_phases=2*len(inventory["plans"]), full_checker_reports=sum(p["full_report_present"] for p in phases),
        phase_verdicts=dict(stage_counts), phase_errors=phase_error_count,
        recorded_check_seconds=all_phase_seconds, process_lifetime_peak_rss_bytes=peak_rss,
        blocked_outside_write_attempts=blocked_writes,
        cumulative_denial_max_by_pid={str(k): v for k,v in denial_max_by_pid.items()},
        source_valid_phases=sum(p["source_pins_valid"] is True for p in phases),
        runtime_root=str(runtime_root), current_source_mismatches=current_source_mismatches,
        live_source_mismatches=live_source_mismatches,
        live_checker_dependency_changes=live_checker_dependency_changes, findings=findings,
        cohort_counts={k: dict(v) for k,v in cohort_stats.items()},
        added_rule_counts=dict(added_rules), removed_rule_counts=dict(removed_rules),
        changed_non_e8_rule_counts=dict(non_e8_rules), verdict_changes=[r for r in plan_rows if r["direction"] in {"fail_to_pass", "pass_to_fail"}],
        all_changed_reference_ids=[r["key"] for r in plan_rows if r["direction"] not in {"unchanged", "unavailable"}],
        per_input=input_rows, changed_details=changed_details,
        unavailable_official_inputs=[r["path"] for r in input_rows if r["official_blocker"]],
        artifact_sha256=artifacts, config_sha256=config_sha,
        assumptions=["The saved 80-plan snapshot is authoritative; newer reference attempts are not silently added.",
            "The interrupted live-source campaign is historical diagnostic evidence and is not pooled with this fresh frozen-runtime campaign.",
            "Ablations and tail attempts remain separately labeled and are not independent input counts.",
            "Both phases call the same full checker; only the proposed E8 function differs. No solver, ballast finisher, or mask replay was rerun for this measurement.",
            "Checker phase order is baseline then patched, so warm caches can affect timings; phase timing differences are not causal speedup measurements.",
            "Peak RSS is the process lifetime high-water mark, not a per-check allocation; blocked-write logs are cumulative per process and counted by maximum per PID.",
            "Experimental patched reports and the incomplete showcase prefix are not release certification."])
    save(output / "heat-report.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    save(output / "heat-reference-comparisons.csv", csv_text(plan_rows, list(plan_rows[0]) if plan_rows else ["key"]))
    save(output / "heat-phase-costs.csv", csv_text(phases, list(phases[0]) if phases else ["key"]))
    save(output / "heat-input-coverage.csv", csv_text(input_rows, list(input_rows[0]) if input_rows else ["instance_id"]))
    lines = ["# Heat-ID full-checker comparison on frozen references", "",
        f"Rendered {summary['rendered_utc']}. Saved-evidence renderer only; no numerical work was run by this renderer.", "",
        f"**{pair_complete}/{len(inventory['plans'])} paired full-checker comparisons**, "
        f"{summary['full_checker_reports']}/{summary['expected_phases']} persisted full-checker reports. "
        f"Measurement errors: {phase_error_count}. Audit findings: {len(findings)}.", "",
        "The frozen population is 15 tiny + 15 small TEST v2.1 inputs, with every available approved reference cohort kept separate. "
        "The extra feeder-800 showcase is a 17/18 prefix without finalized ballast and remains diagnostic. "
        "No plan is accepted from an E8-only check: the paired verdicts come from the full checker. "
        "No generator, solver, ballast finisher or mask replay was rerun.", "",
        "## Cohorts", ""]
    lines += table(["Cohort", "Pairs", "Baseline P/F/U", "Patched P/F/U", "F→P", "P→F", "Changed, same verdict", "Unavailable"],
        [[k, s["expected"], f"{s['baseline_PASS']}/{s['baseline_FAIL']}/{s['baseline_unavailable']+s['baseline_missing']}",
          f"{s['patched_PASS']}/{s['patched_FAIL']}/{s['patched_unavailable']+s['patched_missing']}",
          s["fail_to_pass"], s["pass_to_fail"], s["changed_same_verdict"], s["unavailable"]]
         for k,s in cohort_stats.items()])
    lines += ["", "Counts above are plan/attempt counts, not independent input counts. Tail overlaps and no-screen ablations are retained as separate cohorts.",
        "", "## Every verdict change", ""]
    verdict_changes = summary["verdict_changes"]
    lines += table(["Reference ID", "Frozen input", "Cohort", "Baseline → patched", "First removed rule", "First added rule"],
        [[r["key"], r["input_path"], r["cohort"], r["baseline"]+" → "+r["patched"], r["first_removed_rule"], r["first_added_rule"]]
         for r in verdict_changes]) if verdict_changes else ["No full-checker PASS/FAIL verdict changes were measured."]
    lines += ["", "Every reference, including same-verdict violation changes and errors, is listed in "
        "[heat-reference-comparisons.csv](heat-reference-comparisons.csv). Full added/removed violation payloads are retained in "
        "[heat-report.json](heat-report.json).", "", "## Rule changes across all paired plans", ""]
    rules = sorted(removed_rules.keys() | added_rules.keys())
    lines += table(["Rule", "Removed violations", "Added violations", "Non-E8 changed occurrences"],
        [[r,removed_rules[r],added_rules[r],non_e8_rules[r]] for r in rules]) if rules else ["No violation payloads changed."]
    lines += ["", "Occurrences aggregate all reference attempts, including overlapping cohorts. "
        "`checker.missing_vessel_data` is treated as E8-related only when its message explicitly names `expert.e8`.",
        "", "## All 30 frozen test inputs", ""]
    lines += table(["Input", "Rung / ship", "Pairs measured / expected", "Baseline P / patched P", "Changed reference IDs", "Exact reference results", "Official blocker"],
        [[r["path"],r["rung"]+" / "+r["ship"],f"{r['measured_pairs']}/{r['expected_reference_plans']}",
          f"{r['baseline_pass']} / {r['patched_pass']}", ", ".join(r["changed_reference_ids"]) or "none",
          "; ".join(x["key"]+": "+x["baseline"]+"→"+x["patched"]+" ("+x["direction"]+")" for x in r["exact_reference_results"]) or "not an exact cohort",
          r["official_blocker"]] for r in input_rows])
    lines += ["", "**Official reference remains unavailable for exactly this frozen inventory slot:** "
        + ", ".join(summary["unavailable_official_inputs"]) + ". Its separately labeled ablation is not a replacement. "
        "[Complete per-input coverage](heat-input-coverage.csv).", "", "## Frozen feeder-800 showcase prefix", ""]
    showcase = [r for r in plan_rows if r["cohort"] == "showcase_prefix"]
    lines += table(["Reference", "Baseline", "Patched", "Direction", "First removed rule", "First added rule", "Baseline / patched hard violations"],
        [[r["key"],r["baseline"],r["patched"],r["direction"],r["first_removed_rule"],r["first_added_rule"],
          f"{r['baseline_hard_count']} / {r['patched_hard_count']}"] for r in showcase])
    lines += ["", "This is the exact attempt-3 prefix pinned by the saved heat explanation. A data-binding fix can remove a false heat error "
        "while incomplete loading, missing ballast, or other hard failures still reject the plan. No complete showcase solve or mask acceptance is claimed.",
        "", "## Cost, source and write-fence evidence", ""]
    cost_rows = []
    for cohort in cohort_stats:
        grouped = [p for p in phases if p["cohort"] == cohort]
        times = {phase:[p["wall_s"] for p in grouped if p["phase"]==phase and p["wall_s"] is not None] for phase in ("baseline","patched")}
        rss = [p["process_lifetime_peak_rss_bytes"] for p in grouped if p["process_lifetime_peak_rss_bytes"] is not None]
        cost_rows.append([cohort, f"{sum(times['baseline']):.3f}",f"{sum(times['patched']):.3f}",
            f"{statistics.median(times['baseline']):.3f}" if times['baseline'] else None,
            f"{statistics.median(times['patched']):.3f}" if times['patched'] else None,
            max(rss) if rss else None])
    lines += table(["Cohort", "Baseline total s", "Patched total s", "Baseline median s", "Patched median s", "Lifetime peak RSS bytes"],cost_rows)
    lines += ["", f"Recorded phase effort: **{all_phase_seconds:.3f} s**. Largest lifetime peak RSS: **{peak_rss:,} bytes**. "
        f"Completed-phase source pins valid: **{summary['source_valid_phases']}/{len(phases)}**. "
        f"Blocked outside-write attempts: **{blocked_writes}**; out-of-fence writes are guarded. "
        f"Frozen/runtime source mismatches at report time: **{len(current_source_mismatches)}**. "
        f"Live-tree differences from that runtime: **{len(live_source_mismatches)}**, including "
        f"**{len(live_checker_dependency_changes)}** outside solver/generator code.",
        "", "Both phases run sequentially on one native thread, no GPU. Baseline is measured first; cache warming can make the second call faster, "
        "so these timings do not isolate a heat-fix speed effect. Peak RSS is a process lifetime high-water mark. "
        "[All 160 expected phase records, costs, checker-part errors and source flags](heat-phase-costs.csv).", "",
        "The renderer independently recalculates verdict directions, input/plan hashes, and phase configuration identities. "
        "Report `parts` with `ran=false` or an error are retained in the phase CSV; they are not silently presented as passed parts.", ""]
    lines += ["", "The checker runtime is `" + str(runtime_root) + "`. Source validity is verified against this runtime; "
        "live-tree differences are disclosed separately. The earlier interrupted campaign is not pooled into these counts.", ""]
    if findings or current_source_mismatches or live_source_mismatches:
        lines += ["## Audit disclosures", "", "```json", json.dumps(dict(findings=findings,
            runtime_source_mismatches=current_source_mismatches,
            live_source_mismatches=live_source_mismatches,
            live_checker_dependency_changes=live_checker_dependency_changes),indent=2), "```", ""]
    save(output / "heat-report.md", "\n".join(lines)+"\n")
    print(json.dumps({k:summary[k] for k in ("complete","expected_pairs","persisted_full_checker_pairs",
        "full_checker_reports","phase_errors","source_valid_phases","blocked_outside_write_attempts")},indent=2))
    return 0 if summary["complete"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=FENCE/"heat_inventory/inventory.json")
    parser.add_argument("--output", type=Path, default=FENCE/"heat_measure_results")
    parser.add_argument("--runtime-root", type=Path, default=ROOT,
        help="Source root used for this checker campaign; default live root, pass heat_runtime for private snapshot")
    return render(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())

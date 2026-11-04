"""Report CPU-only validation polish from saved inventory and checked per-map results.

Usage: python report_polish_val.py [--polish-dir PATH] [--out PATH] [--summary PATH]
No solver, model, or GPU imports. Incomplete runs produce an explicitly partial report
and exit 2; missing passing plans never silently become polished incumbents.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import json
from math import comb
from pathlib import Path
import statistics as st

HERE = Path(__file__).resolve().parent
SETS = ("audit30", "reserve25")
LABELS = {"8.2": "StowLM 8.2", "8.3": "StowLM 8.3", "twostep": "two-step v4.4"}


def sign_test(fewer: int, more: int) -> dict:
    """Exact, two-sided binomial sign test; ties do not enter the trial count."""
    n = fewer + more
    p = min(Fraction(1), Fraction(2 * sum(comb(n, k) for k in range(min(fewer, more) + 1)), 2**n)) if n else Fraction(1)
    return {"n_discordant": n, "p": float(p), "p_exact": str(p)}


def mean(values):
    values = list(values)
    return st.mean(values) if values else None


def timing(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0, "mean": None, "median": None, "p95": None, "max": None, "total": None}
    # Nearest-rank percentile; it is an observed per-map cost, not an interpolated one.
    index = (95 * len(values) + 99) // 100 - 1
    return {"n": len(values), "mean": st.mean(values), "median": st.median(values),
            "p95": values[index], "max": values[-1], "total": sum(values)}


def key(job):
    return job["arm"], job["set"], job["instance_id"]


def completed(row):
    return (row.get("status") == "completed" and row.get("own_report_passed") is True
            and row.get("final_report_passed") is True and row.get("assignments_preserved") is True
            and row.get("ballast_preserved") is True and row.get("before_moves") is not None
            and row.get("after_moves") is not None and row["after_moves"] <= row["before_moves"])


def compare(left, right, expected):
    mutual = sorted(set(left) & set(right))
    fewer = sum(left[i] < right[i] for i in mutual)
    more = sum(left[i] > right[i] for i in mutual)
    return {"expected_mutual": len(expected), "mutual": len(mutual), "complete": set(mutual) == set(expected),
            "mean_left": mean(left[i] for i in mutual), "mean_right": mean(right[i] for i in mutual),
            "fewer": fewer, "more": more, "equal": len(mutual) - fewer - more,
            "sign_test": sign_test(fewer, more), "instance_ids": mutual,
            "excluded_mutual_ids": sorted(set(expected) - set(mutual))}


def build(polish_dir: Path) -> dict:
    inventory = json.loads((polish_dir / "inventory.json").read_text())
    jobs = inventory["jobs"]
    problems = []
    if len({key(j) for j in jobs}) != len(jobs):
        raise ValueError("duplicate arm/set/map in polish inventory")
    groups = {(g["arm"], g["set"]): g for g in inventory["groups"]}
    rows = {}
    issues = []
    for job in jobs:
        k = key(job)
        path = polish_dir / k[0] / k[1] / f"{k[2]}.json"
        if job.get("status") == "missing_plan":
            issues.append({**job, "issue": "missing stored passing plan"})
            continue
        if not path.exists():
            issues.append({**job, "issue": "pending polish result"})
            continue
        row = json.loads(path.read_text())
        if not job.get("fingerprint") or row.get("fingerprint") != job["fingerprint"]:
            issues.append({**job, "issue": "saved result fingerprint differs from inventory"})
            continue
        if any(row.get(field) != expected for field, expected in zip(("arm", "set", "instance_id"), k)):
            issues.append({**job, "issue": "saved result identity differs from inventory"})
            continue
        if not completed(row):
            issues.append({**job, "issue": f"unusable polish result: {row.get('status')}",
                           "error": row.get("error")})
            continue
        if job.get("before_moves") is not None and job["before_moves"] != row["before_moves"]:
            issues.append({**job, "issue": "checked baseline objective differs from source row"})
            continue
        rows[k] = row
    arms = sorted({j["arm"] for j in jobs} | {g[0] for g in groups}, key=lambda a: (a != "8.2", a != "twostep", a))
    problems += [f"{arm}: missing source arm" for arm in ("8.2", "twostep") if arm not in arms]
    for arm in arms:
        for name in SETS:
            g = groups.get((arm, name))
            if g is None:
                problems.append(f"{arm}/{name}: missing source group")
            elif g["n_rows"] != g["expected_maps"]:
                problems.append(f"{arm}/{name}: source has {g['n_rows']}/{g['expected_maps']} maps")
            if g is not None:
                n_jobs = sum(j["arm"] == arm and j["set"] == name for j in jobs)
                if n_jobs != g["n_passed"]:
                    problems.append(f"{arm}/{name}: inventory has {n_jobs}/{g['n_passed']} passing jobs")
    summaries = []
    for arm in arms:
        for name in (*SETS, "combined"):
            selected_jobs = [j for j in jobs if j["arm"] == arm and (name == "combined" or j["set"] == name)]
            selected = [rows[key(j)] for j in selected_jobs if key(j) in rows]
            source_groups = [g for (a, s), g in groups.items() if a == arm and (name == "combined" or s == name)]
            missing = sum(j.get("status") == "missing_plan" for j in selected_jobs)
            summary = {"arm": arm, "set": name, "source_maps": sum(g["n_rows"] for g in source_groups),
                       "expected_maps": sum(g["expected_maps"] for g in source_groups),
                       "passes": sum(g["n_passed"] for g in source_groups), "completed": len(selected),
                       "missing_plans": missing, "mean_before_own_passes": mean(j.get("before_moves") for j in selected_jobs if j.get("before_moves") is not None),
                       "mean_before_completed": mean(r["before_moves"] for r in selected),
                       "mean_after_completed": mean(r["after_moves"] for r in selected),
                       "improved": sum(r["after_moves"] < r["before_moves"] for r in selected) if selected else None,
                       "polish_s": timing(r.get("polish_s") for r in selected),
                       "requested_polish_s": sorted({j["requested_polish_s"] for j in selected_jobs}),
                       "check_s_total": timing(r.get("check_s_total") for r in selected),
                       "own_check_s": timing(r.get("own_check_s") for r in selected),
                       "candidate_check_s": timing(r.get("candidate_check_s") for r in selected),
                       "qualification_check_s": timing(r.get("qualification_check_s") for r in selected),
                       "acceptance_check_s": timing(r.get("acceptance_check_s") for r in selected),
                       "total_s": timing(r.get("total_s") for r in selected),
                       "historical_checker_s": timing(j.get("historical_checker_s") for j in selected_jobs)}
            summary["complete"] = (summary["completed"] == summary["passes"] and summary["source_maps"] == summary["expected_maps"]
                                   and len(source_groups) == (2 if name == "combined" else 1))
            summaries.append(summary)
    comparisons = []
    specs = [("8.2", "twostep", True), ("8.2", "twostep", False)]
    if "8.3" in arms:
        specs += [("8.3", "twostep", True), ("8.3", "twostep", False), ("8.3", "8.2", True)]
    for left_arm, right_arm, right_polished in specs:
        for name in (*SETS, "combined"):
            selected_jobs = [j for j in jobs if name == "combined" or j["set"] == name]
            left_jobs = {(j["set"], j["instance_id"]): j for j in selected_jobs if j["arm"] == left_arm}
            right_jobs = {(j["set"], j["instance_id"]): j for j in selected_jobs if j["arm"] == right_arm}
            expected = set(left_jobs) & set(right_jobs)
            left = {i: rows[key(j)]["after_moves"] for i, j in left_jobs.items() if key(j) in rows}
            right = ({i: rows[key(j)]["after_moves"] for i, j in right_jobs.items() if key(j) in rows} if right_polished else
                     {i: j["before_moves"] for i, j in right_jobs.items() if j.get("before_moves") is not None})
            comparison = compare(left, right, expected)
            comparisons.append({"set": name, "left_arm": left_arm, "right_arm": right_arm, "right_polished": right_polished, **comparison})
    return {"label": "Validation only: 15 s CPU solver polish (hybrid)", "schema_version": 1,
            "polish_run": bool(rows),
            "complete": not issues and not problems and all(s["complete"] for s in summaries),
            "inventory": str(polish_dir / "inventory.json"), "arms": arms, "summaries": summaries,
            "sources": inventory["groups"],
            "comparisons": comparisons, "issues": issues, "source_problems": problems,
            "missing_plans": [i for i in issues if i["issue"] == "missing stored passing plan"]}


def fmt(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


def render(report):
    lines = ["# Validation: StowLM + 15 s solver polish (hybrid)", "",
             "**COMPLETE**" if report["complete"] else ("**INCOMPLETE — polish NOT RUN: no usable stored passing plans/results; after-polish moves, improvements and actual polish times are unavailable.**" if not report["polish_run"] else "**INCOMPLETE — partial results; unavailable plans/results are excluded from after-polish statistics.**"), "",
             "Validation maps only: audit30 + reserve25 (55 small maps). StowLM and two-step v4.4 are configured for the same 15 s requested CPU polish.",
             "The solver preserves box-to-slot assignments and ballast; candidates require mask replay, the securing screen and the full checker.",
             "The passing incumbent is retained when a candidate fails admission or does not improve. These are separately labelled hybrid rows.", "",
             "Baseline means use all original own passes; completed-subset before/after means use identical maps. A partial after mean is not an all-pass estimate.", "",
             "| Arm | Set | Original passes / source maps | Polished / passes | Own-pass before | Completed before → after | Improved / completed | Missing plans |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for s in report["summaries"]:
        improved = f"{s['improved']} / {s['completed']}" if s["completed"] else "— (not run)"
        lines.append(f"| {LABELS.get(s['arm'], s['arm'])} | {s['set']} | {s['passes']} / {s['source_maps']} | {s['completed']} / {s['passes']} | {fmt(s['mean_before_own_passes'])} | {fmt(s['mean_before_completed'])} → {fmt(s['mean_after_completed'])} | {improved} | {s['missing_plans']} |")
    lines += ["", "## Paired weighted moves", "",
              "Each comparison uses the intersection of the two arms' original passing maps. Missing polish results are explicitly excluded; completed/expected mutual counts expose partial comparisons.",
              "Fewer/more/equal refer to the left arm. Exact two-sided binomial sign tests drop ties; no multiple-comparison adjustment.", "",
              "| Pair (left vs right) | Set | Mutual completed / expected | Mean left vs right | Fewer / more / equal | Exact sign p |",
              "|---|---|---:|---:|---:|---:|"]
    for c in report["comparisons"]:
        pair = f"{c['left_arm']} + polish vs {LABELS.get(c['right_arm'], c['right_arm'])}" + (" + polish" if c["right_polished"] else " (unpolished)")
        p = f"{c['sign_test']['p']:.8g}" if c["mutual"] else "—"
        counts = f"{c['fewer']} / {c['more']} / {c['equal']}" if c["mutual"] else "—"
        lines.append(f"| {pair} | {c['set']} | {c['mutual']} / {c['expected_mutual']} | {fmt(c['mean_left'])} vs {fmt(c['mean_right'])} | {counts} | {p} |")
    lines += ["", "## Measured time", ""]
    if report["issues"] and all(i["issue"] == "missing stored passing plan" for i in report["issues"]):
        lines += ["No solver calls were made for the missing plans: 0 s spent on polish. No after-polish measurements exist.", ""]
    lines += [
              "Seconds are wall-clock measurements per map. `polish_s` measures the actual solver call, including its internal admission checks; 15 s is a requested search budget, not a hard end-to-end cutoff.",
              "Checker time below sums measured full-checker calls (baseline, qualification, candidate admission and final recheck); it overlaps the solver time for internal calls and must not be added to solver time.",
              "Totals sum per-map CPU-worker wall times and are not the parallel run's elapsed time. Check costs depend on this shared machine's load.", "",
              "| Arm | Set | Requested s | Actual polish mean / max s | Actual polish total s | Checker mean / p95 / max s | Checker total s | End-to-end mean s |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for s in report["summaries"]:
        p, c = s["polish_s"], s["check_s_total"]
        lines.append(f"| {LABELS.get(s['arm'], s['arm'])} | {s['set']} | {', '.join(f'{v:g}' for v in s['requested_polish_s']) or '—'} | {fmt(p['mean'])} / {fmt(p['max'])} | {fmt(p['total'])} | {fmt(c['mean'])} / {fmt(c['p95'])} / {fmt(c['max'])} | {fmt(c['total'])} | {fmt(s['total_s']['mean'])} |")
    lines += ["", "When a result exists, its per-map checker measurements and retained checked result are in `polish/{arm}/{set}/{instance_id}.json`; timing components and exact rational sign-test p values are also in `polish_summary.json`.", "",
              "### Original validation checker cost (historical)", "",
              "These are original validation full-checker times on passing maps, **not polish timings**. They show the cost of one checker call; a fresh polish run can require baseline qualification, candidate checking and a final recheck. Unavailable times are omitted.", "",
              "The 8.2 audit source is the parent 90 s audit: its passing maps and objectives match the canonical 120 s audit, but these historical timings belong to the parent source listed below.", "",
              "| Arm | Set | Measured maps | Checker mean / median / p95 / max s |", "|---|---|---:|---:|"]
    for s in report["summaries"]:
        c = s["historical_checker_s"]
        lines.append(f"| {LABELS.get(s['arm'], s['arm'])} | {s['set']} | {c['n']} | {fmt(c['mean'])} / {fmt(c['median'])} / {fmt(c['p95'])} / {fmt(c['max'])} |")
    lines += ["",
              "## Coverage and missing plans", ""]
    if report["source_problems"]:
        lines += [f"- {p}" for p in report["source_problems"]] + [""]
    counts = Counter(i["issue"] for i in report["issues"])
    if not counts:
        lines += ["Every original passing map has a stored plan and a completed checked polish result.", ""]
    else:
        lines += [", ".join(f"{n} {issue}" for issue, n in sorted(counts.items())) + ".", "",
                  "The source evaluators serialized outcomes and timings without the full finalized plans. Regeneration would be a new run and is not substituted for the missing historical incumbents.", "",
                  "| Arm | Set | Map | Issue | Original checker s |", "|---|---|---|---|---:|"]
        for issue in report["issues"]:
            detail = issue["issue"] + (f": {issue['error']}" if issue.get("error") else "")
            lines.append(f"| {issue['arm']} | {issue['set']} | `{issue['instance_id']}` | {detail.replace('|', '/').replace(chr(10), ' ')} | {fmt(issue.get('historical_checker_s'))} |")
        lines.append("")
    lines += ["## Reproduce", "", "```sh", ".venv/bin/python experiments/mvp/sft/final-validation/polish_val.py",
              ".venv/bin/python experiments/mvp/sft/final-validation/report_polish_val.py", "```", "",
              "Sources and plan provenance are recorded in `polish/inventory.json`. 8.3 appears here only when its validation rows have been inventoried; this report does not wait for training.", "",
              "| Arm | Set | Source rows (including historical checker timing source) |", "|---|---|---|"]
    for source in report["sources"]:
        paths = source.get("source_rows", [])
        if isinstance(paths, str):
            paths = [paths]
        def display(path):
            try:
                return str(Path(path).relative_to(HERE.parents[3]))
            except ValueError:
                return str(path)
        if len(paths) > 1 and len({str(Path(p).parent) for p in paths}) == 1:
            shown = f"`{display(Path(paths[0]).parent)}/*.json` ({len(paths)} files; exact list in inventory)"
        else:
            shown = ", ".join(f"`{display(p)}`" for p in paths) or "unspecified"
        lines.append(f"| {source['arm']} | {source['set']} | {shown} |")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--polish-dir", type=Path, default=HERE / "polish")
    parser.add_argument("--out", type=Path, default=HERE / "POLISH.md")
    parser.add_argument("--summary", type=Path, default=HERE / "polish_summary.json")
    args = parser.parse_args()
    report = build(args.polish_dir)
    args.summary.write_text(json.dumps(report, indent=2) + "\n")
    args.out.write_text(render(report))
    print(json.dumps({"path": str(args.out), "summary": str(args.summary), "complete": report["complete"],
                      "completed": sum(s["completed"] for s in report["summaries"] if s["set"] == "combined"),
                      "missing_plans": len(report["missing_plans"])}))
    raise SystemExit(0 if report["complete"] else 2)


if __name__ == "__main__":
    main()

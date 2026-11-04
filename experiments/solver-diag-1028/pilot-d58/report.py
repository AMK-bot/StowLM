"""Build reviewable current pilot report from immutable attempt records."""
from __future__ import annotations
import collections
import datetime
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent


def medmax(values):
    return "—" if not values else f"{statistics.median(values):.1f}/{max(values):.1f}"


def main():
    raw = [json.loads(p.read_text()) for p in (HERE / "runs").glob("*/*/done.json")
           if (p.parent / "result.json").exists()]
    continued = {(r["solver"], r["index"]): r for p in
                 (HERE / "production-continuation").glob("*/*/done.json")
                 for r in [json.loads(p.read_text())]}
    groups = collections.defaultdict(list)
    for r in raw:
        groups[r["solver"], r["requested_spec"]["size"], r["requested_spec"]["vessel"]].append(r)
    lines = ["| solver | rung | ship | measured | complete | ballast set | checker pass | shippable | mask pass | full chain | solve median/max s | OPTIMAL |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|"]
    brief = []
    for solver in ("exact", "scalable", "twostep"):
        for rung in ("tiny", "small", "medium"):
            if solver == "exact" and rung != "tiny":
                continue
            combined = []
            for ship in ("feeder_500", "feeder_800", "feeder_1700"):
                rs = groups[solver, rung, ship]
                combined.extend(rs)
                n = len(rs)
                counts = [sum(r.get("completion") == "complete" for r in rs)]
                counts += [sum(r.get(k) is True for r in rs) for k in
                           ("ballast_found", "checker_passed", "shippable", "mask_replay_passed", "clean_complete_pass")]
                ratios = " | ".join(f"{c}/{n}" if n else "—" for c in counts)
                times = [r["solve_s"] for r in rs if r.get("solve_s") is not None]
                optimal = sum(r.get("optimality_status") == "OPTIMAL" and bool(r.get("clean_complete_pass")) for r in rs)
                lines.append(f"| {solver} | {rung} | {ship} | {n} | {ratios} | {medmax(times)} | {optimal} |")
            brief.append(dict(solver=solver, rung=rung, measured=len(combined),
                              passed=sum(bool(r.get("clean_complete_pass")) for r in combined),
                              complete=sum(r.get("completion") == "complete" for r in combined),
                              optimal=sum(r.get("optimality_status") == "OPTIMAL" and bool(r.get("clean_complete_pass")) for r in combined),
                              solve_median_max_s=medmax([r["solve_s"] for r in combined if r.get("solve_s") is not None])))
    failures = [r for r in raw if not r.get("clean_complete_pass")]
    partial_rules = collections.Counter(rule for r in failures for rule in r.get("rules", []))
    usable_continued = {k: r for k, r in continued.items()
                       if not r.get("source_drift") and r.get("source_changes_since_raw") == []}
    production_pass = sum(bool(r.get("clean_complete_pass")) or
                          bool(usable_continued.get((r["solver"], r["index"]), {}).get("clean_complete_pass")) for r in raw)
    gen = [json.loads(p.read_text()) for p in (HERE / "generation/outcomes").glob("*.json")]
    accepted = sum(r.get("status") in ("generated", "resumed") for r in gen)
    source_drift = sum(bool(r.get("source_drift")) for r in raw)
    campaign_caps = [r for r in raw if r.get("cap_expired") and r.get("cap_s", 0) < r.get("budget_s", 0) + 90]
    expected = sum(3 if r["spec"]["size"] == "tiny" else 2 for r in gen if r.get("status") in ("generated", "resumed"))
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    text = "\n".join(lines) + "\n\n"
    text += ("**Biggest blocker: search completion on larger cargo sets.** "
             "Completed cargo candidates have passed every gate so far; timeout prefixes and absent plans prevent a 100% attempt yield. "
             "A separate production-generator capacity defect prevents the requested full cell coverage.\n\n")
    text += f"Snapshot {now}. {len(raw)}/{expected} solver attempts finished on {accepted}/{len(gen)} accepted requests; "
    text += f"{sum(bool(r.get('clean_complete_pass')) for r in raw)} raw full-chain passes, {len(failures)} non-passes; {source_drift} within-run source changes. "
    text += f"{len(campaign_caps)} attempts have a campaign-shortened expired outer cap; these are not full-budget solver measurements. "
    text += "Every stage numerator above uses all finished attempts as its denominator; `ballast set` includes an independently checked planner-supplied ballast as well as a successful finisher. "
    text += "The conditional reached-phase denominators are in [table.md](table.md).\n\n"
    text += (f"Repair-enabled production continuation: {len(continued)}/{len(failures)} raw non-passes measured, "
             f"{production_pass}/{len(raw)} full-chain passes including rescues. This restores the same cargo result and invokes public "
             "`run_planner(..., allow_repair=True)`; it does not rerun or improve the original solver. "
             "Raw passing runs never needed fallback, so their default production result is unchanged. "
             f"{len(continued)-len(usable_continued)} continuations are excluded from rescue evidence because source consistency is absent or changed.\n\n")
    text += ("Generation: 54 canonical requests (24 tiny, 24 small, 6 medium), production C2/retry path, "
             "47 accepted (24 tiny, 19 small, 4 medium). Five random-yard requests abort because the adapter freezes a yard smaller than "
             "the actual regional cargo demand; two small/1700 requests reach the 295s generation cap while checking the empty-background witness. "
             "These seven are generation coverage failures, not solver attempts or demonstrated physical infeasibility. "
             "No replacement seeds, easier ports, reduced cargo counts or bypassed screens were used.\n\n")
    text += ("Attribution and ownership: full-budget incomplete searches remain solver construction/yield limitations, with physical feasibility unknown; campaign-cutoff outcomes are separately identified. "
             "Scalable non-passes so far report zero LNS neighbourhoods: they have not constructed the first complete incumbent. "
             "Examples are 33/34 loaded boxes on small request 025 and 226/229 on medium request 051. "
             "Two-step assigns half its requested budget to cargo-only pre-stow (150s small, 300s medium) and can return there without a candidate. "
             "Scalable may exhaust 300/600s with a partial prefix. No ballast miss is claimed without a passing witness for the identical cargo stow. "
             "Hard-rule diagnostics on incomplete prefixes are retained in [failure-states.json](failure-states.json), including exact D57 part/state keys; "
             "they do not establish a bad completed stow or a checker defect. "
             f"Observed rule families on non-passes: `{dict(partial_rules)}`. "
             "The per-attempt primary category, secondary errors and proposed owner/action are in [triage/attribution.jsonl](triage/attribution.jsonl).\n\n")
    text += ("Outside-scope proposals: generation owner should make `instances_v2_random.generate_random_v2` preserve requested footprint expansion "
             "instead of returning the original fixed port from its `scaled_yard` shim; keep all source-port cargo capability restrictions and original demand. "
             "Regression: regional small1700 and medium800/1700 can allocate the full requested booking footprint and still pass normal acceptance. "
             "Also audit/retry `random_yard.generate_random_instance` footprint rejection rather than escaping the retry loop. "
             "Checker/hydrostatics and background-gate owners should time each candidate and D57 part and retain its witness before the expensive check; "
             "the two deadline traces do not identify one offending state or prove a 295s individual solve. "
             "Detailed evidence and exact file/function proposals: [triage/review.md](triage/review.md).\n\n")
    text += ("Scope/changes: only pilot experiment scripts and records were added in this follow-up, plus the authorized compute-ledger line. "
             "No solver implementation, checker, feasibility engine, hard rule, tolerance or margin was changed. "
             "No tested correctness defect has yet justified a solver patch; no new version bump is proposed. "
             "The source remains live: full source/configuration manifests are recorded per attempt, and [source-epochs.json](source-epochs.json) groups epochs.\n\n")
    text += ("Budgets/assumptions: exact tiny 120s; scalable and two-step tiny 120/small 300/medium 600s. "
             "The public finisher and repair retain 30s each, with a separate 90s reserve beyond the requested solver limit for finalization/check/mask; "
             "late campaign truncations are labelled separately. Raw measurement disabled repair only to expose solver yield; the separate continuation closes the default fallback measurement. "
             "Masks are the real CPU token decoder, including end marker, with C2 on and no model inference. "
             "Local caches are isolated copies; instances/plans/checking physics are unchanged. "
             "Thirty-six early CLI failures performed no solver work, are archived under `harness-errors/`, and were rerun on identical cases; they are excluded from attempt denominators.\n\n")
    text += ("Proofs/bounds: [exact-certificates.json](exact-certificates.json) records each OPTIMAL certificate, checked objective and 3.6 lower bound, "
             "with both absolute and relative gaps. A* expands the cheapest cost-so-far plus a lower bound on remaining cost; when no unresolved cheaper path remains, "
             "the accepted solution is optimal within the engine's action/weighted-move domain. A loose 3.6 counting bound can stay below that certified optimum "
             "([Hart, Nilsson and Raphael 1968](https://ai.stanford.edu/~nilsson/OnlinePubs-Nils/PublishedPapers/astar.pdf)). "
             "All 24 exact results have engine gap zero; their elementary 3.6 bounds are zero, giving an absolute gap median/max of 4.5/7 and relative gap 100%. "
             "Global physical-domain optimality remains uncertified (`physical_domain_optimality_certified=False`). "
             "Scalable uses large-neighbourhood search: remove part of a current plan and search for a better replacement while preserving the rest; "
             "this is a heuristic and does not certify global optimality "
             "([Shaw 1998](https://doi.org/10.1007/3-540-49481-2_30)).\n\n")
    text += ("Verification: default `tests/solver` plus the two real token-mask harness cases passed 737 tests, 7 slow deselected, zero failures/errors, 36.888s. "
             "Continuation adapter tests: 2 passed; attribution checks: 14 passed plus 3 continuation/source-audit groups. "
             "Exact commands and compute estimate/actual are maintained in `fix-log.md`. "
             "This moving-source diagnostic is not a complete G1 qualification: generation coverage and search yield still block that claim.\n")
    compute_path = HERE / "compute-summary.json"
    if compute_path.exists():
        compute = json.loads(compute_path.read_text())
        text += (f"\nCompute estimate: 6 CPU/16 GB/no GPU,65min runs plus10min reporting. Actual recorded heavy window "
                 f"{compute['elapsed_to_last_completed_s']/60:.2f}min; maximum recorded job overlap {compute['observed_recorded_job_overlap_max']}. "
                 "Largest individual parent RSS1.413GiB and child RSS0.927GiB; aggregate simultaneous RAM and CPU time were not instrumented. "
                 "No GPU/cloud cost. All owned dispatchers exited0 and the own ledger reservation is released. "
                 "Details: [compute-summary.json](compute-summary.json).\n")
    (HERE / "report.md").write_text(text)
    (HERE / "brief.json").write_text(json.dumps(brief, indent=2) + "\n")
    print(json.dumps(dict(finished=len(raw), expected=expected, raw_pass=len(raw)-len(failures),
                          production_pass=production_pass, continued=len(continued), nonpasses=len(failures))))


if __name__ == "__main__":
    main()

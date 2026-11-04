"""Write the scoped leader report only after every required check succeeds."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

RUN = Path(__file__).resolve().parent
ROOT = RUN.parents[3]
REPORT = ROOT/"experiments/review/fixes/w2/w2-linear-requal.md"


def read(relative):
    return json.loads((RUN/relative).read_text())


def main():
    assert read("finish-execution.json")["passed"] is True
    promotion = read("promotion/report.json")
    assert promotion["passed"] and promotion["applied"]
    bank_sha = hashlib.sha256((ROOT/"src/stowage/linear/margin_policy_data.json").read_bytes()).hexdigest()
    assert bank_sha == promotion["promoted_bank_sha256"]
    guard = read("closure/latest-check.json")
    assert guard["passed"] and not guard["strict_source_changes"] and not guard["profile_changes"]
    results, compute = read("results.json"), read("compute-summary.json")
    assert sum(row["ordinary_holdouts"] for row in results.values()) == 576
    assert sum(row["edge_holdouts"] for row in results.values()) == 197
    comparison = read("numerical-margin-comparison.json")
    assert all(values[direction+"/default_operational_reserves"]["bit_identical"]
        for values in comparison.values() for direction in ("necessary","conservative"))
    xml = ET.parse(RUN/"linear-feasibility-tests.xml").getroot()
    suites = list(xml.iter("testsuite"))
    totals = {key:sum(int(suite.get(key,"0")) for suite in suites)
        for key in ("tests","failures","errors","skipped")}
    assert totals["failures"] == totals["errors"] == 0
    targets = ("test_claude_edge_rows", "test_d49_holdout_inclusion", "test_d49_integration",
        "test_performance_equivalence")
    target_cases = [case for case in xml.iter("testcase")
        if any(name in case.get("classname","") for name in targets)]
    assert len(target_cases) == 44
    assert all(case.find("skipped") is None and case.find("failure") is None
        and case.find("error") is None for case in target_cases)
    passed = totals["tests"]-totals["skipped"]
    prefix = "../../../linear-model/epoch_requalification/production-1028b/"
    lines = ["## Outcome and verification", "",
        f"All nine cases qualified; bank atomically promoted as `{bank_sha}`. Necessary and conservative operational reserves, including LIST moments, are bit-identical to the old qualified bank. No reserve extension beyond the existing D49 enrichment protocol was needed.", "",
        "576 ordinary + 197 independent fresh edge holdouts pass with zero necessary-reserve breaches. LIST also passes 64 independent boundary holdouts per side per ship. Both directional inclusion audits pass; ordinary replay gives 487 modeled-exact feasible / 576 necessary accepted / 133 conservative accepted, with zero inclusion violations. These fractional/point-load states do not certify legal cargo plans.", "",
        "| Command (all use `uv run`, Python3.12, native threads1) | Outcome |",
        "|---|---|",
        "| `python experiments/linear-model/epoch_requalification/driver.py --ship SHIP --output RUN/ordinary --phase raw`; same original physical populations and targeted/boundary/edge commands retained in execution metadata |46 ordinary workers +18 final edge commands exit 0 |",
        "| `python experiments/linear-model/epoch_requalification/promote.py --input RUN/ordinary --output RUN/promotion --include-necessary --necessary-edge-input RUN/edges-v3 --necessary-calibration-enrichment RUN/edges-v1 RUN/edges-v2 --apply` |exit 0; applied=true |",
        f"| `python -m stowage.cache_warm` |exit 0; complete=true;54.114676792s; zero warnings/errors |",
        "| `python experiments/linear-model/epoch_requalification/public_validate.py --root RUN/ordinary --output RUN/public-validation.json --latency-variants 10` |exit 0;9/9 cases pass |",
        "| `python RUN/alias_validate.py`; `python RUN/production_smoke.py` |exit 0;800/1000 identical necessary rows; generator C2=true, global_screen=on |",
        f"| `pytest tests/linear tests/feasibility -q -o junit_family=legacy --junitxml=RUN/linear-feasibility-tests.xml` |exit 0;{passed} passed, {totals['skipped']} skipped; all 44 prior-stale regression cases pass without skips |",
        "| `python RUN/closure/guard.py --check`; scoped `git diff --check` |exit 0; no source/profile/version drift; whitespace clean |", "",
        "`RUN` is the fresh `production-1028b` directory; complete exact argv/PIDs/exit/RSS records are in its `finish-logs/`, `ordinary-logs/`, `edge-logs/`, and `verification-logs/`.", "",
        "Final cache-warm stdout summary:", "", "```json",
        json.dumps(dict(complete=True, elapsed_seconds=54.114676791999955, report=str(ROOT/'experiments/cache/stability/warmup/summary.json'))), "```", "",
        f"Full stdout is retained in [cache-warm.log]({prefix}finish-logs/cache-warm.log). All 15 calibration hooks reused their qualified 192-state calibrations;12 coefficient layouts built and 3 reused. Both middle-ship names resolve to the promoted bank.", "",
        "## Per-ship qualification", "",
        "| Ship | Ordinary holdouts | Fresh edge holdouts | Qualification wall s |",
        "|---|---:|---:|---:|"]
    labels = {"feeder_500":"feeder_500", "feeder_1000":"feeder_800 (alias feeder_1000)","feeder_1700":"feeder_1700"}
    for ship,row in results.items():
        lines.append(f"| {labels[ship]} |{row['ordinary_holdouts']}|{row['edge_holdouts']}|{row['qualification_wall_s']:.3f}|")
    lines += ["", "Wall spans first fresh raw measurement through last edge completion; ships overlap. Maximum observed errors below include reference transfer and combine ordinary/edge populations. Each maximum is paired with its own side/station reserve; every other station/direction also passes.", "",
        "| Quantity |500 error / reserve |800 error / reserve |1700 error / reserve |",
        "|---|---:|---:|---:|"]
    quantities = [("GM m","gm_m",1),("Trim m","trim_m",1),("Mean draught m","draught_m",1),
        ("Aft draught m","draught_aft_m",1),("Forward draught m","draught_forward_m",1),
        ("SF MN","sf_n",1e6),("BM MNm","bm_nm",1e6),("LIST million kg m","list_boundary_kgm",1e6)]
    for label,metric,scale in quantities:
        cells=[]
        for row in results.values():
            value=max((value for key,value in row["metrics"].items() if key.endswith("/"+metric)),key=lambda value:value["max_error"])
            reserve=value.get("reserve_at_max_error",value.get("reserve"))
            cells.append(f"{value['max_error']/scale:.6g} / {reserve/scale:.6g}")
        lines.append("|"+"|".join([label,*cells])+"|")
    lines += ["", f"Full per-direction/station maxima, excesses and LIST angle diagnostics: [results.md]({prefix}results.md), [results.json]({prefix}results.json). Operational LIST maxima include both boundary and interior holdout gates.", "",
        "All 18 regenerated edge populations retain exactly the old attempts, requests, IDs, admissions and centroid exclusions;64/67/66 independent edge holdouts are unchanged. All-observed residual gate passes5804 slots /28543 comparisons. The previous5805/29836 totals counted the identical reviewer record in both v2 and v3; the fresh gate counts it once. No physical population was removed. See `evidence-population-comparison.json`.", "",
        "## Files and compute", "",
        "Changed: `src/stowage/linear/margin_policy_data.json` only via atomic promoter; three evidence migrations in `tests/linear/{test_claude_edge_rows,test_claude_qualification,test_d49_holdout_inclusion}.py`; fresh `production-1028b/**`; retired initial `production-1028/` pilot preserved; this fix log; own fleet ledger/history lines. No feasibility test edit or numerical source/model/rule/version edit. Historical old-reserve reconstruction now correctly uses archived raw errors, retaining the exact 0.6775873854429051m assertion and old-reject/current-admit checks.", "",
        "| Job | Estimate | Actual wall |",
        "|---|---|---:|"]
    for key,label,estimate in (("campaign","All-ship campaign","30min initial /35min revised"),
        ("ordinary_raw_and_resume","Ordinary raw+resume","20min/6CPU/16GB"),
        ("diagnostics","Review/diagnostics","90s/1CPU/2GB"),
        ("lattice_prefetch","V3 prefix prefetch","4min/3CPU/6GB"),
        ("replay_prefetch","Replay prefix prefetch","3min/3CPU/6GB"),
        ("final_checks","Final gates/warm/replay/tests","10min initial /15min /25min revised"),
        ("cache-warm","Cache warm","3min/3CPU/4GB"),
        ("linear-feasibility-tests","Default suite","8min/1CPU/4GB")):
        job=compute["jobs"][key];seconds=job.get("actual_s",job.get("actual_execution_s",job.get("elapsed_s")))
        lines.append(f"|{label}|{estimate}|{seconds:.3f}s|")
    lines += ["", f"Peak reservation9CPU/24GB, later7CPU/16GB then2CPU/6GB; no GPU/cloud. Highest measured child RSS {compute['peak_observed_child_rss_bytes']/1024**3:.3f}GiB; aggregate peak RAM was not instrumented. Subjob walls overlap. V3 prefixes were used;144 later replay-prefetch states missed the publication window and remain archived unused; canonical records were never overwritten. [Exact compute metadata]({prefix}compute-summary.json). Reservation released after completion; own fleet history rows appended.", "",
        "APPROXIMATION (existing): finite empirical local-affine qualification is not a uniform domain proof. No new approximation, relaxed rule or margin reduction. The full exact checker remains mandatory for every departure plan. Assumption verified: frozen closure stayed unchanged; fleet/generator module exclusions are explicit above, while actual numerical parameter/domain changes still invalidate the appropriate keys.", "",
        "Open issues: none for this order. No commit, staging, push, leader-only doc edit, paid call or foreign process signal.", "",
        "Proposed leader evaluation/status text: D49 production linear policy requalified post-D3/M1 on all three ships;576+ 197 fresh independent holdouts, zero necessary reserve breaches; identical operational reserves; atomic promotion, cache_warm and public/C2/alias/default-suite checks pass. Exact checker remains sole plan certifier."]
    original=REPORT.read_text().split("## Verification and compute")[0]
    REPORT.write_text(original+"\n"+"\n".join(lines)+"\n")
    (RUN/"verification-summary.json").write_text(json.dumps(dict(passed=True,tests=totals,
        prior_stale_cases=len(target_cases),prior_stale_cases_all_passed=True,bank_sha256=bank_sha),indent=2)+"\n")
    print(json.dumps(dict(report=str(REPORT),tests=totals,prior_stale_cases=len(target_cases))))


if __name__ == "__main__":
    main()

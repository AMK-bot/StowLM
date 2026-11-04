"""Render the TRAIN scale report from frozen baseline and saved run artifacts.

No solver/checker imports, map reads, retries, repair, or baseline mutation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STAGES = ("f60", "f70", "f80", "D185")


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def new_outcome(folder, job, run):
    row = read(folder / "summary.json", {})
    timing = row.get("timings", {})
    metrics = row.get("metrics", {})
    seconds = dict(planner=timing.get("planner_seconds"),
                   carrier=metrics.get("preplan_time_s"),
                   terminal=metrics.get("terminal_time_s"),
                   finisher=timing.get("finisher_seconds"),
                   checker=timing.get("checker_seconds"))
    result = dict(resolved=False, passed=False, moves=None, seconds=seconds,
                  stage=row.get("stage", "pending"), error=row.get("error"))
    if not row.get("complete"):
        return result
    result.update(resolved=True, stage="carrier")
    if row.get("solver_status") == "ok":
        result["stage"] = "finisher" if row.get("finalize_status") != "ok" else "checker"
    elif metrics.get("preplan_status") == "ok":
        result["stage"] = "terminal"
    if not row.get("passed"):
        return result
    try:
        assert run and run.get("split") == "train", "missing TRAIN run authority"
        assert job.get("split") == row.get("job", {}).get("split") == "train", "missing TRAIN map authority"
        assert "/train/" in job.get("input", "") and row["job"].get("input") == job["input"], "missing TRAIN input binding"
        assert run["config"]["no_repair"] is True, "no-repair guard missing"
        assert run["config"]["rule_freeze_v1"] is False, "rule-freeze setting differs"
        assert row["job"]["input_sha256"] == job.get("input_sha256", job.get("sha256")), "input binding differs"
        assert row["solver_status"] == row["finalize_status"] == "ok", "unsuccessful planner or finisher"
        assert row["checker_passed"] is True, "no full-checker acceptance"
        report_path, finished_path = folder / "report.json", folder / "finished.json"
        assert sha(report_path) == row["report_sha256"], "checker report hash differs"
        assert sha(finished_path) == row["finished_sha256"], "finished-plan hash differs"
        report, finished = read(report_path), read(finished_path)
        assert report["passed"] is True and not report.get("violations"), "full report rejects"
        assert finished["status"] == "ok" and finished.get("plan") is not None, "missing successful finished plan"
        moves = report["count"]["weighted_nonproductive"]
        assert number(moves) and row["moves"] == moves, "checked moves differ"
        result.update(passed=True, moves=moves, stage="pass")
    except (AssertionError, OSError, KeyError, TypeError, ValueError) as error:
        result.update(resolved=False, stage="certificate_invalid", error=str(error))
    return result


def old_outcome(job, baseline):
    if job["stage"] == "D185":
        # Exact failed cohort is documented in the four required source notes;
        # no per-map source timings were frozen by the ladder census.
        return dict(resolved=True, passed=False, moves=None, seconds={},
                    stage="documented_failure", documented=True)
    row = baseline.get(job["map_id"])
    if not row or not row["resolved"]:
        return dict(resolved=False, passed=False, moves=None, seconds={}, stage="pending")
    arms = row["arms"]
    keys = dict(planner="solver", carrier="carrier", terminal="terminal", finisher="finisher", checker="checker")
    seconds = {key: sum(a["stage_seconds"].get(source, 0) for a in arms.values()) for key, source in keys.items()}
    return dict(resolved=True, passed=row["passed"], moves=row["moves"], seconds=seconds,
                stage="pass" if row["passed"] else "/".join(sorted({a["stage"] for a in arms.values()})),
                default=arms["default"]["stage"] == "pass", rule=arms["rule"]["stage"] == "pass")


def count(rows, name):
    values = [r[name] for r in rows]
    return sum(v["passed"] for v in values), sum(v["resolved"] for v in values)


def rate(rows, name):
    passes, resolved = count(rows, name)
    return f"{passes}/{resolved}"


def move(value):
    return f"{value:g}" if number(value) else "—"


def times(outcome):
    return " / ".join(f"{outcome['seconds'][key]:.1f}" if number(outcome["seconds"].get(key)) else "—"
                      for key in ("planner", "carrier", "terminal", "finisher", "checker"))


def planned_bounds(before_passes, before_resolved, after_passes, planned):
    """Outcome-independent bounds on accepted plans over the fixed cohort."""
    if not (0 <= before_passes <= before_resolved <= planned and 0 <= after_passes <= planned):
        raise ValueError("Invalid planned-cohort pass counts")
    baseline_upper = before_passes + planned - before_resolved
    return dict(baseline_upper=baseline_upper, new_lower=after_passes,
                superiority=after_passes > baseline_upper)


def report_filename(out, *, primary_exists, primary_name="v3"):
    """Keep each retired epoch's controller off the latest primary report."""
    runs = ROOT / "experiments/mvp/scale/runs"
    if primary_exists and out == runs / "main":
        return "PROTOTYPE.md"
    if primary_exists and primary_name == "v4" and out == runs / "v3":
        return "V3.md"
    return "SCALE-SOLVER.md"


def changed(config, out):
    packing = config.get("packing_module", "carrier_v4" if out.name == "v4" else "carrier" if out.name == "v3" else "packing")
    finisher = config.get("finisher_module", "finisher_v3" if out.name == "v4" else "finisher_v2" if out.name == "v3" else "finisher")
    text = [f"Selected implementation: `{packing}` / `{finisher}`. "
            "`reservations.py` orders the constructor's original legal actions by optimistic remaining short/long slot capacity and the loss of future stack modes. It neither adds nor removes an action, changes a domain, or bypasses a capacity/legality screen; deadline fallback preserves the original action list. Optimistic scores are search guidance, never feasibility certificates."]
    if packing == "carrier_v4":
        text.append("`carrier_v4` omits CP entirely: native greedy receives up to 60 s/40% of the shared 150 s carrier allowance, then the direct native constructor receives the remaining time (about 90 s after an exhausted greedy slice). The total planner allowance stays 300 s. Constructor search retains six full-fact box profiles per state, versus three, and allows 512 backtracks per attempt across four strategies, within the same deadline. Ranking protects restricted twenty-foot booking pools and scarce unions, while preserving native hold-before-deck priority and every retained action. The native-source delta is staged in `reservations_v4_source.diff`; no src file is edited. Exact deck support geometry is screened earlier only as a conservative constructive-search restriction; native action membership, final geometry, E6, terminal replay and full-checker requirements remain unchanged. Screening can discard a prefix repairable by later restow, so a bounded failure is not an infeasibility certificate.")
        text.append("This revision follows the retained v3 feeder800 high-mass seed192200100 attempt: its CP solve took 84.2 s despite its bounded request, leaving only 2.3 s for constructor initialization. The first v3 outcomes remain separate adaptive TRAIN evidence. New admissions were stopped while existing calls drained; unattempted maps are not failures. No claim of a completed v3 ladder or blind selection is made.")
        text.append("`terminal_v4` prefers a successful native carrier-representative replay over yard-first substitutions; yard-first remains fallback if that replay fails. Both existing replays, their legality/securing checks, bounded row adjustments, deadlines and measured move counts remain intact. This preserves carrier box identities; exact original slots additionally require zero recorded carrier-approval adjustments. The source comparator change is staged in `terminal_v4.source.diff`. On the retained f60 low-mass feeder500 diagnostic, substitution count fell from 58 to zero and inherited-ballast departure securing hard violations fell from eight to four; this partial check is not a pass. The known v3 high-mass certificate already used carrier representatives.")
    if packing == "carrier":
        text.append("`carrier.py` allocates the shared 150 s carrier slice within the unchanged 300 s planner allowance: original native greedy receives up to 60 s or 40% of the available carrier time; on failure, V3 joint CP receives up to 35 s or half the remaining time; the direct native constructor receives what remains. Calling the constructor directly avoids spending another ten-second greedy slice. Every stage shares the same carrier deadline; total planner time is not increased. Reservations affect ordering only in the constructor.")
        text.append("This pivot follows two high-mass feeder500 V3 CP probes returning UNKNOWN after about 36 s each, while the frozen baseline's same-case greedy took 7.7 s and the current ten-second greedy probe timed out. These observations motivate search allocation and do not prove infeasibility or a speed improvement. All earlier prototypes/probes remain separate adaptive TRAIN development evidence, not blind results or primary passes.")
    if packing in ("packing_v3", "carrier"):
        text.append("`packing_v3` scopes native pair replay to avoid an ordering-induced rejection. When heavy cargo must go on deck, it proposes conservative full holds while retaining the exact existing E6 rule and native replay; the unchanged full checker remains mandatory. Full holds are a sufficient search restriction, not a relaxation or proof that other layouts are infeasible.")
    elif packing != "carrier_v4":
        text.append("The original capacity prototype retained the original native constructor and exact E6 rule. Its changed ordering exposed a negative E6 regression on a baseline-passing TRAIN map. The separate discrete CP slot proposal remains diagnostic; its probes supply no accepted plan by themselves, and CP non-solves are not infeasibility certificates.")
    if finisher == "finisher_v3":
        text.append("`finisher_v3` adds exact direct-liquid numerical result reuse within the candidate search to the earlier visibility/lashing/stability/strength part ordering and durable telemetry. One bounded root workspace spans candidates, keyed by complete immutable geometry, exact scalar inputs and polynomial mode. Up to 128 completed visibility/lashing loading conditions are retained across finite-difference workspaces using full instance/ship content keys. These caches change computation only, not liquid physics, tank vectors, thresholds, or acceptance. Both the finisher's full-checker gate and the additional independent unchanged full checker run with search caches inactive; search-cache hits cannot create a certificate.")
    elif finisher == "finisher_v2":
        text.append("`finisher_v2` tries V4 visibility, lashing, stability and strength parts earlier in its search order and records durable telemetry. Exact completed numerical calculations are reused only within candidate part probes; acceptance checks run with that cache inactive. Search order and telemetry do not change rule limits, physics equations, or acceptance.")
    else:
        text.append("`finisher` reuses exact completed numerical calculations within candidate part probes; full acceptance checks run with that cache inactive. These are computation changes, with no rule or tolerance changes.")
    return " ".join(text)


def prototype(out, jobs, baseline):
    """Retain first outcomes for every earlier epoch without portfolio pooling."""
    lines = []
    reasons = {
        "main": "The first high-mass feeder500 seed192200000 constructor stopped at 87/88 placements despite a baseline pass; further admissions were stopped after the negative E6 regression.",
        "v3": "Further admissions were stopped following the feeder800 high-mass seed192200100 CP overrun and constructor starvation. Its CP solve took about 84.2 s, leaving about 2.3 s for the constructor. Already admitted calls retain their first completed outcomes.",
    }
    for epoch in ("main", "v3"):
        original = ROOT / "experiments/mvp/scale/runs" / epoch
        if out == original or not (original / "run.json").is_file():
            continue
        # The first prototype report must not characterize a later run as old.
        if out.name == "main":
            continue
        run = read(original / "run.json")
        attempted = []
        for job in jobs:
            folder = original / "maps" / job["map_id"]
            if (folder / "summary.json").is_file():
                saved = read(folder / "summary.json")
                attempted.append(dict(job=job, saved=saved,
                                      before=old_outcome(job, baseline),
                                      after=new_outcome(folder, job, run)))
        n = len(attempted)
        resolved = sum(r["after"]["resolved"] for r in attempted)
        passes = sum(r["after"]["passed"] for r in attempted)
        lines += ["", f"## Retained {epoch} epoch", "",
                  f"`runs/{epoch}` is retained separately from `{out.relative_to(ROOT)}`. The original protocol planned 48 maps; {n} were attempted, {resolved} have resolved outcomes, {passes} have certified passes, and {48-n} were unattempted. Unattempted maps are not failures. In-flight finishing/checker calls may still commit their first outcome. This is adaptive TRAIN development, not a blind comparison or a completed full-ladder result.", "",
                  reasons[epoch], "",
                  "All negative and positive first outcomes remain attributed to their original configuration. Source guards stay intact; no earlier-epoch result contributes to the selected primary gate or its moves. Pending outcomes and accepted full-checker certificates retain their usual meanings.", "",
                  f"Original config: `{json.dumps(run.get('config', {}), sort_keys=True)}`.", "",
                  "| Retained TRAIN map | Before stage / moves | Epoch stage / moves | Constructor prefix | Planner / carrier / terminal / finisher / checker s |", "|---|---|---|---|---|"]
        for r in attempted:
            saved = r["saved"]
            depth = saved.get("metrics", {}).get("constructor_metrics", {}).get("deepest_depth")
            prefix = f"{depth}/{r['job'].get('n_loads', '—')}" if depth is not None else "—"
            lines.append(f"| {r['job']['map_id']} | {r['before']['stage']} / {move(r['before']['moves'])} | {r['after']['stage']} / {move(r['after']['moves'])} | {prefix} | {times(r['after'])} |")
    return lines


def render(out, baseline_path):
    baseline = read(baseline_path)
    assert baseline and baseline["format"] == "D199-scale-barrier-census-v1"
    frozen_rows = {r["map_id"]:r for r in baseline["rows"]}
    run = read(out / "run.json", {})
    jobs = [dict(j, stage=f"f{j['fill_target']}") for j in baseline["ladder_jobs"]]
    jobs += [dict(j, stage="D185") for j in baseline["d185_jobs"]]
    assert len(jobs) == len({j["map_id"] for j in jobs}) == 48
    if run:
        assert run["split"] == "train"
        declared = {j["map_id"]:j for j in run["cohort"]["maps"]}
        assert set(declared) == {j["map_id"] for j in jobs}, "run differs from frozen TRAIN cohort"
        for job in jobs:
            assert declared[job["map_id"]]["input_sha256"] == job.get("input_sha256", job.get("sha256"))
    rows = []
    for job in sorted(jobs, key=lambda j:(STAGES.index(j["stage"]), j["ship"], j.get("mass_level", "original"), j["seed"])):
        assert job["split"] == "train" and "/train/" in job["input"]
        rows.append(dict(job=job, before=old_outcome(job, frozen_rows),
                         after=new_outcome(out / "maps" / job["map_id"], job, run)))
    config = run.get("config", {})
    planner, finisher = config.get("planner_seconds", 300), config.get("finisher_seconds", 1200)
    status = read(out / "status.json", {})
    complete = read(out / "completion.json")
    resolved = sum(r["after"]["resolved"] for r in rows)
    label = "complete" if resolved == len(rows) and complete is not None else "interim"
    lines = ["# D199 scale solver", "", f"Updated {datetime.now(timezone.utc).isoformat()}. Status: **{label}**. TRAIN only; CPU only, one native thread per numerical library, at most four compute workers including owned V4 children.", "",
             f"New run: **{sum(r['after']['passed'] for r in rows)}/{resolved}** certified passes among resolved maps; **{len(rows)-resolved} pending/unresolved** of 48 planned maps. " + (f"Controller stage: `{status.get('stage', 'unknown')}`." if run else "Run has not started; all new outcomes are pending."), "",
             "## Barrier ranking supplied within the first hour", "", f"Frozen census: `{baseline_path.relative_to(ROOT)}`, SHA256 `{sha(baseline_path)}`; recorded {baseline['updated_at']}. {baseline['resolved_maps']}/90 sweep maps resolved, {baseline['passing_maps']} portfolio passes, {baseline['failed_maps']} failed maps.", "",
             "| Barrier | Failing arms | Failed maps with barrier | Sole barrier on map |", "|---|---:|---:|---:|---:|"]
    for r in baseline["ranking"]:
        lines.append(f"| {r['barrier']} | {r['failing_arms']} | {r['failed_maps_with_barrier']} | {r['failed_maps_only_barrier']} |")
    lines += ["", "Carrier construction is the largest measured barrier: 61/64 carrier arms exhaust 512 backtracks; 43 report no fitting box, 19 bounded exhaustion and 2 deadlines. Missing discrete slot packing and late compatible-cell shortages are mechanisms to address within these failures; sweep telemetry cannot separately assign causal counts to twenty-foot shortage or individual rules. Finisher has 27 raw/compact watchdog failures across 14 arms; one affected arm also passes another candidate, leaving 13 arm-level failures. Mixed-stage maps appear in both coverage columns.", "",
              "## What changed", "", changed(config, out), "",
              "## Frozen baseline and new outcomes by cell", "", "Before is the better checked default/rule portfolio, allowing identical optional compaction to both arms. Denominators are resolved maps, not planned maps. D185 baseline is documented 0/12 in the required MEDIUM-WALL/DIAGNOSIS notes; individual baseline timings were not frozen. All other baseline outcomes are frozen in barrier-ranking.json and never refreshed from a changing sweep.", "",
              "| Rung | Ship | Mass | Before passes/resolved | New passes/resolved | Planned | Before pending | New pending |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for stage in STAGES:
        ships = ("feeder_500", "feeder_800", "feeder_1700") if stage == "D185" else ("feeder_500", "feeder_800")
        for ship in ships:
            for mass in (("original",) if stage == "D185" else ("high", "low")):
                cell = [r for r in rows if r["job"]["stage"] == stage and r["job"]["ship"] == ship and r["job"].get("mass_level", "original") == mass]
                lines.append(f"| {stage} | {ship} | {mass} | {rate(cell, 'before')} | {rate(cell, 'after')} | {len(cell)} | {len(cell)-count(cell, 'before')[1]} | {len(cell)-count(cell, 'after')[1]} |")
    lines += ["", "## Scale gate S", "", "Gate S compares new certificates against the frozen better-of-two portfolio on identical maps. Paired means both outcomes are resolved. A pending baseline map is excluded from paired counts and is never treated as a failure. The planned-cohort baseline upper bound is its certified passes plus every pending baseline map; the new lower bound is its certified passes, even if every remaining new attempt fails. Strictly exceeding that baseline upper bound certifies PASS without resolving pending maps. Otherwise only a complete paired comparison can certify PASS; partial paired improvement remains provisional. Frozen f60 has 1 pass among 8 resolved of12, so baseline can achieve at most 5/12 and six certified new passes would clear the worst-case bound. This threshold is fixed by cohort accounting and does not depend on new outcomes.", "",
              "| Rung | Before resolved/planned | New resolved/planned | Paired maps | Before paired passes | New paired passes | Paired new-only / lost | Before pass upper bound | New pass lower bound | Gate |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for stage in STAGES:
        cell = [r for r in rows if r["job"]["stage"] == stage]
        paired = [r for r in cell if r["before"]["resolved"] and r["after"]["resolved"]]
        old, new = sum(r["before"]["passed"] for r in paired), sum(r["after"]["passed"] for r in paired)
        gain = sum(r["after"]["passed"] and not r["before"]["passed"] for r in paired)
        lost = sum(r["before"]["passed"] and not r["after"]["passed"] for r in paired)
        before_passes, before_resolved = count(cell, 'before')
        new_passes, new_resolved = count(cell, 'after')
        bound = planned_bounds(before_passes, before_resolved, new_passes, len(cell))
        if bound['superiority']:
            gate = "PASS (worst-case baseline bound)"
        elif len(paired) == len(cell):
            gate = "PASS" if new > old else "not passed"
        else:
            gate = "pending; paired improvement" if new > old else "pending"
        lines.append(f"| {stage} | {before_resolved}/{len(cell)} | {new_resolved}/{len(cell)} | {len(paired)} | {old} | {new} | {gain} / {lost} | {bound['baseline_upper']}/{len(cell)} | {bound['new_lower']}/{len(cell)} | {gate} |")
    lines += ["", "## Per-map certificates and moves", "", "Moves are full-checker `weighted_nonproductive`; failed and pending plans have no imputed moves. Before chooses the lowest-move passing raw/compact candidate across default and rule. Prefix depths and solver completion are never acceptance.", "",
              "| TRAIN map | Before stage | Before moves | New stage | New moves |", "|---|---|---:|---|---:|"]
    for r in rows:
        lines.append(f"| {r['job']['map_id']} | {r['before']['stage']} | {move(r['before']['moves'])} | {r['after']['stage']} | {move(r['after']['moves'])} |")
    lines += ["", "## Time by stage", "", "All times are elapsed seconds. Each entry is **planner / carrier preplan / terminal / finisher / checker**. Carrier and terminal come from native `metrics.preplan_time_s` and `metrics.terminal_time_s` and are nested within planner time. Before sums work across both arms and their raw/compact candidates; new is one proposed pipeline per map, so these are compute-accounting figures, not matched algorithm-speed ratios. A dash means unavailable or not reached.", "",
              "| TRAIN map | Before seconds | New seconds |", "|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['job']['map_id']} | {times(r['before'])} | {times(r['after'])} |")
    lines += prototype(out, jobs, frozen_rows)
    lines += ["", "## Safeguards, assumptions and verification", "",
              f"- Runtime/source is the pinned D192 sweep snapshot, copied by scale_solver with recorded SHA256 bindings. Budgets: {planner:g} s planner / {finisher:g} s finisher, seed 0; original comparison budgets 300/1200 s. No repair. Rule freeze OFF matches the sweep; no hard rule, tolerance or checker relaxation is authorized.",
              "- Every new displayed pass requires completed solver and finisher, a separately saved unchanged full-checker acceptance, matching report/finished-plan hashes, a bound TRAIN input hash, and checked weighted moves. Invalid certificates remain unresolved and cannot satisfy Gate S. This report imports no solver/checker and performs no fresh evaluation.",
              "- Only the 36 feeder500/800 TRAIN ladder maps, followed by the exact 12-map TRAIN D185 cohort, are eligible. No test, test-v3, validation or showcase input is read. Fill/mass derivatives share parent seeds and are not independent samples.",
              "- Actual frozen sweep portfolio counts are f40 6/12, f50 1/6, f60 1/12, f70 0/6, f80 0/9. The order's f40 8/12 is not reproduced by committed full-checker certificates; f60 1/12 includes feeder1700, whereas the requested two-ship f60 comparison is 1/8 resolved of12 planned.",
              "- D185 per-map baseline failures follow the documented all-map 0/12 result; precise baseline stages/times are unavailable in the frozen ladder artifact and are shown explicitly as unavailable.",
              f"- New first-failure/activity stages: `{dict(Counter(r['after']['stage'] for r in rows))}`. Ledger: `experiments/fleet/ledger.md`, row `scale-solver/run`; local execution evidence: `scale/ledger.json` and run controller/status/completion artifacts."]
    if config.get("check_failed_finishers") is False:
        lines.append("- `check_failed_finishers=False`: failed finisher outcomes retain their finished/partial result and progress telemetry; an additional full check of that unsuccessful result is not reached. Every successful finisher still requires the fresh independent unchanged full checker before it can contribute a pass or moves. Failed finishers remain failures without acceptance.")
    errors = [(r["job"]["map_id"], r["after"]["error"]) for r in rows if r["after"]["error"]]
    if errors:
        lines += ["", "Unresolved harness/certificate errors:", ""] + [f"- `{name}`: {error}" for name,error in errors]
    return "\n".join(lines)+"\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "runs/v4")
    parser.add_argument("--baseline", type=Path, default=HERE / "barrier-ranking.json")
    args = parser.parse_args()
    report = render(args.out.resolve(), args.baseline.resolve())
    primary_name = "v4" if (HERE / "runs/v4/run.json").is_file() else "v3"
    target = HERE / report_filename(args.out.resolve(), primary_exists=(HERE / "runs" / primary_name / "run.json").is_file(), primary_name=primary_name)
    temporary = target.with_name(target.name+f".tmp-{os.getpid()}")
    temporary.write_text(report)
    temporary.replace(target)
    print(target)


if __name__ == "__main__":
    main()

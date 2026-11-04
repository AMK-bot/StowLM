"""Incremental TRAIN fill-sweep report; accepted costs require saved certificates.

This module only reads study artifacts and writes FILLSWEEP.md/summary.json.
It neither generates maps nor imports the planner/checker runtime.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FILLS = (40, 50, 60, 70, 80)
ARMS = ("default", "rule", "portfolio")


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(value)
    tmp.replace(path)


def get(job, *keys, default=None):
    for source in (job, job.get("design", {}), job.get("metrics", {})):
        for key in keys:
            if source.get(key) is not None:
                return source[key]
    return default


def fill_target(job):
    value = get(job, "fill_target_pct", "target_fill_pct", "fill_pct", "fill_target", "target_fill")
    if value is None:
        return None
    return round(float(value) * (100 if float(value) <= 1 else 1))


def actual_fill(job):
    pct = get(job, "realised_cell_fill_pct", "realized_cell_fill_pct", "physical_cell_fill_pct")
    if pct is not None:
        return float(pct)
    frac = get(job, "realised_cell_fill", "realized_cell_fill", "physical_cell_fill")
    return None if frac is None else 100 * float(frac)


def mass_level(job):
    return str(get(job, "mass_level", "mass_label", default="unknown"))


def path_at(root, path):
    result = Path(path)
    return result if result.is_absolute() else root / result


def checked_cost(record, root):
    """Fail closed, including when a metadata pass lacks a saved full report."""
    if not record.get("passed"):
        return None
    assert record.get("checker_attempted") is True, "pass without full checker attempt"
    assert record.get("checker_passed") is True, "pass without full checker acceptance"
    assert record.get("finalize_status") == "ok", "pass without successful finisher"
    assert not record.get("layout_violations"), "pass with layout violations"
    report_path, finished_path = path_at(root, record["report_path"]), path_at(root, record["finished_path"])
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == record["report_sha256"], "full checker report hash mismatch"
    assert hashlib.sha256(finished_path.read_bytes()).hexdigest() == record["finished_sha256"], "finished plan hash mismatch"
    report = read(report_path)
    finished = read(finished_path)
    assert report and report.get("passed") is True, "missing or rejecting full checker report"
    assert not report.get("violations"), "passing report contains violations"
    assert finished and finished.get("plan") is not None, "missing saved finished plan"
    assert finished.get("status") == "ok", "saved finished result is not ok"
    cost = report["count"]["weighted_nonproductive"]
    assert isinstance(cost, (int, float)) and math.isfinite(cost), "non-finite checked cost"
    assert record.get("moves") == cost, "reported cost differs from full checker"
    return float(cost)


def certificate(row, arm, variant):
    # The compact endpoint offers compaction and retains the better checked raw
    # plan, exactly as blockrule. A partially written summary may lack selection.
    selected = row.get("selected", {}).get(variant, {}).get("planner" if arm == "portfolio" else arm)
    if selected is not None:
        return selected
    if arm == "portfolio":
        return {}
    a = row.get("arms", {}).get(arm, {})
    if variant == "raw":
        return a.get("raw", {})
    raw, compact = a.get("raw", {}), a.get("compact", {})
    if compact.get("passed") and (not raw.get("passed") or compact["moves"] < raw["moves"]):
        return compact
    # Do not finalize the compact endpoint until the compaction attempt resolves.
    return raw if "compact" in a else {}


def outcome(job, row, arm, variant, root):
    c = certificate(row, arm, variant)
    a = row.get("arms", {}).get(c.get("arm", "default") if arm == "portfolio" else arm, {})
    state = str(get(job, "generation_status", default=""))
    generated = bool(job.get("input"))
    if not generated and state in ("failed", "rejected", "generation_failed", "error"):
        return dict(stage="generation", resolved=True, cost=None, rules=[])
    if a.get("status") in ("exception", "error"):
        return dict(stage="infrastructure", resolved=False, cost=None, rules=sorted(set(c.get("rules", []))),
                    error=a.get("message", a.get("error", "Solver wrapper reported an exception/error.")))
    if not c:
        # Infrastructure interruptions remain unresolved; a failed computation
        # does not silently become a scientific planner failure.
        return dict(stage="infrastructure" if row.get("error") else "pending", resolved=False, cost=None, rules=[])
    rules = sorted(set(c.get("rules", [])))
    try:
        cost = checked_cost(c, root)
    except (AssertionError, KeyError, TypeError, ValueError, OSError) as exc:
        return dict(stage="certificate_invalid", resolved=False, cost=None, rules=rules, error=str(exc))
    if cost is not None:
        return dict(stage="pass", resolved=True, cost=cost, rules=[])
    metrics = a.get("metrics", {})
    if not a.get("solver_complete", c.get("solver_complete", False)):
        preplan = metrics.get("preplan_status", metrics.get("constructor_status"))
        terminal = metrics.get("terminal_status")
        stage = "terminal" if preplan == "ok" and terminal not in (None, "not_started", "ok") else "carrier"
    elif c.get("error"):
        return dict(stage="infrastructure", resolved=False, cost=None, rules=rules, error=c["error"])
    elif c.get("finalize_status") == "compaction_unavailable":
        stage = "compaction"
    elif c.get("finalize_status") != "ok":
        stage = "finisher"
    elif c.get("layout_violations"):
        stage = "layout"
    elif c.get("checker_attempted"):
        stage = "checker"
    else:
        return dict(stage="pending", resolved=False, cost=None, rules=rules)
    return dict(stage=stage, resolved=True, cost=None, rules=rules,
                checker_rejected=c.get("checker_attempted") is True and c.get("checker_passed") is False,
                finalize_status=c.get("finalize_status"), solver_status=a.get("status"),
                terminal_status=metrics.get("terminal_status"),
                cause=c.get("metrics", {}).get("not_found_cause", c.get("message", a.get("message"))))


def aggregate(rows, arm, variant):
    values = [r["outcomes"][variant][arm] for r in rows]
    passed = sum(v["stage"] == "pass" for v in values)
    resolved = sum(v["resolved"] for v in values)
    generated = sum(bool(r["job"].get("input")) for r in rows)
    generation_failed = sum(v["stage"] == "generation" for v in values)
    resolved_generated = sum(bool(r["job"].get("input")) and v["resolved"] for r,v in zip(rows, values))
    fills = [actual_fill(r["job"]) for r in rows if actual_fill(r["job"]) is not None]
    return dict(planned=len(rows), generated=generated, resolved=resolved,
                generation_failed=generation_failed, resolved_generated=resolved_generated,
                pending=len(rows)-resolved, passes=passed,
                pass_fraction=passed/generated if generated and resolved == len(rows) else None,
                pass_fraction_bounds=[passed/len(rows), (passed+len(rows)-resolved)/len(rows)] if rows else None,
                stages=dict(Counter(v["stage"] for v in values)),
                checker_rejections=sum(v.get("checker_rejected", False) for v in values),
                checker_rules=dict(Counter(rule for v in values for rule in v["rules"])),
                realised_fill_pct=[min(fills), max(fills)] if fills else None)


def mutual(rows, variant, arm="rule"):
    pairs = [(r["outcomes"][variant]["default"]["cost"], r["outcomes"][variant][arm]["cost"]) for r in rows]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    base = statistics.mean(a for a, _ in pairs) if pairs else None
    rule = statistics.mean(b for _, b in pairs) if pairs else None
    return dict(comparator=arm, n=len(pairs), default_mean=base, rule_mean=rule,
                mean_difference=rule-base if pairs else None,
                reduction_pct=100*(base-rule)/base if base else None,
                fewer=sum(b<a for a,b in pairs), equal=sum(b==a for a,b in pairs), more=sum(b>a for a,b in pairs))


def threshold(groups):
    """First observed grid point strictly below half, without assuming monotonicity."""
    for fill in FILLS:
        s = groups.get(fill)
        if not s or s["pending"]:
            return dict(status="pending", fill_pct=None, detail=f"Unresolved at {fill}%; later cells cannot establish the first crossing.")
        if not s["generated"]:
            return dict(status="unknown", fill_pct=None, detail=f"No generated calls at {fill}%; generation failures cannot establish a planner threshold.")
        if 2*s["passes"] < s["generated"]:
            neighbors = {}
            for label, f in (("previous", fill-10), ("next", fill+10)):
                cell = groups.get(f)
                neighbors[label] = dict(target_fill_pct=f, passes=cell["passes"], generated=cell["generated"], pending=cell["pending"]) if cell else None
            return dict(status="observed", fill_pct=fill, neighbors=neighbors, detail="First completed grid point below half on generated calls; no monotonicity or continuous threshold is inferred." + (" Generation failures reduce this cell's sample." if s["generation_failed"] else ""))
    return dict(status="not_observed", fill_pct=None, detail="Not below half on this completed 40–80% grid.")


def fmt_range(values):
    return "—" if not values else f"{values[0]:.2f}–{values[1]:.2f}%"


def numeric_range(jobs, key, multiplier=1):
    values = [float(get(j, key))*multiplier for j in jobs if get(j, key) is not None]
    return [min(values), max(values)] if values else None


def number_range(values, places=1):
    if values is None:
        return "—"
    return f"{values[0]:.{places}f}–{values[1]:.{places}f}"


def pipeline_diagnostics(row):
    result = {}
    for arm in ("default", "rule"):
        a = row.get("arms", {}).get(arm, {})
        metrics = a.get("metrics", {})
        result[arm] = dict(solver_status=a.get("status", "not_reached"),
                           carrier_status=metrics.get("preplan_status", "not_reached"),
                           terminal_status=metrics.get("terminal_status", "not_reached"),
                           candidates={})
        for variant in ("raw", "compact"):
            c = a.get(variant, {})
            result[arm]["candidates"][variant] = dict(finalize_status=c.get("finalize_status", "not_reached"),
                checker="pass" if c.get("checker_passed") else "reject" if c.get("checker_attempted") else "not_reached",
                compaction_status=c.get("compaction", {}).get("status", "unavailable" if c.get("finalize_status") == "compaction_unavailable" else "recorded" if "compaction" in c else "not_reached"),
                rules=sorted(set(c.get("rules", []))))
    return result


def render(here=HERE, root=ROOT, run=None):
    run = run or here / "runs" / "main"
    manifest = read(run / "run.json", {})
    cohort = read(here / "cohort.json", {})
    planned = read(here / "grid.json", read(here / "planned-grid.json", {}))
    planned_jobs = planned if isinstance(planned, list) else planned.get("maps", planned.get("jobs", planned.get("requests", [])))
    generated_jobs = cohort.get("maps", []) + manifest.get("jobs", [])
    indexed = {j["map_id"]:j for j in planned_jobs}
    for job in generated_jobs:
        indexed[job["map_id"]] = {**indexed.get(job["map_id"], {}), **job}
    jobs = list(indexed.values())
    assert all(j.get("split", "train") == "train" for j in jobs), "non-TRAIN input refused"
    rows = []
    for job in jobs:
        row = read(run / "maps" / job["map_id"] / "summary.json", {})
        merged = {**job, **row.get("job", {})}
        outcomes = {v:{a:outcome(merged, row, a, v, root) for a in ARMS} for v in ("raw", "compact")}
        rows.append(dict(job=merged, outcomes=outcomes, diagnostics=pipeline_diagnostics(row), summary_complete=bool(row.get("complete"))))
    ship_order = {"feeder_500":0, "feeder_800":1, "feeder_1700":2}
    ships = sorted({j["ship"] for j in jobs}, key=lambda ship:(ship_order.get(ship, 3), ship))
    masses = sorted({mass_level(j) for j in jobs})
    updated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    result = dict(updated_at=updated, planned_maps=len(jobs), generated_maps=sum(bool(j.get("input")) for j in jobs),
                  complete_maps=sum(r["summary_complete"] for r in rows), variants={}, maps=rows)
    result["status"] = "complete" if len(jobs) == 90 and all((r["summary_complete"] or r["job"].get("generation_status") == "failed") and all(o["resolved"] for arms in r["outcomes"].values() for o in arms.values()) for r in rows) else "interim"
    result["infrastructure_history"] = [dict(path=str(p.relative_to(here)), **read(p)) for p in sorted((here / "infrastructure").glob("*/classification.json"))]
    lines = ["# Fill sweep — D192 TRAIN medium scale ladder", "", f"Updated {updated}. CPU only; detached and resumable. No training, GPU, validation, test or showcase.", "",
             f"Status: **{result['status']}**. A pending map is neither a pass nor a failure. Final planner rates use generated calls in resolved cells; generation failures are separate. Interim planned-cohort acceptance bounds are [passes / planned, (passes + unresolved) / planned], with generation failures retained as nonacceptances. These bounds are not a final planner pass rate when generation fails.", "",
             "Registered design: **90 derivative TRAIN maps from nine independent fresh parent seeds**, with nested 40/50/60/70/80% subsets paired across mass arms. High retains parent gross masses; low uses max(tare, 0.65 × original gross), empty boxes unchanged. Cargo classes are recomputed. The 90 maps are not independent samples. Per [pre-run amendment 1](AMENDMENT-1.md), the full parent inventory, yard geometry and stack order remain fixed across all ten derivatives; unselected targets remain ashore as non-loading cargo/blockers. Only load-list demand and selected cargo masses change. This synthetic intervention holds terminal congestion fixed and does not model observed demand/congestion correlations. See the preregistration for stratified cargo-mix controls and exact generator settings.", "",
             "Default two-step and the port-rule arm use the blockrule medium budgets: 300 s solve, 1,200 s V4 finisher per candidate, seed 0 and one native thread per worker. Compact is the primary endpoint: it offers identical optional compaction to each arm and retains its better passing original. Raw results are also shown. Rule is the restricted rule alone; portfolio is the checker-selected rule with default fallback, whose nonloss guarantee is by construction.", "",
             "Every reported move count comes from a saved passing full-checker report and saved successful finished plan. Moves are checker `weighted_nonproductive`, including future-call work; means compare only mutual passes. A checker rejection after a finisher timeout is counted at the finisher as its first failure and is also retained in checker-rule counts.", "",
             "See [PREREG.md](PREREG.md), [LEDGER.md](LEDGER.md), `requests.json`, `cohort.json`, and `runs/main/run.json` for the planned grid, realised composition, fixed-background bindings, immutable runtime and budgets. These TRAIN maps form a later StowLM scale ladder; no model performance is measured here.", "",
             f"Registered maps: **90**; published planned rows: **{len(jobs)}**; generated: **{result['generated_maps']}**; paired pipelines complete: **{result['complete_maps']}**.", ""]
    if not jobs:
        lines += ["Grid artifacts are not available yet. No outcome or fill threshold is established.", ""]
    if result["infrastructure_history"]:
        lines += ["### Setup interruptions", "", "Setup failures remain separate from map-generation or planner outcomes; the original evidence and retry policy are retained.", ""]
        for entry in result["infrastructure_history"]:
            lines += [f"- [{entry.get('classification', 'Infrastructure interruption')}]({entry['path']}): parent draw attempts {entry.get('parent_attempts', 'unknown')}, derived maps {entry.get('derived_maps', 'unknown')}, planner calls {entry.get('planner_calls', 'unknown')}. Missing asset: `{entry.get('missing_asset', 'not recorded')}`. {entry.get('retry_policy', '')}."]
        lines.append("")
    lines += ["## Realised demand and fixed background", "", "Ranges describe generated inputs only; pending draws contribute no measured values. Mass-budget percentage means movable load-list gross mass / the generator's regional booking mass budget, not whole-ship utilization or a physical carrying-capacity certificate. H/M/L are recomputed cargo-class box counts. The low multiplier is not a mass-budget percentage.", "",
              "| Ship | Target fill | Mass | Generated / planned | Physical fill | Loads | Cargo t | Generator mass budget | H / M / L count ranges |", "|---|---:|---|---:|---:|---:|---:|---:|---|"]
    result["design_cells"] = []
    for ship in ships:
        for fill in FILLS:
            for mass in masses:
                planned_cell = [r["job"] for r in rows if r["job"]["ship"] == ship and fill_target(r["job"]) == fill and mass_level(r["job"]) == mass]
                if not planned_cell:
                    continue
                generated_cell = [j for j in planned_cell if j.get("input")]
                fills = [actual_fill(j) for j in generated_cell if actual_fill(j) is not None]
                classes = {c: ([min(j["weight_classes"].get(c, 0) for j in generated_cell if "weight_classes" in j), max(j["weight_classes"].get(c, 0) for j in generated_cell if "weight_classes" in j)] if any("weight_classes" in j for j in generated_cell) else None) for c in ("H", "M", "L")}
                cell = dict(ship=ship, target_fill_pct=fill, mass=mass, generated=len(generated_cell), planned=len(planned_cell),
                    physical_fill_pct=[min(fills), max(fills)] if fills else None,
                    loads=numeric_range(generated_cell, "n_loads"), cargo_tonnes=numeric_range(generated_cell, "cargo_tonnes"),
                    generator_mass_budget_pct=numeric_range(generated_cell, "generator_mass_fraction", 100), weight_classes=classes)
                result["design_cells"].append(cell)
                mix = " / ".join(number_range(classes[c], 0) for c in ("H", "M", "L"))
                lines.append(f"| {ship} | {fill}% | {mass} | {cell['generated']}/{cell['planned']} | {fmt_range(cell['physical_fill_pct'])} | {number_range(cell['loads'], 0)} | {number_range(cell['cargo_tonnes'], 1)} | {fmt_range(cell['generator_mass_budget_pct'])} | {mix} |")
    lines += ["", "Exact per-map H/M/L, POD and length counts, reefer/DG/empty counts and special-request mix are recorded in [`cohort.json`](cohort.json) as `weight_classes` and `cargo_mix`; source/cargo-ID bindings are in each `.derivation.json`. Approximate stratification preserves mix across nested fills; equal proportions are not assumed.", "",
              "Fixed synthetic background is listed once per parent seed below. Its mass excludes movable load-list cargo and ballast. The same complete vessel/background and yard are preserved across the parent's ten derivatives; full vessel hashes and parent bindings are in `cohort.json`. Background remains synthetic, with no observed manifest calibration.", "",
              "| Ship | Parent TRAIN seed | Generated derivatives / planned | Fixed background t | Vessel/background hashes |", "|---|---:|---:|---:|---|"]
    result["backgrounds"] = []
    for ship, seed in sorted({(j["ship"], j.get("seed")) for j in jobs}, key=lambda pair:(ship_order.get(pair[0], 3), pair[0], str(pair[1]))):
        parent_jobs = [r["job"] for r in rows if r["job"]["ship"] == ship and r["job"].get("seed") == seed]
        generated_parent = [j for j in parent_jobs if j.get("input")]
        hashes = sorted({j["vessel_sha256"] for j in generated_parent if j.get("vessel_sha256")})
        background = dict(ship=ship, parent_seed=seed, generated_derivatives=len(generated_parent), planned_derivatives=len(parent_jobs),
                          fixed_background_tonnes=numeric_range(generated_parent, "fixed_background_mass_kg", .001), vessel_sha256=hashes)
        result["backgrounds"].append(background)
        lines.append(f"| {ship} | {seed} | {background['generated_derivatives']}/{background['planned_derivatives']} | {number_range(background['fixed_background_tonnes'], 1)} | {', '.join('`'+h[:12]+'…`' for h in hashes) or 'pending'} |")
    lines.append("")
    for variant in ("compact", "raw"):
        lines += [f"## {variant.capitalize()} passes by fill and mass", "", "Ship `all` pools equal planned ship counts; ship rows retain each ship's denominator. Realised fill is physical occupied decision cells / available decision cells, excluding fixed background. Target fill is a grid label, not a measured percentage.", "",
                  "| Ship | Mass | Target fill | Realised fill range | Arm | Passes / generated / planned | Resolved generated | Generation errors | Unresolved | Final rate or planned bounds | First-failure stages |", "|---|---|---:|---:|---|---:|---:|---:|---:|---|---|"]
        variant_result = dict(cells=[], thresholds=[], mutual=[])
        for ship in ["all"]+ships:
            for mass in masses:
                matching = [r for r in rows if (ship == "all" or r["job"]["ship"] == ship) and mass_level(r["job"]) == mass]
                by_arm = {a:{} for a in ARMS}
                for fill in FILLS:
                    cell = [r for r in matching if fill_target(r["job"]) == fill]
                    if not cell:
                        continue
                    for arm in ARMS:
                        s = aggregate(cell, arm, variant)
                        by_arm[arm][fill] = s
                        variant_result["cells"].append(dict(ship=ship, mass=mass, target_fill_pct=fill, arm=arm, **s))
                        rate = f"{100*s['pass_fraction']:.1f}%" if s["pass_fraction"] is not None else "[%.1f%%, %.1f%%]" % tuple(100*x for x in s["pass_fraction_bounds"])
                        stages = ", ".join(f"{k} {v}" for k,v in sorted(s["stages"].items()) if k != "pass") or "none"
                        lines.append(f"| {ship} | {mass} | {fill}% | {fmt_range(s['realised_fill_pct'])} | {arm} | {s['passes']}/{s['generated']}/{s['planned']} | {s['resolved_generated']} | {s['generation_failed']} | {s['pending']} | {rate} | {stages} |")
                    for comparator in ("rule", "portfolio"):
                        variant_result["mutual"].append(dict(ship=ship, mass=mass, target_fill_pct=fill, **mutual(cell, variant, comparator)))
                for arm in ARMS:
                    variant_result["thresholds"].append(dict(ship=ship, mass=mass, arm=arm, **threshold(by_arm[arm])))
        lines += ["", "### First target fill below half", "", "This is the first completed registered grid cell with strictly fewer than half passing; exactly half is not below half. With three generated seeds per ship/cell, at most one passing draw is below half; pooled N=9 means at most four. No interpolation, monotonicity, or population-level cutoff is claimed. Neighbor columns give passes/generated and pending, not a fitted bracket.", "", "| Ship | Mass | Arm | First target fill <50% pass | Previous grid rung | Next grid rung | Status |", "|---|---|---|---:|---|---|---|"]
        for t in variant_result["thresholds"]:
            neighbor = lambda k: (f"{t['neighbors'][k]['target_fill_pct']}%: {t['neighbors'][k]['passes']}/{t['neighbors'][k]['generated']}; {t['neighbors'][k]['pending']} pending" if t.get('neighbors', {}).get(k) else '—')
            lines.append(f"| {t['ship']} | {t['mass']} | {t['arm']} | {str(t['fill_pct'])+'%' if t['fill_pct'] is not None else '—'} | {neighbor('previous')} | {neighbor('next')} | {t['status']}: {t['detail']} |")
        lines += ["", "### Checked moves on mutual passes", "", "| Ship | Mass | Target fill | Comparator | Mutual n | Default mean | Comparator mean | Mean difference | Reduction | Comparator fewer / equal / more |", "|---|---|---:|---|---:|---:|---:|---:|---:|---|"]
        for s in variant_result["mutual"]:
            means = (f"{s['default_mean']:.2f}", f"{s['rule_mean']:.2f}", f"{s['reduction_pct']:.1f}%" if s['reduction_pct'] is not None else "—") if s["n"] else ("—", "—", "—")
            diff = f"{s['mean_difference']:+.2f}" if s['n'] else "—"
            lines.append(f"| {s['ship']} | {s['mass']} | {s['target_fill_pct']}% | {s['comparator']} | {s['n']} | {means[0]} | {means[1]} | {diff} | {means[2]} | {s['fewer']} / {s['equal']} / {s['more']} |")
        lines += ["", "### Stage and checker evidence", "", "Stage counts use first failure. Checker rule counts can overlap and include checks of returned plans after finisher failure. Infrastructure/certificate errors remain unresolved.", "", "| Arm | First-failure / pending counts | Full-checker rejections | Checker rule IDs (maps) |", "|---|---|---:|---|"]
        for arm in ARMS:
            s = aggregate(rows, arm, variant)
            lines.append(f"| {arm} | {', '.join(f'{k}: {v}' for k,v in sorted(s['stages'].items())) or '—'} | {s['checker_rejections']} | {', '.join(f'{k}: {v}' for k,v in sorted(s['checker_rules'].items())) or '—'} |")
        lines.append("")
        result["variants"][variant] = variant_result
    lines += ["## Candidate-stage diagnostics", "", "These counts describe both attempted candidates, before choosing the better checked one. Not reached includes pending maps; full per-map status remains in `summary.json`. Later stages are not inferred to fail after an earlier stop.", "", "| Arm | Stage / candidate | Recorded status counts |", "|---|---|---|"]
    for arm in ("default", "rule"):
        for stage in ("carrier_status", "terminal_status", "solver_status"):
            counts = Counter(r["diagnostics"][arm][stage] for r in rows)
            lines.append(f"| {arm} | {stage} | {', '.join(f'{k}: {v}' for k,v in sorted(counts.items())) or '—'} |")
        for variant in ("raw", "compact"):
            for stage in ("finalize_status", "checker", "compaction_status"):
                if variant == "raw" and stage == "compaction_status":
                    continue
                counts = Counter(r["diagnostics"][arm]["candidates"][variant][stage] for r in rows)
                lines.append(f"| {arm} | {variant} / {stage} | {', '.join(f'{k}: {v}' for k,v in sorted(counts.items())) or '—'} |")
    lines.append("")
    lines += ["## Capacity and realism", "", "Known: the earlier strict TRAIN small cohort finished 29/30 at 25.0–36.96% physical decision-region fill; current dense TRAIN medium finished 0/12 at 78.75–85.84%. Those cohorts also differ in region size and cargo composition, so they do not locate a medium fill threshold. This matched medium study measures bounded planner completion on its declared synthetic population; failures do not prove map infeasibility.", "",
              "Unknown: there is no matched observed-call utilization sample using the same decision-region boundary, fixed-background treatment and cargo-mobility definitions. Real-call decision fill, a realism target percentage, and whether these draws are harder than real calls remain unknown. Named ship capacity, whole-ship synthetic utilization, nominal TEU and generator mass-budget fill have different denominators; none substitutes for observed decision-cell fill. A 45-foot box occupies two physical cells while nominal TEU counts 2.25. See [MEDIUM-WALL.md](../MEDIUM-WALL.md#capacity-and-generator-realism).", "",
              "## Per-map audit", "", "`summary.json` contains every planned draw, measured fill and per-arm stage/certificate status, plus the complete cell tables and threshold status. Per-map saved `checks/`, `reports/` and `finished/` artifacts retain acceptance and rejection evidence. Unresolved and generator-rejected draws are retained for the scale ladder rather than silently replaced.", ""]
    errors = [(r["job"]["map_id"], v, a, o["error"]) for r in rows for v, arms in r["outcomes"].items() for a,o in arms.items() if o.get("error")]
    if errors:
        lines += ["Certificate or infrastructure issues:", ""] + [f"- `{m}` / {v} / {a}: {e}" for m,v,a,e in errors] + [""]
    atomic(here / "FILLSWEEP.md", "\n".join(lines)+"\n")
    atomic(here / "summary.json", json.dumps(result, indent=2, sort_keys=True)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path)
    args = parser.parse_args()
    result = render(run=args.run)
    print(json.dumps({k:result[k] for k in ("updated_at", "planned_maps", "generated_maps", "complete_maps")}, sort_keys=True))

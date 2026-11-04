"""Render the Frontier scale study from its frozen manifest and local run records.

No planner, historical result, or checker module is imported. A planner pass is
reported as a pass only after its saved fresh-process audit accepts it.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics


HERE = Path(__file__).resolve().parent


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def number(value, places=1):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    return f"{value:.{places}f}" if value != int(value) else str(int(value))


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def map_id(record):
    return record.get("map_id", record.get("instance_id", record.get("id")))


def selected(record):
    return bool(record.get("selected_pass", record.get("selected_compact_pass", False))
                or record.get("selection_reason") == "selected_compact_planner_passed")


def jobs(manifest):
    maps = sorted(manifest.get("jobs", manifest.get("maps", [])),
                  key=lambda item: (item.get("n_boxes", 0), map_id(item)))
    result = [("base", item) for item in maps]
    # The frozen selection rule, rather than completion order, selects x4 jobs.
    explicit = manifest.get("budget_x4_map_ids", manifest.get("x4_ids", manifest.get("budget_x4_ids")))
    if explicit is not None:
        wanted = set(explicit)
    else:
        wanted = set()
        for boxes in (59, 74, 108):
            candidates = sorted(map_id(m) for m in maps if selected(m) and m.get("n_boxes") == boxes)
            if candidates:
                wanted.add(candidates[0])
    result.extend(("x4", item) for item in maps if map_id(item) in wanted)
    return result


def limited(event):
    """Trust explicit instrumentation; statuses are a conservative fallback."""
    limit = event.get("limit")
    if isinstance(limit, bool):
        return limit
    if limit is not None:
        return str(limit).lower() not in ("", "none", "false", "no", "not_hit", "not_reached")
    status = str(event.get("status", "")).lower()
    return any(token in status for token in ("time_limit", "timeout", "deadline", "budget_exhausted"))


def termination(event):
    limit = event.get("limit")
    if limit in ("solver_unproven_budget", "unknown_termination"):
        return "uncertain termination"
    if limit in ("rounds_limit", "cuts_limit", "candidate_exhaustion"):
        return "finite work bound"
    return "time budget reached" if limited(event) else "no recorded limit"


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def audit_valid(audit):
    if audit.get("passed") is not True:
        return False
    if any(audit.get(key) is not True for key in ("legal", "mask_ok", "complete", "all_loaded")):
        return False
    if audit.get("error"):
        return False
    if audit.get("fresh_process") is not True or audit.get("no_repair") is not True:
        return False
    auditor = audit.get("auditor_pid")
    if not isinstance(auditor, int) or isinstance(auditor, bool) or auditor <= 0:
        return False
    cost = audit.get("weighted")
    stored = audit.get("stored")
    return numeric(cost) and numeric(stored) and cost == stored


def audit_for(record, label, weighted):
    aliases = {"twostep": {"twostep", "two-step", "twostep_finish"},
               "blocked": {"blocked", "block-rule", "blocked_finish"},
               "final": {"final", "frontier", "winner", "returned"}}.get(label, {label})
    return any(audit_valid(a) and a.get("auditor_pid") != record.get("worker_pid")
               and a.get("weighted") == weighted and a.get("label") in aliases
               for a in record.get("audits", []))


def accepted(record):
    return (record.get("passed") is True and record.get("audited_passed") is True
            and audit_for(record, "final", record.get("weighted")))


def baseline(record, name):
    row = record.get(name + "_finish") or {}
    if not row:
        return "not produced", None
    if row.get("passed") is not True:
        return "fail (" + cell(row.get("finalize_status", row.get("status", "checker"))) + ")", None
    cost = row.get("weighted")
    if not audit_for(record, name, cost):
        return "audit unresolved", None
    return "pass; " + number(cost), cost


def issues_for(record):
    issues = []
    if record.get("error"):
        issues.append(str(record["error"]))
    if record.get("supplemental_error"):
        issues.append("supplemental baseline: " + str(record["supplemental_error"]))
    if record.get("complete") is not True:
        issues.append("result is not marked complete")
    if record.get("passed") and not accepted(record):
        issues.append("Frontier claimed a pass without a matching complete fresh-process audit")
    if record.get("passed") and record.get("origin") not in ("twostep", "blocked", "search", "construction"):
        issues.append("winning tables.origin classification is missing or unrecognized")
    for name in ("twostep", "blocked"):
        row = record.get(name + "_finish") or {}
        if row.get("passed") and not audit_for(record, name, row.get("weighted")):
            issues.append(name + " claimed a pass without a matching complete fresh-process audit")
    for attempt in record.get("tries", []):
        if attempt.get("passed") and not audit_for(record, attempt.get("label"), attempt.get("weighted")):
            issues.append(str(attempt.get("label")) + " passing try lacks a matching complete fresh-process audit")
    for audit in record.get("audits", []):
        if audit.get("stored_passed") and (not audit_valid(audit)
                                          or audit.get("auditor_pid") == record.get("worker_pid")):
            issues.append(str(audit.get("label")) + " claimed pass failed fresh-process reproduction")
    for candidate in record.get("candidates", []):
        if not any(audit_valid(a) and a.get("auditor_pid") != record.get("worker_pid")
                   and a.get("candidate_sha256") == candidate.get("sha256")
                   and a.get("label") == candidate.get("label")
                   and a.get("weighted") == candidate.get("weighted")
                   for a in record.get("audits", [])):
            issues.append(str(candidate.get("label")) + " captured pass lacks a matching fresh audit")
    return issues


def provenance_issues(record, item, manifest, manifest_hash):
    expected = dict(input=item.get("input"), input_sha256=item.get("input_sha256"),
                    n_boxes=item.get("n_boxes"), route=item.get("route"), seed=0,
                    version=manifest.get("frontier_version"), source_sha256=manifest.get("source_sha256"),
                    source_sha256_after=manifest.get("source_sha256"), manifest_sha256=manifest_hash)
    return [f"{key} differs from frozen manifest" for key, value in expected.items()
            if value is None or record.get(key) != value]


def stage_cells(record):
    events = record.get("stage_events", [])
    groups = defaultdict(list)
    for event in events:
        supplemental = (event.get("details") or {}).get("supplemental")
        name = str(event.get("stage", "unknown")) + (" supplemental" if supplemental else "")
        groups[name].append(event)
    timings, limits = [], []
    for name, items in groups.items():
        seconds = sum(e.get("seconds", 0) or 0 for e in items)
        timings.append(f"{cell(name)} {number(seconds)}" + (f" ({len(items)} calls)" if len(items) > 1 else ""))
        hit = [e for e in items if limited(e)]
        if hit:
            reasons = sorted({str(e.get("limit") or e.get("status")) for e in hit})
            limits.append(cell(name) + (f" {len(hit)}/{len(items)}" if len(items) > 1 else "")
                          + ": " + ", ".join(reasons))
    return "; ".join(timings) or "—", "; ".join(limits) or ("none recorded" if events else "—")


def quality_text(records):
    base = [(item, record) for budget, item, record, _ in records if budget == "base" and record]
    if not base:
        return ["Quality and the ×4 comparison are pending; no completed run is available."]
    output = []
    by_size = defaultdict(list)
    for item, record in base:
        by_size[item.get("n_boxes")].append(record)
    summaries = []
    for boxes, rows in sorted(by_size.items(), key=lambda pair: pair[0] or 0):
        costs = [r["weighted"] for r in rows if accepted(r)]
        detail = f"{boxes} boxes: {len(costs)}/{len(rows)} audited passes"
        if costs:
            detail += f", moves {number(min(costs))}–{number(max(costs))}"
            detail += f" ({number(min(costs) / boxes, 2)}–{number(max(costs) / boxes, 2)} per box)"
        summaries.append(detail)
    output.append("Base outcomes by size among completed records: " + "; ".join(summaries) + ".")
    unresolved_sizes = [boxes for boxes, rows in by_size.items() if not any(accepted(r) for r in rows)]
    if unresolved_sizes:
        output.append("Passing quality does not hold uniformly across the completed sizes: there is no audited "
                      "Frontier pass yet at " + ", ".join(str(n) for n in sorted(unresolved_sizes)) + " boxes.")
    else:
        output.append("An audited Frontier pass exists at every completed size. That establishes feasibility on "
                      "these maps; the same-run comparisons below assess move quality.")
    for name, title in (("twostep", "two-step"), ("blocked", "block-rule")):
        pairs = []
        for _, record in base:
            _, cost = baseline(record, name)
            if accepted(record) and cost is not None:
                pairs.append((record["weighted"], cost))
        if pairs:
            delta = statistics.mean(a - b for a, b in pairs)
            output.append(f"Against same-run {title}, {len(pairs)} mutually audited base maps: "
                          f"Frontier has fewer/equal/more moves on {sum(a < b for a,b in pairs)}/"
                          f"{sum(a == b for a,b in pairs)}/{sum(a > b for a,b in pairs)}; "
                          f"mean Frontier minus baseline {delta:+.2f} moves.")
        else:
            output.append(f"No mutually audited base comparison with same-run {title} is available yet.")
    lookup = {(budget, map_id(item)): record for budget, item, record, _ in records}
    comparisons = []
    paired_costs, gained, lost = [], 0, 0
    for budget, item, larger, _ in records:
        if budget != "x4":
            continue
        smaller = lookup.get(("base", map_id(item)))
        label = f"{item.get('n_boxes')} boxes ({map_id(item)})"
        if not larger or not smaller:
            comparisons.append(label + ": pending")
        elif accepted(larger) and accepted(smaller):
            delta = larger["weighted"] - smaller["weighted"]
            paired_costs.append(delta)
            comparisons.append(label + f": {number(smaller['weighted'])} → {number(larger['weighted'])} moves ({delta:+g})")
        else:
            gained += int(accepted(larger) and not accepted(smaller))
            lost += int(accepted(smaller) and not accepted(larger))
            comparisons.append(label + f": base {'pass' if accepted(smaller) else 'no audited pass'}, "
                               f"×4 {'pass' if accepted(larger) else 'no audited pass'}")
    output.append("×4 paired outcomes: " + "; ".join(comparisons) + ".")
    if paired_costs or gained or lost:
        output.append(f"Among resolved pairs, ×4 buys fewer/equal/more moves on "
                      f"{sum(d < 0 for d in paired_costs)}/{sum(d == 0 for d in paired_costs)}/"
                      f"{sum(d > 0 for d in paired_costs)} mutually passing maps; it adds {gained} "
                      f"audited passes and loses {lost}. More search time does not guarantee improvement "
                      "because each run is restarted with its own timing-dependent search trajectory.")
    output.append("These are observed quality outcomes on fixed maps, not an isolated effect of box count: ship, fill, "
                  "and cargo composition also vary. Pass rates and same-run move comparisons are reported separately; "
                  "a failed or unaudited plan has no accepted move count.")
    return output


def timing_text(records):
    groups = defaultdict(list)
    for budget, _, record, _ in records:
        if not record:
            continue
        for event in record.get("stage_events", []):
            seconds = event.get("seconds")
            if isinstance(seconds, (int, float)):
                stage = event.get("stage", "unknown")
                if (event.get("details") or {}).get("supplemental"):
                    stage += " supplemental"
                groups[(budget, stage, termination(event))].append(seconds)
    lines = ["All wall and stage seconds are elapsed measurements. A stage that reaches its time budget is "
             "a censored computation: its elapsed time measures the allocation, not the time required to solve the "
             "map. ALNS search normally runs until its deadline, so its near-linear allocation (6 s × boxes in base; "
             "24 s × boxes in ×4) cannot establish intrinsic scaling. Finishing has a 600 s or 2,400 s cap per start, "
             "not a single cap per map. Construction/master budgets, retry counts, and early exits remain those of "
             "the unedited 0.9.0 route.",
             "The limit column follows source instrumentation and terminal status, not an elapsed-time threshold. "
             "A finite round/cut loop can exhaust without hitting a time limit; solver status such as FEASIBLE or "
             "UNKNOWN alone does not prove a timeout. `solver_unproven_budget` and `unknown_termination` are "
             "uncertain termination, not confirmed timeouts, and are excluded from uncensored timing. "
             "`rounds_limit`, `cuts_limit`, and `candidate_exhaustion` record finite work bounds, rather than "
             "time limits. Unclassified limits remain an explicit uncertainty in the "
             "event details. Detailed per-call budgets, statuses, and limit evidence are in each linked result JSON."]
    if groups:
        lines.append("Observed stage calls (ranges in seconds, separated by termination evidence):")
        for (budget, stage, evidence), values in sorted(groups.items()):
            lines.append(f"- {budget}, {cell(stage)}: {len(values)} {evidence} "
                         f"calls, {number(min(values))}–{number(max(values))} s.")
    else:
        lines.append("Stage measurements are pending.")
    lines.append("No scaling curve or exponent is fitted. The small, purposively selected set changes map structure "
                 "alongside box count, routes can differ, and budget-limited stages cannot supply uncensored solve "
                 "times. The remaining real elapsed stage times support descriptive ranges, not a defensible "
                 "box-count complexity exponent. Route wall time includes instrumentation, in-route checkers, "
                 "and Python overhead; supplemental baselines and fresh audits are shown separately. Overlapping "
                 "or nested stage events must not be added as independent work.")
    return lines


def render(root):
    manifest = read(root / "manifest.json", {})
    manifest_hash = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest() if manifest else None
    status = read(root / "supervisor-status.json", {})
    control = read(root / "control.json", {})
    paused_discard = control.get("action") == "stop" and control.get("discard_overloaded_window") is True
    expected = jobs(manifest)
    records, issues = [], []
    for budget, item in expected:
        path = root / "runs" / budget / str(map_id(item)) / "result.json"
        try:
            # A stopped, invalid window contributes no outcome or measurement,
            # including results written by workers while the supervisor drains.
            record = None if paused_discard else read(path)
        except (ValueError, OSError) as exc:
            record = None
            issues.append(f"{budget}/{map_id(item)}: unreadable result: {exc}")
        records.append((budget, item, record, path))
        if record:
            issues.extend(f"{budget}/{map_id(item)}: {issue}" for issue in issues_for(record))
            issues.extend(f"{budget}/{map_id(item)}: {issue}"
                          for issue in provenance_issues(record, item, manifest, manifest_hash))
            if record.get("budget") != budget:
                issues.append(f"{budget}/{map_id(item)}: result budget differs from its directory")
    completed = sum(record is not None for _, _, record, _ in records)
    final = (not paused_discard and len(expected) == 15 and completed == 15 and not issues and not status.get("errors")
             and not status.get("error")
             and status.get("phase") == "complete")
    lines = ["# Frontier 0.9.0 above 22 boxes", "",
             f"Status: **{'PAUSED — RESTART REQUIRED' if paused_discard else 'FINAL' if final else 'IN PROGRESS / NOT FINAL'}**. "
             f"{completed}/{len(expected) or 15} usable result records; supervisor phase: "
             f"{cell(status.get('phase', 'not started'))}. Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.", "",
             "The maps and budget assignments were frozen in [manifest.json](manifest.json) before planner runs. "
             "Seed 0; base search 6 s per box without a cap and finisher 600 s per start; ×4 search 24 s per box "
             "and finisher 2,400 s per start. Frontier source is unedited. Routes are recorded per row. "
             "CPU only, with one native thread per library.", "",
             "Map scheduling is separate from the frozen experimental manifest: all 12 base maps run before "
             "the three ×4 maps, each budget sorted by (box count, map id). The supervisor allows two concurrent "
             "map workers until `experiments/mvp/hybrid/HYBRID.md` is FINAL, then three, with admission load "
             "threshold 36. The inner solver worker counts in the unedited Frontier routes remain unchanged "
             "(two for the slot master, three for the column master); map concurrency is not a cap on the "
             "sum of inner solver workers. See [scheduling.json](scheduling.json) and "
             "[supervisor-status.json](supervisor-status.json) for the scheduling policy and live state.", "",
             "Routing is an explicit extrapolation from existing callers: `plan_and_check` below 121 boxes and "
             "`plan_big` from 121 boxes, the smallest existing column-route case. Frontier has no automatic "
             "size cutoff. For `plan_big`, block-rule is a supplemental same-job comparator and is not injected "
             "into the Frontier route. Its elapsed time is separate from route wall time.", "",
             "Pass means the full checker accepted the saved plan again in a fresh process, with no repair, "
             "and mask legality and complete loading passed. Moves are weighted nonproductive moves from "
             "that audit. Origin is the recorded `tables.origin` content classification, not a `search-N` label. "
             "Two-step and block-rule below are rerun in the same job with the same finisher budget; no historical "
             "baseline is substituted. `not produced` means no finishable candidate was recorded, not a checker pass.", "",
             "Wall is route elapsed time; `+ supplemental` and `+ audit` show additional measured seconds outside "
             "that route. Stage events marked supplemental refer to the separate same-job block-rule comparator.", "",
             "| Map | Budget | Boxes | Route | Pass | Moves | Winning origin | Wall s | Stage s | Stage limits hit | Two-step: pass; moves | Block-rule: pass; moves |",
             "| --- | --- | ---: | --- | --- | ---: | --- | ---: | --- | --- | --- | --- |"]
    if paused_discard:
        lines[4:4] = [
            "**The overloaded execution window is invalid. All of its partial or completed run records, "
            "including later output from draining workers, are quarantined from analysis and discarded "
            "as study measurements.** No passes, move counts, wall times, stage times, or baseline outcomes "
            "from that window are included below. The manifest and frozen runtime remain unchanged.", "",
            "[control.json](control.json) requests stop and discard. No new maps may be admitted and no "
            "restart is authorized until the leader explicitly instructs it. Existing owned workers drain "
            "before their run folders are archived. All 15 study jobs require a fresh authorized run.", ""]
    for budget, item, record, path in records:
        ident = cell(map_id(item))
        if record is None:
            pending = "pending; restart required" if paused_discard else "pending"
            values = [ident, budget, item.get("n_boxes", "—"), item.get("route", "—"), pending, "—", "—", "—", "—", "—", pending, pending]
        else:
            pass_text = "yes" if accepted(record) else "audit unresolved" if record.get("passed") else "error" if record.get("error") else "no"
            timings, limits = stage_cells(record)
            wall = number(record.get("wall_s"))
            if record.get("supplemental_wall_s") is not None:
                wall += " + " + number(record["supplemental_wall_s"]) + " supplemental"
            audit_seconds = record.get("audit_wall_s")
            if audit_seconds is None:
                audit_seconds = sum(a.get("audit_wall_s", a.get("seconds", 0)) or 0 for a in record.get("audits", []))
            if record.get("audits"):
                wall += " + " + number(audit_seconds) + " audit"
            values = [f"[{ident}]({path.relative_to(root).as_posix()})", budget, record.get("n_boxes", item.get("n_boxes", "—")),
                      cell(record.get("route", item.get("route", "—"))), pass_text,
                      number(record.get("weighted")) if accepted(record) else "—",
                      cell(record.get("origin", "unrecorded")) if accepted(record) else "—", wall,
                      timings, limits, baseline(record, "twostep")[0], baseline(record, "blocked")[0]]
        lines.append("| " + " | ".join(str(v) for v in values) + " |")
    lines.extend(["", "## Timing and scaling", ""])
    timing_paragraphs = (["All timings from the overloaded window are invalid and quarantined. No measured "
                          "timing ranges, scaling curve, or exponent are reported. Timing conclusions require "
                          "a fresh authorized execution of the fixed study."]
                         if paused_discard else timing_text(records))
    for paragraph in timing_paragraphs:
        lines.extend([paragraph, ""])
    lines.extend(["## Quality and the larger budget", ""])
    quality_paragraphs = (["All overloaded-window outcomes are excluded, including any saved passing certificates. "
                           "Whether quality holds with box count and whether ×4 buys fewer moves remain unanswered. "
                           "There is no usable base or ×4 result, and no same-run baseline comparison is reported."]
                          if paused_discard else quality_text(records))
    for paragraph in quality_paragraphs:
        lines.extend([paragraph, ""])
    lines.extend(["## Selection and limitations", "",
                  "Eight of the 12 maps were chosen because the selected compact simpler planner passed in the "
                  "fill sweep at the fixed 14:30 EDT snapshot. This enriches the sample for feasible, easier maps "
                  "and biases success upward relative to the full fill sweep. The other four are the first sorted "
                  "nonpassing id in each of feeder_800-f40, feeder_500-f60, feeder_1700-f40, and feeder_800-f60. "
                  "Those four reduce, but do not remove, the selection effect. The ×4 comparison is further limited "
                  "to the lowest-id passing map at each of 59, 74, and 108 boxes. No conclusion is generalized "
                  "to unseen maps or a population pass rate.", ""])
    if paused_discard:
        lines.extend(["The study is paused, with all 15 jobs pending an explicit restart instruction. "
                      "This report is not final and contains no measurements from the discarded window.", ""])
    elif not final:
        lines.extend(["This report remains provisional until all 15 records are present, all claimed passes have "
                      "matching complete audits, and the supervisor marks the study complete.", ""])
    if issues:
        lines.extend(["Unresolved errors or audit issues:", ""])
        lines.extend("- " + cell(issue) for issue in issues)
        lines.append("")
    errors = status.get("errors", status.get("error"))
    if errors:
        lines.extend(["Supervisor errors: " + cell(errors), ""])
    target = root / "SCALE.md"
    tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
    tmp.write_text("\n".join(lines))
    tmp.replace(target)
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE)
    parser.add_argument("--require-final", action="store_true")
    args = parser.parse_args()
    final = render(args.root.resolve())
    if args.require_final and not final:
        raise SystemExit("SCALE.md is provisional: results, audits, or supervisor completion are outstanding")


if __name__ == "__main__":
    main()

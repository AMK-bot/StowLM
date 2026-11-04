"""Lightweight reports for the 900-second, process-isolated live campaign.

The supervisor authenticates the frozen reused rows once. This module only reads
that snapshot and durable final job records; it never replans or rechecks them.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import statistics
import time
import uuid

HERE = Path(__file__).resolve().parents[1]
KINDS = ("baseline", "close_bay", "max_tiers", "dg_keep_away", "one_port_per_hatch")
DEADLINE_S = 900
WORKERS = 3


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def median(values):
    values = [value for value in values if number(value)]
    return statistics.median(values) if values else None


def fmt(value):
    return f"{value:.1f}" if number(value) else "—"


def is_deadline(row):
    return row.get("status") == "deadline_exceeded" or row.get("deadline_exceeded") is True


def checked_incumbent(row):
    # A saved plan alone is insufficient: worker records this only after audits.
    return (row.get("deadline_with_incumbent") is True
            or row.get("live_outcome") == "checked_incumbent_inside_live_budget"
            or (is_deadline(row) and row.get("passed") is True))


def first_checked(row):
    value = row.get("first_checked_wall_s")
    if value is None:
        value = (row.get("evidence") or {}).get("first_checked_wall_s")
    return value if number(value) else None


def _rows(value):
    if isinstance(value, list):
        return value
    return value.get("rows", []) if isinstance(value, dict) else []


def load_rows(output):
    """Choose one final record per map/type; retain duplicate diagnostics."""
    output = Path(output)
    snapshot = read_json(output / "reused.json", {})
    records = [(dict(row), "reused") for row in _rows(snapshot)]
    malformed = []
    for path in sorted((output / "jobs").glob("*.json")):
        row = read_json(path)
        if not isinstance(row, dict) or not row.get("map") or row.get("rule_type") not in KINDS:
            malformed.append(path.name)
            continue
        records.append((dict(row), "new"))
    chosen, duplicates = {}, []
    for row, origin in records:
        if not row.get("map") or row.get("rule_type") not in KINDS:
            malformed.append("reused:" + str(row.get("key", "unknown")))
            continue
        row["report_origin"] = origin
        logical = (row.get("cell", "medium"), row["map"], row["rule_type"])
        row.setdefault("cell", "medium")
        if logical in chosen:
            prior = chosen[logical]
            duplicates.append(dict(map=row["map"], rule_type=row["rule_type"],
                                   prior_key=prior.get("key"), replacement_key=row.get("key")))
            # New finished attempts supersede the frozen snapshot. Within an
            # origin use the final timestamp, then deterministic filename order.
            if prior["report_origin"] == "new" and origin == "reused":
                continue
            if prior["report_origin"] == origin:
                old_time = prior.get("finished_at", prior.get("completed_at", 0)) or 0
                new_time = row.get("finished_at", row.get("completed_at", 0)) or 0
                if number(old_time) and number(new_time) and old_time > new_time:
                    continue
        chosen[logical] = row
    return list(chosen.values()), snapshot, duplicates, malformed


def _selected(manifest, cell):
    key = "selected_medium" if cell == "medium" else "opportunistic"
    records = manifest.get(key, [])
    return {record.get("path", record.get("map")) if isinstance(record, dict) else record
            for record in records if not isinstance(record, dict) or record.get("cell", cell) == cell}


def comparison(row, baseline, sidecars):
    if row["rule_type"] == "baseline":
        return "not_applicable"
    if baseline is None or baseline.get("passed") is not True:
        return "unknown"
    details = sidecars.get(row.get("key"), row.get("comparison") or {})
    value = details.get("binding_classification", row.get("binding_classification"))
    violations = details.get("baseline_instruction_violations", row.get("baseline_instruction_violations"))
    if violations is not None:
        return "binding" if violations else "already_obeyed"
    return value if value in ("binding", "already_obeyed") else "unknown"


def _statistics(rows, baselines, sidecars):
    passes = [row for row in rows if row.get("passed") is True]
    deadline = [row for row in rows if is_deadline(row)]
    classifications = Counter(comparison(row, baselines.get((row["cell"], row["map"])), sidecars)
                              for row in passes)
    pairs = [(row.get("lifts"), baselines[(row["cell"], row["map"])].get("lifts"))
             for row in passes if row["rule_type"] != "baseline"
             and (row["cell"], row["map"]) in baselines
             and baselines[(row["cell"], row["map"])].get("passed") is True]
    pairs = [(a, b) for a, b in pairs if number(a) and number(b)]
    times = [row.get("wall_s") for row in rows if number(row.get("wall_s"))]
    return dict(final=len(rows), reused=sum(row["report_origin"] == "reused" for row in rows),
                full_passes=len(passes), relaxed=sum(row.get("status") == "relaxed_plan"
                                                  or (row.get("evidence") or {}).get("status") == "relaxed_plan"
                                                  for row in rows if row.get("passed") is not True),
                deadlines_with_checked=sum(checked_incumbent(row) for row in deadline),
                deadlines_without_checked=sum(not checked_incumbent(row) for row in deadline),
                binding_passes=classifications["binding"], already_obeyed_passes=classifications["already_obeyed"],
                unknown_passes=classifications["unknown"],
                baseline_also_failed=sum(row["rule_type"] != "baseline"
                                         and (row["cell"], row["map"]) in baselines
                                         and baselines[(row["cell"], row["map"])].get("passed") is not True
                                         and row.get("passed") is not True for row in rows),
                first_checked_median_s=median([first_checked(row) for row in rows]),
                wall_median_s=median(times), wall_max_s=max(times) if times else None,
                paired_n=len(pairs), paired_lifts_median=median([a for a, b in pairs]),
                paired_baseline_lifts_median=median([b for a, b in pairs]))


def build_report(output, *, now=None):
    output = Path(output)
    now = time.time() if now is None else now
    manifest = read_json(output / "campaign.json", {})
    rows, snapshot, duplicates, malformed = load_rows(output)
    selected = _selected(manifest, "medium")
    # Fixed selected-map coverage is required for completion, rather than a
    # pooled row count that could hide a missing map or duplicated rule.
    cohort_rows = [row for row in rows if row["cell"] != "medium" or not selected or row["map"] in selected]
    medium = [row for row in cohort_rows if row["cell"] == "medium"]
    coverage = {path: sorted(row["rule_type"] for row in medium if row["map"] == path)
                for path in sorted(selected)}
    complete = len(selected) == 8 and all(set(kinds) == set(KINDS) for kinds in coverage.values())
    baselines = {(row["cell"], row["map"]): row for row in cohort_rows if row["rule_type"] == "baseline"}
    sidecars = {}
    for path in sorted((output / "comparisons").glob("*.json")):
        value = read_json(path, {})
        if value.get("row_key"):
            sidecars[value["row_key"]] = value
    groups = defaultdict(list)
    for row in cohort_rows:
        groups[row["cell"], row["rule_type"]].append(row)
    stats = {cell + ":" + kind: _statistics(groups[cell, kind], baselines, sidecars)
             for cell in ("medium", "tiny", "small") for kind in KINDS}
    remaining = max(0, 40 - len(medium))
    # Requested coarse scheduling projection, not an empirical completion time.
    eta_hours = remaining * DEADLINE_S / WORKERS / 3600
    active = read_json(output / "active.json", {})
    status = dict(observed_at=now, reporter_pid=os.getpid(), medium_target=40,
                  medium_finalized=len(medium), medium_remaining=remaining, medium_complete=complete,
                  reused_rows=sum(row["report_origin"] == "reused" for row in cohort_rows),
                  new_rows=sum(row["report_origin"] == "new" for row in cohort_rows),
                  medium_coverage=coverage, selected_medium_count=len(selected), statistics=stats,
                  duplicate_records=duplicates, malformed_records=malformed, active=active,
                  eta_scheduling_hours=eta_hours, eta_admission_additional=True,
                  row_keys=[row.get("key") for row in cohort_rows],
                  reused_snapshot_sources=snapshot.get("sources", []) if isinstance(snapshot, dict) else [])
    lines = ["# Development live run — 900-second deadline", "",
             f"{len(medium)}/40 medium rows finalized ({status['reused_rows']} preserved rows; "
             f"{status['new_rows']} new rows across all cells). Three process-isolated worker slots. "
             "Eight public r0 development strata, baseline plus four confirmed rule types; "
             "this is a structured-rule diagnostic, not Reader accuracy, sealed evaluation, or whole-ship evidence.", "",
             "New attempts have a 900s process-group deadline; requested live-v2 search/finish budgets are90s/30s. "
             "Preserved rows retain their original timing and provenance. Fleet admission is additional. "
             "Deadline outcomes distinguish an independently checked instruction-obeying incumbent from no checked plan "
             "inside the live budget. A deadline or unsuccessful search is not proof of impossibility. "
             "Only full checker passes obeying every instruction count as passes; relaxed plans are separate.", "",
             "| Cell | Rule | Final | Preserved | Full pass | Relaxed | Deadline + checked | Deadline, no checked | Binding pass | Already obeyed pass | Unknown pass | First checked median s | Final median/max s | Paired lifts / baseline |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for cell in ("medium", "tiny", "small"):
        for kind in KINDS:
            item = stats[cell + ":" + kind]
            if cell != "medium" and not any(stats[cell + ":" + k]["final"] for k in KINDS):
                continue
            counts = (item['binding_passes'], item['already_obeyed_passes'], item['unknown_passes'])
            bind = [str(value) for value in counts] if kind != "baseline" else ["—"] * 3
            pairs = f"{fmt(item['paired_lifts_median'])} / {fmt(item['paired_baseline_lifts_median'])} (n={item['paired_n']})"
            lines.append(f"|{cell}|{kind}|{item['final']}|{item['reused']}|{item['full_passes']}|{item['relaxed']}|"
                         f"{item['deadlines_with_checked']}|{item['deadlines_without_checked']}|{'|'.join(bind)}|"
                         f"{fmt(item['first_checked_median_s'])}|{fmt(item['wall_median_s'])}/{fmt(item['wall_max_s'])}|{pairs}|")
    lines += ["", "A binding pass requires the matched passed baseline to violate that exact rule. "
              "Without a passed baseline, binding is unknown. Paired lifts use the same mutually passing maps; "
              "lift spread is search variation, not causal instruction cost or saving. Best means best found at this work budget.", "",
              f"Remaining medium scheduling projection: {remaining}×900s/3 = {eta_hours:.2f}h, plus admission "
              "and baseline dependencies. At30 remaining rows this is2.5h of slot scheduling and may take about3h "
              "before additional admission waits; it is not a promised finish time.", "",
              "Tiny/small jobs are opportunistic after the medium queue; a tiny single-hatch section with multiple "
              "discharge ports is not a useful hatch-allocation test. No pending outcome is counted as final."]
    return "\n".join(lines) + "\n", status


def refresh(output):
    output = Path(output).resolve()
    if not output.is_relative_to(HERE.resolve()):
        raise ValueError("deadline report output must remain under binding")
    text, status = build_report(output)
    atomic_write(output / "status.json", json.dumps(status, indent=2, sort_keys=True) + "\n")
    for path in (output / "RESULTS.md", HERE / "RESULTS.md", HERE / "RESULTS-LIVE.md"):
        atomic_write(path, text)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(refresh(args.out), sort_keys=True))


if __name__ == "__main__":
    main()

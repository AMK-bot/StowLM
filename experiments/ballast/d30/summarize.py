"""Aggregate D30 JSON records without importing or running ship physics.

Only independently verified full-check passes count as validated success.
A failed reference condition is not an infeasibility certificate. The second
finisher invocation has warm disk caches; worker reuse is reported separately
where recorded, since timing out can discard an isolated process.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
PHYSICS = ("stability", "strength", "visibility", "lashing")


def read(path):
    return json.loads(path.read_text()) if path.exists() else []


def part_pass(part):
    return bool(part and part.get("ran") and not part.get("error") and not any(
        violation.get("severity", "hard") == "hard" for violation in part.get("violations", [])))


def physics_pass(parts):
    return all(part_pass(parts.get(name)) for name in PHYSICS)


def timing(calls):
    times = [call["wall_s"] for call in calls if isinstance(call.get("wall_s"), (int, float))]
    return dict(count=len(times), median_s=median(times) if times else None,
                max_s=max(times) if times else None,
                above_5s=sum(value > 5. for value in times))


def call_stats(calls):
    return dict(calls=len(calls), statuses=dict(Counter(call.get("status", "missing") for call in calls)),
                validated_successes=sum(call.get("full_check_passed") is True for call in calls),
                timings=timing(calls), warm_timings=timing([call for call in calls if call.get("warm")]))


def literal_rows(path):
    result = []
    for row in read(path):
        phases = row.get("phases", [])
        final = next((phase for phase in phases if phase.get("phase") == "result"), None)
        witness = next((phase for phase in phases if phase.get("phase") == "reference_witness"), {})
        calls = (final or {}).get("calls")
        if calls is None:
            calls = [phase for phase in phases if phase.get("phase") == "finished"]
        result.append(dict(fleet=row.get("vessel"), condition=row.get("condition"),
            complete=final is not None, worker_error=row.get("exit_code") != 0,
            watchdog_timeout=row.get("watchdog_timeout", False),
            witness_full_pass=witness.get("passed"),
            witness_timeout=witness.get("timed_out", False),
            witness_s=witness.get("wall_s"),
            witness_physics_pass=(physics_pass(witness.get("parts", {})) if witness.get("parts") else None),
            calls=[dict(**call, full_check_passed=call.get("passed")) for call in calls]))
    return result


def slice_rows(path):
    rows = []
    for row in read(path):
        baseline = row.get("baseline", {})
        parts = baseline.get("report", {}).get("parts", {})
        rows.append(dict(id=row.get("id", f'{row.get("generator")}-{row.get("index")}'),
            generator=row.get("generator"), fleet=row.get("fleet", "unknown"),
            generator_version=row.get("generator_version"), error=row.get("error"),
            construction_failed=row.get("construction_failed", False),
            legal=row.get("legal") is True, static_pass=part_pass(row.get("static")),
            baseline_status=baseline.get("status", "missing"),
            baseline_full_pass=baseline.get("passed"),
            baseline_physics_available=(all(parts.get(name, {}).get("ran") and not parts.get(name, {}).get("error")
                for name in PHYSICS) if parts else None),
            baseline_physics_pass=physics_pass(parts) if parts else None,
            baseline_s=baseline.get("wall_s"),
            calls=[dict(repeat=call.get("repeat"), warm=call.get("warm"), wall_s=call.get("wall_s"),
                status=call.get("outcome", {}).get("status", "missing"),
                iterations=call.get("outcome", {}).get("iterations"),
                full_check_passed=call.get("full_checker_passed"),
                full_checker_status=call.get("full_checker", {}).get("status", "not_run"))
                for call in row.get("calls", [])]))
    return rows


def group_stats(rows):
    calls = [call for row in rows for call in row["calls"]]
    return dict(instances=len(rows), legal=sum(row.get("legal", False) for row in rows),
        static_pass=sum(row.get("static_pass", False) for row in rows),
        baseline_full_pass=sum(row.get("baseline_full_pass") is True for row in rows),
        baseline_physics_pass=sum(row.get("baseline_physics_pass") is True for row in rows),
        baseline_physics_available=sum(row.get("baseline_physics_available") is True for row in rows),
        baseline_timeouts=sum(row.get("baseline_status") == "timeout" for row in rows),
        baseline_errors=sum(row.get("baseline_status") == "error" for row in rows),
        generation_or_construction_errors=sum(bool(row.get("error") or row.get("construction_failed")) for row in rows),
        output_check_timeouts=sum(call.get("full_checker_status") == "timeout" for call in calls),
        **call_stats(calls))


def fmt(value):
    return "—" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)


def verdict(passed, timed_out=False):
    return "timeout" if timed_out else "pass" if passed is True else "fail" if passed is False else "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE)
    parser.add_argument("--expected-slices", type=int, default=20)
    args = parser.parse_args()
    root = args.root
    summary = dict(reviewer={}, slices={})
    text = ["# D30 ballast measurements", "",
        "Read-only aggregation of experiment JSON. The second invocation has warm disk caches; "
        "a worker process is warm only when it survives and is reused. A finisher status of `ok` "
        "counts as validated success only after an independent full-check pass. "
        "`not_found` and failed witnesses never prove infeasibility.", ""]
    for name, label in (("literal", "Literal reviewer C1–C3"), ("manual", "Optional loading-manual budget")):
        path = root / f"{name}.json"
        if not path.exists():
            continue
        rows = literal_rows(path)
        calls = [call for row in rows for call in row["calls"]]
        summary["reviewer"][name] = dict(conditions=len(rows),
            complete=sum(row["complete"] for row in rows),
            witness_full_pass=sum(row["witness_full_pass"] is True for row in rows),
            witness_physics_pass=sum(row["witness_physics_pass"] is True for row in rows),
            witness_timeouts=sum(row["witness_timeout"] for row in rows),
            worker_errors=sum(row["worker_error"] for row in rows),
            watchdog_timeouts=sum(row["watchdog_timeout"] for row in rows),
            groups_by_fleet={fleet:call_stats([call for row in rows if row["fleet"] == fleet for call in row["calls"]])
                for fleet in sorted({row["fleet"] for row in rows})},
            rows=rows, **call_stats(calls))
        text.extend([f"## {label}", "", "| Fleet | Condition | First status / s | Second status / s | Worker reused | Witness full / physics | Witness s |",
            "|---|---|---|---|---|---|---:|"])
        for row in rows:
            descriptions = [f'{call["status"]} / {fmt(call.get("wall_s"))}' for call in row["calls"]]
            descriptions += ["missing"] * max(0, 2-len(descriptions))
            reused = row["calls"][1].get("worker_reused") if len(row["calls"]) > 1 else None
            text.append(f'| {row["fleet"]} | {row["condition"]} | {descriptions[0]} | {descriptions[1]} | {fmt(reused)} | '
                f'{verdict(row["witness_full_pass"],row["witness_timeout"])} / {verdict(row["witness_physics_pass"],row["witness_timeout"])} | {fmt(row["witness_s"])} |')
        stats = summary["reviewer"][name]
        text.extend(["", f'Completed {stats["complete"]}/{stats["conditions"]} conditions; '
            f'validated success {stats["validated_successes"]}/{stats["calls"]} calls; '
            f'witness full passes {stats["witness_full_pass"]}, witness physics passes {stats["witness_physics_pass"]}, '
            f'witness timeouts {stats["witness_timeouts"]}. Warm median/max: '
            f'{fmt(stats["warm_timings"]["median_s"])} / {fmt(stats["warm_timings"]["max_s"])} s.', ""])
    rows = slice_rows(root / "slices_results/results.json")
    summary["slices"] = dict(expected_instances=args.expected_slices,
        complete=len(rows) == args.expected_slices, overall=group_stats(rows),
        by_generator={key:group_stats([row for row in rows if row["generator"] == key]) for key in sorted({row["generator"] for row in rows})},
        by_fleet={key:group_stats([row for row in rows if row["fleet"] == key]) for key in sorted({row["fleet"] for row in rows})},
        by_generator_and_fleet={f'{gen}/{fleet}':group_stats([row for row in rows if row["generator"] == gen and row["fleet"] == fleet])
            for gen, fleet in sorted({(row["generator"],row["fleet"]) for row in rows})}, rows=rows)
    text.extend(["## Reconditioned slices", "",
        f'Checkpoint contains {len(rows)}/{args.expected_slices} expected instances. '
        f'{"Complete." if len(rows)==args.expected_slices else "INCOMPLETE: rerun aggregation after the benchmark finishes."}', "",
        "| Instance | Legal / static | Baseline full / physics | Call | Status | Seconds | Iterations | Output full check |",
        "|---|---|---|---|---|---:|---:|---|"])
    for row in rows:
        for call in row["calls"] or [{}]:
            text.append(f'| {row["id"]} | {row["legal"]} / {row["static_pass"]} | '
                f'{verdict(row["baseline_full_pass"],row["baseline_status"]=="timeout")} / '
                f'{verdict(row["baseline_physics_pass"],row["baseline_status"]=="timeout")} | '
                f'{"second" if call.get("warm") else "first"} | {call.get("status",row.get("error") or "construction failed")} | '
                f'{fmt(call.get("wall_s"))} | {fmt(call.get("iterations"))} | {verdict(call.get("full_check_passed"),call.get("full_checker_status")=="timeout")} |')
    text.extend(["", "## Slice group totals", "", "| Group | Instances | Legal / static | Physics available | Baseline full / physics | Baseline timeouts / errors | Validated / calls | Warm median / max s | Over 5 s |",
        "|---|---:|---|---:|---|---|---|---|---:|"])
    groups = {"all":summary["slices"]["overall"], **summary["slices"]["by_generator"],
        **summary["slices"]["by_fleet"], **summary["slices"]["by_generator_and_fleet"]}
    for name, stats in groups.items():
        text.append(f'| {name} | {stats["instances"]} | {stats["legal"]} / {stats["static_pass"]} | '
            f'{stats["baseline_physics_available"]} | '
            f'{stats["baseline_full_pass"]} / {stats["baseline_physics_pass"]} | '
            f'{stats["baseline_timeouts"]} / {stats["baseline_errors"]} | '
            f'{stats["validated_successes"]} / {stats["calls"]} | '
            f'{fmt(stats["warm_timings"]["median_s"])} / {fmt(stats["warm_timings"]["max_s"])} | {stats["timings"]["above_5s"]} |')
    (root / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    (root / "measurements.md").write_text("\n".join(text)+"\n")
    print(json.dumps(dict(literal=summary["reviewer"].get("literal", {}).get("warm_timings"), slices=summary["slices"]["overall"])))


if __name__ == "__main__":
    main()

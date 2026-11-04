"""Six real generator backgrounds: persist every independent full-checker part.

Run with one BLAS thread and uv Python3.12. Final matrix files are read only.
Selection is the first accepted random-yard seed per ship × tiny/small cell.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import resource
import sys
import time

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
sys.path.insert(0, str(PARENT))
from measure import sources
from stowage.checker import check
from stowage.generate.background_acceptance import background_instance, evaluate_background
from stowage.generate.random_yard import generate_random_instance, RandomGenerationReport
from stowage.model import MovePlan
from stowage.model.plan import BallastCondition


def phase(part):
    return next((name for name in ("departure", "mid_voyage", "arrival")
                 if f"condition.phase: {name}" in part.metrics), None)


def main():
    started = time.perf_counter()
    start_utc = datetime.now(timezone.utc).isoformat()
    print(f"part-margin worker pid={os.getpid()} start={start_utc}", flush=True)
    rows = [json.loads(line) for line in (PARENT / "final-draws.jsonl").read_text().splitlines()]
    selected = [next(row for row in rows if row["generator"] == "random"
                     and row["vessel"] == vessel and row["rung"] == rung and row["accepted"])
                for vessel in ("feeder_500", "feeder_800", "feeder_1700")
                for rung in ("tiny", "small")]
    source_start = sources()[0]
    matrix_source = selected[0]["source_start"]
    old_manifest = json.loads((PARENT / "sources" / f"{matrix_source}.json").read_text())
    current_manifest = json.loads((PARENT / "sources" / f"{source_start}.json").read_text())
    source_differences = [path for path in sorted(set(old_manifest["sources"]) | set(current_manifest["sources"]))
                          if old_manifest["sources"].get(path) != current_manifest["sources"].get(path)]
    results = []
    strength = defaultdict(list)
    visibility = defaultdict(list)
    zero_margins = []
    negative_margins = []
    for row in selected:
        tick = time.perf_counter()
        stem = f"{row['vessel']}-{row['rung']}-seed{row['seed']}"
        audit = RandomGenerationReport()
        instance = generate_random_instance(row["rung"], seed=row["seed"],
            vessel_name=row["vessel"], max_attempts=1, report=audit)
        generation_s = time.perf_counter()-tick
        accepted = evaluate_background(instance)
        assert accepted.accepted, accepted.reasons
        background = background_instance(instance)
        adjustable = {tank.id for tank in background.vessel.tanks if tank.adjustable}
        plan = MovePlan(id="part-margin-empty", instance_id=background.id,
            call_index=background.call_index, moves=[], ballast=BallastCondition(
                tank_masses_kg={key: value for key, value in accepted.tank_masses_kg.items()
                                if key in adjustable}, source="finisher"))
        check_started = time.perf_counter()
        report = check(background, plan)
        assert report.passed and all(part.ran and not part.error and not part.violations
                                     for part in report.parts.values()), report.violations
        (HERE / f"{stem}.instance.json").write_text(instance.model_dump_json(indent=2)+"\n")
        (HERE / f"{stem}.plan.json").write_text(plan.model_dump_json(indent=2)+"\n")
        (HERE / f"{stem}.report.json").write_text(report.model_dump_json(indent=2)+"\n")
        state_summary = {}
        for name, part in report.parts.items():
            for key, value in part.margins.items():
                item = dict(file=stem, part=name, key=key, value=value)
                if abs(value) <= 1e-9:
                    item["classification"] = ("Boolean applicability boundary: value1 required>=1; zero is a passing flag, not a physical clearance."
                        if key.endswith("weather_table_applicability") else
                        "Zero/equality or numerical boundary; inspect the saved part and its units. Not claimed as positive physical clearance.")
                    zero_margins.append(item)
                elif value < -1e-9:
                    negative_margins.append(item)
            if not name.startswith("condition:"):
                continue
            state = phase(part)
            state_summary.setdefault(state, {})
            if name.endswith(":strength"):
                values = {key: part.metrics[key] for key in ("max_sf_ratio", "max_bm_ratio")}
                values.update({f"margin_{key}": part.margins[key] for key in ("sf_ratio", "bm_ratio")})
                state_summary[state]["strength"] = values
                for key, value in values.items():
                    strength[key].append(value)
            if name.endswith(":visibility"):
                values = {"blind_distance_m": part.metrics["blind_distance_m"],
                          "limit_m": part.metrics["limit_m"],
                          **{f"margin_{key}": value for key, value in part.margins.items()}}
                state_summary[state]["visibility"] = values
                for key, value in values.items():
                    visibility[key].append(value)
        record = dict(vessel=row["vessel"], rung=row["rung"], seed=row["seed"],
            generation_s=generation_s, independent_check_s=time.perf_counter()-check_started,
            wall_s=time.perf_counter()-tick, passed=report.passed,
            report=f"{stem}.report.json", all_parts=len(report.parts),
            states=state_summary, generation_report=asdict(audit))
        results.append(record)
        (HERE / "results.jsonl").open("a").write(json.dumps(record, sort_keys=True)+"\n")
        print(stem, f"{record['wall_s']:.2f}s", "all parts PASS", state_summary, flush=True)
    summary = dict(source_start=source_start, source_end=sources()[0], matrix_source=matrix_source,
        source_matches_matrix=source_start == matrix_source, changed_files_since_matrix=source_differences,
        command="OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/background-1028/part-margins/collect.py",
        raw_instances=len(results), checked_d57_states=3*len(results), all_passed=all(r["passed"] for r in results),
        maximum_shear_force_utilisation=max(strength["max_sf_ratio"]),
        maximum_bending_moment_utilisation=max(strength["max_bm_ratio"]),
        minimum_shear_force_margin_ratio=min(strength["margin_sf_ratio"]),
        minimum_bending_moment_margin_ratio=min(strength["margin_bm_ratio"]),
        minimum_visibility_blind_distance_margin_m=min(visibility["margin_blind_distance_m"]),
        minimum_total_blind_sector_margin_deg=math.degrees(min(visibility["margin_total_blind_sector_rad"])),
        minimum_clear_sector_margin_deg=math.degrees(min(visibility["margin_min_clear_sector_rad"])),
        zero_margins=zero_margins, negative_margins=negative_margins,
        resources=dict(pid=os.getpid(), cores=1, reserved_ram_gb=3, gpu=False,
            start_utc=start_utc, finish_utc=datetime.now(timezone.utc).isoformat(),
            estimated_wall_s=180, actual_wall_s=time.perf_counter()-started,
            peak_self_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2),
            note="Main process only; reusable finisher child resources excluded."),
        results=results)
    assert summary["source_start"] == summary["source_end"]
    (HERE / "margin-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True)+"\n")
    print(json.dumps({key: value for key, value in summary.items() if key not in {"results", "zero_margins"}}, indent=2), flush=True)


if __name__ == "__main__":
    main()

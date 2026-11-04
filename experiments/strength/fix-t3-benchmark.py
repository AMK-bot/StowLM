"""Repeatable loaded-fleet strength/full-check timing for the task 3.2 fix.

ASSUMPTION: reuse the saved C1 historical-witness recipes, binary peak mapping
and explicit synthetic CSC ratings from fix-t2-vessel/recheck.py. This is a
performance comparison on identical conditions, not new class approval.
One CPU, <4 GB RAM, no GPU; warm cache only, never invokes cache_warm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from stowage.conventions import canonical_vessel_id
import resource
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "experiments/physics-review/convergence/post_d30"
sys.path[:0] = [str(REVIEW), str(ROOT / "experiments/ballast/effectiveness")]


def source_hashes():
    paths = [*sorted((ROOT / "src/stowage/checker").glob("strength*.py")),
             ROOT / "src/stowage/checker/full.py",
             ROOT / "src/stowage/checker/stability.py",
             Path(__file__).resolve(), REVIEW / "harness2.py"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    import harness2
    from regression import witness_masses
    from stowage.checker.full import _ballast, check
    from stowage.checker.strength import check_strength
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.moves import apply_plan
    from stowage.vessel.fleet import load_ship_params, load_vessel
    from stowage.versions import component_versions

    started = time.perf_counter()
    result = dict(source_hashes_before=source_hashes(), components=component_versions(),
                  repeats=args.repeats, warmups_per_callable=1,
                  compute=dict(cpu_threads=1, gpu=False, estimated_peak_ram_gb=1,
                               estimated_wall_s=90), rows=[])
    for name in ("feeder_500", "feeder_800", "feeder_1700"):
        source = next(path for path in REVIEW.glob("search_*_C1.json")
                      if canonical_vessel_id(json.loads(path.read_text())["ship"]) == name)
        historical = json.loads(source.read_text())
        candidate = historical.get("witness") or historical["candidates"][0]
        vessel = load_vessel(name)
        masses = witness_masses(vessel, candidate["label"])
        instance, info = harness2.build(name, vessel, load_ship_params(name), "C1", sum(masses.values()))
        for box in instance.containers:
            box.max_gross_mass_kg = 30480.
        plan = MovePlan(id=f"{instance.id}.strength-fix-benchmark", instance_id=instance.id,
                        call_index=instance.call_index,
                        ballast=BallastCondition(tank_masses_kg=masses, source="planner"))
        ship, problems = _ballast(instance, plan, apply_plan(instance, plan).final_state.ship)
        if problems:
            raise ValueError(problems)
        row = dict(ship=name, condition="C1", info=info, historical_label=candidate["label"],
                   instance_sha256=hashlib.sha256(instance.model_dump_json().encode()).hexdigest(),
                   plan_sha256=hashlib.sha256(plan.model_dump_json().encode()).hexdigest(),
                   source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        for label, call in (("strength", lambda: check_strength(instance, ship, call_index=instance.call_index)),
                            ("full", lambda: check(instance, plan))):
            before = time.perf_counter()
            report = call()
            cold_s = time.perf_counter() - before
            times = []
            for _ in range(args.repeats):
                before = time.perf_counter()
                report = call()
                times.append(time.perf_counter() - before)
            row[label] = dict(cold_s=cold_s, warm_seconds=times, median_warm_s=statistics.median(times),
                              report=report.model_dump(mode="json"))
            print(json.dumps(dict(ship=name, part=label, cold_s=cold_s,
                                  median_warm_s=row[label]["median_warm_s"])), flush=True)
        result["rows"].append(row)
        args.out.write_text(json.dumps(result, indent=2) + "\n")
    result.update(source_hashes_after=source_hashes(), elapsed_s=time.perf_counter()-started,
                  peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9)
    args.out.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()

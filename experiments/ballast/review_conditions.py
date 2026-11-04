"""Reproduce loading-manual C1-C3 with production fleet and bounded workers.

This imports the reviewer's condition builder unchanged. Each condition runs in
its own recorded subprocess, and a supervisor kills only that subprocess if its
watchdog expires. Two successive calls expose cold and warm finisher timings.
Build and full-check time are reported separately from the finisher budget.
Source: experiments/physics-review/design/harness.py (synthetic design conditions).
"""
from __future__ import annotations

import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
from stowage.conventions import canonical_vessel_id
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
os.environ.setdefault("STOWAGE_VESSEL_CACHE_DIR", str(ROOT / "experiments/ballast/.cache/vessels"))


def load_harness():
    path = ROOT / "experiments/physics-review/design/harness.py"
    spec = importlib.util.spec_from_file_location("ballast_review_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker(args):
    import faulthandler
    faulthandler.dump_traceback_later(45., repeat=True)
    harness = load_harness()
    from stowage.model import Instance, MovePlan
    from stowage.checker.full import check
    from stowage.solver.ballast import finish_ballast
    name, condition = args.worker
    name = canonical_vessel_id(name)
    started = time.perf_counter()
    print(json.dumps({"phase":"loading", "vessel":name, "condition":condition}), flush=True)
    # A ballast-version bump cannot alter an already assembled physical ship.
    # Reuse a saved condition only if params and every physical builder/model
    # source predate its snapshot. Frozen model validation still runs on load.
    physical_sources = [ROOT / "params/fleet" / f"{name}.toml"]
    for area in ("vessel", "model"):
        physical_sources.extend((ROOT / "src/stowage" / area).rglob("*.py"))
    physical_changed = max(p.stat().st_mtime_ns for p in physical_sources)
    condition_root = ROOT / "experiments/ballast/.cache/conditions"
    snapshots = sorted(condition_root.glob(f"{name}_{condition}_*.json"),
                       key=lambda p:p.stat().st_mtime_ns, reverse=True)
    reusable = next((p for p in snapshots if p.stat().st_mtime_ns >= physical_changed), None)
    params = harness._ORIGINAL_LOAD(name)
    if reusable is not None:
        payload = json.loads(reusable.read_text())
        instance, info = Instance.model_validate(payload["instance"]), payload["info"]
        vessel = instance.vessel
        info["snapshot_reused"] = True
    else:
        vessel, params = harness.apply_overrides(name, None)
        print(json.dumps({"phase":"loaded", "wall_s":time.perf_counter()-started}), flush=True)
        fingerprint = hashlib.sha256((vessel.model_dump_json()+repr(params)).encode()).hexdigest()[:16]
        cached = condition_root / f"{name}_{condition}_{fingerprint}.json"
        instance, info = harness.build_condition(name, vessel, params, condition)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps({"instance":instance.model_dump(mode="json"), "info":info}))
    import stowage.linear.cache as lc
    lc._DEFAULT_ROOT = ROOT / "experiments/ballast/.cache/linear"
    from stowage.versions import component_versions
    info.update(vessel=name, components=component_versions(), params_sha256=hashlib.sha256(repr(params).encode()).hexdigest(), build_s=time.perf_counter()-started,
                adjustable_tanks=sum(t.adjustable for t in vessel.tanks),
                ballast_capacity_m3=sum(t.capacity_m3 for t in vessel.tanks if t.adjustable))
    print(json.dumps({"phase": "built", **info}), flush=True)
    plan = MovePlan(id=f"{instance.id}.ballast_review", instance_id=instance.id,
                    call_index=instance.call_index)
    records = []
    if args.build_only:
        return
    if args.probe:
        from stowage.checker.stability import check_stability
        from stowage.checker.strength import check_strength
        for label, part in (("stability", check_stability), ("strength", check_strength)):
            for repeat in range(2):
                started = time.perf_counter()
                report = part(instance, instance.onboard, call_index=instance.call_index)
                print(json.dumps({"phase":"probe", "part":label, "warm":repeat>0,
                                  "wall_s":time.perf_counter()-started, "ran":report.ran,
                                  "violations":[v.rule_id for v in report.violations]}), flush=True)
        return
    for repeat in range(args.repeat):
        started = time.perf_counter()
        outcome = finish_ballast(instance, plan, time_limit_s=args.limit, seed=0)
        record = dict(repeat=repeat, warm=repeat>0, status=outcome.status,
                      wall_s=time.perf_counter()-started,
                      reported_wall_s=outcome.wall_time_s, iterations=outcome.iterations,
                      proof=outcome.proof,
                      violations={k:[v.rule_id for v in p.violations] for k,p in outcome.parts.items()},
                      margins={k:dict(p.margins) for k,p in outcome.parts.items()})
        print(json.dumps({"phase":"finished", "vessel":name, "condition":condition, **record}), flush=True)
        if outcome.plan is not None and args.check:
            started = time.perf_counter()
            report = check(instance, outcome.plan)
            record.update(passed=report.passed, check_s=time.perf_counter()-started,
                          checker_violations=[v.rule_id for v in report.violations])
        records.append(record)
    print(json.dumps({"phase":"result", **info, "calls":records}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ships", default="feeder_500,feeder_800,feeder_1700")
    parser.add_argument("--conditions", default="C1,C2,C3")
    parser.add_argument("--limit", type=float, default=5.)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--watchdog", type=float, default=120.)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT/"experiments/ballast/review_conditions.json")
    parser.add_argument("--worker", nargs=2, metavar=("SHIP","CONDITION"))
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    results = []
    for ship in args.ships.split(","):
        for condition in args.conditions.split(","):
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", ship, condition,
                       "--limit", str(args.limit), "--repeat", str(args.repeat)]
            if args.check:
                command.append("--check")
            if args.probe:
                command.append("--probe")
            if args.build_only:
                command.append("--build-only")
            started = time.perf_counter()
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, cwd=ROOT, env=os.environ.copy())
            print(f"started pid={process.pid} {ship} {condition}", flush=True)
            timed_out = False
            try:
                output, error = process.communicate(timeout=args.watchdog)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()  # Only this directly owned, recorded child PID.
                output, error = process.communicate()
            phases = []
            for line in output.splitlines():
                try:
                    phases.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            result = dict(vessel=ship, condition=condition, pid=process.pid,
                          watchdog_timeout=timed_out, exit_code=process.returncode,
                          total_s=time.perf_counter()-started, phases=phases,
                          stderr=error[-4000:])
            results.append(result)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(results, indent=2)+"\n")
            calls = [p for p in phases if p.get("phase")=="finished"]
            print(f"{ship} {condition}: watchdog={timed_out} " +
                  ", ".join(f"{'warm' if c['warm'] else 'cold'} {c['status']} {c['wall_s']:.3f}s" for c in calls), flush=True)


if __name__ == "__main__":
    main()

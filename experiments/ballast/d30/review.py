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
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
os.environ.setdefault("STOWAGE_VESSEL_CACHE_DIR", str(ROOT / "experiments/cache/vessels"))


def load_harness():
    path = ROOT / "experiments/physics-review/design/harness.py"
    spec = importlib.util.spec_from_file_location("ballast_review_harness", path)
    module = importlib.util.module_from_spec(spec)
    # Schema 1.3 FixedLoad objects are records, not tuple iteration.
    # Adapt the reviewer expression locally; source harness remains untouched.
    source = path.read_text().replace("sum(m for *_, m in vessel.fixed_loads)",
                                      "sum(load.mass_kg for load in vessel.fixed_loads)")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def worker(args):
    import faulthandler
    trace_path = ROOT / f"experiments/ballast/d30/trace_{args.worker[0]}_{args.worker[1]}_{'manual' if args.manual else 'literal'}.log"
    trace_file = trace_path.open("w")
    faulthandler.dump_traceback_later(45., repeat=True, file=trace_file)
    summary_path = ROOT / "experiments/cache/stability/warmup/summary.json"
    warmup = json.loads(summary_path.read_text())
    if not warmup.get("complete"):
        raise RuntimeError("Run uv run python -m stowage.cache_warm successfully before benchmarking")
    import stowage.linear.cache as lc
    production_linear_root = lc._DEFAULT_ROOT
    harness = load_harness()
    from stowage.model import Instance, MovePlan
    from stowage.checker.full import check
    from stowage.solver.ballast import finish_ballast
    name, condition = args.worker
    started = time.perf_counter()
    print(json.dumps({"phase":"loading", "vessel":name, "condition":condition}), flush=True)
    # Content identity deliberately excludes solver/checker version counters.
    # Physical sources and all condition recipes are hashed, not dated by mtime.
    physical_sources = [Path(__file__).resolve(),
                        ROOT / "src/stowage/params.py",
                        ROOT / "experiments/physics-review/design/harness.py",
                        ROOT / "experiments/physics-review/design/after_run.py",
                        ROOT / "experiments/physics-review/design/after.json"]
    physical_sources.extend((ROOT / "params").rglob("*.toml"))
    physical_sources.extend((ROOT / "data/fixtures").rglob("*.json"))
    for area in ("vessel", "model"):
        physical_sources.extend((ROOT / "src/stowage" / area).rglob("*.py"))
    digest = hashlib.sha256()
    for source in sorted(physical_sources):
        digest.update(str(source.relative_to(ROOT)).encode())
        digest.update(source.read_bytes())
    source_hash = digest.hexdigest()
    condition_root = ROOT / "experiments/ballast/d30/conditions"
    if args.manual:
        condition_root = condition_root / "manual"
    manual_recipes = json.loads((ROOT / "experiments/physics-review/design/after.json").read_text())
    recipe = manual_recipes[name]
    def manual_masses(vessel):
        declared = {}
        for spec in recipe["ballast_plan"][condition]:
            prefix, _, fraction = spec.partition("@")
            for tank in vessel.tanks:
                if tank.adjustable and tank.id.startswith(prefix):
                    declared[tank.id] = float(fraction or 1.)*tank.capacity_m3*tank.content_density_kg_m3
        return declared
    cached = condition_root / f"{name}_{condition}_{source_hash[:20]}.json"
    params = harness._ORIGINAL_LOAD(name)
    if cached.exists():
        payload = json.loads(cached.read_text())
        if payload["source_sha256"] != source_hash:
            raise ValueError("condition source content hash mismatch")
        instance, info = Instance.model_validate(payload["instance"]), payload["info"]
        vessel = instance.vessel
        if hashlib.sha256(vessel.model_dump_json().encode()).hexdigest() != payload["vessel_sha256"]:
            raise ValueError("condition physical content hash mismatch")
        harness._redirect_param_readers()
        info["snapshot_reused"] = True
    else:
        vessel, params = harness.apply_overrides(name, None)
        if args.manual:
            # Use the reviewer's actual context manager, not a reimplementation.
            # Only its condition definition is applied; no ship design overrides.
            import importlib
            sys.modules["harness"] = harness
            search = sys.path[:]
            try:
                sys.path.insert(0, str(ROOT / "experiments/physics-review/design"))
                after_run = importlib.import_module("after_run")
            finally:
                sys.path[:] = search
            declared = manual_masses(vessel)
            with after_run.condition_context(declared, recipe["draught_margin_m"][condition]):
                instance, info = harness.build_condition(name, vessel, params, condition)
            instance = instance.model_copy(update={"onboard":instance.onboard.model_copy(update={
                "tank_masses_kg":{**instance.onboard.tank_masses_kg, **{k:0. for k in declared}}})})
        else:
            instance, info = harness.build_condition(name, vessel, params, condition)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps({"source_sha256":source_hash,
            "vessel_sha256":hashlib.sha256(vessel.model_dump_json().encode()).hexdigest(),
            "instance":instance.model_dump(mode="json"), "info":info}))
    # Keep the production linear cache warmed by stowage.cache_warm.
    lc._DEFAULT_ROOT = production_linear_root
    info["linear_cache_root"] = str(production_linear_root)
    from stowage.versions import component_versions
    info.update(source_sha256=source_hash, cache_warm_summary_sha256=hashlib.sha256(summary_path.read_bytes()).hexdigest(), vessel=name, components=component_versions(), params_sha256=hashlib.sha256(repr(params).encode()).hexdigest(), build_s=time.perf_counter()-started,
                adjustable_tanks=sum(t.adjustable for t in vessel.tanks),
                ballast_capacity_m3=sum(t.capacity_m3 for t in vessel.tanks if t.adjustable))
    print(json.dumps({"phase": "built", **info}), flush=True)
    plan = MovePlan(id=f"{instance.id}.ballast_review", instance_id=instance.id,
                    call_index=instance.call_index)
    records = []
    info["condition_context"] = "manual_budget" if args.manual else "literal_harness"
    if not args.build_only:
        from stowage.model.plan import BallastCondition
        declared = manual_masses(vessel) if args.manual else {}
        witness = plan.model_copy(update={"ballast":BallastCondition(tank_masses_kg=declared,source="planner")})
        witness_start = time.perf_counter()
        from stowage.solver.ballast_deadline import run_with_deadline, BallastDeadline, shutdown_worker
        try:
            witness_check = run_with_deadline(check, (instance,witness), deadline=time.monotonic()+30.)
            info["reference_witness"] = {"passed":witness_check.passed,
                "wall_s":time.perf_counter()-witness_start,
                "tank_masses_kg":declared,
                "parts":{k:harness._part_summary(v) for k,v in witness_check.parts.items()}}
        except BallastDeadline:
            info["reference_witness"] = {"passed":None,"timed_out":True,
                "wall_s":time.perf_counter()-witness_start,"tank_masses_kg":declared,"parts":{}}
        finally:
            shutdown_worker()  # Keep finisher first-call isolation; only its owned worker.
        info["reference_witness"]["kind"] = "loading_manual" if args.manual else "empty_ballast_reference"
        print(json.dumps({"phase":"reference_witness", **info["reference_witness"]}), flush=True)
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
        import stowage.solver.ballast_deadline as deadline_worker
        previous_worker = deadline_worker._process
        previous_pid = previous_worker.pid if previous_worker is not None else None
        started = time.perf_counter()
        outcome = finish_ballast(instance, plan, time_limit_s=args.limit, seed=0)
        elapsed = time.perf_counter()-started
        current_worker = deadline_worker._process
        current_pid = current_worker.pid if current_worker is not None else None
        record = dict(repeat=repeat, warm=repeat>0, status=outcome.status,
                      wall_s=elapsed,
                      worker_pid_before=previous_pid, worker_pid_after=current_pid,
                      worker_reused=previous_pid is not None and previous_pid == current_pid,
                      part_errors={k:p.error for k,p in outcome.parts.items()},
                      reported_wall_s=outcome.wall_time_s, iterations=outcome.iterations,
                      proof=outcome.proof,
                      violations={k:[v.rule_id for v in p.violations] for k,p in outcome.parts.items()},
                      metrics={k:dict(p.metrics) for k,p in outcome.parts.items()},
                      margins={k:dict(p.margins) for k,p in outcome.parts.items()})
        print(json.dumps({"phase":"finished", "vessel":name, "condition":condition, **record}), flush=True)
        if outcome.plan is not None and args.check:
            started = time.perf_counter()
            report = check(instance, outcome.plan)
            check_elapsed = time.perf_counter()-started
            from stowage.checker.full import departure_condition
            from stowage.checker.stability import check_stability
            from stowage.checker.strength import check_strength
            from stowage.checker.visibility import check_visibility
            from stowage.checker.lashing import check_lashing
            ship = departure_condition(instance, outcome.plan)
            physics = {}
            for label, checker in (("stability",check_stability),("strength",check_strength),
                                   ("visibility",check_visibility),("lashing",check_lashing)):
                part_start = time.perf_counter()
                part = checker(instance, ship, call_index=instance.call_index)
                physics[label] = {"ran":part.ran, "error":part.error,
                                  "violations":[v.rule_id for v in part.violations],
                                  "metrics":dict(part.metrics),
                                  "wall_s":time.perf_counter()-part_start}
            record["independent_physics"] = physics
            record.update(passed=report.passed, check_s=check_elapsed,
                          independent_check_and_physics_s=time.perf_counter()-started,
                          checker_violations=[v.rule_id for v in report.violations])
        records.append(record)
    print(json.dumps({"phase":"result", **info, "calls":records}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ships", default="feeder_500,feeder_1000,feeder_1700")
    parser.add_argument("--conditions", default="C1,C2,C3")
    parser.add_argument("--limit", type=float, default=5.)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--watchdog", type=float, default=120.)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT/"experiments/ballast/d30/review.json")
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
            if args.manual:
                command.append("--manual")
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

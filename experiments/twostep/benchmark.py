"""Bounded, single-core evidence for the E10 industry baseline.

Every quality number belongs to a full-checker-approved, mask-replayed plan.
The optional tiny oracle uses an independent physical brute-force lower bound
and a full-checker witness; equality certifies the weighted-lift optimum.
Each rung runs in its own bounded child process; interruption preserves its
latest checkpoint. No repair is used because that would change the baseline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUNGS = ("tiny", "small", "medium", "full")


def source_epoch():
    """Conservatively pin local producer, physics, checker, and policy bytes."""
    paths = sorted(path for root in (ROOT / "src/stowage", ROOT / "params")
                   for path in root.rglob("*") if path.is_file() and path.suffix in (".py", ".toml", ".json"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def write(path, row):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def worker(args):
    from stowage.actions import get_action_space
    from stowage.bounds import lower_bound
    from stowage.checker.full import check
    from stowage.io import load_instance
    from stowage.planner import finalize
    from stowage.versions import component_versions

    started = time.perf_counter()
    epoch = source_epoch()
    path = args.output.parent / f"{args.worker}-{args.sample_index:03d}.checkpoint.json"
    def expired(*_):
        raise SystemExit(124)
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(max(1, int(args.process_seconds)))
    row = dict(rung=args.worker, seed=args.seed, status="loading", legal=False,
               checker_passed=False, mask_status="not_run", objective=None,
               lifts=None, gap_to_lb=None, versions=component_versions())
    row.update(runner_version="4.1.0", pid=os.getpid(), source_epoch=epoch,
               warm_status=json.loads(args.warm_status.read_text()) if args.warm_status is not None else None,
               settings={"planning_s": args.plan_seconds, "ballast_s": args.ballast_seconds,
                         "oracle_s": args.oracle_seconds, "process_cap_s": args.process_seconds},
               planner_source_sha256={name: hashlib.sha256((ROOT / "src/stowage/solver" / name).read_bytes()).hexdigest()
                                      for name in ("twostep.py", "twostep_adapter.py", "twostep_terminal.py")})
    write(path, row)
    manifest_path = args.instance_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    group_key = f"random_yard/{args.worker}"
    shards = manifest["groups"][group_key]["shards"]
    # Read only train/val shard bytes. Manifest membership is inspected before I/O.
    eligible = [shard for split in ("train", "val") for shard in shards if shard["split"] == split]
    if args.sample_index >= len(eligible):
        raise ValueError(f"only {len(eligible)} train/val shards in {group_key}")
    selected = eligible[args.sample_index]
    instance_path = args.instance_root / group_key / selected["instance"]
    actual_sha = hashlib.sha256(instance_path.read_bytes()).hexdigest()
    if selected["sha256"] != actual_sha:
        raise ValueError("canonical instance checksum differs from manifest")
    row.update(split=selected["split"], instance_sha256=actual_sha,
               selection="train then val in manifest order", sample_index=args.sample_index,
               manifest_path=str(manifest_path), instance_seed=selected["seed"])
    generation_epoch = selected.get("source_epoch")
    row["generation_epoch_verified"] = generation_epoch is not None and generation_epoch == epoch
    row["generation_changed_sources"] = ([name for name in set(generation_epoch) | set(epoch)
        if generation_epoch.get(name) != epoch.get(name)] if generation_epoch is not None else None)
    instance = load_instance(instance_path)
    if instance.vessel.hull is None or not instance.vessel.hull.stations:
        raise ValueError("benchmark refuses incomplete hull/strength data")
    row.update(instance_id=instance.id, targets=len(instance.load_list),
               lower_bound=lower_bound(instance).weighted,
               instance_path=str(instance_path), status="planning")
    write(path, row)
    result = None
    if not args.oracle_only:
        from stowage.solver.twostep import TwoStepPlanner
        result = TwoStepPlanner().plan(instance, seed=args.seed, time_limit_s=args.plan_seconds)
        row.update(status=result.status, message=result.message, metrics=result.metrics,
                   planning_seconds=result.wall_time_s)
        write(path, row)
        row["ballast_attempted"] = result.status == "ok" and result.plan is not None
        try:
            result = finalize(instance, result, time_limit_s=args.ballast_seconds)
        except Exception as error:
            # Keep the failed finisher and its cargo plan visible. The checker
            # can diagnose this plan, but it is never a post-ballast submission.
            result = result.model_copy(update={"status": "error",
                "message": f"Finalization failed: {type(error).__name__}: {error}"})
        row.update(status=result.status, message=result.message, metrics=result.metrics)
        row["ballast_present"] = result.plan is not None and result.plan.ballast is not None
        if result.plan is not None:
            write(path.with_suffix(".plan.json"), result.plan.model_dump(mode="json"))
            check_started = time.perf_counter()
            report = check(instance, result.plan)
            write(path.with_suffix(".checker.json"), report.model_dump(mode="json"))
            row["checker_seconds"] = time.perf_counter()-check_started
            row.update(checker_passed=report.passed,
                       checker_rule_ids=sorted({v.rule_id for v in report.violations}),
                       shut_out=len(report.shut_out),
                       checker_part_errors={key: part.error for key, part in report.parts.items() if part.error})
            try:
                space = get_action_space("boxslot")
                from stowage.feasibility import make_feasibility_engine
                engine = make_feasibility_engine(instance, space)
                row["mask_backend"] = f"{type(engine).__module__}.{type(engine).__qualname__}"
                row["shared_v2_gate"] = int(component_versions()["feasibility"].split(".")[0]) >= 2
                for action in space.from_plan(instance, result.plan):
                    if action not in engine.legal_actions():
                        raise ValueError(f"action masked at depth {engine.depth}: {space.encode(action)}")
                    engine.push(action)
                row["mask_status"] = "passed" if engine.is_complete() else "incomplete"
            except NotImplementedError:
                row["mask_status"] = "unavailable: feasibility engine stub"
            except Exception as error:
                row["mask_status"] = f"failed: {type(error).__name__}: {error}"
            row["legal"] = result.status == "ok" and report.passed and row["mask_status"] == "passed"
            # Diagnostic counts are retained for rejected plans, but excluded from quality averages.
            if report.count is not None:
                count = report.count
                row["diagnostic_objective"] = count.weighted_nonproductive
                row["diagnostic_lifts"] = dict(load=count.loads, discharge=count.discharges,
                    yard=count.relocations, restow=count.restow_lifts, cover=count.hatch_cover_lifts,
                    future_restow=sum(f.restow_lifts for f in count.future),
                    future_cover=sum(f.hatch_cover_lifts for f in count.future))
                if row["legal"]:
                    objective = count.weighted_nonproductive
                    row["objective_d11"] = dict(hard_rules_passed=True, loaded=count.loads, shut_out=0, weighted_nonproductive=objective, tertiary_margins_certified=False)
                    row.update(objective=objective, lifts=row["diagnostic_lifts"],
                        gap_to_lb=(objective-row["lower_bound"])/objective if objective else 0.0)
        row["seconds"] = time.perf_counter()-started
        row["peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
        final_epoch = source_epoch()
        row["runtime_changed_sources"] = [name for name in set(epoch) | set(final_epoch)
            if epoch.get(name) != final_epoch.get(name)]
        row["epoch_stable"] = (row["generation_epoch_verified"] and not row["runtime_changed_sources"]
                               and row["versions"] == component_versions())
        if not row["epoch_stable"]:
            row["raw_legal"] = row["legal"]
            row["legal"] = False
        write(path, row)
    if args.worker == "tiny" and args.oracle_seconds > 0:
        from stowage.solver.exact_oracle import checker_brute_force_certificate
        oracle = checker_brute_force_certificate(instance,
            incumbent=result.plan if result is not None and row["legal"] else None,
            time_limit_s=args.oracle_seconds, max_nodes=args.oracle_nodes)
        oracle_row = dict(status=oracle.status, objective=oracle.objective,
            lower_bound=oracle.lower_bound, nodes=oracle.expanded_nodes,
            seconds=oracle.wall_time_s, relaxed_status=oracle.relaxed_status,
            relaxed_objective=oracle.relaxed_objective,
            brute_force_agreement=oracle.brute_force_agreement,
            certificate_source=oracle.certificate_source,
            checker_passed=oracle.checker_passed, message=oracle.message,
            scope="independent physical brute force bound + full checker witness; weighted D11 lifts")
        if oracle.report is not None and oracle.report.count is not None:
            count = oracle.report.count
            oracle_row["lifts"] = dict(load=count.loads, discharge=count.discharges,
                yard=count.relocations, restow=count.restow_lifts, cover=count.hatch_cover_lifts,
                future_restow=sum(f.restow_lifts for f in count.future),
                future_cover=sum(f.hatch_cover_lifts for f in count.future))
        row["checker_brute_force_oracle"] = oracle_row
        row["difference_to_optimum"] = (row["objective"]-oracle.objective
            if row["legal"] and oracle.status == "OPTIMAL" and oracle.objective is not None else None)
        row["seconds"] = time.perf_counter()-started
        write(path, row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rungs", nargs="+", choices=RUNGS, default=list(RUNGS))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--plan-seconds", type=float, default=10.)
    parser.add_argument("--ballast-seconds", type=float, default=5.)
    parser.add_argument("--oracle-seconds", type=float, default=5.)
    parser.add_argument("--oracle-only", action="store_true")
    parser.add_argument("--oracle-nodes", type=int, default=10000)
    parser.add_argument("--process-seconds", type=float, default=30.)
    parser.add_argument("--output", type=Path, default=HERE / "results.json")
    parser.add_argument("--instance-root", type=Path, default=ROOT / "experiments" / "instances-v3")
    parser.add_argument("--warm-status", type=Path, default=None)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--sample-index", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--worker", choices=RUNGS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.worker and not (args.instance_root / "manifest.json").is_file():
        parser.error("instance manifest is absent; complete generation before benchmarking")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args)
        return
    rows = []
    jobs = [(rung, index) for rung in args.rungs for index in range(args.count)]
    for rung, index in jobs:
        path = args.output.parent / f"{rung}-{index:03d}.checkpoint.json"
        path.unlink(missing_ok=True)
        started = time.perf_counter()
        command = [sys.executable, __file__, "--worker", rung, "--sample-index", str(index), "--seed", str(args.seed),
                   "--plan-seconds", str(args.plan_seconds), "--ballast-seconds", str(args.ballast_seconds),
                   "--oracle-seconds", str(args.oracle_seconds), "--oracle-nodes", str(args.oracle_nodes),
                   "--process-seconds", str(args.process_seconds), "--output", str(args.output),
                   "--instance-root", str(args.instance_root)]
        if args.warm_status is not None:
            command.extend(["--warm-status", str(args.warm_status)])
        if args.oracle_only:
            command.append("--oracle-only")
        # This parent owns exactly the recorded PID and always reaps it, including
        # KeyboardInterrupt. Workers additionally enforce an independent alarm.
        stdout_path = path.with_suffix(".stdout.log")
        stderr_path = path.with_suffix(".stderr.log")
        error = None
        with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
            child = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr)
            path.with_suffix(".pid").write_text(str(child.pid)+"\n")
            try:
                returncode = child.wait(timeout=args.process_seconds+2)
                if returncode:
                    error = f"child exit {returncode}: " + stderr_path.read_text()[-4000:]
            except subprocess.TimeoutExpired:
                error = f"child exceeded {args.process_seconds:g} seconds"
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
        row = json.loads(path.read_text()) if path.exists() else dict(rung=rung, seed=args.seed, legal=False)
        row["process_seconds"] = time.perf_counter()-started
        row["pid"] = child.pid
        row["process_reaped"] = True
        if error:
            row.update(status="process_error", process_error=error, legal=False)
        rows.append(row)
        write(args.output, rows)
        print(json.dumps(row, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

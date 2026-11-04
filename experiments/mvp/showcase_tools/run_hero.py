#!/usr/bin/env python3
"""Bounded one-core hero reference run, with durable real-prefix checkpoints.

Run under ``nice -n 10 uv run python .../run_hero.py --output <new folder>``.
The supervising process waits while exactly one worker phase runs. It terminates
only its own recorded worker PID on timeout. No source or corpus files change.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def hashes() -> dict[str, str]:
    paths = sorted((ROOT / "src").rglob("*.py"))
    paths += sorted((ROOT / "src").rglob("*.toml"))
    paths += sorted((ROOT / "src").rglob("*.json"))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def memory_stats() -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # Darwin reports bytes; Linux reports KiB.
    return {"max_rss_bytes": int(usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)),
            "user_cpu_s": usage.ru_utime, "system_cpu_s": usage.ru_stime}


def load_result(path: Path):
    from stowage.model import PlannerResult
    data = json.loads(path.read_text())
    data.pop("schema_version", None)
    return PlannerResult.model_validate(data)


def isolate_caches(output: Path) -> None:
    """Redirect new numerical-cache writes to this run's owned directory.

    Existing cache files are linked read-only by convention; numerical caches use
    atomic replace on writes, so replacements remain within the owned directory.
    """
    cache = output / "cache"
    marker = cache / ".mirror-complete"
    if not marker.exists():
        for name in ("linear", "stability"):
            destination = cache / name
            destination.mkdir(parents=True, exist_ok=True)
            existing = ROOT / "experiments/cache" / name
            if existing.exists():
                for source in existing.rglob("*"):
                    target = destination / source.relative_to(existing)
                    if (source.is_file() and source.suffix != ".tmp"
                            and not target.exists() and not target.is_symlink()):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.symlink_to(source.resolve())
        # APPROXIMATION: link the existing content-addressed inventory once per
        # owned run. Later cache misses rebuild privately; cache validation and
        # checker limits are unchanged. Avoid rescanning 160k files each phase.
        marker.write_text("Existing cache inventory linked; later misses rebuild privately.\n")
    import stowage.linear.cache as linear
    import stowage.checker.stability_tables as tables
    import stowage.checker.stability_liquid_tables as liquid
    import stowage.checker.stability_liquid as liquid_direct
    import stowage.checker.stability_sounding as sounding
    linear._DEFAULT_ROOT = cache / "linear"
    for module in (liquid, liquid_direct, sounding):
        module.CACHE_DIRECTORY = cache / "stability"
    tables.CACHE_DIR = cache / "stability"
    # Functions bind their path defaults at definition time.
    for name in ("load_table_geometry", "cached_geometry", "migrate_legacy"):
        function = getattr(tables, name)
        if function.__kwdefaults__ and "cache_dir" in function.__kwdefaults__:
            function.__kwdefaults__["cache_dir"] = cache / "stability"


def plan_worker(args, instance) -> None:
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig
    from stowage.frozen import current_provenance
    from stowage.model import MovePlan, PlannerResult
    from stowage.solver import scalable
    output = args.output
    started = time.monotonic()
    space = get_action_space("boxslot")
    max_depth = -1
    loaded_best = -1
    hook_errors = []

    def checkpoint(node):
        nonlocal max_depth, loaded_best
        depth = node.engine.depth
        loaded = sum(node.ex.where.get(cid, (None,))[0] == "ship" for cid in instance.load_list)
        longest = depth > max_depth
        fullest = loaded > loaded_best
        if not (longest or fullest):
            return
        provenance = current_provenance(created_by="showcase.hero.prefix", seed=0).merge(
            instance.provenance, parent_ids=[instance.id])
        # These are the exact lifts already accepted by the public feasibility
        # oracle and expander. Do not invent cover closure or a completed loading.
        plan = MovePlan(id=f"{instance.id}.hero.prefix.{depth}", instance_id=instance.id,
                        call_index=instance.call_index, moves=list(node.ex.moves),
                        action_space=f"{space.name}@{space.version}",
                        actions=[space.encode(a) for a in node.engine.actions()], provenance=provenance)
        result = PlannerResult(status="timeout", plan=plan, planner="solver.scalable",
            planner_version=scalable.SCALABLE_VERSION, seed=0, wall_time_s=time.monotonic()-started,
            metrics={"status": "partial", "partial": True, "oracle_approved_actions": depth,
                     "partial_loaded": loaded, "required_loads": len(instance.load_list),
                     "checker_passed": False, "requires_finalize_and_check": True,
                     "c2_global_screen": 0, "num_workers": 1},
            message="Partial hero replay; oracle-approved prefix, not a departure-approved plan.",
            provenance=provenance)
        payload = result.model_dump(mode="json")
        if longest:
            write_json(output / "longest-prefix.result.json", payload)
            max_depth = depth
        if fullest:
            write_json(output / "most-loaded-prefix.result.json", payload)
            loaded_best = loaded
        write_json(output / "progress.json", {"status": "running", "time": utc(),
            "elapsed_s": time.monotonic()-started, "longest_actions": max_depth,
            "most_loaded": loaded_best, "required_loads": len(instance.load_list),
            "checkpoint": "longest-prefix.result.json", "hook_errors": hook_errors})

    original = scalable._construct
    signature = inspect.signature(original)

    def instrumented(*positional, **keywords):
        bound = signature.bind(*positional, **keywords)
        previous = bound.arguments.get("progress")
        def progress(node):
            if previous is not None:
                previous(node)
            try:
                checkpoint(node)
            except Exception as exc:
                # Checkpoint I/O cannot change a solver decision or legality.
                hook_errors.append(f"{type(exc).__name__}: {exc}")
                logging.exception("prefix checkpoint failed")
        bound.arguments["progress"] = progress
        return original(*bound.args, **bound.kwargs)

    if "progress" not in signature.parameters:
        raise RuntimeError("Scalable constructor has no progress callback; unsupported source version")
    scalable._construct = instrumented
    config = scalable.ScalableConfig(feasibility_config=FeasibilityConfig(extra={"global_screen": 0}))
    write_json(output / "solver-config.json", {"seed": 0, "time_limit_s": args.time_limit_s,
        "config": {key: repr(value) for key, value in vars(config).items()},
        "c2_global_screen": 0, "num_workers": 1})
    result = scalable.ScalableSolver(config).plan(instance, seed=0, time_limit_s=args.time_limit_s)
    # Save BEFORE finalization/checking: a later expensive stage cannot lose it.
    write_json(output / "raw.result.json", result.model_dump(mode="json"))
    write_json(output / "progress.json", {"status": "solver_finished", "time": utc(),
        "planner_status": result.status, "elapsed_s": time.monotonic()-started,
        "longest_actions": max_depth, "most_loaded": loaded_best,
        "required_loads": len(instance.load_list), "hook_errors": hook_errors})


def worker(args) -> int:
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        raise RuntimeError("Hero wrapper requires rule-freeze OFF")
    isolate_caches(args.output)
    instance = load_instance(args.instance)
    started = time.monotonic()
    write_json(args.output / f"{args.phase}.source-hashes.json", hashes())
    try:
        if args.phase == "plan":
            plan_worker(args, instance)
        elif args.phase == "finalize":
            from stowage.planner import finalize
            result = load_result(args.output / "selected.result.json")
            result = finalize(instance, result, time_limit_s=args.finalize_seconds)
            write_json(args.output / "finalized.result.json", result.model_dump(mode="json"))
        elif args.phase == "check":
            from stowage.checker import check
            from stowage.moves._physical import Replay
            from stowage.moves import initial_state
            result = load_result(args.output / "finalized.result.json")
            if result.plan is None:
                raise RuntimeError("No actual plan exists to check")
            # Prefix legality excludes departure completion (missing loads/open
            # covers are expected); full checker independently records those.
            replay = Replay(instance, initial_state(instance))
            prefix_error = None
            executed = 0
            for index, move in enumerate(result.plan.moves):
                try:
                    replay.move(move, move_index=index)
                    executed += 1
                except Exception as exc:
                    prefix_error = f"{type(exc).__name__}: {exc}"
                    break
            write_json(args.output / "prefix-replay.json", {"legal_lifts": prefix_error is None,
                "executed_lifts": executed, "planned_lifts": len(result.plan.moves),
                "error": prefix_error, "departure_approval": False,
                "note": "Physical lift legality only; full checker is the departure gate."})
            report = check(instance, result.plan)
            write_json(args.output / "check-report.json", report.model_dump(mode="json"))
            result._record_full_check(instance, report)
            result.metrics.update({"checker_passed": report.passed, "shippable": result.shippable})
            write_json(args.output / "checked.result.json", result.model_dump(mode="json"))
        return 0
    finally:
        write_json(args.output / f"{args.phase}.resources.json", {
            "wall_time_s": time.monotonic()-started, **memory_stats(), "finished_at": utc()})


def supervise(args) -> int:
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "execution.json").exists():
        raise RuntimeError("Use a new output folder; refusing to overwrite an existing run")
    before = hashes()
    write_json(args.output / "source-hashes.before.json", before)
    snapshot = args.output / "source-snapshot"
    # Preserve source bytes for audit while keeping normal package paths and
    # existing parameter/cache identities. Concurrent changes are flagged below.
    for relative in before:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    execution = {"started_at": utc(), "command": sys.argv, "pid": os.getpid(),
        "instance": str(args.instance), "instance_sha256": hashlib.sha256(args.instance.read_bytes()).hexdigest(),
        "c2_global_screen": 0, "num_workers": 1, "gpu": False, "phases": []}
    write_json(args.output / "execution.json", execution)
    environment = os.environ.copy()
    environment.update({name: "1" for name in THREAD_ENV})
    # No extra numerical worker threads, including HiGHS native optimizers.
    command = [sys.executable, str(Path(__file__).resolve()), "--instance", str(args.instance),
        "--output", str(args.output), "--time-limit-s", str(args.time_limit_s),
        "--finalize-seconds", str(args.finalize_seconds), "--check-seconds", str(args.check_seconds)]

    def run_phase(name, timeout):
        started = time.monotonic()
        phase = {"name": name, "started_at": utc(), "timeout_s": timeout}
        with (args.output / f"{name}.log").open("w") as log:
            child = subprocess.Popen([*command, "--phase", name], env=environment,
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            phase["pid"] = child.pid
            execution["phases"].append(phase)
            write_json(args.output / "execution.json", execution)
            try:
                phase["returncode"] = child.wait(timeout=timeout)
                phase["timed_out"] = False
            except subprocess.TimeoutExpired:
                # This is the exact PID we started and recorded, never a pattern.
                child.kill()
                phase["returncode"] = child.wait()
                phase["timed_out"] = True
        phase["wall_time_s"] = time.monotonic()-started
        write_json(args.output / "execution.json", execution)

    try:
        # A hard process deadline also bounds indivisible native oracle calls.
        run_phase("plan", args.time_limit_s)
        raw_path = args.output / "raw.result.json"
        prefix_path = args.output / "longest-prefix.result.json"
        if raw_path.exists() and json.loads(raw_path.read_text()).get("status") == "ok":
            selected = raw_path
        elif prefix_path.exists():
            selected = prefix_path
        elif raw_path.exists() and json.loads(raw_path.read_text()).get("plan") is not None:
            selected = raw_path
        else:
            execution.update({"status": "no_replay", "reason": "No legal crane prefix checkpoint was reached"})
            return 2
        shutil.copyfile(selected, args.output / "selected.result.json")
        run_phase("finalize", args.finalize_seconds + 2)
        if not (args.output / "finalized.result.json").exists():
            shutil.copyfile(args.output / "selected.result.json", args.output / "finalized.result.json")
        run_phase("check", args.check_seconds)
        report_path = args.output / "check-report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else None
        final_path = args.output / ("checked.result.json" if (args.output / "checked.result.json").exists()
                                   else "finalized.result.json")
        result = json.loads(final_path.read_text())
        passed = bool(report and report.get("passed") and result.get("status") == "ok")
        violations = [v for v in report.get("violations", []) if v.get("severity") == "hard"] if report else []
        execution.update({"status": "complete" if passed else "partial", "result": str(final_path),
            "selected_from": selected.name, "checker_passed": bool(report and report.get("passed")),
            "checker_unavailable_reason": None if report else "Checker did not finish; inspect check.log/execution phases",
            "first_hard_violation": violations[0] if violations else None,
            "planned_lifts": len(result.get("plan", {}).get("moves", [])),
            "report": str(report_path) if report else None})
        return 0
    finally:
        after = hashes()
        write_json(args.output / "source-hashes.after.json", after)
        execution.update({"finished_at": utc(), "source_changes_during_run": sorted(
            path for path in before.keys() | after.keys() if before.get(path) != after.get(path)),
            "source_notice": "Source bytes preserved at start; live imports occurred independently in each phase. Changed files require per-phase audit.",
            "supervisor_resources": memory_stats()})
        write_json(args.output / "execution.json", execution)
        print(json.dumps({key: execution.get(key) for key in ("status", "result", "report", "checker_passed", "source_changes_during_run")}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", type=Path, default=ROOT / "experiments/mvp/corpus/showcase/feeder_1700-full.json")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--time-limit-s", type=float, default=5400)
    parser.add_argument("--finalize-seconds", type=float, default=120)
    parser.add_argument("--check-seconds", type=float, default=300)
    parser.add_argument("--phase", choices=("plan", "finalize", "check"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.instance, args.output = args.instance.resolve(), args.output.resolve()
    if not 0 < args.time_limit_s <= 5400:
        parser.error("solver budget must be >0 and <=5400 seconds")
    for name in THREAD_ENV:
        os.environ[name] = "1"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return worker(args) if args.phase else supervise(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)

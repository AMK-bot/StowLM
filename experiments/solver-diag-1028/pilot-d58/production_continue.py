"""Continue a recorded cargo result through the public repair-enabled pipeline.

The original solver is not rerun. Planning time is historical; continuation
wall time measures only restored-result admission, finalize/check/repair/check,
and CPU token-mask replay. Raw artifacts are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
from worker import InstanceDeadline, digest, report_row, replay_mask, save, source_hashes


def restore_raw(directory):
    """Restore exact cargo envelope, or a conservative no-plan envelope.

    Old worker checkpoints omitted cargo.result.json when there was no plan.
    That case can recover original scalar fields from result.json while keeping
    final.result.json's merged provenance. Extra lineage is retained, never
    removed or replaced with apparently cleaner current provenance.
    """
    from stowage.model import PlannerResult
    directory = Path(directory)
    row = json.loads((directory / "result.json").read_text())
    cargo = directory / "cargo.result.json"
    if cargo.exists():
        result = PlannerResult.model_validate_json(cargo.read_text())
        mode = "identical_serialized_cargo_result"
    else:
        final = directory / "final.result.json"
        if not final.exists() or row.get("has_plan") is not False:
            raise ValueError("No exact cargo result or recoverable recorded no-plan result")
        envelope = PlannerResult.model_validate_json(final.read_text())
        if envelope.plan is not None or envelope.later_plans:
            raise ValueError("Final envelope is not the recorded no-plan outcome")
        required = ("solver_status", "solver_metrics", "solve_s")
        if any(name not in row for name in required):
            raise ValueError("No-plan checkpoint lacks original solver fields")
        result = envelope.model_copy(update={
            "status": row["solver_status"], "metrics": row["solver_metrics"],
            "wall_time_s": row["solve_s"], "message": row.get("solver_message"),
            "repaired": False, "repair_note": None,
        })
        mode = "no_plan_fields_restored_conservative_final_lineage_retained"
    if result.planner != row.get("planner_name", result.planner):
        raise ValueError("Recorded planner identity differs from restored cargo")
    if result.planner_version != row.get("planner_version", result.planner_version):
        raise ValueError("Recorded planner version differs from restored cargo")
    return result, row, mode


class RecordedPlanner:
    """Planner protocol adapter returning a fresh copy of one recorded result."""

    def __init__(self, result, solver):
        declarations = {
            "exact": ("solver.exact", True, False),
            "scalable": ("solver.scalable", False, False),
            "twostep": ("baseline.twostep", False, False),
        }
        name, self.sets_ballast, self.deterministic = declarations[solver]
        if result.planner != name:
            raise ValueError(f"Unexpected planner identity {result.planner!r} for {solver}")
        self.name = result.planner
        self.version = result.planner_version
        self._result = result

    def plan(self, instance, *, seed=0, time_limit_s=None):
        if self._result.plan is not None and self._result.plan.instance_id != instance.id:
            raise ValueError("Recorded cargo plan belongs to a different instance")
        return self._result.model_copy(deep=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-run", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--budget", type=float, default=30,
                        help="Public finisher and repair per-phase limit (solver is not rerun)")
    parser.add_argument("--cap", type=float, default=150)
    args = parser.parse_args(argv)
    args.raw_run = args.raw_run.resolve()
    raw, previous, restore_mode = restore_raw(args.raw_run)
    solver = previous["solver"]
    args.input = (args.input or Path(previous["input"])).resolve()
    args.output = (args.output or HERE / "production-continuation" / solver / args.raw_run.name).resolve()
    if not args.output.is_relative_to(HERE / "production-continuation"):
        parser.error("output must be in pilot-d58/production-continuation")
    if args.output.is_relative_to(args.raw_run) or args.raw_run.is_relative_to(args.output):
        parser.error("continuation output must be separate from raw records")
    if digest(args.input) != previous["input_sha256"]:
        parser.error("recorded instance content changed")
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(HERE.parent))
    os.chdir(ROOT)
    started = time.monotonic()
    sources = source_hashes()
    save(args.output / "source-start.json", sources)
    raw_end_file = args.raw_run / "source-end.json"
    raw_end = json.loads(raw_end_file.read_text()) if raw_end_file.exists() else {}
    prior_drift = (sorted(k for k in set(raw_end) | set(sources)
                         if raw_end.get(k) != sources.get(k)) if raw_end else None)
    row = dict(solver=solver, raw_run=str(args.raw_run), input=str(args.input),
               input_sha256=digest(args.input), diagnostic=True, evidence_eligible=False,
               allow_repair=True, solver_rerun=False, restore_mode=restore_mode,
               source_changes_since_raw=prior_drift, continuation_budget_s=args.budget,
               cap_s=args.cap, historical_solve_s=raw.wall_time_s,
               historical_raw_wall_s=previous.get("wall_s"),
               checker_passed=None, shippable=False, mask_replay_passed=None,
               ballast_found=False, ballast_status="not_reached", completion="incomplete",
               continuation_semantics="same recorded cargo result; fresh public finalize/check/repair/check; current source",
               timing_semantics="solve_s is historical; continuation_wall_s excludes original solver; summed estimates labelled")

    def checkpoint(**values):
        row.update(values)
        row["continuation_wall_s"] = time.monotonic() - started
        row["wall_s"] = row["continuation_wall_s"]
        save(args.output / "result.json", row)

    def deadline(signum, frame):
        raise InstanceDeadline("production continuation whole-worker cap expired")

    signal.signal(signal.SIGTERM, deadline)
    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, args.cap)
    checkpoint(stage="setup")
    try:
        from cache_runtime import redirect_cache, enforce_scope
        redirect_cache(HERE / "runtime")
        enforce_scope()
        from stowage.io import load_instance, save_plan
        from stowage.planner import run_planner
        from stowage.bounds import lower_bound
        instance = load_instance(args.input)
        planner = RecordedPlanner(raw, solver)
        checkpoint(instance_id=instance.id, vessel=instance.vessel.id, n_loads=len(instance.load_list),
                   lower_bound_36=lower_bound(instance).weighted, planner_name=planner.name,
                   planner_version=planner.version, original_solver_status=raw.status,
                   solve_s=raw.wall_time_s, stage="production_pipeline")
        save(args.output / "restored-cargo.result.json", raw.model_dump(mode="json"))
        result, report = run_planner(planner, instance, seed=raw.seed or 0,
                                     time_limit_s=args.budget, allow_repair=True)
        save(args.output / "final.result.json", result.model_dump(mode="json"))
        timings = result.metrics.get("pipeline_timing", {})
        ballast_status = result.metrics.get("ballast_status", "kept" if result.plan is not None
                                           and result.plan.ballast is not None else "not_reached")
        values = dict(final_status=result.status, final_message=result.message,
                      repaired=result.repaired, repair_note=result.repair_note,
                      pipeline_timing=timings, pipeline_metrics=result.metrics,
                      ballast_status=ballast_status, ballast_found=ballast_status in ("ok", "kept"),
                      shippable=result.shippable,
                      replayed_planner_adapter_s=timings.get("planning_s"),
                      estimated_original_plus_continued_pipeline_s=(raw.wall_time_s
                         + timings.get("pipeline_s", 0) - timings.get("planning_s", 0)))
        if report is not None:
            save(args.output / "report.json", report.model_dump(mode="json"))
            values.update(report_row(report))
            moves = report.parts.get("moves")
            complete = (report.count is not None and not report.shut_out and moves is not None
                        and moves.ran and not any(v.severity == "hard" for v in moves.violations))
            values["completion"] = "complete" if complete else "incomplete"
            objective = values["checked_objective"]
            if objective is not None:
                bound = row["lower_bound_36"]
                values.update(gap_to_lb36_absolute=objective - bound,
                              gap_to_lb36_relative=(objective - bound) / objective if objective else
                              0.0 if bound == 0 else None,
                              gap_is_full_checker_approved=report.passed)
        checkpoint(**values, stage="mask_replay")
        if result.plan is not None:
            save_plan(result.plan, args.output / "final.plan.json", allow_stubs=True)
            phase = time.monotonic()
            try:
                replay = replay_mask(instance, result.plan, checkpoint)
            except Exception as exc:
                replay = dict(verified=False, passed=False, failure_kind="exception",
                              error=f"{type(exc).__name__}: {exc}")
            save(args.output / "mask-replay.json", replay)
            checkpoint(mask_replay=replay, mask_replay_passed=replay["passed"],
                       mask_replay_s=time.monotonic() - phase)
        checkpoint(stage="finished", clean_complete_pass=bool(row["completion"] == "complete"
                   and row["checker_passed"] and row["shippable"] and row["mask_replay_passed"]))
    except (Exception, InstanceDeadline) as exc:
        checkpoint(error=f"{type(exc).__name__}: {exc}", timed_out=isinstance(exc, (TimeoutError, InstanceDeadline)),
                   interrupted_stage=row.get("stage"), stage="failed")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            from stowage.solver.ballast_deadline import shutdown_worker
            shutdown_worker()
        except Exception:
            pass
        ending = source_hashes()
        save(args.output / "source-end.json", ending)
        changes = sorted(k for k in set(sources) | set(ending) if sources.get(k) != ending.get(k))
        scale = 2 ** 30 if sys.platform == "darwin" else 2 ** 20
        checkpoint(source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
                   source_end_sha256=hashlib.sha256(json.dumps(ending, sort_keys=True).encode()).hexdigest(),
                   source_drift=changes, feasibility_drift=[k for k in changes if "/feasibility/" in k],
                   solver_drift=[k for k in changes if "/solver/" in k],
                   peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale,
                   child_peak_rss_gb=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / scale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

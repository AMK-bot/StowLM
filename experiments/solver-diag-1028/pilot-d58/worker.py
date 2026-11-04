"""Current-source D58 production-pipeline pilot worker (one instance/solver).

Repair is disabled to isolate the requested solver. Time limit is the public
per-phase planner API limit; --cap is a separate honest whole-worker deadline.
No solver, checker, or feasibility implementation is substituted.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
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


class InstanceDeadline(BaseException):
    pass


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, sort_keys=True, default=str) + "\n")
    temp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    """Include complete live source, configuration, and additions/deletions."""
    return {
        str(path.relative_to(ROOT)): digest(path)
        for base in ("src/stowage", "src/llm/stowlm", "params")
        for path in sorted((ROOT / base).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def report_row(report):
    return dict(
        checker_passed=report.passed,
        shut_out=report.shut_out,
        rules=sorted({v.rule_id for v in report.violations if v.severity == "hard"}),
        states={k: sorted({v.rule_id for v in part.violations if v.severity == "hard"})
                for k, part in report.parts.items()
                if any(v.severity == "hard" for v in part.violations) or not part.ran},
        part_errors={k: part.error for k, part in report.parts.items() if not part.ran},
        violations=[v.model_dump(mode="json") for v in report.violations if v.severity == "hard"],
        checked_objective=None if report.count is None else report.count.weighted_nonproductive,
    )


def replay_mask(instance, plan, checkpoint=None):
    """Replay canonical action tokens and end marker through the CPU mask."""
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MASK_VERSION, MaskConfig, MaskedDecoder
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    space = get_action_space()
    actions = ([space.decode(action) for action in plan.actions]
               if plan.actions is not None else space.from_plan(instance, plan))
    result = dict(verified=False, passed=False, actions=len(actions), pushed=0, tokens=0,
                  config=asdict(FeasibilityConfig()), failure_index=None,
                  mask_version=MASK_VERSION, mask_config=asdict(MaskConfig()),
                  rule_ids=[], sequence_source="plan.actions" if plan.actions is not None else "from_plan")
    if space.expand(instance, actions).moves != plan.moves:
        return {**result, "error": "action expansion changes final checked moves",
                "failure_kind": "action_representation"}
    engine = make_feasibility_engine(instance, space, config=FeasibilityConfig())
    result["diagnostics"] = dict(getattr(engine, "diagnostics", {}))
    result["global_screen"] = result["diagnostics"].get("global_screen", "unavailable")
    if result["global_screen"] != "on":
        return {**result, "error": "C2 production global screen is not on", "failure_kind": "c2_unavailable"}
    load_tokenizer.cache_clear()
    decoder = MaskedDecoder(engine, ActionCodec(load_tokenizer(), [box.id for box in instance.containers]), MaskConfig())
    for index, action in enumerate(actions):
        if checkpoint:
            checkpoint(mask_replay_progress={**result, "next_index": index})
        try:
            for token in decoder.codec.encode_action(action):
                decoder.advance(token)
                result["tokens"] += 1
            result["pushed"] = index + 1
        except Exception as exc:
            engine = decoder.engine
            dead = decoder.dead_end or engine.dead_end()
            return {**result, "failure_index": index, "action": space.encode(action),
                    "error": f"{type(exc).__name__}: {exc}", "failure_kind": "action_rejected",
                    "dead_end": None if dead is None else asdict(dead),
                    "rule_ids": [] if dead is None else list(dead.rule_ids),
                    "token_index": result["tokens"], "decoder_status": decoder.status,
                    "diagnostics": dict(getattr(engine, "diagnostics", {}))}
    try:
        for token in decoder.codec.end_plan_tokens:
            decoder.advance(token)
            result["tokens"] += 1
    except Exception as exc:
        engine = decoder.engine
        dead = decoder.dead_end or engine.dead_end()
        return {**result, "failure_index": len(actions), "token_index": result["tokens"],
                "decoder_status": decoder.status, "failure_kind": "end_marker_rejected",
                "error": f"{type(exc).__name__}: {exc}",
                "rule_ids": [] if dead is None else list(dead.rule_ids)}
    engine = decoder.engine
    complete = engine.is_complete() and decoder.status == "complete" and decoder.actions == list(actions)
    result.update(verified=complete, passed=complete, complete=complete,
                  decoder_status=decoder.status,
                  failure_kind=None if complete else "incomplete", stats=asdict(engine.stats()))
    if not complete:
        dead = engine.dead_end()
        result.update(dead_end=None if dead is None else asdict(dead),
                      rule_ids=[] if dead is None else list(dead.rule_ids))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--solver", choices=["exact", "scalable", "twostep", "background"], required=True)
    parser.add_argument("--budget", type=float, default=120)
    parser.add_argument("--ballast-budget", type=float, default=30)
    parser.add_argument("--cap", type=float, default=None)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    args.input = args.input.resolve()
    args.output = args.output.resolve()
    if not args.output.is_relative_to(HERE):
        parser.error("output must be under pilot-d58")
    args.cap = args.cap if args.cap is not None else args.budget + args.ballast_budget + 60
    args.output.mkdir(parents=True, exist_ok=True)
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(HERE.parent))
    os.chdir(ROOT)
    started = time.monotonic()
    sources = source_hashes()
    save(args.output / "source-start.json", sources)
    row = dict(solver=args.solver, input=str(args.input), input_sha256=digest(args.input),
               diagnostic=True, evidence_eligible=False, budget_s=args.budget, cap_s=args.cap,
               ballast_budget_s=args.ballast_budget,
               budget_scope="capture passes solver budget; run_planner passes finisher budget; separate whole-worker cap",
               checker_passed=None, shippable=False, mask_replay_passed=None,
               ballast_status="not_reached", ballast_found=False, completion="incomplete",
               timed_out=False, allow_repair=False, c2="on/default")

    def checkpoint(**values):
        row.update(values)
        row["wall_s"] = time.monotonic() - started
        save(args.output / "result.json", row)

    def deadline(signum, frame):
        raise InstanceDeadline("whole-worker wall cap expired")

    signal.signal(signal.SIGTERM, deadline)
    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, args.cap)
    checkpoint(stage="setup")
    try:
        from cache_runtime import redirect_cache, enforce_scope
        redirect_cache(HERE / "runtime")
        enforce_scope()
        from stowage.io import load_instance, save_plan
        from stowage.moves import apply_plan
        from stowage.bounds import lower_bound
        from stowage.model import MovePlan, BallastCondition
        from stowage import checker
        instance = load_instance(args.input)
        checkpoint(instance_id=instance.id, vessel=instance.vessel.id,
                   n_loads=len(instance.load_list), lower_bound_36=lower_bound(instance).weighted,
                   versions=instance.provenance.versions)
        if args.solver == "background":
            plan = MovePlan(id=instance.id + ".background-control", instance_id=instance.id,
                            call_index=instance.call_index, moves=[],
                            ballast=BallastCondition(tank_masses_kg={}, source="planner"))
            report = checker.check(instance, plan)
            save(args.output / "report.json", report.model_dump(mode="json"))
            checkpoint(**report_row(report), stage="finished", control="empty_moves_zero_adjustable_ballast",
                       physical_rules=sorted({v.rule_id for v in report.violations
                                              if v.severity == "hard" and v.rule_id != "objective.all_loaded"}))
            return 0
        from stowage.planner import run_planner
        if args.solver == "exact":
            from stowage.solver.exact import ExactPlanner
            planner = ExactPlanner()
        elif args.solver == "scalable":
            from stowage.solver.scalable import ScalableSolver
            planner = ScalableSolver()
        else:
            from stowage.solver.twostep import TwoStepPlanner
            planner = TwoStepPlanner()
        original_plan = planner.plan

        def capture(*positional, **keywords):
            # Effort-only adapter: preserve the requested solver budget while
            # run_planner sends its separate public default budget to finalize.
            keywords["time_limit_s"] = args.budget
            result = original_plan(*positional, **keywords)
            checkpoint(solver_status=result.status, solve_s=result.wall_time_s,
                       solver_message=result.message, solver_metrics=result.metrics,
                       has_plan=result.plan is not None,
                       optimality_status=result.metrics.get("optimality_status"),
                       timed_out=result.status == "timeout", stage="finishing")
            if result.plan is not None:
                save_plan(result.plan, args.output / "cargo.plan.json", allow_stubs=True)
                save(args.output / "cargo.result.json", result.model_dump(mode="json"))
                replay = apply_plan(instance, result.plan)
                placements = replay.final_state.ship.placements
                loaded = ({p.container_id for p in placements} if isinstance(placements, list)
                          else set(placements))
                complete = replay.legal and set(instance.load_list) <= loaded
                checkpoint(cargo_replay_legal=replay.legal, cargo_loaded=len(set(instance.load_list) & loaded),
                           completion="complete" if complete else "incomplete")
            return result

        planner.plan = capture
        checkpoint(stage="planning", planner_name=planner.name, planner_version=planner.version)
        result, report = run_planner(planner, instance, seed=args.seed,
                                     time_limit_s=args.ballast_budget, allow_repair=False)
        save(args.output / "final.result.json", result.model_dump(mode="json"))
        ballast_status = result.metrics.get("ballast_status", "kept" if result.plan is not None
                                           and result.plan.ballast is not None else "not_reached")
        values = dict(final_status=result.status, final_message=result.message,
                      ballast_status=ballast_status, ballast_found=ballast_status in ("ok", "kept"),
                      shippable=result.shippable, pipeline_metrics=result.metrics,
                      pipeline_timing=result.metrics.get("pipeline_timing", {}))
        if report is not None:
            save(args.output / "report.json", report.model_dump(mode="json"))
            values.update(report_row(report))
            # Report.shut_out is necessary, and physical move replay must be legal.
            move_part = report.parts.get("moves")
            complete = (report.count is not None and not report.shut_out and move_part is not None
                        and move_part.ran and not any(v.severity == "hard" for v in move_part.violations))
            values["completion"] = "complete" if complete else "incomplete"
            objective = values["checked_objective"]
            if objective is not None:
                bound = row["lower_bound_36"]
                values["gap_to_lb36_absolute"] = objective - bound
                values["gap_to_lb36_relative"] = ((objective - bound) / objective if objective else
                                                   0.0 if bound == 0 else None)
                values["gap_is_full_checker_approved"] = bool(report.passed)
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
                   cap_expired=isinstance(exc, InstanceDeadline), interrupted_stage=row.get("stage"), stage="failed")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            from stowage.solver.ballast_deadline import shutdown_worker
            shutdown_worker()
        except Exception:
            pass
        ending = source_hashes()
        save(args.output / "source-end.json", ending)
        changed = sorted(k for k in set(sources) | set(ending) if sources.get(k) != ending.get(k))
        scale = 2 ** 30 if sys.platform == "darwin" else 2 ** 20
        checkpoint(source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
                   source_end_sha256=hashlib.sha256(json.dumps(ending, sort_keys=True).encode()).hexdigest(),
                   source_drift=changed, feasibility_drift=[k for k in changed if "/feasibility/" in k],
                   solver_drift=[k for k in changed if "/solver/" in k],
                   peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale,
                   child_peak_rss_gb=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / scale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

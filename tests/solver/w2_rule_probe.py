"""Bounded full-checker comparison on seed-7 real-hull tiny/small canaries.

Run with ``uv run python tests/solver/w2_rule_probe.py``. C2 is explicitly off;
all physical checker parts and the production finisher remain enabled. The
canaries contain two mixed-length boxes in differently sized decision regions,
so this is an integration comparison, not a yield or scalability campaign.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
from time import monotonic

from stowage.actions import get_action_space
from stowage.checker import check
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import MovePlan, PlannerResult
from stowage.moves import apply_plan
from stowage.planner import finalize
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver.construct import ConstructivePlanner
from stowage.solver.exact import ExactPlanner
from stowage.solver.exact_deadline import run_with_deadline
from stowage.solver.repair import _repair as repair
from stowage.solver.scalable import ScalableConfig, ScalableSolver
from stowage.solver.twostep import TwoStepPlanner

_spec = importlib.util.spec_from_file_location("w2_full_canary", Path(__file__).parents[1] / "feasibility/test_w2_full_agreement.py")
_canary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_canary)
_case = _canary._case


def probe(instance, name, limit, checkpoint=None):
    config = FeasibilityConfig(extra={"global_screen": 0})
    planners = {
        "exact": lambda: ExactPlanner(feasibility_config=config, max_nodes=1000),
        "scalable": lambda: ScalableSolver(ScalableConfig(feasibility_config=config,
            max_neighborhoods=0, beam_width=2, branch_factor=4)),
        "construct": lambda: ConstructivePlanner(feasibility_config=config, max_rounds=3),
        "twostep": lambda: TwoStepPlanner(feasibility_config=config),
    }
    if name == "repair":
        original = PlannerResult(status="dead_end", planner="probe", planner_version="1",
            plan=MovePlan(id=instance.id+".empty", instance_id=instance.id, call_index=0))
        result = repair(instance, original, None, seed=7, time_limit_s=limit,
                        feasibility_config=config)
    else:
        result = planners[name]().plan(instance, seed=7, time_limit_s=limit)
    if result.status == "ok":
        result = finalize(instance, result, time_limit_s=limit)
    row = dict(solver=name, status=result.status, message=result.message,
               checker_passed=False, moves=None, nonproductive=None, rule_failures={})
    if result.plan is not None:
        replay = apply_plan(instance, result.plan)
        row["moves"] = len(result.plan.moves)
        row["nonproductive"] = replay.count.weighted_nonproductive
        row["moves_verified_by_engine"] = replay.legal
        row["crossing"] = replay.d62_crossing.model_dump(mode="json")
        if checkpoint is not None:
            checkpoint.write_text(json.dumps(row, default=str))
        report = check(instance, result.plan)
        row["checker_passed"] = report.passed
        row["rule_failures"] = dict(Counter(v.rule_id for v in report.violations if v.severity == "hard"))
        if report.count:
            row["nonproductive"] = report.count.weighted_nonproductive
        engine = make_feasibility_engine(instance, get_action_space(), config=config)
        try:
            for action in get_action_space().from_plan(instance, result.plan):
                engine.push(action)
            row["mask_complete"] = engine.is_complete()
        except (ValueError, KeyError) as exc:
            row["mask_complete"] = False
            row["mask_error"] = str(exc)
    else:
        row["rule_failures"] = result.metrics.get("repair_rule_failures", {})
    row["diagnostic"] = {k: result.metrics[k] for k in (
        "ballast_status", "model_error", "last_violations", "attribution",
        "preplan_status", "preplan_message", "repair_last_attempt_metrics") if k in result.metrics}
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=float, default=5.)
    parser.add_argument("--bound-dir", type=Path)
    parser.add_argument("--worker", choices=("exact", "scalable", "construct", "twostep", "repair"))
    parser.add_argument("--rung", choices=("tiny", "small"))
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--skip-witness", action="store_true")
    args = parser.parse_args()
    for rung in ((args.rung,) if args.rung else ("tiny", "small")):
        # Use exactly the feasibility owner's seed-7 real-hull fixture; its
        # declared background and geometry are retained, without physics edits.
        if args.bound_dir:
            from stowage.model import Instance
            evidence = json.loads((args.bound_dir / f"w2-full-feeder_500-{rung}-H8-1.json").read_text())
            instance = Instance.model_validate_json(evidence["payloads"]["instance"])
            witness = MovePlan.model_validate_json(evidence["payloads"]["plan"])
        else:
            with rule_freeze_v1(enabled=False):
                instance, _, witness = _case("feeder_500", rung, 8)
        with rule_freeze_v1():
            if args.worker:
                print(json.dumps(probe(instance, args.worker, args.limit, args.checkpoint), default=str), flush=True)
                return
            if not args.skip_witness:
                witness_outcome = run_with_deadline(check, (instance, witness), deadline=monotonic()+20.)
                witness_report = witness_outcome.value
                print(json.dumps(dict(rung=rung, witness_passed=bool(witness_report and witness_report.passed),
                    witness_rules=[v.rule_id for v in witness_report.violations if v.severity == "hard"] if witness_report else [],
                    witness_timeout=witness_outcome.timed_out)), flush=True)
            for name in ("exact", "scalable", "construct", "twostep", "repair"):
                started = monotonic()
                try:
                    # A fresh interpreter avoids macOS Objective-C imports in
                    # a post-fork CP-SAT worker. Only this recorded process is
                    # stopped; solver-owned watchdogs retain their own PIDs.
                    with tempfile.TemporaryDirectory(prefix="w2-solvers-") as work:
                        folder = Path(work)
                        checkpoint = folder / "checkpoint.json"
                        command = [sys.executable, str(Path(__file__).resolve()), "--worker", name,
                                   "--rung", rung, "--limit", str(args.limit), "--checkpoint", str(checkpoint)]
                        if args.bound_dir:
                            command += ["--bound-dir", str(args.bound_dir)]
                        with (folder / "stdout").open("w+") as output, (folder / "stderr").open("w+") as errors:
                            process = subprocess.Popen(command, stdout=output, stderr=errors)
                            timed_out = False
                            try:
                                process.wait(timeout=args.limit*3+5)
                            except subprocess.TimeoutExpired:
                                timed_out = True
                                process.kill()
                                process.wait()
                            output.seek(0)
                            lines = output.read().splitlines()
                            row = json.loads(lines[-1]) if lines else (
                                json.loads(checkpoint.read_text()) if checkpoint.exists() else
                                dict(solver=name, status="watchdog_timeout" if timed_out else "error", checker_passed=False))
                            row.update(watchdog_timeout=timed_out, worker_pid=process.pid)
                            if not timed_out and process.returncode:
                                errors.seek(0)
                                row["worker_error"] = errors.read()[-2000:]
                except Exception as exc:
                    row = dict(solver=name, status="error", message=f"{type(exc).__name__}: {exc}", checker_passed=False)
                row.update(rung=rung, seed=7, c2="off", wall_s=monotonic()-started)
                print(json.dumps(row, default=str), flush=True)


if __name__ == "__main__":
    main()

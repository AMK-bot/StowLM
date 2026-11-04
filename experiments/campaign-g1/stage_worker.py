"""One-core adapters around existing task experiment runners.

Only orchestration, artifact capture and output relocation live here. The
solver, physical checker, ballast finisher and production mask are unchanged.
"""
from __future__ import annotations

from eval.snapshot_io import read_snapshot

import argparse
from dataclasses import asdict
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "1"
sys.dont_write_bytecode = True


def load(relative, name):
    path = ROOT / relative
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temp.replace(path)


def cache_fence():
    """Relocate caches and refuse any write outside this campaign's scope."""
    module = load("experiments/campaign-g1/cache_adapter.py", "campaign_scoped_cache")
    module.redirect_cache(Path(os.environ["CAMPAIGN_CACHE_ROOT"]))
    module.enforce_scope()


def exact(instance, directory, args):
    from stowage.io import save_instance, save_plan
    import stowage.solver.exact as exact_api
    runner = load("experiments/exact-solver/fix_epoch_evidence.py", "campaign_exact")
    runner.OUT = directory
    save_instance(instance, directory / "generation-pilot.json")
    original = exact_api.solve_exact
    def capture(*positional, **kwargs):
        result = original(*positional, **kwargs)
        if result.plan is not None:
            save_plan(result.plan, directory / "exact.candidate-plan.json")
        return result
    with patch.object(exact_api, "solve_exact", capture):
        runner.current_pilot(SimpleNamespace(limit=args.search_s, oracle_limit=args.oracle_s))
    row = read_snapshot((directory / "current-pilot-result.json")).json()
    row["rung"] = args.rung
    row["oracle_checker_passed"] = row.pop("checker_passed", None)
    row["checker_passed"] = None  # exact candidate is checked after ballast in replay
    # Existing runner independently verifies physical optima; an engine-domain
    # OPTIMAL is never silently promoted to a physical or full-D11 certificate.
    return row


def scalable(instance, directory, args):
    from stowage.io import save_plan
    from stowage.solver.scalable import ScalableSolver
    runner = load("experiments/scalable-solver/g1_pilot.py", "campaign_scalable")
    original = ScalableSolver.plan
    def capture(solver, *positional, **kwargs):
        result = original(solver, *positional, **kwargs)
        if result.plan is not None:
            save_plan(result.plan, directory / "scalable.candidate-plan.json")
        return result
    settings = SimpleNamespace(solver_seed=0, overhead_reserve_s=args.check_s * 2 + args.ballast_s,
                               ballast_limit_s=args.ballast_s)
    with patch.object(ScalableSolver, "plan", capture):
        return runner.measure(instance, settings, args.search_s, runner.helpers(),
                              directory / "scalable.approved-plan.json")


def twostep(instance, directory, args):
    from stowage.io import save_instance
    runner = load("experiments/twostep/benchmark.py", "campaign_twostep")
    instance_root = directory / "input"
    save_instance(instance, instance_root / f"random_yard/{args.rung}/instance.json")
    import hashlib
    path = instance_root / f"random_yard/{args.rung}/instance.json"
    save(instance_root / "manifest.json", {"groups": {f"random_yard/{args.rung}": {"shards": [dict(
        instance=path.name, sha256=read_snapshot(path).sha256, split=args.split,
        seed=args.instance_seed, source_epoch=runner.source_epoch())]}}})
    settings = SimpleNamespace(worker=args.rung, sample_index=0, seed=0,
        output=directory / "results.json", process_seconds=args.timeout_s,
        warm_status=None, instance_root=instance_root, plan_seconds=args.search_s,
        ballast_seconds=args.ballast_s, oracle_seconds=0, oracle_only=False, oracle_nodes=100_000)
    # The wrapper manifest only relocates an already-selected input. Its frozen
    # release membership was checked by campaign.py before any shard starts.
    runner.worker(settings)
    return read_snapshot((directory / f"{args.rung}-000.checkpoint.json")).json()


def ballast(instance, directory, args):
    from stowage.io import save_instance, save_plan
    from stowage.solver.twostep import TwoStepPlanner
    import hashlib
    runner = load("experiments/ballast/review_fix/campaign.py", "campaign_ballast")
    runner.HERE = directory
    result = TwoStepPlanner().plan(instance, seed=0, time_limit_s=args.search_s)
    probe = False
    if result.plan is None:
        if not args.diagnostic:
            return dict(status="no_cargo_candidate", solver_status=result.status, ballast_calls=0,
                        not_found_rate=None, checker_passed=None)
        from stowage.model import MovePlan, Provenance
        cargo_plan = MovePlan(id=f"{instance.id}.diagnostic-invalid-ballast", instance_id=instance.id,
            call_index=instance.call_index, moves=[], provenance=Provenance(
                created_by="campaign-g1.diagnostic-invalid-ballast-probe"))
        probe = True
    else:
        cargo_plan = result.plan
        save_plan(cargo_plan, directory / "cargo.candidate-plan.json")
    folder = directory / "inputs/case"
    save_instance(instance, folder / "instance.json")
    save_plan(cargo_plan, folder / "input-plan.json")
    save(directory / "manifest.json", [dict(case="case", instance_sha256=read_snapshot(folder / "instance.json").sha256, plan_sha256=read_snapshot(folder / "input-plan.json").sha256)])
    runner.run("measurement", 1, args.ballast_s, 1, args.check_s)
    row = read_snapshot((directory / "measurement/summary.json")).json()
    row["case"] = read_snapshot((directory / "measurement/case.json")).json()
    if probe:
        return dict(status="no_cargo_candidate", solver_status=result.status, ballast_calls=0,
            not_found_rate=None, checker_passed=None, diagnostic_ballast_probe=True,
            diagnostic_control=row,
            qualification="Diagnostic invalid empty-plan control; not a solver plan and excluded from production ballast rates")
    return row


def sampling(instance, directory, args):
    runner = load("experiments/mask/pass_or_dead_end.py", "campaign_sampling")
    from llm.stowlm.format import load_tokenizer
    row = runner.sample_and_check(instance, load_tokenizer(), seed=17, c2=True,
                                  ballast_seconds=args.ballast_s)
    row["production_c2_active"] = row.get("engine_diagnostics", {}).get("global_screen") == "on"
    if not row["production_c2_active"]:
        row["infrastructure_blocked"] = True
    return row


def replay(instance, directory, args):
    from stowage.io import save_plan
    from stowage.model import MovePlan
    from stowage.model import PlannerResult
    from stowage.planner import finalize
    runner = load("experiments/gold-plans/tiny/driver.py", "campaign_gold")
    config = runner.Config(output=directory, c2_off=False)
    plan = read_snapshot(args.plan).model(MovePlan)
    row = dict(verified=False, checker_passed=None, finalized_status=None, ballast_status=None)
    # Recheck every distinct solver candidate after real ballast finishing,
    # including candidates whose original pipeline rejected or timed out.
    try:
        result = finalize(instance, PlannerResult(status="ok", plan=plan,
            planner="campaign-g1.recheck", planner_version="1", seed=0), time_limit_s=args.ballast_s)
        row.update(finalized_status=result.status, ballast_status=result.metrics.get("ballast_status"))
        if result.plan is not None:
            plan = result.plan
            save_plan(plan, directory / "finalized-plan.json")
            row["finalized_plan_identity"] = hashlib.sha256(json.dumps(
                plan.model_dump(mode="json", exclude={"id", "provenance"}),
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        report = runner._check(instance, plan)
        row.update(checker_passed=report.passed,
            checker_hard_rules=[v.rule_id for v in report.violations if v.severity == "hard"])
        if report.passed and result.status == "ok":
            row["objective"] = report.count.weighted_nonproductive
    except Exception as exc:
        row["checker_error"] = f"{type(exc).__name__}: {exc}"
    try:
        row.update(runner._replay_mask(instance, plan, config))
    except Exception as exc:
        row.update(verified=False, status="failed", mask_error=f"{type(exc).__name__}: {exc}")
    row["gold_eligible"] = bool(row.get("verified") and row.get("checker_passed") and row.get("finalized_status") == "ok")
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", required=True, choices=("4.2", "4.6", "4.7", "4.8", "5.1", "replay"))
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--plan", type=Path)
    p.add_argument("--rung", default="tiny")
    p.add_argument("--split", choices=("train", "val"), default="train")
    p.add_argument("--instance-seed", type=int, default=0)
    p.add_argument("--diagnostic", action="store_true")
    for name, default in (("search", 60.), ("oracle", 30.), ("ballast", 30.), ("check", 30.), ("timeout", 240.)):
        p.add_argument(f"--{name}-s", type=float, default=default)
    args = p.parse_args()
    args.output = args.output.resolve()
    if not args.output.is_relative_to(HERE):
        p.error("output must stay inside campaign-g1")
    args.output.mkdir(parents=True, exist_ok=True)
    def interrupted(signum, frame):
        raise TimeoutError("campaign owned worker received termination; preserving diagnostic and shutting down its ballast worker")
    signal.signal(signal.SIGTERM, interrupted)
    started = time.monotonic()
    row = {}
    try:
        cache_fence()
        from stowage.model import Instance
        instance = read_snapshot(args.input).model(Instance)
        function = {"4.2": ballast, "4.6": exact, "4.7": scalable, "4.8": twostep,
                    "5.1": sampling, "replay": replay}[args.stage]
        row = dict(adapter_completed=True, measurement=function(instance, args.output, args))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        # The production epoch guard failing closed is an observed task outcome;
        # it is never a pass/dead-end or a reason to disable that guard.
        blocked = any(part in str(exc).lower() for part in (
            "epoch is stale", "requalify", "qualification", "certificate is stale"))
        row = dict(adapter_completed=blocked, error=error)
        if blocked:
            row["measurement"] = dict(status="infrastructure_blocked", error=error,
                infrastructure_blocked=True, checker_passed=None)
    finally:
        # Only public worker shutdown is used; never kill shared machine jobs.
        try:
            from stowage.solver.ballast_deadline import shutdown_worker
            shutdown_worker()
        except Exception:
            pass
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    divisor = 2**20 if sys.platform == "darwin" else 1024
    row.update(stage=args.stage, wall_s=time.monotonic()-started,
               attempt_id=os.environ.get("CAMPAIGN_ATTEMPT_ID"),
               peak_rss_mb=own/divisor, child_peak_rss_mb=child/divisor,
               peak_ram_upper_bound_mb=(own+child)/divisor, cores=1, gpu=False)
    save(args.output / "result.json", row)
    print(json.dumps(row), flush=True)
    return 0 if row["adapter_completed"] else 1


if __name__ == "__mp_main__":
    cache_fence()

if __name__ == "__main__":
    raise SystemExit(main())

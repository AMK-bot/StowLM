"""Pinned train/val quality comparisons for exact-window LNS (one core/process).

Compare the same fresh input before/after improvement, with the counting lower
bound and a time-capped exact A* solve. A* proves optimality only when its result
says OPTIMAL, and only in its reported engine domain; a timed-out incumbent is
not an optimum. LNS frees a small group while retaining the surrounding plan
(Shaw, 1998, doi:10.1007/3-540-49481-2_30). Construction-only objectives are
labelled unqualified until ballast finishing and the full checker accept them.
The driver never selects or opens frozen test specifications.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import time

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

SCOPE = Path(__file__).resolve().parent


def campaign():
    spec = importlib.util.spec_from_file_location("quality_fresh_helpers", SCOPE / "fresh_campaign.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(instance, result, *, args, helpers, target):
    """Count raw lifts, then separately certify; never mutate the raw witness."""
    from stowage.moves import apply_plan
    from stowage.solver.scalable_oracle import SearchDeadline
    row = dict(status=result.status, message=result.message, metrics=result.metrics,
               search_s=result.wall_time_s, completed=False, checker_passed=False,
               approved=False, cost=None, lifts=None)
    if result.plan is None:
        return row
    applied = apply_plan(instance, result.plan)
    aboard = {p.container_id for p in applied.final_state.ship.placements}
    row.update(shut_out=len(set(instance.load_list)-aboard),
               physical_violations=[v.model_dump(mode="json") for v in applied.violations],
               lifts=applied.count.model_dump(mode="json"))
    row["completed"] = result.status == "ok" and applied.legal and row["shut_out"] == 0
    if not row["completed"]:
        return row
    row["cost"] = applied.count.weighted_nonproductive
    row["qualification"] = "move-engine-only"
    if not args.check:
        return row
    started = time.monotonic()
    try:
        from stowage.checker import check
        from stowage.frozen import require_clean
        from stowage.planner import finalize
        with helpers.search_deadline(args.validation_limit_s):
            finalized = finalize(instance, result, time_limit_s=args.ballast_limit_s)
            row.update(finalized_status=finalized.status, finalized_message=finalized.message,
                       ballast_status=finalized.metrics.get("ballast_status"))
            report = check(instance, finalized.plan)
            require_clean(finalized, report, what="quality-window checked candidate")
            row.update(checker_passed=report.passed,
                       checker_violations=[v.model_dump(mode="json") for v in report.violations])
            if report.passed:
                row.update(qualification="full-checker", checked_cost=report.count.weighted_nonproductive)
                row["checked_ballast"] = (finalized.plan.ballast.model_dump(mode="json")
                                           if finalized.plan.ballast is not None else None)
                if not args.diagnostic_c2_off:
                    row["mask"] = helpers.replay(instance, finalized.plan)
                    row["approved"] = finalized.status == "ok" and row["mask"]["status"] == "passed"
                    if row["approved"]:
                        from stowage.io import save_plan
                        save_plan(finalized.plan, target)
                        row["approved_plan"] = str(target)
    except SearchDeadline:
        row["validation_error"] = "validation_timeout"
    except Exception as exc:
        row["validation_error"] = f"{type(exc).__name__}: {exc}"
    row["validation_s"] = time.monotonic()-started
    return row


def summarize(rows):
    out = {}
    for rung in ("tiny", "small", "medium"):
        group = [r for r in rows if r["selection"]["rung"] == rung]
        if not group:
            continue
        summary = dict(attempted=len(group), generation_failures=sum("generation_error" in r for r in group))
        for phase in ("construct", "improve"):
            measures = [r[phase] for r in group if phase in r]
            complete = [r for r in measures if r.get("completed")]
            costs = [r["cost"] for r in complete]
            gaps = [r["gap_to_lb"] for r in complete if r.get("gap_to_lb") is not None]
            exact_excess = [r["relative_excess_vs_exact"] for r in complete if r.get("relative_excess_vs_exact") is not None]
            summary[phase] = dict(attempted=len(measures), completed=len(complete),
                checker_passed=sum(r.get("checker_passed", False) for r in measures),
                approved=sum(r.get("approved", False) for r in measures),
                mean_cost=statistics.mean(costs) if costs else None,
                mean_candidate_gap_to_lb=statistics.mean(gaps) if gaps else None,
                mean_relative_excess_vs_exact=statistics.mean(exact_excess) if exact_excess else None,
                gap_qualification="See individual checker flags; construction-only costs are uncertified",
                mean_search_s=statistics.mean(r.get("search_s", 0) for r in measures) if measures else None,
                total_search_s=sum(r.get("search_s", 0) for r in measures))
        paired = [r for r in group if r.get("construct", {}).get("completed") and r.get("improve", {}).get("completed")]
        summary["paired"] = dict(n=len(paired),
            cost_saved=sum(r["construct"]["cost"]-r["improve"]["cost"] for r in paired),
            changed=sum(r["construct"]["cost"] != r["improve"]["cost"] for r in paired),
            worsened=sum(r["construct"]["cost"] < r["improve"]["cost"] for r in paired))
        out[rung] = summary
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instances-dir", type=Path)
    parser.add_argument("--rungs", nargs="+", choices=("tiny", "small", "medium"), default=["tiny", "small", "medium"])
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--seed-start", type=int, default=76000000)
    parser.add_argument("--solver-seed", type=int, default=0)
    parser.add_argument("--split", nargs="+", choices=("train", "val"), default=["train", "val"])
    parser.add_argument("--construction-limit-s", type=float, default=60)
    parser.add_argument("--improvement-limit-s", type=float, default=120)
    parser.add_argument("--exact-limit-s", type=float, default=60)
    parser.add_argument("--exact-rungs", nargs="*", choices=("tiny", "small", "medium"), default=["tiny", "small"])
    parser.add_argument("--max-nodes", type=int, default=100000)
    parser.add_argument("--lns-config", type=json.loads, default={}, help="ScalableConfig JSON overrides for improvement")
    parser.add_argument("--construct-config", type=json.loads, default={}, help="ScalableConfig JSON overrides; max_neighborhoods forced to zero")
    parser.add_argument("--diagnostic-c2-off", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--ballast-limit-s", type=float, default=30)
    parser.add_argument("--validation-limit-s", type=float, default=90)
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args(argv)
    limits = (args.construction_limit_s, args.improvement_limit_s, args.exact_limit_s,
              args.ballast_limit_s, args.validation_limit_s)
    if args.count < 1 or args.max_nodes < 1 or not all(math.isfinite(x) and x > 0 for x in limits):
        parser.error("counts/node budget and deadlines must be positive and finite")
    for name in ("output", "instances_dir"):
        path = getattr(args, name)
        if path is not None:
            path = path.resolve()
            if not path.is_relative_to(SCOPE):
                parser.error(f"{name} must be under {SCOPE}")
            setattr(args, name, path)
    fresh = campaign()
    selected = fresh.selections(args.rungs, args.count, args.seed_start, args.split)
    if args.list_only:
        print(json.dumps(selected, indent=2)); return 0
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.scalable import ScalableConfig, ScalableSolver, improve
    from stowage.versions import component_versions
    feasibility = FeasibilityConfig(extra={"global_screen": 0}) if args.diagnostic_c2_off else None
    forbidden = {"feasibility_config"}
    if forbidden.intersection(args.construct_config) or forbidden.intersection(args.lns_config):
        parser.error("set feasibility configuration with --diagnostic-c2-off only")
    base = ScalableConfig(**{**args.construct_config, "max_neighborhoods": 0, "feasibility_config": feasibility})
    lns = replace(base, **{**args.lns_config, "max_neighborhoods": args.lns_config.get("max_neighborhoods", 12)})
    helpers = fresh.helpers()
    common = dict(source_sha256=helpers.source_hash(), driver_sha256=fresh.sha(Path(__file__)),
                  versions=component_versions(), solver_version=ScalableSolver.version,
                  construction_config=asdict(base), improvement_config=asdict(lns),
                  diagnostic_c2_off=args.diagnostic_c2_off, cores=1,
                  budgets_s=dict(construction=args.construction_limit_s, improvement=args.improvement_limit_s,
                                 exact=args.exact_limit_s, ballast=args.ballast_limit_s, validation=args.validation_limit_s),
                  exact_rungs=args.exact_rungs, exact_max_nodes=args.max_nodes, check=args.check,
                  solver_seed=args.solver_seed)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = dict(identity=common, selections=selected)
    manifest_path = args.output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("source/configuration/selection changed; use a fresh output directory")
    fresh.save(manifest_path, manifest)
    rows = []
    for entry in selected:
        name = f"{entry['rung']}-{entry['spec']['seed']}"
        target = args.output / "results" / f"{name}.json"
        if target.exists():
            row = json.loads(target.read_text())
            if row["identity"] != common or row["selection"] != entry:
                raise ValueError("checkpoint identity mismatch")
            if row.get("finished"):
                rows.append(row); continue
            raise ValueError("incomplete phase checkpoint; retain it and choose a fresh output directory")
        row = dict(identity=common, selection=entry)
        started = time.monotonic()
        try:
            instance, audit, path = (fresh.load_generated(entry, args.instances_dir) if args.instances_dir else
                                    fresh.generate(entry, args.output / "instances"))
        except Exception as exc:
            row.update(generation_error=f"{type(exc).__name__}: {exc}",
                       generation_status="background_balance_failure" if "cannot balance slice background" in str(exc) else "generation_failure",
                       generation_s=time.monotonic()-started, finished=True)
            fresh.save(target, row); rows.append(row)
            fresh.save(args.output / "summary.json", summarize(rows)); continue
        row.update(input_path=str(path), input_sha256=audit["sha256"], generation_audit=audit,
                   boxes=len(instance.load_list))
        if args.generate_only:
            row.update(generation_only=True, finished=True)
            fresh.save(target, row); rows.append(row); continue
        from stowage.bounds import lower_bound
        from stowage.solver.scalable_oracle import SearchDeadline
        row["lower_bound"] = lower_bound(instance).model_dump(mode="json")
        bound = row["lower_bound"]["weighted"]
        raw = None
        for phase, budget in (("construct", args.construction_limit_s), ("improve", args.improvement_limit_s)):
            if phase == "improve" and (raw is None or not row.get("construct", {}).get("completed")):
                row[phase] = dict(status="skipped_no_complete_incumbent"); continue
            phase_start = time.monotonic()
            try:
                with helpers.search_deadline(budget):
                    result = (ScalableSolver(base).plan(instance, seed=args.solver_seed, time_limit_s=budget)
                              if phase == "construct" else improve(instance, raw.plan, seed=args.solver_seed,
                                                                    time_limit_s=budget, config=lns))
                if phase == "construct":
                    raw = result
                row[phase] = evaluate(instance, result, args=args, helpers=helpers,
                                      target=args.output / "results" / f"{name}.{phase}.approved-plan.json")
                if phase == "construct" and row[phase].get("checker_passed") and row[phase].get("checked_ballast"):
                    # Improve the checked incumbent, including its ballast,
                    # rather than silently discarding the finisher's work.
                    from stowage.model import BallastCondition
                    raw = result.model_copy(update={"plan": result.plan.model_copy(update={
                        "ballast": BallastCondition.model_validate(row[phase]["checked_ballast"])})})
                cost = row[phase].get("cost")
                if cost is not None:
                    row[phase].update(gap_to_lb=(cost-bound)/cost if cost else (0 if bound == 0 else None),
                                      gap_qualification="certified" if row[phase]["checker_passed"] else "uncertified candidate")
            except SearchDeadline:
                row[phase] = dict(status="timeout", search_s=time.monotonic()-phase_start)
            except Exception as exc:
                row[phase] = dict(status="error", error=f"{type(exc).__name__}: {exc}", search_s=time.monotonic()-phase_start)
            fresh.save(target, row)
        if entry["rung"] in args.exact_rungs:
            from stowage.solver.exact import solve_exact
            phase_start = time.monotonic()
            try:
                exact = solve_exact(instance, time_limit_s=args.exact_limit_s, max_nodes=args.max_nodes,
                                    seed=args.solver_seed, num_workers=1, feasibility_config=feasibility)
                row["exact"] = dict(status=exact.status, objective=exact.objective, lower_bound=exact.lower_bound,
                                    gap=exact.gap, search_s=exact.wall_time_s, metrics=exact.metrics,
                                    expanded_nodes=exact.expanded_nodes,
                                    domain="operational engine with explicit diagnostic C2-off" if args.diagnostic_c2_off else "operational default engine")
                if exact.status == "OPTIMAL" and exact.objective is not None:
                    for phase in ("construct", "improve"):
                        cost = row[phase].get("cost")
                        if cost is not None:
                            row[phase].update(relative_excess_vs_exact=(cost-exact.objective)/exact.objective if exact.objective else (0 if cost == 0 else None),
                                              exact_comparison_qualification="both checker-passing" if row[phase].get("checker_passed") else "candidate is uncertified")
            except Exception as exc:
                row["exact"] = dict(status="error", error=f"{type(exc).__name__}: {exc}", search_s=time.monotonic()-phase_start)
        row.update(finished=True, total_pipeline_s=time.monotonic()-started,
                   source_sha256_after=helpers.source_hash())
        row["source_changed_during_run"] = row["source_sha256_after"] != common["source_sha256"]
        fresh.save(target, row); rows.append(row)
        fresh.save(args.output / "summary.json", summarize(rows))
        print(json.dumps(dict(rung=entry["rung"], seed=entry["spec"]["seed"],
                              construct=row["construct"].get("cost"), improve=row["improve"].get("cost"),
                              exact=row.get("exact", {}).get("objective"))), flush=True)
    fresh.save(args.output / "summary.json", summarize(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

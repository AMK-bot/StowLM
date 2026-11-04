"""Sequential, resumable solver measurements; only G1-passing plans count as solved.

The gap is (objective - lower bound) / objective, zero when both are zero.
A lower bound counts unavoidable work, so the gap is a certificate of remaining
possible improvement, not an estimate of distance to the unknown optimum.
See stowage.bounds for the counting proofs and references.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
from stowage.conventions import VESSEL_ID_ALIASES
from statistics import mean
import time

# Set before importing numerical libraries; the harness has no parallel jobs.
for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                  "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_variable] = "1"


def replay_mask(instance, plan, *, planning_mode=False) -> dict:
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    from stowage.frozen import require_clean

    try:
        space = get_action_space()
        if planning_mode:
            from stowage.solver.scalable_oracle import build_engine
            engine = build_engine(instance, space)
        else:
            engine = make_feasibility_engine(instance, space)
        actions = space.from_plan(instance, plan)
        for index, action in enumerate(actions):
            if not engine.is_legal(action):
                return {"status": "failed", "action_index": index}
            engine.push(action)
        if not engine.is_complete():
            return {"status": "failed", "reason": "replay incomplete"}
        require_clean(engine.to_plan(), what="scalable benchmark mask replay")
        mode = getattr(engine, "adapter_mode", "native")
        diagnostic = "diagnostic" in mode or "v1" in mode
        return {"status": "diagnostic_passed" if diagnostic else "passed",
                "actions": len(actions), "adapter_mode": mode}
    except NotImplementedError as exc:
        return {"status": "unavailable", "reason": str(exc)}
    except Exception as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def measure(instance, planner, seed: int, limit: float, ballast_limit: float, *, planning_mask=False) -> dict:
    from stowage.bounds import lower_bound
    from stowage.checker import check
    from stowage.frozen import require_clean
    from stowage.planner import finalize

    row = {"planner": planner.name, "version": planner.version,
           "status": "error", "checker_passed": False, "solved": False, "checker_solved": False,
           "mask": {"status": "no_plan"}, "objective": None, "gap": None,
           "lower_bound": lower_bound(instance).weighted}
    start = time.monotonic()
    try:
        require_clean(instance, what="scalable benchmark")
        result = planner.plan(instance, seed=seed, time_limit_s=limit)
        row.update(raw_status=result.status, solver_wall_time_s=result.wall_time_s,
                   solver_metrics=result.metrics, message=result.message)
        row["complete"] = result.status == "ok"
        result = finalize(instance, result, time_limit_s=ballast_limit)
        row["status"] = result.status
        row["final_message"] = result.message
        if result.plan is not None:
            report = check(instance, result.plan)
            require_clean(result, result.plan, report, what="scalable benchmark")
            row.update(checker_passed=report.passed,
                       violations=sorted({v.rule_id for v in report.violations}),
                       shut_out=len(report.shut_out))
            # A partial plan cannot pass G1, and replaying it through a witness
            # engine needlessly starts another whole-completion search.
            row["mask"] = (replay_mask(instance, result.plan, planning_mode=planning_mask)
                           if row["complete"] else {"status": "incomplete"})
            if report.passed and report.count is not None:
                objective = report.count.weighted_nonproductive
                bound = row["lower_bound"]
                row["objective"] = objective
                row["gap"] = ((objective - bound) / objective if objective else
                              (0.0 if bound == 0 else None))
                row["checker_solved"] = result.status == "ok"
                row["solved"] = (result.status == "ok" and
                                 row["mask"]["status"] == "passed")
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["total_wall_time_s"] = time.monotonic() - start
    return row


def summary(rows: list[dict]) -> str:
    lines = ["| Rung | n | Solved after ballast/checker | Objective¹ | Gap to LB¹ | Time (s)² | Checker | Mask |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    for rung in ("tiny", "small", "medium", "full", "feeder_500_450", "feeder_800_900"):
        group = [r["scalable"] for r in rows if r["rung"] == rung]
        if not group:
            lines.append(f"| {rung} | 0 | unmeasured | — | — | — | unmeasured | unmeasured |")
            continue
        objectives = [r["objective"] for r in group if r["objective"] is not None]
        gaps = [r["gap"] for r in group if r["gap"] is not None]
        obj = f"{mean(objectives):.3f}" if objectives else "—"
        gap = f"{mean(gaps):.1%}" if gaps else "—"
        masks = ", ".join(f"{s}:{sum(r['mask']['status'] == s for r in group)}"
                          for s in sorted({r["mask"]["status"] for r in group}))
        n = len(group)
        not_checked = sum("violations" not in r for r in group)
        checker = f"{sum(r['checker_passed'] for r in group)}/{n}"
        if not_checked:
            checker += f" (not run:{not_checked})"
        times = [r["total_wall_time_s"] for r in group if r["total_wall_time_s"] is not None]
        timing = f"{mean(times):.3f}" if times else "—"
        lines.append(f"| {rung} | {n} | {sum(r.get('checker_solved', r['solved']) for r in group)}/{n} | {obj} | {gap} | "
                     f"{timing} | "
                     f"{checker} | {masks} |")
    return "\n".join(lines) + "\n\n¹ Means over checker-passing plans only; — means none. " \
        "² Mean plan + finalize + checker + mask time; instance loading excluded.\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rungs", nargs="+", choices=["tiny", "small", "medium", "full", "feeder_500_450", "feeder_800_900"], default=["tiny"])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--time-limit-s", type=float, default=2)
    parser.add_argument("--ballast-limit-s", type=float, default=2)
    parser.add_argument("--instances", type=Path, default=Path("experiments/scalable-solver/instances"))
    parser.add_argument("--output", type=Path, default=Path("experiments/scalable-solver/results"))
    parser.add_argument("--compare-exact", action="store_true")
    parser.add_argument("--planning-mask", action="store_true",
                        help="Replay with the planning adapter; interim diagnostic success is not G1 approval")
    args = parser.parse_args(argv)
    if args.count < 1 or any(not math.isfinite(t) or t <= 0 for t in (args.time_limit_s, args.ballast_limit_s)):
        parser.error("count and finite time limits must be positive")
    from stowage.io import load_instance
    from stowage.solver.scalable import ScalableSolver
    from stowage.solver.scalable_oracle import build_engine
    from stowage.versions import component_versions

    planner = ScalableSolver()
    exact = None
    if args.compare_exact:
        from stowage.solver import exact as exact_module
        exact_class = getattr(exact_module, "ExactPlanner", None) or getattr(exact_module, "ExactSolver", None)
        if exact_class is None:
            parser.error("exact comparison implementation is unavailable")
        parameters = inspect.signature(exact_class).parameters
        if "num_workers" not in parameters:
            parser.error("exact comparison needs an explicit num_workers constructor parameter")
        exact = exact_class(num_workers=1)
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    audit_path = args.instances / "generation-audit.json"
    generation = json.loads(audit_path.read_text()) if audit_path.exists() else []
    for rung in args.rungs:
        for seed in range(args.seed, args.seed + args.count):
            # Archived profile directories retain their original spelling.
            input_rungs = [rung, *(old + rung[len(current):]
                for old, current in VESSEL_ID_ALIASES.items()
                if rung.startswith(current + "_"))]
            path = next((args.instances / candidate / f"seed-{seed:08d}.json"
                         for candidate in input_rungs
                         if (args.instances / candidate / f"seed-{seed:08d}.json").exists()),
                        args.instances / rung / f"seed-{seed:08d}.json")
            blocked = next((item for item in generation if item["profile"] in input_rungs
                            and item["seed"] == seed and item["status"] == "blocked"), None)
            if not path.exists() and blocked is None:
                raise FileNotFoundError(f"Fresh input missing: {path}; run generate_fresh.py first")
            config = {"rung": rung, "seed": seed,
                      "instance_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                      "generation_error": blocked["error"] if blocked and not path.exists() else None,
                      "time_limit_s": args.time_limit_s, "ballast_limit_s": args.ballast_limit_s,
                      "planning_mask": args.planning_mask,
                      "scalable_version": planner.version, "versions": component_versions(),
                      "scalable_source_sha256": hashlib.sha256(Path(inspect.getfile(ScalableSolver)).read_bytes()).hexdigest(),
                      "oracle_adapter_source_sha256": hashlib.sha256(Path(inspect.getfile(build_engine)).read_bytes()).hexdigest(),
                      "benchmark_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      "exact_source_sha256": (hashlib.sha256(Path(inspect.getfile(type(exact))).read_bytes()).hexdigest()
                                              if exact is not None else None),
                      "exact_version": exact.version if exact is not None and rung in ("tiny", "small") else None}
            output = args.output / f"{rung}-{seed:08d}.json"
            if output.exists():
                row = json.loads(output.read_text())
                if row["config"] != config:
                    raise ValueError(f"resume configuration mismatch: {output}; choose another --output")
            else:
                instance = load_instance(path) if path.exists() else None
                if instance is None:
                    measurement = {"status": "input_blocked", "checker_passed": False,
                                   "solved": False, "checker_solved": False, "objective": None,
                                   "gap": None, "total_wall_time_s": None,
                                   "mask": {"status": "input_blocked"}, "message": blocked["error"]}
                else:
                    measurement = measure(instance, planner, seed, args.time_limit_s, args.ballast_limit_s,
                                          planning_mask=args.planning_mask)
                row = {"rung": rung, "boxes": len(instance.load_list) if instance else blocked["requested_boxes"],
                       "config": config, "input_status": "ready" if instance else "blocked",
                       "scalable": measurement}
                if instance is not None and exact is not None and rung in ("tiny", "small"):
                    row["exact"] = measure(instance, exact, seed, args.time_limit_s, args.ballast_limit_s)
                    a, b = row["scalable"]["objective"], row["exact"]["objective"]
                    row["scalable_minus_exact"] = a - b if a is not None and b is not None else None
                temporary = output.with_suffix(".partial.json")
                temporary.write_text(json.dumps(row, indent=2, sort_keys=True, allow_nan=False) + "\n")
                temporary.replace(output)
            rows.append(row)
            print(json.dumps({"rung": rung, "seed": seed, "status": row["scalable"]["status"],
                              "solved": row["scalable"]["solved"]}), flush=True)
    table = summary(rows)
    (args.output / "summary.md").write_text(table)
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

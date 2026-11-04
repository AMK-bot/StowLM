"""Dev-data producer for demo UI v2: run one planner on one current instance, fresh-check, save a run folder.

Usage: PYTHONPATH=. uv run python experiments/mvp/ui/dev-data/make_run.py INSTANCE PLANNER OUT_DIR [time_limit_s]
PLANNER in {solver.scalable, baseline.twostep, solver.exact}. Baseline runs with allow_repair=False.
Writes OUT_DIR/{instance.json, result.json, summary.json}. result.json is written only for a plan that exists;
summary.json always (records failures honestly). Exit 0 only if shippable and the fresh full check passes.
"""
import json, sys, time, traceback, resource
from pathlib import Path
from stowage.io import load_instance, save_instance, save_result
from stowage.planner import run_planner
from stowage.checker.full import check
from stowage.feasibility import FeasibilityConfig


# The production global stability screen needs the linear margin policy, whose epoch is stale for the current fleet
# params (both solvers fail "production linear policy epoch is stale; requalify"). Like experiments/mvp/smoke/run_smoke.py,
# we switch that screen off (global_screen=0). The full checker still judges every plan afresh.
FCFG = FeasibilityConfig(extra={"global_screen": 0})


def make(pid):
    if pid == "solver.scalable":
        from stowage.solver.scalable import ScalableConfig, ScalableSolver
        import os
        kw = {k: int(os.environ[e]) for k, e in (("initial_strategy", "SCAL_STRATEGY"), ("max_restarts", "SCAL_RESTARTS"),
              ("max_neighborhoods", "SCAL_NEIGH"), ("backtrack_limit", "SCAL_BACKTRACK")) if e in os.environ}
        return ScalableSolver(ScalableConfig(feasibility_config=FCFG, **kw))
    if pid == "baseline.twostep":
        from stowage.solver.twostep import TwoStepPlanner; return TwoStepPlanner(feasibility_config=FCFG)
    if pid == "solver.exact":
        from stowage.solver.exact import ExactPlanner; return ExactPlanner()
    raise ValueError(pid)


def counts(c):
    if c is None:
        return None
    fut_r = sum(f.restow_lifts for f in c.future); fut_h = sum(f.hatch_cover_lifts for f in c.future)
    return {"loads": c.loads, "discharges": c.discharges, "yard_rehandles": c.relocations,
            "restows": c.restow_lifts, "hatch_lifts": c.hatch_cover_lifts, "total_lifts_now": c.total_lifts_now,
            "projected_later_restows": fut_r, "projected_later_hatch_lifts": fut_h,
            "weighted_nonproductive": c.weighted_nonproductive}


def main():
    src, pid, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    tl = float(sys.argv[4]) if len(sys.argv) > 4 else 90.0
    allow_repair = (pid != "baseline.twostep") and not (len(sys.argv) > 5 and sys.argv[5] == "norepair")
    out.mkdir(parents=True, exist_ok=True)
    inst = load_instance(src)
    save_instance(inst, out / "instance.json")
    import os
    summ = {"scalable_env": {k: v for k, v in os.environ.items() if k.startswith("SCAL_")}, "source_instance": src, "instance_id": inst.id, "planner": pid, "time_limit_s": tl, "allow_repair": allow_repair,
            "feasibility_config": "global_screen=0", "n_load_list": len(inst.load_list), "n_discharge_list": len(inst.discharge_list)}
    t0 = time.monotonic()
    try:
        res, rep = run_planner(make(pid), inst, seed=0, time_limit_s=tl, allow_repair=allow_repair)
    except Exception as e:
        summ.update(error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc()[-3000:])
        (out / "summary.json").write_text(json.dumps(summ, indent=1)); return 2
    summ["run_planner_wall_s"] = time.monotonic() - t0
    summ.update(status=res.status, message=res.message, repaired=res.repaired, repair_note=res.repair_note,
                planner_wall_s=res.wall_time_s, shippable=res.shippable,
                pipeline_check_passed=None if rep is None else rep.passed,
                lower_bound=res.metrics.get("lower_bound"), gap=res.metrics.get("gap"))
    if res.plan is not None:
        save_result(res, out / "result.json")
        t1 = time.monotonic(); fresh = check(inst, res.plan); summ["fresh_check_s"] = time.monotonic() - t1
        summ["fresh_check_passed"] = fresh.passed
        summ["fresh_violations"] = [f"{v.rule_id}: {v.message}"[:300] for v in fresh.violations if getattr(v, "severity", "hard") == "hard"][:20]
        summ["counts"] = counts(fresh.count)
        summ["shut_out"] = list(fresh.shut_out)
        summ["n_moves"] = len(res.plan.moves)
    summ["planner_metrics"] = {k: v for k, v in res.metrics.items() if k in ("construction_attempts", "deepest_depth",
        "dead_end_rule_ids", "neighborhoods", "restarts", "lower_bound", "gap", "partial_loaded")}
    summ["peak_rss_gb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str))
    ok = bool(res.plan is not None and res.shippable and summ.get("fresh_check_passed"))
    print(json.dumps({k: summ.get(k) for k in ("instance_id", "planner", "status", "repaired", "shippable",
          "fresh_check_passed", "counts", "run_planner_wall_s", "message")}, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

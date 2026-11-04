"""Diagnostic: does planner.finalize find ballast for C3 given a warm process and a longer limit?

Loads the C3 instance saved by timing.py, runs planner.finalize with --limit seconds and checks
the result. Pure production code. Output: finisher_long_<ship>.json
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
from stowage.model import Instance, MovePlan, PlannerResult  # noqa: E402
from stowage.checker.full import check  # noqa: E402
from stowage.planner import finalize  # noqa: E402


def main():
    ship, limit = sys.argv[1], float(sys.argv[2])
    inst = Instance.model_validate_json((HERE / ".timing" / f"{ship}.instance.json").read_text())
    saved = MovePlan.model_validate_json((HERE / ".timing" / f"{ship}.plan.json").read_text())
    plan = MovePlan(id=f"{inst.id}.long", instance_id=inst.id, call_index=inst.call_index)
    out = dict(ship=ship, condition="C3", limit_s=limit, saved_30s_plan_has_ballast=saved.ballast is not None)
    t0 = time.perf_counter()
    fin = finalize(inst, PlannerResult(status="ok", plan=plan, planner="diag", planner_version="1", seed=0), time_limit_s=limit)
    out.update(finalize_s=time.perf_counter() - t0, status=fin.status, ballast_status=fin.metrics.get("ballast_status"),
               message=(fin.message or "")[:400])
    if fin.plan is not None and fin.plan.ballast is not None:
        t0 = time.perf_counter()
        r = check(inst, fin.plan)
        out.update(check_s=time.perf_counter() - t0, passed=r.passed,
                   ballast_total_kg=sum(fin.plan.ballast.tank_masses_kg.values()),
                   violations=sorted({(v.rule_id, v.measured, v.limit) for v in r.violations}, key=str)[:30])
    (HERE / f"finisher_long_{ship}.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps(out, default=str)[:1500])


if __name__ == "__main__":
    main()

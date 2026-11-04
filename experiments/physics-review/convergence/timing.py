"""Time stowage.checker.full.check cold and warm on the C3 (export mix) condition.

  prepare: build C3 + finalize ballast, save instance/plan JSON (uses harness).
  time:    in a FRESH process (caller sets STOWAGE_VESSEL_CACHE_DIR to an empty
           dir), load the saved JSON, time the first check (cold), then 3+ more
           (warm, median).
Pure production code; no spies are installed in the timing step.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAVE = HERE / ".timing"


def prepare(ship):
    sys.path.insert(0, str(HERE))
    import harness
    from stowage.model import MovePlan, PlannerResult
    from stowage.planner import finalize
    import stowage.vessel.fleet as fleet
    v = fleet.load_vessel(ship, with_hull=True)
    p = fleet.load_ship_params(ship)
    inst, _ = harness.build_condition(ship, v, p, "C3")
    plan = MovePlan(id=f"{inst.id}.t", instance_id=inst.id, call_index=inst.call_index)
    fin = finalize(inst, PlannerResult(status="ok", plan=plan, planner="t", planner_version="1", seed=0))
    final = fin.plan if fin.plan is not None and fin.plan.ballast is not None else plan
    SAVE.mkdir(exist_ok=True)
    (SAVE / f"{ship}.instance.json").write_text(inst.model_dump_json())
    (SAVE / f"{ship}.plan.json").write_text(final.model_dump_json())


def timed(ship, repeats=3):
    t_import = time.perf_counter()
    from stowage.model import Instance, MovePlan
    from stowage.checker.full import check
    import_s = time.perf_counter() - t_import
    inst = Instance.model_validate_json((SAVE / f"{ship}.instance.json").read_text())
    plan = MovePlan.model_validate_json((SAVE / f"{ship}.plan.json").read_text())
    t0 = time.perf_counter()
    r = check(inst, plan)
    cold = time.perf_counter() - t0
    warm, parts = [], []
    for _ in range(repeats):
        t0 = time.perf_counter()
        r2 = check(inst, plan)
        warm.append(time.perf_counter() - t0)
    st = r.parts.get("stability")
    out = dict(ship=ship, condition="C3", cold_s=cold, warm_s=warm, warm_median_s=statistics.median(warm),
               import_s=import_s, passed=r.passed, passed_warm=r2.passed,
               stability_geometry_table_used=st.metrics.get("geometry_table_used") if st else None,
               stability_liquid_table_used=st.metrics.get("liquid_table_used") if st else None,
               stability_table_limit_fallback=st.metrics.get("geometry_table_limit_fallback") if st else None,
               vessel_cache_dir=os.environ.get("STOWAGE_VESSEL_CACHE_DIR"))
    print(json.dumps(out))


if __name__ == "__main__":
    mode, ship = sys.argv[1], sys.argv[2]
    prepare(ship) if mode == "prepare" else timed(ship, int(sys.argv[3]) if len(sys.argv) > 3 else 3)

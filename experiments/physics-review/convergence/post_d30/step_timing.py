"""Time each step of one harness condition (build / finalize / check) separately."""
import sys, time, json
sys.path.insert(0, "experiments/physics-review/convergence")
t = time.perf_counter()
import harness  # installs passive spies
import stowage.vessel.fleet as fleet
from stowage.model import MovePlan, PlannerResult
from stowage.planner import finalize
from stowage.checker.full import check
ship, cond, lim = sys.argv[1], sys.argv[2], float(sys.argv[3])
def lap(msg):
    global t
    now = time.perf_counter(); print(f"{msg}: {now - t:.1f}s", flush=True); t = now
lap("import")
v = fleet.load_vessel(ship, with_hull=True); lap("load_vessel")
p = fleet.load_ship_params(ship); lap("params")
inst, info = harness.build_condition(ship, v, p, cond); lap("build_condition")
plan = MovePlan(id="x", instance_id=inst.id, call_index=inst.call_index)
r0 = check(inst, plan); lap(f"check(no ballast) passed={r0.passed}")
fin = finalize(inst, PlannerResult(status="ok", plan=plan, planner="t", planner_version="1", seed=0), time_limit_s=lim)
lap(f"finalize status={fin.status} ballast={fin.metrics.get('ballast_status')}")

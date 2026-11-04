"""Profile the slow check on 500 C1 (no ballast) and print stability metrics."""
import cProfile, pstats, sys, time, json
sys.path.insert(0, "experiments/physics-review/convergence")
import harness
import stowage.vessel.fleet as fleet
from stowage.model import MovePlan
from stowage.checker.full import check
ship, cond = sys.argv[1], sys.argv[2]
v = fleet.load_vessel(ship, with_hull=True); p = fleet.load_ship_params(ship)
inst, info = harness.build_condition(ship, v, p, cond)
plan = MovePlan(id="x", instance_id=inst.id, call_index=inst.call_index)
pr = cProfile.Profile(); t0 = time.perf_counter(); pr.enable(); r = check(inst, plan); pr.disable()
st = r.parts.get("stability")
print(json.dumps(dict(s=time.perf_counter()-t0, part_s={k: getattr(x, "seconds", None) for k, x in r.parts.items()},
      metrics={k: v for k, v in (st.metrics if st else {}).items() if any(w in k for w in ("table","fallback","method","draft","trim"))}), default=str))
pstats.Stats(pr).sort_stats("cumulative").print_stats(45)

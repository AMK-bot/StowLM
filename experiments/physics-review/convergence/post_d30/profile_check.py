"""Profile one production check on a saved C3 instance (post-D30, production caches)."""
import cProfile, pstats, sys, time, json
from pathlib import Path
from stowage.model import Instance, MovePlan
from stowage.checker.full import check
SAVE = Path("experiments/physics-review/convergence/.timing")
ship = sys.argv[1]
inst = Instance.model_validate_json((SAVE / f"{ship}.instance.json").read_text())
plan = MovePlan.model_validate_json((SAVE / f"{ship}.plan.json").read_text())
pr = cProfile.Profile(); t0 = time.perf_counter(); pr.enable(); r = check(inst, plan); pr.disable()
cold = time.perf_counter() - t0
st = r.parts.get("stability")
print(json.dumps(dict(ship=ship, cold_s=cold, passed=r.passed,
      metrics={k: v for k, v in (st.metrics if st else {}).items() if "table" in k or "fallback" in k or "method" in k})))
pstats.Stats(pr).sort_stats("cumulative").print_stats(30)

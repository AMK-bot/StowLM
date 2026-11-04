"""Full-check timing on the C1 witness condition: fresh process (disk caches warm, post-D30) then warm repeats.

'cold' here = first check in a new process with the production disk caches already built by
`python -m stowage.cache_warm` (in-process caches empty). Fully cold (no disk caches) was measured
before D30: 467 / 794 / 1,223 s (../logs/timing_*.log) and is now a cache_warm cost, not a checker cost.
"""
import json, statistics, sys, time
t_imp = time.perf_counter()
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness2 as h2
import stowage.vessel.fleet as fleet
from stowage.model import MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker.full import check
imp = time.perf_counter() - t_imp
ship = sys.argv[1]
w = json.loads((HERE / f"search_{ship}_C1.json").read_text())["witness"]
t0 = time.perf_counter()
v = fleet.load_vessel(ship, with_hull=True); p = fleet.load_ship_params(ship)
cap = {t.id: t.capacity_m3 * t.content_density_kg_m3 for t in v.tanks if t.adjustable}
masses = {k: w["ballast"].get(k, 0.0) * 1e3 for k in cap}
inst, _ = h2.build(ship, v, p, "C1", sum(masses.values()))
plan = MovePlan(id="t", instance_id=inst.id, call_index=inst.call_index, ballast=BallastCondition(tank_masses_kg=masses, source="planner"))
load_s = time.perf_counter() - t0
t0 = time.perf_counter(); r = check(inst, plan); cold = time.perf_counter() - t0
warm = []
for _ in range(3):
    t0 = time.perf_counter(); check(inst, plan); warm.append(time.perf_counter() - t0)
m = r.parts["stability"].metrics
print(json.dumps(dict(ship=ship, import_s=imp, load_build_s=load_s, cold_s=cold, warm_s=warm, warm_median_s=statistics.median(warm),
                      passed=r.passed, geometry_table_used=m.get("geometry_table_used"), liquid_table_used=m.get("liquid_table_used"))))

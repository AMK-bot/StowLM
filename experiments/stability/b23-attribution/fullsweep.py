"""Full checker (all parts, D57 three states) on symmetric double-bottom combos of an empty plan."""
import itertools, json, sys, time
from pathlib import Path
from stowage.model import Instance, MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker import check
sys.path.insert(0, str(Path(__file__).parent))
from sweep import pairs
path = Path(sys.argv[1]); out = Path(sys.argv[2]); fracs = [float(x) for x in sys.argv[3:]] or [0, .5, 1]
inst = Instance.model_validate_json(path.read_text())
plan = MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[])
ps = pairs(inst); groups = [g for g in ("DB-A", "DB-M", "DB-F") if g in ps]
for combo in itertools.product(fracs, repeat=len(groups)):
    t = time.time()
    ballast = {tk.id: f * tk.capacity_m3 * tk.content_density_kg_m3 for g, f in zip(groups, combo) for tk in ps[g]}
    rep = check(inst, plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg=ballast, source="planner")}))
    viol = sorted({(v.rule_id) for v in rep.violations})
    gm = {k: round(v, 3) for p in rep.parts.values() for k, v in p.metrics.items() if k.endswith("gm_m") or "max_gm" in k}
    rec = dict(instance=path.name, combo=dict(zip(groups, combo)), passed=rep.passed, rules=viol, wall_s=time.time()-t)
    with out.open("a") as fh: fh.write(json.dumps(rec) + "\n")
    print(rec["combo"], rep.passed, viol, f"{rec['wall_s']:.1f}s", flush=True)

"""Full check of the empty plan with zero ballast and with 100% DB-A on saved batch instances: B 2.3 per D57 state."""
import json, sys, time
from pathlib import Path
from stowage.model import Instance, MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker import check
sys.path.insert(0, str(Path(__file__).parent))
from sweep import pairs, KEYS, COEF
HERE = Path(__file__).parent
def main():
    out = HERE / f"fullzero-{sys.argv[1]}.jsonl"
    done = {json.loads(l)["file"] + json.dumps(json.loads(l)["recipe"]) for l in out.open()} if out.exists() else set()
    shard, n = int(sys.argv[1]), int(sys.argv[2])
    files = sorted((HERE / "instances").glob("*.json")) + sorted(Path("experiments/mask/c2-on-current").glob("*.instance.json"))
    for path in files[shard::n]:
        inst = Instance.model_validate_json(path.read_text())
        ps = pairs(inst)
        for recipe in ({},):
            if path.name + json.dumps(recipe) in done: continue
            t = time.time()
            plan = MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[],
                ballast=BallastCondition(tank_masses_kg={tk.id: f*tk.capacity_m3*tk.content_density_kg_m3 for g, f in recipe.items() for tk in ps.get(g, [])}, source="planner"))
            rep = check(inst, plan)
            states = {}
            for name, p in rep.parts.items():
                if name.startswith("condition:") and name.endswith(":stability"):
                    m = p.metrics; C = m.get("containership_form_factor")
                    if C:
                        ratios = {k: m[k] / (COEF[k] / C) for k in KEYS}
                        w = min(ratios, key=ratios.get)
                        states[name.split(":")[-2]] = dict(ratio=ratios[w], worst=w, gm=m.get("gm_m"), kg=m.get("kg_m"), d=m.get("draught_mean_m"), phi_f_deg=m.get("containership_total_area_end_rad", 0)*57.2958, disp_t=m.get("displacement_kg", 0)/1e3)
            rules = sorted({v.rule_id for v in rep.violations} - {"objective.all_loaded"})
            rec = dict(file=path.name, vessel=inst.vessel.id, recipe=recipe, rules=rules, states=states, max_disp_t=inst.vessel.hull.max_displacement_kg/1e3, wall_s=time.time()-t)
            with out.open("a") as fh: fh.write(json.dumps(rec) + "\n")
            print(path.name, recipe, rules, {k: round(v["ratio"], 3) for k, v in states.items()}, flush=True)
if __name__ == "__main__":
    main()

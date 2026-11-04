"""Marginal effect of each completed masked plan vs background-only: unballasted departure B 2.3,
full check (3 D57 states) at zero ballast, and the ballast finisher at a generous budget."""
import json, sys, time
from pathlib import Path
from stowage.model import Instance, MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker import check
from stowage.checker.full import departure_condition
from stowage.checker.stability import check_stability
from stowage.solver.ballast import finish_ballast
sys.path.insert(0, str(Path(__file__).parent))
from sweep import b23, KEYS, COEF
HERE = Path(__file__).parent
def states(rep):
    out = {}
    for name, p in rep.parts.items():
        if name.startswith("condition:") and name.endswith(":stability"):
            m = p.metrics; C = m.get("containership_form_factor")
            if C:
                r = {k: m[k] / (COEF[k] / C) for k in KEYS}; w = min(r, key=r.get)
                out[name.split(":")[-2]] = dict(ratio=round(r[w], 4), worst=w, gm=round(m["gm_m"], 3), kg=round(m["kg_m"], 3), phi_f_deg=round(m["containership_total_area_end_rad"]*57.2958, 2), area=round(m["area_0_flood_mrad"], 4), req=round(.029/C, 4))
    return out
def main():
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 120
    for name in ("random-tiny-1", "simulated-tiny-1"):
        inst = Instance.model_validate_json(Path(f"experiments/mask/c2-on-current/{name}.instance.json").read_text())
        boxes = {b.id: b for b in inst.containers}
        plans = {"empty": MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[])}
        for p in sorted((HERE / "plans").glob(f"{name}-s*-noc2.plan.json")):
            plans[p.name.split(".")[0]] = MovePlan.model_validate_json(p.read_text())
        for label, plan in plans.items():
            t = time.time()
            dep = b23(check_stability(inst, departure_condition(inst, plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="planner")})), call_index=inst.call_index))
            full0 = check(inst, plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="planner")}))
            fb = finish_ballast(inst, plan, time_limit_s=budget)
            loads = [m.model_dump(mode="json") for m in plan.moves if type(m).__name__ == "Load"]
            rec = dict(instance=name, plan=label, n_loads=len(loads), load_mass_t=round(sum(boxes[m["container_id"]].gross_mass_kg for m in loads)/1e3, 2) if loads and "container_id" in loads[0] else None,
                       loads=loads, dep_unballasted={k: (round(v, 4) if isinstance(v, float) else v) for k, v in dep.items() if k != "ratios"},
                       full_zero_rules=sorted({v.rule_id for v in full0.violations} - {"objective.all_loaded"}), full_zero_states=states(full0),
                       finisher=dict(status=fb.status, proof=fb.proof, wall_s=round(fb.wall_time_s, 1), budget_s=budget), wall_s=time.time()-t)
            with (HERE / "planeval.jsonl").open("a") as fh: fh.write(json.dumps(rec, default=str) + "\n")
            print(name, label, rec["n_loads"], rec["load_mass_t"], "dep", rec["dep_unballasted"]["worst_ratio"], "states", {k: v["ratio"] for k, v in rec["full_zero_states"].items()}, "finisher", fb.status, flush=True)
if __name__ == "__main__":
    main()

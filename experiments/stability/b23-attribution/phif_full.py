"""Sensitivity (NOT a checker change): full check, 3 D57 states, with hold vents credited weathertight-closable."""
import sys
from pathlib import Path
import stowage.checker.stability as S
from stowage.model import Instance, MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker import check
sys.path.insert(0, str(Path(__file__).parent))
from planeval import states
orig = S._geometry
def patched(name, payload):
    g = orig(name, payload)
    for o in g.openings:
        if o.get("kind") == "hold_ventilator":
            o["closure"] = "weathertight_closable"
    return g
def main():
    S._geometry = patched
    for f in sys.argv[1:]:
        inst = Instance.model_validate_json(Path(f).read_text())
        plans = {"empty": MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[])}
        for p in sorted(Path(__file__).with_name("plans").glob(Path(f).name.split(".")[0] + "-s*-noc2.plan.json")):
            plans[p.name.split(".")[0]] = MovePlan.model_validate_json(p.read_text())
        for label, plan in plans.items():
            rep = check(inst, plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="planner")}))
            print(Path(f).name, label, "rules", sorted({v.rule_id for v in rep.violations} - {"objective.all_loaded"}),
                  {k[:10]: (v["ratio"], v["phi_f_deg"]) for k, v in states(rep).items()}, flush=True)
if __name__ == "__main__":
    main()

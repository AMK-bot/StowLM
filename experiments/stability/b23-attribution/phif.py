"""Sensitivity probe (NOT a checker change): B 2.3 with hold ventilators credited as weathertight-closable.
Monkeypatches the geometry in-process only."""
import sys
from pathlib import Path
import stowage.checker.stability as S
from stowage.model import Instance, MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker.full import departure_condition
sys.path.insert(0, str(Path(__file__).parent))
from sweep import b23
orig = S._geometry
def patched(name, payload):
    g = orig(name, payload)
    for o in g.openings:
        if o.get("kind") == "hold_ventilator":
            o["closure"] = "weathertight_closable"
    return g
mode = sys.argv[2] if len(sys.argv) > 2 else "patched"
if mode == "patched":
    S._geometry = patched
inst = Instance.model_validate_json(Path(sys.argv[1]).read_text())
plan = MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[])
for label, ballast in (("none", {}),):
    ship = departure_condition(inst, plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg=ballast, source="planner")}))
    rep = S.check_stability(inst, ship, call_index=inst.call_index)
    r = b23(rep)
    print(mode, label, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k != "ratios"})
    print("   ", {k: round(v, 3) for k, v in r["ratios"].items()})
    print("   vanishing", rep.metrics.get("containership_vanishing_angle_rad", 0) * 57.3, "detected", rep.metrics.get("containership_vanishing_angle_detected"))

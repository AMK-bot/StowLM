"""Full check (all parts, 3 D57 states) of one symmetric ballast recipe on the empty plan or a rebuilt masked plan."""
import json, random, sys
from pathlib import Path
from stowage.model import Instance, MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker import check
sys.path.insert(0, str(Path(__file__).parent))
from sweep import pairs
def main():
    inst = Instance.model_validate_json(Path(sys.argv[1]).read_text())
    recipe = json.loads(sys.argv[2]); seed = int(sys.argv[3]) if len(sys.argv) > 3 else None
    plan = MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[])
    if seed is not None:
        from llm.stowlm.format import ActionCodec, load_tokenizer
        from llm.stowlm.mask import random_masked_plan
        from stowage.actions import get_action_space
        from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
        eng = make_feasibility_engine(inst, get_action_space("boxslot"), config=FeasibilityConfig())
        dec = random_masked_plan(eng, ActionCodec(load_tokenizer(), [b.id for b in inst.containers]), random.Random(seed))
        plan = dec.engine.to_plan()
    ps = pairs(inst)
    ballast = {t.id: f * t.capacity_m3 * t.content_density_kg_m3 for g, f in recipe.items() for t in ps[g]}
    rep = check(inst, plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg=ballast, source="planner")}))
    print("ballast_t", round(sum(ballast.values())/1e3, 1), "passed", rep.passed)
    for v in rep.violations:
        print("  ", v.rule_id, v.message[:140])
    for name, p in rep.parts.items():
        if "stability" in name:
            m = p.metrics
            print("  ", name, {k: round(m[k], 3) for k in ("gm_m", "kg_m", "draught_mean_m", "draught_fwd_m", "draught_aft_m", "trim_m", "displacement_kg", "containership_form_factor", "area_0_flood_mrad", "containership_total_area_end_rad") if k in m})
if __name__ == "__main__":
    main()

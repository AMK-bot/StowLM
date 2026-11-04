"""Quick look: background-only stability metrics of the two c2-on-current instances."""
import json, sys, time
from pathlib import Path
from stowage.model import Instance, MovePlan
from stowage.checker.full import departure_condition
from stowage.checker.stability import check_stability
ROOT = Path(__file__).resolve().parents[3]
for name in ("random-tiny-1", "simulated-tiny-1"):
    inst = Instance.model_validate_json((ROOT/f"experiments/mask/c2-on-current/{name}.instance.json").read_text())
    plan = MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[])
    t = time.time()
    ship = departure_condition(inst, plan)
    rep = check_stability(inst, ship, call_index=inst.call_index)
    print(name, inst.vessel.hull.lpp_m, "ran", rep.ran, f"{time.time()-t:.1f}s")
    for k, v in sorted(rep.metrics.items()):
        if any(s in k for s in ("containership", "gm", "kg", "draught", "displacement", "area", "gz", "trim")):
            print("  ", k, v)
    print("  violations", [(v.rule_id, v.message[:80]) for v in rep.violations])

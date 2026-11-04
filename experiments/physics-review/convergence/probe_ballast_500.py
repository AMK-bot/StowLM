"""Hand ballast probe for feeder_500 C3 (finisher found none): is there ANY passing ballast?

Pure production check_stability / full check on a few manual tank sets (all fills 100 %, so no
free surface in those tanks). Output probe_ballast_500.json.
"""
import json, math, time
from pathlib import Path
from stowage.model import Instance, MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker.full import check

HERE = Path(__file__).resolve().parent
SETS = {"FPT": ["FPT-P", "FPT-S"],
        "FPT+DB-F": ["FPT-P", "FPT-S", "DB-F-P", "DB-F-S"],
        "FPT+DB-F+DB-M": ["FPT-P", "FPT-S", "DB-F-P", "DB-F-S", "DB-M-P", "DB-M-S"]}

def main():
    inst = Instance.model_validate_json((HERE / ".timing/feeder_500.instance.json").read_text())
    cap = {t.id: t.capacity_m3 * t.content_density_kg_m3 for t in inst.vessel.tanks if t.adjustable}
    out = {}
    for name, tanks in SETS.items():
        masses = {k: (cap[k] if k in tanks else 0.0) for k in cap}
        plan = MovePlan(id=f"{inst.id}.{name}", instance_id=inst.id, call_index=inst.call_index,
                        ballast=BallastCondition(tank_masses_kg=masses, source="planner"))
        t0 = time.perf_counter()
        r = check(inst, plan)
        m = r.parts["stability"].metrics
        out[name] = dict(seconds=time.perf_counter() - t0, passed=r.passed, ballast_t=sum(masses.values()) / 1e3,
                         gm=m.get("gm_m"), ta=m.get("draught_aft_m"), tf=m.get("draught_fwd_m"), trim=m.get("trim_m"),
                         violations=sorted({(v.rule_id, round(v.measured, 4) if v.measured is not None else None,
                                             round(v.limit, 4) if v.limit is not None else None) for v in r.violations}, key=str))
        print(name, json.dumps(out[name], default=float), flush=True)
        (HERE / "probe_ballast_500.json").write_text(json.dumps(out, indent=1, default=float) + "\n")

if __name__ == "__main__":
    main()

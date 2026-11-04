"""Is there ANY passing ballast? Declared symmetric tank sets (pressed full, no free surface) on the v2 conditions.

Production full check only. ASSUMPTION: sets are hand-picked loading-manual style; a pass proves feasibility, a fail
of every set does not prove infeasibility. Output declared_<ship>.json.
"""
import json, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness2 as h2
from stowage.model import MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker.full import check
import stowage.vessel.fleet as fleet

SETS = {"DB": ["DB-"], "DB+HEEL": ["DB-", "HEEL"], "DB+WING-F": ["DB-", "WING-F"], "DB+WING": ["DB-", "WING"],
        "DB+WING+HEEL": ["DB-", "WING", "HEEL"], "DB+WING+HEEL+FPT": ["DB-", "WING", "HEEL", "FPT"],
        "DB-M+DB-F+FPT": ["DB-M", "DB-F", "FPT"]}
ship, cond, frac = sys.argv[1], sys.argv[2], float(sys.argv[3])
v = fleet.load_vessel(ship, with_hull=True); p = fleet.load_ship_params(ship)
cap = {t.id: t.capacity_m3 * t.content_density_kg_m3 for t in v.tanks if t.adjustable}
out = {}
for name, pre in SETS.items():
    masses = {k: (c if any(k.startswith(x) for x in pre) else 0.0) for k, c in cap.items()}
    inst, info = h2.build(ship, v, p, cond, sum(masses.values()))
    plan = MovePlan(id=f"{inst.id}.{name}", instance_id=inst.id, call_index=inst.call_index,
                    ballast=BallastCondition(tank_masses_kg=masses, source="planner"))
    h2.h1.SPY.clear(); t0 = time.perf_counter(); r = check(inst, plan); dt = time.perf_counter() - t0
    m = r.parts["stability"].metrics
    rec = dict(check_s=dt, passed=r.passed, ballast_t=sum(masses.values()) / 1e3, boxes=info["boxes"],
               cargo_t=info["cargo_mass_kg"] / 1e3, gm=m.get("gm_m"), ta=m.get("draught_aft_m"), tf=m.get("draught_fwd_m"),
               trim=m.get("trim_m"), list_deg=(m.get("list_rad") or 0) * 57.2958, table=m.get("geometry_table_used"),
               violations=sorted({(x.rule_id, None if x.measured is None else round(x.measured, 4),
                                   None if x.limit is None else round(x.limit, 4)) for x in r.violations}, key=str))
    if "eval" in h2.h1.SPY:
        a = h2.h1.part_a_2_2(h2.h1.SPY["eval"], fine=False)
        rec["a2_2_passed"] = f"{a['passed_count']}/{a['total']}"
        rec["a2_2"] = {k: round(x["value"], 4) for k, x in a["criteria"].items()}
    b = {k: x for k, x in r.parts["stability"].margins.items() if k.startswith("containership_")}
    rec["b2_3_passed"] = f"{sum(x >= -1e-12 for x in b.values())}/{len(b)}"
    out[name] = rec
    print(name, json.dumps({k: rec[k] for k in ("check_s", "passed", "ballast_t", "boxes", "gm", "trim", "list_deg", "table", "b2_3_passed")}, default=float), flush=True)
    (HERE / f"declared_{ship}_{cond}.json").write_text(json.dumps(out, indent=1, default=float) + "\n")

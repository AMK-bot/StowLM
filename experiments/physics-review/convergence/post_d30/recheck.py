"""Re-check every witness with exact ballast masses; record the production strength/lashing maxima."""
import json, glob, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness2 as h2
import stowage.vessel.fleet as fleet
from stowage.model import MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker.full import check
out = {}
for f in sorted(glob.glob(str(HERE / "search_feeder_*_C*.json"))):
    d = json.load(open(f)); w = d.get("witness")
    if not w: continue
    s, c = d["ship"], d["condition"]
    v = fleet.load_vessel(s, with_hull=True); p = fleet.load_ship_params(s)
    cap = {t.id: t.capacity_m3 * t.content_density_kg_m3 for t in v.tanks if t.adjustable}
    base_pre, peak = w["label"].split("+")[:-1], w["label"].split("+")[-1]
    pre = {"DB": ["DB-"], "HEEL": ["HEEL"], "WING-F": ["WING-F"], "WING": ["WING"]}
    prefixes = [x for b in base_pre for x in pre[b]]
    frac = float(peak[3:]) if peak[:3] in ("FPT", "APT") else None
    if frac is None: prefixes += pre[peak]
    masses = {k: (cv if any(k.startswith(x) for x in prefixes) else (frac * cv if frac is not None and k.startswith(peak[:3]) else 0.0)) for k, cv in cap.items()}
    inst, info = h2.build(s, v, p, c, sum(masses.values()))
    r = check(inst, MovePlan(id="r", instance_id=inst.id, call_index=inst.call_index, ballast=BallastCondition(tank_masses_kg=masses, source="planner")))
    sm = r.parts["strength"].metrics; lm = r.parts["lashing"].metrics
    out[f"{s} {c}"] = dict(passed=r.passed, max_sf_ratio=sm.get("max_sf_ratio"), max_bm_ratio=sm.get("max_bm_ratio"),
                           lashing={k: round(x, 3) for k, x in lm.items() if k.startswith("max_")}, boxes=info["boxes"])
    print(s, c, json.dumps(out[f"{s} {c}"]), flush=True)
(HERE / "recheck.json").write_text(json.dumps(out, indent=1) + "\n")

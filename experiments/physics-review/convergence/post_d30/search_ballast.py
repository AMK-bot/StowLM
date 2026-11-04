"""Loading-manual ballast search for the v2 conditions (feasibility witness + A 2.2 / B 2.3 evidence).

For each condition: try symmetric base sets (pressed full, no free surface). For each set, trim-correct with the
forward or aft peak at a bisected fill (free surface of that one tank is charged by the checker). Cargo is re-budgeted
to (design draught − 0.10 m) minus the ballast mass every time (harness2.build). Every candidate's full-check result is
recorded, with A 2.2.1–3 and B 2.3 evaluated on its GZ curve. Stops at the first full pass. Then the production
finisher (`planner.finalize`, 60 s) is run on that passing condition's *unballasted* instance, to see whether it finds
ballast when a witness is known to exist.

ASSUMPTION: hand-picked tank sets and a 1-D trim search. A pass proves feasibility; no pass doesn't prove infeasibility.
Pure production code. Output search_<ship>_<cond>.json.
"""
import json, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness2 as h2  # noqa: E402
from stowage.model import MovePlan, PlannerResult  # noqa: E402
from stowage.model.plan import BallastCondition  # noqa: E402
from stowage.checker.full import check  # noqa: E402
from stowage.planner import finalize  # noqa: E402
import stowage.vessel.fleet as fleet  # noqa: E402

BASES = [("DB+HEEL", ["DB-", "HEEL"]), ("DB+WING-F", ["DB-", "WING-F"]), ("DB+WING", ["DB-", "WING"]),
         ("DB+WING+HEEL", ["DB-", "WING", "HEEL"])]


def evaluate(ship, v, p, cond, masses, label):
    inst, info = h2.build(ship, v, p, cond, sum(masses.values()))
    plan = MovePlan(id=f"{inst.id}.{label}", instance_id=inst.id, call_index=inst.call_index,
                    ballast=BallastCondition(tank_masses_kg=masses, source="planner"))
    h2.h1.SPY.clear()
    t0 = time.perf_counter()
    r = check(inst, plan)
    dt = time.perf_counter() - t0
    st = r.parts["stability"]
    m = st.metrics
    rec = dict(label=label, check_s=dt, passed=r.passed, ballast_t=sum(masses.values()) / 1e3,
               ballast={k: round(x / 1e3, 1) for k, x in masses.items() if x > 0},
               boxes=info["boxes"], boxes_hold=info["boxes_hold"], boxes_deck=info["boxes_deck"],
               cargo_t=info["cargo_mass_kg"] / 1e3, t_per_teu=info.get("t_per_teu"),
               gm=m.get("gm_m"), fsc=m.get("fsc_m"), kg=m.get("kg_m"), ta=m.get("draught_aft_m"), tf=m.get("draught_fwd_m"),
               tm=m.get("draught_mean_m"), trim=m.get("trim_m"), list_deg=(m.get("list_rad") or 0) * 57.29578,
               phi_f_deg=(m.get("angle_flooding_rad") or 0) * 57.29578, weather_ratio=m.get("weather_ratio"),
               C=m.get("containership_form_factor"), table=m.get("geometry_table_used"),
               violations=sorted({(x.rule_id, None if x.measured is None else round(x.measured, 4),
                                   None if x.limit is None else round(x.limit, 4)) for x in r.violations}, key=str))
    if "strength" in r.parts and r.parts["strength"].ran:
        rec["strength"] = h2.h1.strength_extremes(inst.vessel, r.parts["strength"])
    for part in ("lashing", "visibility"):
        if part in r.parts:
            pm = r.parts[part].metrics
            rec[part] = {k: x for k, x in pm.items() if isinstance(x, (int, float)) and
                         any(w in k for w in ("ratio", "blind", "distance", "margin"))}
    if "eval" in h2.h1.SPY:
        a = h2.h1.part_a_2_2(h2.h1.SPY["eval"], fine=True)
        rec["a2_2"] = {k: dict(value=x["value"], limit=x["limit"], passed=x["passed"]) for k, x in a["criteria"].items()}
        rec["a2_2_passed"] = [a["passed_count"], a["total"]]
    lim = {"area_0_30_mrad": .009, "area_0_40_mrad": .016, "area_30_40_mrad": .006, "gz_at_or_above_30_m": .033,
           "gz_max_m": .042, "area_0_flood_mrad": .029}
    C = m.get("containership_form_factor")
    b = {k: x for k, x in st.margins.items() if k.startswith("containership_")}
    rec["b2_3"] = {k: dict(margin=x, limit=(lim[k[len('containership_'):]] / C if C and k[len('containership_'):] in lim else None))
                   for k, x in b.items()}
    rec["b2_3_passed"] = [sum(x >= -1e-12 for x in b.values()), len(b)]
    print(f"   {label}: pass={r.passed} bal={rec['ballast_t']:.0f}t boxes={rec['boxes']} GM={rec['gm']:.2f} "
          f"trim={rec['trim']:.2f} A={rec.get('a2_2_passed')} B={rec['b2_3_passed']} {dt:.1f}s "
          f"viol={[x[0] for x in rec['violations']]}", flush=True)
    return rec, inst


def main():
    ship, cond = sys.argv[1], sys.argv[2]
    v = fleet.load_vessel(ship, with_hull=True)
    p = fleet.load_ship_params(ship)
    cap = {t.id: t.capacity_m3 * t.content_density_kg_m3 for t in v.tanks if t.adjustable}
    lim = p["operating_limits"]
    out = dict(ship=ship, condition=cond, ballast_capacity_t=sum(cap.values()) / 1e3, candidates=[], witness=None)
    path = HERE / f"search_{ship}_{cond}.json"
    t_start = time.perf_counter()
    for name, pre in BASES:
        base = {k: (c if any(k.startswith(x) for x in pre) else 0.0) for k, c in cap.items()}
        rec, _ = evaluate(ship, v, p, cond, base, name)
        out["candidates"].append(rec)
        best = rec
        if not rec["passed"] and abs(rec["trim"]) > 0.05:
            peak = "FPT" if rec["trim"] > 0 else "APT"  # trim = aft - fwd > 0: by the stern -> fill forward peak
            ids = [k for k in cap if k.startswith(peak)]
            lo, hi = 0.0, 1.0
            for _ in range(5):
                f = (lo + hi) / 2
                masses = dict(base, **{k: f * cap[k] for k in ids})
                r2, _ = evaluate(ship, v, p, cond, masses, f"{name}+{peak}{f:.2f}")
                out["candidates"].append(r2)
                if r2["passed"]:
                    best = r2
                    break
                if (r2["trim"] > 0) == (rec["trim"] > 0):
                    lo = f
                else:
                    hi = f
                if abs(r2["trim"]) < abs(best["trim"]):
                    best = r2
        path.write_text(json.dumps(out, indent=1, default=float) + "\n")
        if best["passed"]:
            out["witness"] = best
            break
    out["search_s"] = time.perf_counter() - t_start
    if out["witness"] is not None:
        w = out["witness"]
        inst, _ = h2.build(ship, v, p, cond, w["ballast_t"] * 1e3)
        plan = MovePlan(id=f"{inst.id}.fin", instance_id=inst.id, call_index=inst.call_index)
        t0 = time.perf_counter()
        fin = finalize(inst, PlannerResult(status="ok", plan=plan, planner="physics-review.search", planner_version="1",
                                           seed=0), time_limit_s=60.0)
        out["finisher_on_witness_condition"] = dict(status=fin.status, ballast_status=fin.metrics.get("ballast_status"),
                                                    wall_s=time.perf_counter() - t0, found=fin.plan is not None and fin.plan.ballast is not None,
                                                    message=(fin.message or "")[:300])
        print("   finisher on witness condition:", out["finisher_on_witness_condition"], flush=True)
    path.write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(f"{ship} {cond}: witness={'yes' if out['witness'] else 'no'} search_s={out['search_s']:.0f}", flush=True)


if __name__ == "__main__":
    main()

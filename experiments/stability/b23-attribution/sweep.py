"""Exhaustive symmetric low-ballast sweep: can B 2.3 pass on background-only (or a given plan)?

Read-only use of the checker. For each instance: departure stability for every
combination of symmetric fill fractions of the selected ballast pairs, and the
ballast finisher with a generous budget. One thread.
"""
import argparse, itertools, json, sys, time
from pathlib import Path
from stowage.model import Instance, MovePlan
from stowage.model.plan import BallastCondition
from stowage.checker.full import departure_condition
from stowage.checker.stability import check_stability

KEYS = ("area_0_30_mrad", "area_0_40_mrad", "area_30_40_mrad", "gz_at_or_above_30_m", "gz_max_m", "area_0_flood_mrad")
COEF = dict(zip(KEYS, (.009, .016, .006, .033, .042, .029)))


def b23(rep):
    m = rep.metrics
    C = m.get("containership_form_factor")
    if C is None:
        return None
    ratio = {k: m[k] / (COEF[k] / C) for k in KEYS}
    worst = min(ratio, key=ratio.get)
    return dict(C=C, worst=worst, worst_ratio=ratio[worst], ratios=ratio,
                phi_f_deg=m.get("containership_total_area_end_rad", 0) * 57.2958,
                gm=m.get("gm_m"), kg=m.get("kg_m"), d=m.get("draught_mean_m"), trim=m.get("trim_m"),
                disp_t=m.get("displacement_kg", 0) / 1e3,
                rules=sorted({v.rule_id for v in rep.violations}))


def evaluate(inst, plan, ballast):
    p = plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg=ballast, source="planner")})
    ship = departure_condition(inst, p)
    return check_stability(inst, ship, call_index=inst.call_index)


def pairs(inst):
    out = {}
    for t in inst.vessel.tanks:
        if t.adjustable and t.kind == "ballast":
            out.setdefault(t.id.rsplit("-", 1)[0], []).append(t)
    return out


def sweep(inst, plan, groups, fracs):
    ps = pairs(inst)
    rows = []
    for combo in itertools.product(fracs, repeat=len(groups)):
        ballast = {}
        for g, f in zip(groups, combo):
            for t in ps[g]:
                ballast[t.id] = f * t.capacity_m3 * t.content_density_kg_m3
        rep = evaluate(inst, plan, ballast)
        r = b23(rep) or {}
        r.update(combo=dict(zip(groups, combo)), ballast_t=sum(ballast.values()) / 1e3)
        rows.append(r)
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("instances", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--groups", nargs="+", default=["DB-A", "DB-M", "DB-F"])
    ap.add_argument("--fracs", nargs="+", type=float, default=[0, .25, .5, .75, 1])
    a = ap.parse_args()
    with a.out.open("a") as fh:
        for path in a.instances:
            inst = Instance.model_validate_json(path.read_text())
            plan = MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[])
            t = time.time()
            rows = sweep(inst, plan, [g for g in a.groups if g in pairs(inst)], a.fracs)
            passing = [r for r in rows if r.get("worst_ratio", 0) >= 1]
            best = max(rows, key=lambda r: r.get("worst_ratio", 0))
            allpass = [r for r in rows if not r.get("rules")]
            rec = dict(instance=str(path), vessel=inst.vessel.id, base=rows[0], best=best,
                       n=len(rows), n_b23_pass=len(passing), n_stab_pass=len(allpass), wall_s=time.time() - t)
            fh.write(json.dumps(rec) + "\n"); fh.flush()
            print(path.name, inst.vessel.id, "base", round(rows[0].get("worst_ratio", 0), 4), rows[0].get("worst"),
                  "best", round(best.get("worst_ratio", 0), 4), best.get("worst"), best["combo"],
                  "b23pass", len(passing), "stabpass", len(allpass), f"{time.time()-t:.0f}s", flush=True)

"""Convergence rerun v2 (post-D30): loading-manual conditions with a ballast reserve.

Why v2: the v1 builder (../harness.py) budgeted cargo right up to design displacement, so ANY ballast took the ship
over its draught limit. The finisher's `not_found` was then a property of the condition, not of the solver, and the
no-ballast check fell to the slow direct stability path (list ≈ 2.7°, outside the qualified tables).

ASSUMPTION (loading-manual convention, as in ../../design/after.md): a condition declares a ballast reserve R that
counts against deadweight. Cargo is budgeted to the displacement at (design draught − 0.10 m) minus R. R is tried as
0.25, 0.45 and 0.65 of total ballast capacity, in that order. The first R for which the production finisher
(`planner.finalize`) finds ballast is kept. The ballast itself is the finisher's own choice.
Everything else (placement, box types, consumables 97 %, export mix) is v1's.
Pure production code; v1's passive spies record the GZ curve for the A 2.2 evaluation.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import harness as h1  # noqa: E402  (installs passive spies)
import numpy as np  # noqa: E402
import stowage.vessel.fleet as fleet  # noqa: E402
from stowage.model import MovePlan, PlannerResult  # noqa: E402

FRACTIONS = (0.25, 0.45, 0.65)


def build(name, vessel, params, cond, reserve_kg, seed=7):
    payload = h1.build_instance().model_dump(mode="python")
    rho = payload["port"]["water_density_kg_m3"]
    hull = vessel.hull
    target = h1._displacement_at(params, hull.design_draught_m - 0.10, rho)
    tanks = h1._consumables(vessel, 0.97)
    fixed = sum(load.mass_kg for load in vessel.fixed_loads)
    base = hull.lightship_mass_kg + fixed + sum(tanks.values())
    budget = target - base - reserve_kg
    n20 = sum(1 for c in vessel.cells if c.accepts_20)
    info = dict(condition=cond, ballast_reserve_kg=reserve_kg, cargo_budget_kg=budget, target_draught_m=hull.design_draught_m - 0.10,
                base_mass_kg=base, cells=len(vessel.cells), cells_20=n20,
                hold_cells=sum(len(s.cell_ids) for s in vessel.stacks if not s.on_deck))
    if cond == "C1":
        n = max(0, min(n20, int(budget // 14000.0)))
        placements = h1.place(vessel, [14000.0] * n)
        info.update(t_per_teu=14.0, requested=n)
    elif cond == "C2":  # homogeneous, heaviest uniform mass whose full placement fits the budget
        best, best_tot = None, -1.0
        for m in np.arange(2300.0, h1.MAX_20FT_GROSS + 1, 100.0):
            pl = h1.place(vessel, [float(m)] * n20)
            tot = sum(x for _, x in pl)
            if best_tot < tot <= budget:
                best, best_tot = (float(m), pl), tot
        m, placements = best
        info.update(t_per_teu=m / 1000, requested=len(placements))
    elif cond == "C3":
        draw, src = h1.export_mass_sampler(seed)
        masses, tot = [], 0.0
        while len(masses) < n20:
            x = draw()
            if tot + x > budget:
                break
            masses.append(x)
            tot += x
        placements = h1.place(vessel, masses)
        info.update(export_mix=src, requested=len(masses), seed=seed)
    else:
        raise ValueError(cond)
    inst = h1._wrap(name, vessel, cond, placements, tanks, payload)
    h1._summarise(info, vessel, placements, base)
    return inst, info


def run(name, cond, finisher_s):
    from stowage.checker.full import check
    from stowage.planner import finalize
    vessel = fleet.load_vessel(name, with_hull=True)
    params = fleet.load_ship_params(name)
    cap = sum(t.capacity_m3 * t.content_density_kg_m3 for t in vessel.tanks if t.adjustable)
    attempts, chosen = [], None
    for f in FRACTIONS:
        inst, info = build(name, vessel, params, cond, f * cap)
        plan = MovePlan(id=f"{inst.id}.conv2", instance_id=inst.id, call_index=inst.call_index)
        t0 = time.perf_counter()
        fin = finalize(inst, PlannerResult(status="ok", plan=plan, planner="physics-review.convergence2",
                                           planner_version="1", seed=0), time_limit_s=finisher_s)
        found = fin.plan is not None and fin.plan.ballast is not None
        attempts.append(dict(fraction=f, reserve_t=f * cap / 1e3, boxes=info["boxes"], status=fin.status,
                             ballast_status=fin.metrics.get("ballast_status"), wall_s=time.perf_counter() - t0,
                             message=(fin.message or "")[:300]))
        print(f"  {name} {cond} R={f:.2f}: {attempts[-1]['ballast_status']} {attempts[-1]['wall_s']:.1f}s", flush=True)
        if found:
            chosen = (inst, info, fin.plan)
            break
    if chosen is None:
        return dict(vessel=name, condition=cond, ballast_capacity_kg=cap, attempts=attempts, judged=False,
                    note="finisher found no ballast at any reserve; not judged (no-ballast check is the slow direct path)")
    inst, info, final = chosen
    info.update(vessel=name, ballast_capacity_kg=cap, attempts=attempts,
                ballast_kg=dict(final.ballast.tank_masses_kg), ballast_total_kg=sum(final.ballast.tank_masses_kg.values()))
    h1.SPY.clear()
    t0 = time.perf_counter()
    report = check(inst, final)
    info["check_s"] = time.perf_counter() - t0
    info["passed"] = report.passed
    info["violations"] = sorted({(v.rule_id, None if v.measured is None else round(v.measured, 4),
                                  None if v.limit is None else round(v.limit, 4)) for v in report.violations}, key=str)
    info["parts"] = {k: h1._part_summary(p) for k, p in report.parts.items()}
    if "strength" in report.parts and report.parts["strength"].ran:
        info["strength_extremes"] = h1.strength_extremes(inst.vessel, report.parts["strength"])
    if "eval" in h1.SPY:
        spy = h1.SPY["eval"]
        info["gz_curve"] = dict(angles_deg=np.degrees(spy["angles"]).tolist(), gz_m=spy["gz"].tolist(),
                                phi_f_deg=math.degrees(spy["phi_f"]))
        t0 = time.perf_counter()
        info["a2_2"] = h1.part_a_2_2(spy, fine=True)
        info["a2_2_s"] = time.perf_counter() - t0
        st = info["parts"]["stability"]
        b = {k: v for k, v in st["margins"].items() if k.startswith("containership_")}
        info["b2_3"] = dict(margins=b, passed_count=sum(v >= -1e-12 for v in b.values()), total=len(b))
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ships", required=True)
    ap.add_argument("--conditions", default="C1,C2,C3")
    ap.add_argument("--finisher-s", type=float, default=60.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    done = {(r["vessel"], r["condition"]): r for r in json.loads(out.read_text())} if out.exists() else {}
    for name in a.ships.split(","):
        for cond in a.conditions.split(","):
            if (name, cond) in done:
                continue
            t0 = time.perf_counter()
            try:
                info = run(name, cond, a.finisher_s)
            except Exception as exc:
                import traceback
                info = dict(vessel=name, condition=cond, error=f"{type(exc).__name__}: {exc}",
                            traceback=traceback.format_exc()[-3000:])
            info["total_s"] = time.perf_counter() - t0
            done[(name, cond)] = info
            out.write_text(json.dumps(list(done.values()), indent=1, default=float) + "\n")
            print(f"{name} {cond}: passed={info.get('passed')} check_s={info.get('check_s', 0):.1f} "
                  f"total={info['total_s']:.1f} err={info.get('error')}", flush=True)


if __name__ == "__main__":
    main()

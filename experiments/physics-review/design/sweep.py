"""Sensitivity sweep: stability of C1/C3 vs lightship VCG and ballast capacity.

Stability-only (production `check_stability`), so each point costs one exact
check (~5-10 s) instead of a finisher search. Cargo is built once per ship on
the unmodified vessel and reused, so only the swept design value changes.
Each point is judged twice: ballast empty and every ballast tank pressed full
(zero free surface). "Pressed full" is a diagnostic bound on what ballast in
those tanks can do for GM, not a finisher result; trim/list/strength are not
judged here (run harness.py with the same override for the full verdict).

Resumable: results append to sweep_results.json keyed by point id.

Run: OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python \
     experiments/physics-review/design/sweep.py [--ships ...] [--part vcg|ballast]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import harness as H

OUT = H.HERE / "sweep_results.json"
B23 = ("containership_area_0_30_mrad", "containership_area_0_40_mrad", "containership_area_30_40_mrad",
       "containership_gz_at_or_above_30_m", "containership_gz_max_m", "containership_area_0_flood_mrad")


def realistic_ballast(params, *, db_top_frac=0.15):
    """APPROXIMATION: synthetic full-width double bottom 0.10-0.90 Lpp in 16 pairs
    plus fore/aft peak tanks, all fitted inside the KCS hull with 20 mm margin.
    db_top_frac*D is the double-bottom top (typ. feeder DB ~1.2-1.6 m)."""
    h = params["hull"]
    L, D = h["lpp_m"], h["depth_m"]
    top = max(1.2, db_top_frac * D)
    tanks = []
    edges = [round(0.10 + 0.05 * i, 2) for i in range(17)]  # 0.10..0.90 Lpp, 16 pairs
    for k, (a, b) in enumerate(zip(edges, edges[1:]), 1):
        for side, tag in ((1, "P"), (-1, "S")):
            g = H.fit_tank(params, a * L, b * L, 0.15, top, side=side)
            if g:
                tanks.append(dict(id=f"DB-{k}-{tag}", kind="ballast", content_density_kg_m3=1025.0,
                                  heated=False, adjustable=True, geometry=g))
    for tid, (a, b), (z0, z1) in (("FPT", (0.90, 0.96), (0.15 * D, 0.75 * D)),
                                  ("APT", (0.03, 0.08), (0.45 * D, 0.85 * D))):
        for side, tag in ((1, "P"), (-1, "S")):
            g = H.fit_tank(params, a * L, b * L, z0, z1, side=side)
            if g:
                tanks.append(dict(id=f"{tid}-{tag}", kind="ballast", content_density_kg_m3=1025.0,
                                  heated=False, adjustable=True, geometry=g))
    return {"replace": True, "add_tanks": tanks}


def judge(inst, vessel, full):
    from stowage.checker.stability import check_stability
    tanks = dict(inst.onboard.tank_masses_kg)
    if full:
        tanks.update({t.id: t.capacity_m3 * t.content_density_kg_m3 for t in vessel.tanks if t.adjustable})
    else:
        tanks.update({t.id: 0.0 for t in vessel.tanks if t.adjustable})
    ship = inst.onboard.model_copy(update={"tank_masses_kg": tanks})
    t0 = time.perf_counter()
    r = check_stability(inst, ship, call_index=0)
    m, g = r.metrics, r.margins
    hard = sorted({v.rule_id for v in r.violations if v.severity == "hard"})
    gm_ok = g.get("gm_m", -1) >= 0
    b23_ok = all(g.get(k, -1) >= -1e-12 for k in B23)
    weather_ok = all(g.get(k, -1) >= -1e-12 for k in ("weather_ratio", "weather_steady_heel_margin_rad",
                                                        "weather_table_applicability"))
    fails = {k: (m.get(k.replace("containership_", "")), g.get(k)) for k in B23 if g.get(k, -1) < -1e-12}
    return dict(seconds=time.perf_counter() - t0, ran=r.ran, error=r.error, b23_fails=fails,
                roll_period_s=m.get("weather_roll_period_s"), deck_edge_rad=m.get("weather_deck_edge_rad"),
                flooding_rad=m.get("angle_flooding_rad"), gm_m=m.get("gm_m"), kg_m=m.get("kg_m"),
                draught_mean_m=m.get("draught_mean_m"), trim_m=m.get("trim_m"),
                gz_max_m=m.get("gz_max_m"), weather_ratio=m.get("weather_ratio"),
                gm_ok=gm_ok, b23_ok=b23_ok, weather_ok=weather_ok,
                intact_ok=gm_ok and b23_ok and weather_ok, hard=hard,
                ballast_t=sum(t.capacity_m3 * t.content_density_kg_m3 for t in vessel.tanks if t.adjustable) / 1e3 if full else 0.0)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ships", default=",".join(H.FLEET))
    ap.add_argument("--part", default="vcg,ballast")
    ap.add_argument("--conditions", default="C1,C3")
    ap.add_argument("--vcg", default="0.55,0.60,0.65,0.70")
    a = ap.parse_args(argv)
    done = {r["id"]: r for r in json.loads(OUT.read_text())} if OUT.exists() else {}
    for name in a.ships.split(","):
        base_vessel, base_params = H.apply_overrides(name, None)
        insts = {c: H.build_condition(name, base_vessel, base_params, c)[0] for c in a.conditions.split(",")}
        points = []
        if "vcg" in a.part:
            points.append(("baseline", None))
            points += [(f"vcg{f:.2f}D", {"lightship": {"vcg_frac_depth": f}}) for f in map(float, a.vcg.split(","))]
        if "ballast" in a.part:
            rb = realistic_ballast(base_params)
            points.append(("realistic_ballast", {"ballast": rb}))
            points += [(f"realistic_ballast+vcg{f:.2f}D", {"ballast": rb, "lightship": {"vcg_frac_depth": f}})
                       for f in (0.60, 0.65)]
        for pid, ov in points:
            vessel, params = H.apply_overrides(name, ov)
            for cond, inst in insts.items():
                inst2 = inst.model_copy(update={"vessel": vessel, "onboard": inst.onboard.model_copy(update={
                    "tank_masses_kg": H._consumables(vessel, 0.97)})})
                for full in (False, True):
                    key = f"{name}|{pid}|{cond}|{'wbfull' if full else 'wbempty'}"
                    if key in done:
                        continue
                    res = judge(inst2, vessel, full)
                    res.update(id=key, vessel=name, point=pid, condition=cond, ballast="full" if full else "empty",
                               lightship_vcg_m=vessel.hull.lightship_vcg_m,
                               vcg_frac_depth=vessel.hull.lightship_vcg_m / vessel.hull.depth_m,
                               wb_capacity_t=sum(t.capacity_m3 * t.content_density_kg_m3
                                                 for t in vessel.tanks if t.adjustable) / 1e3)
                    done[key] = res
                    OUT.write_text(json.dumps(list(done.values()), indent=1) + "\n")
                    print(f"{key}: GM={res['gm_m'] if res['gm_m'] is None else round(res['gm_m'], 3)} err={res['error']} intact_ok={res['intact_ok']} ({res['seconds']:.1f}s)", flush=True)


if __name__ == "__main__":
    main()

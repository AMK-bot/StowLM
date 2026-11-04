"""feeder_500 at moulded depth 9.2 m (sourced-values.md section 4: load-line freeboard, MagicPort D).

In-memory rebuild through the production builder (fleet._build_vessel) from an edited parameter
tree; params/ untouched. Everything above the old deck moves up by dz = 0.95 m (deck cells, deck
stack bases, hatch covers, coamings, openings, bridge eye, windage rectangles above D). The hold
keeps its two tiers (a third tier would now fit under the deck at 1.3 + 3 x 2.591 = 9.07 m < 9.2 m,
but that is a layout change, reported separately). Tanks are re-fitted to the new deck by
after_design.tank_design. Lightship VCG = 0.72 D = 6.624 m, Z unchanged (S7/S11A depend on L, B, Cb),
A_ref rescaled with D: 0.95*2*(t_s,net*D + t_i,net*(D - h_db)) = 0.320 m2 (strength-design.md section 3).

Run: OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/physics-review/design/depth_500.py \
     [--conditions C1,C2,C3,C4] [--label after_d92] [--probe-only]
"""
from __future__ import annotations

import argparse
import copy
import json
import time
import types
from pathlib import Path

import numpy as np

import harness as H
import after_design as AD
import after_run as AR

NAME, NEW_D = "feeder_500", 9.2


def deepened(params, new_d):
    p = copy.deepcopy(params)
    D = p["hull"]["depth_m"]
    dz = new_d - D
    p["hull"]["depth_m"] = new_d
    for c in p["cells"]:
        if c["vcg_base_m"] >= D - 1e-9:
            c["vcg_base_m"] += dz
            c["vcg_m"] += dz
    for s in p["stacks"]:
        if s["on_deck"]:
            s["base_z_m"] += dz
    for hc in p["hatch_covers"]:
        hc["vcg_m"] += dz
    env = p["envelope"]
    env["deck_z_m"] = new_d
    for c in env["hatch_coamings"]:
        c["z_min_m"] += dz; c["z_max_m"] += dz
    for o in env["openings"]:
        o["z_m"] += dz
    p["bridge"]["eye_z_m"] += dz
    w = p["windage"]
    for r in w["projected_rectangles"]:
        if r["id"] == "hull_freeboard":
            r["z_max_m"] = new_d
        elif r["z_min_m"] >= D - 1e-9:
            r["z_min_m"] += dz; r["z_max_m"] += dz
    # exact union of the profile rectangles (rasterised at 1 cm)
    rects = w["projected_rectangles"]
    xs = sorted({v for r in rects for v in (r["x_min_m"], r["x_max_m"])})
    area = mom = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        xm = (x0 + x1) / 2
        iv = sorted((r["z_min_m"], r["z_max_m"]) for r in rects if r["x_min_m"] <= xm <= r["x_max_m"])
        merged = []
        for a, b in iv:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            area += (x1 - x0) * (b - a); mom += (x1 - x0) * (b * b - a * a) / 2
    w["area_m2"], w["first_moment_m3"], w["centroid_z_m"] = area, mom, mom / area
    ol = p["operating_limits"]
    ol["windage_area_m2"] = area
    ol["windage_lever_m"] = mom / area - w["reference_underwater_centroid_z_m"]
    return p


def build_vessel(params):
    from stowage.vessel import fleet
    saved = fleet.tomllib
    fleet.tomllib = types.SimpleNamespace(loads=json.loads)
    try:
        return fleet._build_vessel(json.dumps(params).encode(), NAME, True)
    finally:
        fleet.tomllib = saved


def install(new_params, new_vessel):
    orig_load, orig_lv = H._ORIGINAL_LOAD, H.fleet.load_vessel
    H._ORIGINAL_LOAD = lambda n: copy.deepcopy(new_params) if n == NAME else orig_load(n)
    H.fleet.load_vessel = lambda n, with_hull=True: new_vessel if n == NAME else orig_lv(n, with_hull=with_hull)


def overrides(new_params, new_vessel, base_ov, *, vent_dz=0.0, coaming_dz=0.0):
    ov = copy.deepcopy(base_ov)
    wb, fo, s = AD.tank_design(NAME, new_params, new_vessel)
    ov["ballast"] = {"replace": True, "add_tanks": wb + fo}
    ov["lightship"] = dict(ov["lightship"]); ov["lightship"].pop("vcg_frac_depth", None)
    ov["lightship"]["vcg_m"] = 0.72 * NEW_D
    ov["strength_stations"] = dict(ov["strength_stations"], A_ref=0.320)
    ov["envelope"] = {"vent_dz_m": vent_dz, "er_dz_m": vent_dz, "coaming_dz_m": coaming_dz}
    ov["_design_summary"] = s
    return ov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", default="C1,C2,C3,C4")
    ap.add_argument("--label", default="after_d92")
    ap.add_argument("--vent-dz", type=float, default=0.0)
    ap.add_argument("--coaming-dz", type=float, default=0.0)
    ap.add_argument("--finisher-s", type=float, default=45.0)
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--patterns", default="")
    a = ap.parse_args()
    base_ov = json.loads((H.HERE / "after.json").read_text())[NAME]
    t0 = time.perf_counter()
    p = deepened(H._ORIGINAL_LOAD(NAME), NEW_D)
    v = build_vessel(p)
    build_s = time.perf_counter() - t0
    print(f"rebuilt feeder_500 at D={NEW_D}: {build_s:.1f}s; windage {p['windage']['area_m2']:.1f} m2", flush=True)
    install(p, v)
    ov = overrides(p, v, base_ov, vent_dz=a.vent_dz, coaming_dz=a.coaming_dz)
    print("tanks", json.dumps(ov["_design_summary"], default=float), flush=True)
    if a.probe_only:
        import after_probe as AP
        ov["draught_margin"] = 0.1
        pats = a.patterns.split(";") if a.patterns else [",".join(base_ov["ballast_plan"]["C1"])]
        rows = AP.probe(NAME, ov, a.conditions.split(","), pats, f"d92_v{a.vent_dz}_c{a.coaming_dz}")
        out = H.HERE / "probe_d92.json"
        old = json.loads(out.read_text()) if out.exists() else []
        out.write_text(json.dumps(old + rows, indent=1, default=float) + "\n")
        return
    vessel, params = AR.apply(NAME, ov)
    out = H.HERE / f"results_{a.label}.json"
    done = {(r["vessel"], r["condition"]): r for r in json.loads(out.read_text())} if out.exists() else {}
    for cond in a.conditions.split(","):
        if (NAME, cond) in done:
            continue
        t1 = time.perf_counter()
        info = AR.run_condition(NAME, vessel, params, cond, finisher_s=a.finisher_s,
                                plan_prefixes=ov["ballast_plan"].get(cond),
                                draught_margin_m=ov["draught_margin_m"].get(cond, 0.0))
        info.update(vessel=NAME, label=a.label, vessel_load_s=build_s, total_s=time.perf_counter() - t1,
                    overrides={k: v for k, v in ov.items() if k != "ballast"} | {"depth_m": NEW_D})
        done[(NAME, cond)] = info
        out.write_text(json.dumps(list(done.values()), indent=1, default=float) + "\n")
        st = info["parts"]["stability"]["metrics"]
        print(f"[{a.label}] {NAME} {cond}: passed={info['passed']} fin={info['finisher']['status']} "
              f"src={info['ballast_source']} GM={st.get('gm_m')} trim={st.get('trim_m')} "
              f"fails={sorted({v['rule'] for p_ in info['parts'].values() for v in p_['violations']})} "
              f"check={info['check_s']:.1f}s total={info['total_s']:.1f}s", flush=True)


if __name__ == "__main__":
    main()

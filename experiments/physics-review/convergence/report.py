"""Merge results_<ship>.json -> results.json and render results.md tables.

Run from repo root: uv run python experiments/physics-review/convergence/report.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHIPS = ("feeder_500", "feeder_1000", "feeder_1700")
B23 = [("containership_downflooding_30_margin_rad", None, "B2.3 phi_f >= 30 deg (rad margin)"),
       ("containership_area_0_30_mrad", "area_0_30_mrad", "B2.3.2.1 area 0-30 >= 0.009/C"),
       ("containership_area_0_40_mrad", "area_0_40_mrad", "B2.3.2.1 area 0-40 >= 0.016/C"),
       ("containership_area_30_40_mrad", "area_30_40_mrad", "B2.3.2.2 area 30-40 >= 0.006/C"),
       ("containership_gz_at_or_above_30_m", "gz_at_or_above_30_m", "B2.3.2.3 GZ(>=30) >= 0.033/C"),
       ("containership_gz_max_m", "gz_max_m", "B2.3.2.4 GZmax >= 0.042/C"),
       ("containership_area_0_flood_mrad", "area_0_flood_mrad", "B2.3.2.5 area 0-flood >= 0.029/C")]


def f(x, nd=3):
    if x is None:
        return "–"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)) and not math.isfinite(x):
        return str(x)
    return f"{x:.{nd}f}"


def ok(b):
    return "PASS" if b else "**FAIL**"


def rows_for(r):
    rows = []
    st = r["parts"]["stability"]
    m, g = st["metrics"], st["margins"]
    if st["ran"]:
        rows.append(("A2.2.4 / loading GM0 (m), FS-corrected", m["gm_m"], m["gm_m"] - g["gm_m"], g["gm_m"] >= 0))
        for mk, vk, label in B23:
            if mk in g:
                val = m[vk] if vk else g[mk]
                lim = (m[vk] - g[mk]) if vk else 0.0
                rows.append((label, val, lim, g[mk] >= -1e-12))
        rows.append(("A2.3 weather ratio b/a", m.get("weather_ratio"), 1.0, g.get("weather_ratio", -1) >= -1e-12))
        rows.append(("A2.3 steady heel margin (rad)", g.get("weather_steady_heel_margin_rad"), 0.0,
                     g.get("weather_steady_heel_margin_rad", -1) >= -1e-12))
        rows.append(("A2.3.5 table applicability (1=ok)", 1.0 + g.get("weather_table_applicability", -1), 1.0,
                     g.get("weather_table_applicability", -1) >= -1e-12))
        rows.append(("|trim| (m)", abs(m["trim_m"]), abs(m["trim_m"]) + g["trim_m"], g["trim_m"] >= 0))
        rows.append(("|list| (deg)", math.degrees(abs(m["list_rad"])),
                     math.degrees(abs(m["list_rad"]) + g["list_rad"]), g["list_rad"] >= 0))
        rows.append(("draught aft (m) <= max", m["draught_aft_m"], m["draught_aft_m"] + g["draught_aft_m"],
                     g["draught_aft_m"] >= 0))
        rows.append(("draught fwd (m) <= max", m["draught_fwd_m"], m["draught_fwd_m"] + g["draught_fwd_m"],
                     g["draught_fwd_m"] >= 0))
        rows.append(("draught fwd (m) >= min", m["draught_fwd_m"], m["draught_fwd_m"] - g["draught_fwd_min_m"],
                     g["draught_fwd_min_m"] >= 0))
    else:
        rows.append(("stability", None, None, False))
    sg = r["parts"].get("strength", {})
    se = r.get("strength_extremes")
    if sg.get("ran") and se:
        rows.append((f"SF+ ratio @x={f(se['sf_pos_x_m'],1)}", se["sf_pos_ratio"], 1.0, se["sf_pos_ratio"] <= 1))
        rows.append((f"SF- ratio @x={f(se['sf_neg_x_m'],1)}", se["sf_neg_ratio"], 1.0, se["sf_neg_ratio"] <= 1))
        rows.append((f"BM hog ratio @x={f(se['bm_hog_x_m'],1)}", se["bm_hog_ratio"], 1.0, se["bm_hog_ratio"] <= 1))
        rows.append((f"BM sag ratio @x={f(se['bm_sag_x_m'],1)}", se["bm_sag_ratio"], 1.0, se["bm_sag_ratio"] <= 1))
    else:
        rows.append(("strength", None, None, False))
    vi = r["parts"].get("visibility", {})
    if vi.get("ran") and vi.get("metrics"):
        rows.append(("visibility blind distance (m)", vi["metrics"].get("blind_distance_m"), vi["metrics"].get("limit_m"),
                     not vi["violations"]))
    la = r["parts"].get("lashing", {})
    if la.get("ran"):
        for k in ("max_racking_ratio", "max_longitudinal_racking_ratio", "max_corner_post_ratio",
                  "max_corner_tension_ratio", "max_lashing_ratio", "max_cell_guide_ratio", "max_fitting_ratio"):
            if k in la["metrics"]:
                rows.append((f"lashing {k}", la["metrics"][k], 1.0, la["metrics"][k] <= 1 + 1e-12))
    else:
        rows.append(("lashing", None, None, False))
    for part in ("moves", "rules", "constraints"):
        p = r["parts"].get(part)
        if p is not None:
            rows.append((f"{part} part", len(p["violations"]), 0, p["ran"] and not p["violations"]))
    return rows


def a22_table(r):
    a = r["a2_2"]
    out = ["| A 2.2 criterion | value | limit | pass |", "|---|---:|---:|---|"]
    for k, c in a["criteria"].items():
        out.append(f"| {k} | {f(c['value'],4)} | {f(c['limit'],3)} | {ok(c['passed'])} |")
    s = a["simpson_1deg_on_linear_interp"]
    out.append(f"\nCross-check (hand): trapezoid on production 2° grid a030/a040/a3040 = {f(a['grid']['a030'],4)}/"
               f"{f(a['grid']['a040'],4)}/{f(a['grid']['a3040'],4)} m·rad; Simpson 1° = {f(s['a030'],4)}/{f(s['a040'],4)}/"
               f"{f(s['a3040'],4)}; production metrics = {f(r['parts']['stability']['metrics']['area_0_30_mrad'],4)}/"
               f"{f(r['parts']['stability']['metrics']['area_0_40_mrad'],4)}/{f(r['parts']['stability']['metrics']['area_30_40_mrad'],4)}.")
    if "fine" in a:
        fi = a["fine"]
        out.append(f"Re-evaluated production GZ at 0.5° steps: {f(fi['a030'],4)}/{f(fi['a040'],4)}/{f(fi['a3040'],4)} "
                   f"(max |ΔGZ| vs 2° interpolation {f(fi['max_abs_diff_vs_grid_m'],4)} m).")
    out.append(f"Peak GZ {f(a['peak_gz_m'],3)} m at {f(a['peak_angle_deg'],2)}° (bounded refinement of production GZ; "
               f"grid peak {f(a['grid_peak_deg'],1)}°); φf = {f(a['phi_f_deg'],1)}°. "
               f"Informative 15° peak (not an A 2.2.3 option in this text): {ok(a['info_alt_15deg_peak']['passed'])}.")
    return "\n".join(out)


def main():
    allr = []
    for s in SHIPS:
        p = HERE / f"results_{s}.json"
        if p.exists():
            allr += json.loads(p.read_text())
    (HERE / "results.json").write_text(json.dumps(allr, indent=1, default=float) + "\n")
    md = ["# Convergence run — results tables (generated by report.py)", ""]
    md.append("| ship | cond | boxes (hold/deck) | cargo t | Δ t | finisher status | fin s | check s | passed | B2.3 pass/7 | A2.2 pass/5 | GM0 m |")
    md.append("|---|---|---|---:|---:|---|---:|---:|---|---:|---:|---:|")
    for r in allr:
        if "error" in r:
            md.append(f"| {r['vessel']} | {r['condition']} | ERROR {r['error'][:80]} |||||||||| |")
            continue
        st = r["parts"]["stability"]["metrics"]
        md.append(f"| {r['vessel']} | {r['condition']} | {r['boxes']} ({r['boxes_hold']}/{r['boxes_deck']}) | "
                  f"{r['cargo_mass_kg']/1e3:.0f} | {st.get('displacement_kg', 0)/1e3:.0f} | {r['finisher']['ballast_status']} | "
                  f"{r['finisher']['wall_s']:.1f} | {r['check_s']:.0f} | {ok(r['passed'])} | "
                  f"{r.get('b2_3',{}).get('passed_count','–')} | {r.get('a2_2',{}).get('passed_count','–')} | {f(st.get('gm_m'))} |")
    for r in allr:
        if "error" in r:
            md += ["", f"## {r['vessel']} {r['condition']} — ERROR", "```", r.get("traceback", r["error"]), "```"]
            continue
        st = r["parts"]["stability"]["metrics"]
        md += ["", f"## {r['vessel']} {r['condition']}", ""]
        desc = {k: r.get(k) for k in ("t_per_teu", "requested", "boxes", "left_ashore", "cells", "hold_cells",
                                      "load_list", "load_list_by_length", "initial_ballast_kg", "generator")
                if r.get(k) is not None}
        md.append(f"Condition: {json.dumps(desc, default=float)}")
        fin = r["finisher"]
        md.append(f"Finisher (`planner.finalize`, limit {fin['time_limit_s']} s): status `{fin['status']}`, "
                  f"ballast_status `{fin['ballast_status']}`, {fin['wall_s']:.1f} s, ballast found: {fin['found']}, "
                  f"judged ballast: {r['judged_ballast']} ({r['ballast_total_kg']/1e3:.0f} t). {fin['message'][:200]}")
        md.append(f"Draughts aft/fwd/mean {f(st.get('draught_aft_m'),2)}/{f(st.get('draught_fwd_m'),2)}/"
                  f"{f(st.get('draught_mean_m'),2)} m, trim {f(st.get('trim_m'),2)} m, list {f(math.degrees(st.get('list_rad',0)),2)}°, "
                  f"KG {f(st.get('kg_m'),2)} m, FSC {f(st.get('fsc_m'),3)} m, geometry table used {st.get('geometry_table_used')}, "
                  f"liquid table used {st.get('liquid_table_used')} (direct queries {st.get('liquid_direct_queries')}).")
        md += ["", "| criterion | value | limit | pass |", "|---|---:|---:|---|"]
        for name, v, l, p in rows_for(r):
            md.append(f"| {name} | {f(v,4)} | {f(l,4)} | {ok(p)} |")
        if "a2_2" in r:
            md += ["", a22_table(r)]
        vio = sorted({v['rule'] for p in r['parts'].values() for v in p['violations']})
        md.append(f"\nHard-violation rule ids: {', '.join(vio) if vio else 'none'}")
    t = HERE / "timing.json"
    if t.exists():
        md += ["", "## Timing (stowage.checker.full.check, C3)", "", "```json", t.read_text().strip(), "```"]
    (HERE / "results_tables.md").write_text("\n".join(md) + "\n")
    print(f"{len(allr)} results")


if __name__ == "__main__":
    main()

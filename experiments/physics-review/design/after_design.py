"""'After' design-value set for the KCS feeders, applied in memory on top of harness.py.

Everything here is a design value of the synthetic ship. No rule or criterion is changed.
Sources per item (see after.md and sourced-values.md / strength/strength-design.md):

- lightship mass/VCG/LCG: sourced-values.md section 2 (confidence M/L).
- ballast ~3,000 / 3,900 / 7,000 m3 (Arctic Trader 2,976 m3; Dina Trader 4,000 t;
  interpolated 1700): sourced-values.md section 3. Arrangement (APPROXIMATION, typical
  feeder): full-breadth double bottom 0.10-0.90 Lpp, double-side wing tanks over the cargo
  length, fore peak, aft peak where it fits, one wing pair labelled as the heeling pair.
  Every tank is fitted inside the KCS sections by harness.fit_tank (20 mm margin).
- HFO 553 / 790 / 1,500 m3: sourced-values.md section 3, as double-side wing tanks just
  forward of the engine room (APPROXIMATION of the usual arrangement).
- Strength: Z 4.730 / 6.417 / 12.842 m3, A_ref 0.287 / 0.421 / 0.590 m2, midbody-plateau
  profile, from strength/strength-design.md (S7.1 + old S11 implied design SWBM; side shell
  and inner skin web area). Criteria (sigma, tau, S11A waves) unchanged.
- envelope: optional vent / ER-inlet / coaming heights (design values) for the B 2.3
  area-to-flooding test; reported as minimum needed change.

Run via after_run.py.
"""
from __future__ import annotations

import copy
import math

import numpy as np

import harness as H

LIGHTSHIP = {  # sourced-values.md section 2
    "feeder_500": dict(mass_kg=2.900e6, vcg_frac_depth=0.72, lcg_frac_lpp=0.46),
    "feeder_1000": dict(mass_kg=5.200e6, vcg_m=7.80, lcg_frac_lpp=0.46),
    "feeder_1700": dict(mass_kg=8.074e6, vcg_m=9.24, lcg_frac_lpp=0.46),
}
FO_M3 = {"feeder_500": 553.0, "feeder_1000": 790.0, "feeder_1700": 1500.0}
WB_TARGET_M3 = {"feeder_500": 3000.0, "feeder_1000": 3900.0, "feeder_1700": 7000.0}
STRENGTH = {  # strength-design.md section 2/3 tables
    "feeder_500": dict(Z=4.730, A_ref=0.287),
    "feeder_1000": dict(Z=6.417, A_ref=0.421),
    "feeder_1700": dict(Z=12.842, A_ref=0.590),
}
PROFILE_U = [0, .10, .15, .30, .40, .60, .70, .85, .90, 1]
PROFILE_P = [.60, .85, 1.20, 1.20, 1.0, 1.0, 1.20, 1.20, .85, .60]


def _wb(tid, g):
    return dict(id=tid, kind="ballast", content_density_kg_m3=1025.0, heated=False, adjustable=True,
                geometry=g, assumption="APPROXIMATION: design-review ballast tank fitted inside KCS sections "
                "(experiments/physics-review/design/after_design.py)")


def _vol(g):
    return (g["x_max_m"] - g["x_min_m"]) * (g["y_max_m"] - g["y_min_m"]) * (g["z_max_m"] - g["z_min_m"])


def _half_breadth_fn(params):
    """Section half-breadth at (x, z) from the declared KCS sections (linear in x between stations)."""
    from stowage.vessel.hydrostatics import HullGeometry
    g = HullGeometry.from_params(params)
    xs = np.asarray(g.x_m)
    def at_section(i, z):
        poly = np.asarray(g.sections[i][0]); nxt = np.roll(poly, -1, axis=0)
        lo, hi = np.minimum(poly[:, 1], nxt[:, 1]), np.maximum(poly[:, 1], nxt[:, 1])
        m = (lo <= z) & (z <= hi) & (hi - lo > 1e-12)
        if not m.any():
            return 0.0
        a, b = poly[m], nxt[m]
        ys = a[:, 0] + (z - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
        return float(np.max(np.abs(ys)))
    def hb(x, z):
        j = int(np.searchsorted(xs, x))
        if j <= 0 or j >= len(xs):
            return 0.0
        i = j - 1; t = (x - xs[i]) / (xs[j] - xs[i])
        return min(at_section(i, z), at_section(j, z)) if t not in (0.0, 1.0) else at_section(i if t == 0 else j, z)
    return hb


def equivalent_prism(hb, x0, x1, z0, z1, y_in, *, y_max=None, margin=0.02, nx=24, nz=24):
    """Hull-following tank (y from y_in to the shell less margin) as a volume- and
    centroid-matched rectangular prism (P side; mirror for S).

    APPROXIMATION: the frozen Tank schema only has rectangular prisms. The prism keeps the
    true x/z extent heights, the integrated volume and the (x, y, z) centroid; its breadth is
    the mean breadth, so FSM = l*b^3/12 uses the mean free-surface breadth.
    Conservative w.r.t. containment of the mass, not a literal fitted solid.
    """
    xs = np.linspace(x0, x1, nx + 1); xs = (xs[1:] + xs[:-1]) / 2
    zs = np.linspace(z0, z1, nz + 1); zs = (zs[1:] + zs[:-1]) / 2
    dx, dz = (x1 - x0) / nx, (z1 - z0) / nz
    V = Mx = My = Mz = 0.0
    for x in xs:
        for z in zs:
            yo = hb(x, z) - margin
            if y_max is not None:
                yo = min(yo, y_max)
            w = yo - y_in
            if w <= 0:
                continue
            dv = w * dx * dz
            V += dv; Mx += dv * x; My += dv * (y_in + w / 2); Mz += dv * z
    if V < 1.0:
        return None
    xc, yc, zc = Mx / V, My / V, Mz / V
    lx, hz = x1 - x0, z1 - z0
    b = V / (lx * hz)
    return dict(shape="rectangular_prism", x_min_m=xc - lx / 2, x_max_m=xc + lx / 2,
                y_min_m=yc - b / 2, y_max_m=yc + b / 2, z_min_m=zc - hz / 2, z_max_m=zc + hz / 2)


def _mirror(g):
    return dict(g, y_min_m=-g["y_max_m"], y_max_m=-g["y_min_m"])


def tank_design(name, params, vessel, *, fo_m3=None, include_wing=True, include_deep=False, **_):
    """Return (ballast tank records, FO tank records, summary).

    Arrangement (APPROXIMATION of a typical feeder; P/S pairs):
      DB-A/M/F   full-breadth double bottom 0.10-0.35 / 0.35-0.65 / 0.65-0.90 Lpp, 0.15 m to tank top
      WING-A/F, HEEL  double-side tanks (inner skin outboard of the hold boxes) over the hold length,
                 tank top to deck; the middle 0.10 Lpp pair is the heeling pair
      DEEP-A/F   under-deck spaces with no hold cells (the KCS fit removed them), tank top to deck
      FPT, APT   fore peak 0.88-0.97 Lpp from 0.15 m to deck; aft peak 0.0-0.08 Lpp above 0.55 D
      FO         HFO: the aft part of DEEP-A (forward of the engine room), length sized to capacity
    """
    h = params["hull"]
    L, D = h["lpp_m"], h["depth_m"]
    hb = _half_breadth_fn(params)
    hold = [c for c in vessel.cells if c.vcg_base_m < D]
    db_top = min(c.vcg_base_m for c in hold)
    inner = max(abs(c.tcg_m) for c in hold) + 2.438 / 2 + 0.10
    hx0 = min(c.lcg_m for c in hold) - 6.058 / 2 - 0.3
    hx1 = max(c.lcg_m for c in hold) + 6.058 / 2 + 0.3
    regions = []  # (id, kind, x0, x1, z0, z1, y_in, y_max)
    regions += [("DB-A", .10 * L, .35 * L, 0.15, db_top, 0.02, None),
                ("DB-M", .35 * L, .65 * L, 0.15, db_top, 0.02, None),
                ("DB-F", .65 * L, .90 * L, 0.15, db_top, 0.02, None)]
    if include_wing:
        mid0, mid1 = .45 * L, .55 * L
        regions += [("WING-A", hx0, mid0, db_top, D, inner, None), ("HEEL", mid0, mid1, db_top, D, inner, None),
                    ("WING-F", mid1, hx1, db_top, D, inner, None)]
    fo_target = FO_M3[name] if fo_m3 is None else fo_m3
    fo_regions = []
    a0 = .12 * L

    def vol2(x0, x1):
        g = equivalent_prism(hb, x0, x1, db_top, D, 0.02, nx=8, nz=12)
        return 0.0 if not g else 2 * _vol(g)

    def grow(x0, x_lim, sign):
        """Shortest length from x0 towards x_lim holding fo_target (bisection)."""
        if vol2(*sorted((x0, x_lim))) < fo_target:
            return x_lim
        lo, hi = 0.0, abs(x_lim - x0)
        for _ in range(14):
            mid = (lo + hi) / 2
            if vol2(*sorted((x0, x0 + sign * mid))) >= fo_target:
                hi = mid
            else:
                lo = mid
        return x0 + sign * hi
    # HFO split half aft / half forward so bunkers do not trim the ship (APPROXIMATION of the usual
    # spread of HFO deep/side tanks): aft half in the under-deck space forward of the engine room
    # (feeder_1700 has none, its holds start at 0.11 L: aft half goes in the aft wing tanks);
    # forward half in the under-deck space forward of the holds (no hold cells there).
    full_target = fo_target
    fo_target = full_target / 2
    wa0 = hx0
    if full_target > 0:
        if hx0 - a0 > 1.0:
            x_fo = grow(a0, hx0, +1)
            fo_regions.append(("FO-A", a0, x_fo, db_top, D, 0.02, None))
            if include_deep and hx0 - x_fo > 1.0:
                regions.append(("DEEP-A", x_fo, hx0, db_top, D, 0.02, None))
        else:
            def vol_w(x0, x1):
                g = equivalent_prism(hb, x0, x1, db_top, D, inner, nx=8, nz=12)
                return 0.0 if not g else 2 * _vol(g)
            lo, hi = 0.0, .45 * L - hx0
            for _ in range(14):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if vol_w(hx0, hx0 + mid) >= fo_target else (mid, hi)
            wa0 = hx0 + hi
            fo_regions.append(("FO-A", hx0, wa0, db_top, D, inner, None))
        x_ff = grow(hx1, .88 * L, +1)
        fo_regions.append(("FO-F", hx1, x_ff, db_top, D, 0.02, None))
        if include_deep and .88 * L - x_ff > 1.0:
            regions.append(("DEEP-F", x_ff, .88 * L, db_top, D, 0.02, None))
    if include_wing and wa0 != hx0:
        regions = [r if r[0] != "WING-A" else ("WING-A", wa0, r[2], r[3], r[4], r[5], r[6]) for r in regions]
    regions += [("FPT", .88 * L, .97 * L, 0.15, D, 0.02, None), ("APT", 0.0, .08 * L, .55 * D, D, 0.02, None)]
    wb, fo = [], []
    for rid, x0, x1, z0, z1, yi, ym in regions:
        g = equivalent_prism(hb, x0, x1, z0, z1, yi, y_max=ym)
        if g:
            wb += [_wb(f"{rid}-P", g), _wb(f"{rid}-S", _mirror(g))]
    for rid, x0, x1, z0, z1, yi, ym in fo_regions:
        g = equivalent_prism(hb, x0, x1, z0, z1, yi, y_max=ym)
        if g:
            for tag, gg in (("P", g), ("S", _mirror(g))):
                fo.append(dict(id=f"{rid}-{tag}", kind="fuel_oil", content_density_kg_m3=970.0, heated=True,
                               adjustable=False, geometry=gg,
                               assumption="APPROXIMATION: design-review HFO deep tank, equivalent prism (after_design.py)"))
    summary = dict(db_top_m=db_top, inner_half_breadth_m=inner, hold_x_m=(hx0, hx1),
                   wb_m3=sum(_vol(t["geometry"]) for t in wb), fo_m3=sum(_vol(t["geometry"]) for t in fo),
                   wb_groups={t["id"][:-2]: round(2 * _vol(t["geometry"]), 1) for t in wb if t["id"].endswith("-P")},
                   wb_vcg_m=sum(_vol(t["geometry"]) * (t["geometry"]["z_min_m"] + t["geometry"]["z_max_m"]) / 2 for t in wb) / max(1e-9, sum(_vol(t["geometry"]) for t in wb)))
    return wb, fo, summary


def stations(params, hull, Z, A_ref):
    """permissible_stations with Z and A_ref given and the midbody-plateau profile.

    Same station set, wave loads and criteria as production strength_limits.permissible_stations.
    """
    import stowage.checker.strength_limits as SL
    from stowage.model import Station
    p, geometry, _, kw = SL._inputs(params, hull)
    L = kw["length_m"]
    xs = {float(x) for x in geometry.x_m}
    xs.update((getattr(geometry, "buoyant_x_min_m", 0.), getattr(geometry, "buoyant_x_max_m", L)))
    xs.update(L * i / 100 for i in range(101))
    xs.update(L * v for v in (.1, .15, .2, .25, .3, .35, .4, .5, .55, .6, .65, .7, .75, .8, .85))
    for a, b, _ in p["hull"]["lightship_distribution"]:
        xs.update((float(a), float(b)))
    for box in p["envelope"].get("hatch_coamings", []):
        a, b = float(box["x_min_m"]), float(box["x_max_m"])
        xs.update((a, b, (a + b) / 2))
    out = []
    for x in sorted(xs):
        rx = SL._rule_station(x, L)
        w = SL.wave_loads(rx, **kw)
        cap = SL.SHEAR_PERMISSIBLE_PA * A_ref * float(np.interp(rx / L, PROFILE_U, PROFILE_P))
        hog = SL.BENDING_PERMISSIBLE_PA * Z - w["hogging_nm"]
        sag = SL.BENDING_PERMISSIBLE_PA * Z + w["sagging_nm"]
        sp, sn = cap - w["max_shear_n"], cap + w["min_shear_n"]
        assert min(hog, sag, sp, sn) > 0
        out.append(Station(x_m=x, max_shear_n=sp, min_shear_n=-sn, max_bending_nm=hog, min_bending_nm=-sag))
    return out


def build(name, *, lightship=True, ballast=True, fo=True, strength=True, envelope=None,
          lcg_m=None, wing=True, deep=False, vcg_m=None):
    """Apply the after-set in memory; returns (vessel, params, summary).

    envelope: {"vent_dz_m": dz, "er_dz_m": dz, "coaming_dz_m": dz}: raise openings / coamings
    (design values). A coaming raise also lifts every deck cell by the same dz (the hatch
    cover sits on the coaming), so cargo KG pays for it.
    """
    from stowage.vessel.tanks import build_tanks
    base_params = H._ORIGINAL_LOAD(name)
    base_vessel = H.fleet.load_vessel(name, with_hull=True)
    h = base_params["hull"]
    L, D = h["lpp_m"], h["depth_m"]
    ov, summary = {}, {}
    if lightship:
        ls = LIGHTSHIP[name]
        rec = dict(mass_kg=ls["mass_kg"], lcg_m=lcg_m if lcg_m is not None else ls["lcg_frac_lpp"] * L)
        if vcg_m is not None:
            rec["vcg_m"] = vcg_m
        elif "vcg_m" in ls:
            rec["vcg_m"] = ls["vcg_m"]
        else:
            rec["vcg_frac_depth"] = ls["vcg_frac_depth"]
        ov["lightship"] = rec
    wb_t, fo_t, tsum = tank_design(name, base_params, base_vessel, include_wing=wing,
                                   include_deep=deep, fo_m3=None if fo else 0.0)
    if ballast or fo:
        ov["ballast"] = {"replace": ballast, "add_tanks": (wb_t if ballast else []) + (fo_t if fo else [])}
        summary["tanks"] = tsum
    vessel, params = H.apply_overrides(name, ov)
    changed = False
    if fo:
        params["tanks"] = [t for t in params["tanks"] if t["id"] not in ("FO-P", "FO-S")]
        params["heat_sources"] = [hs for hs in params.get("heat_sources", []) if hs["id"] not in ("heat-FO-P", "heat-FO-S")]
        for t in params["tanks"]:
            if t["kind"] == "fuel_oil":
                g = t["geometry"]
                params["heat_sources"].append(dict(id=f"heat-{t['id']}", kind="heated_tank",
                                                   **{k: g[k] for k in ("x_min_m", "x_max_m", "y_min_m", "y_max_m", "z_min_m", "z_max_m")}))
        changed = True
    updates = {}
    if envelope:
        env = params["envelope"]
        for o in env["openings"]:
            dz = envelope.get("er_dz_m", 0.0) if o["kind"] == "engine_room_air_inlet" else envelope.get("vent_dz_m", 0.0)
            o["z_m"] = float(o["z_m"]) + dz
        cdz = envelope.get("coaming_dz_m", 0.0)
        if cdz:
            for c in env["hatch_coamings"]:
                c["z_max_m"] = float(c["z_max_m"]) + cdz
            for c in params["cells"]:
                if c["vcg_base_m"] >= D - 1e-9:
                    c["vcg_base_m"] += cdz
                    c["vcg_m"] += cdz
            cells = [c.model_copy(update={"vcg_base_m": c.vcg_base_m + cdz, "vcg_m": c.vcg_m + cdz})
                     if c.vcg_base_m >= D - 1e-9 else c for c in vessel.cells]
            updates["cells"] = cells
            for r in params.get("windage", {}).get("projected_rectangles", []):
                if r["id"].startswith("HC-"):
                    r["z_max_m"] += cdz
        changed = True
        summary["envelope"] = envelope
    if changed:
        for t in params["tanks"]:
            t.pop("table", None)
        updates["tanks"] = build_tanks(params)
        if fo:
            from stowage.model.vessel import HeatSource
            hs = [x for x in vessel.heat_sources if x.id not in ("heat-FO-P", "heat-FO-S")]
            fields = HeatSource.model_fields
            for rec in params["heat_sources"]:
                if rec["id"].startswith("heat-FO-W"):
                    hs.append(HeatSource.model_validate({k: v for k, v in rec.items() if k in fields}))
            updates["heat_sources"] = hs
    if strength:
        s = STRENGTH[name]
        hull = vessel.hull.model_copy(update={"stations": stations(params, vessel.hull, s["Z"], s["A_ref"])})
        updates["hull"] = hull
        summary["strength"] = s
    if updates:
        vessel = vessel.model_copy(update=updates)
    H._OVERRIDDEN[name] = params
    H._redirect_param_readers()
    summary["lightship"] = dict(mass_t=vessel.hull.lightship_mass_kg / 1e3, vcg_m=vessel.hull.lightship_vcg_m,
                                lcg_m=vessel.hull.lightship_lcg_m)
    summary["capacities_m3"] = {k: round(sum(t.capacity_m3 for t in vessel.tanks if t.kind == k), 1)
                                for k in ("ballast", "fuel_oil", "diesel_oil", "fresh_water")}
    summary["n_ballast_tanks"] = sum(1 for t in vessel.tanks if t.adjustable)
    return vessel, params, summary


if __name__ == "__main__" and False:
    import json, time
    for n in H.FLEET:
        t0 = time.perf_counter()
        v, p, s = build(n)
        print(n, json.dumps(s, default=float), f"{time.perf_counter()-t0:.1f}s")


def write_after_json(path=None):
    """Freeze the after-set (tank records included) into after.json."""
    import json
    from pathlib import Path
    out = {}
    for n in H.FLEET:
        p = H._ORIGINAL_LOAD(n)
        v = H.fleet.load_vessel(n, with_hull=True)
        wb, fo, s = tank_design(n, p, v)
        L = p["hull"]["lpp_m"]
        ls = LIGHTSHIP[n]
        light = dict(mass_kg=ls["mass_kg"], lcg_m=ls["lcg_frac_lpp"] * L)
        light.update({"vcg_m": ls["vcg_m"]} if "vcg_m" in ls else {"vcg_frac_depth": ls["vcg_frac_depth"]})
        out[n] = dict(lightship=light, ballast={"replace": True, "add_tanks": wb + fo},
                      remove_tanks=["FO-P", "FO-S"],
                      strength_stations=dict(STRENGTH[n], profile_u=PROFILE_U, profile_p=PROFILE_P),
                      _design_summary=s)
        print(n, json.dumps(s, default=float), flush=True)
    Path(path or H.HERE / "after.json").write_text(json.dumps(out, indent=1, default=float) + "\n")


if __name__ == "__main__":
    write_after_json()

"""Loading-manual design conditions C1-C4 for the KCS feeders, with in-memory overrides.

Builds standard loading-manual conditions with the production fleet, runs the
production ballast finisher and the full checker, and records every criterion
value against its limit. Nothing under src/, params/ or tests/ is modified:
design overrides are applied to an in-memory copy of the parameter tree and the
cached production Vessel, and the checker modules' parameter readers are
redirected to that copy for the duration of the process.

ASSUMPTIONS (labelled; none relaxes a rule or criterion):
- Only 20ft 22G1 standard-height dry boxes (2.591 m, tare 2200 kg); 40/45ft,
  reefers, DG and OOG are left out so the conditions test the ship design, not
  the rule catalogue's cargo-specific provisions.
- Fill order (a designer's homogeneous-condition convention): hold tiers bottom
  up, then deck tiers; within a tier, stacks nearest LCB first, port/starboard
  alternating. Boxes are placed heaviest first; a box skips a stack whose
  20ft stack-mass limit or height limit it would exceed (a lighter box may fill
  it later), and boxes at/above the E6 heavy threshold never go on deck, so no
  heavier-on-lighter, heavy-on-deck or stack overload arises.
- C1 homogeneous 14 t/TEU; boxes are added until the next would exceed design
  displacement (port water density) or no cell accepts one.
- C2 homogeneous to scantling draught: bisection on t/TEU (cap 30.48 t, the
  ISO 668 20ft max gross) for displacement = scantling displacement.
- C3 export mix: 22G1 loaded-mass triangular distribution and empty share from
  params/cargo/feeder_export.toml (synthetic), sampled with a fixed seed until
  the deadweight budget at design draught is used; heavy boxes go low/under deck.
- C4 ballast arrival: no cargo, 10 % consumables; ballast from the finisher.
- Departure consumables 97 % of FO/DO/FW capacity; arrival 10 %.
- Port: fixture THLCH with the port draught limit set to the ship's max draught
  and no port deck-tier limit (ship-design condition, not a terminal plan).
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import time
from pathlib import Path
from random import Random

HERE = Path(__file__).resolve().parent
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("STOWAGE_VESSEL_CACHE_DIR", str(HERE / ".cache" / "vessels"))

import numpy as np  # noqa: E402

from stowage.conventions import make_container_id  # noqa: E402
from stowage.model import Instance, MovePlan  # noqa: E402
from stowage.model.plan import BallastCondition  # noqa: E402
from stowage.model.fixtures import build_instance  # noqa: E402
from stowage.params import load_param_file  # noqa: E402
import stowage.vessel.fleet as fleet  # noqa: E402

FLEET = ("feeder_500", "feeder_1000", "feeder_1700")
BOX_H = 2.591
TARE = 2200.0
MAX_20FT_GROSS = 30480.0  # ISO 668 20ft rating (kg)
# ASSUMPTION: synthetic expert-rule policy for these design conditions (expert Q2/Q11
# are open; the catalogue has no numeric default). Heavy = 25 t: E6 keeps >=25 t boxes
# under deck, E1 keeps them at the stack bottom; E2 weight-order tolerance 0 kg.
RULE_POLICY = {"expert.e6.heavy_under_deck": {"heavy_threshold": 25000.0},
               "expert.e1.heavy_bottom": {"heavy_threshold": 25000.0},
               "expert.e2.weight_order": {"tolerance": 0.0}}
_ORIGINAL_LOAD = fleet.load_ship_params
_OVERRIDDEN: dict[str, dict] = {}


# --------------------------------------------------------------------------- overrides
def _redirect_param_readers():
    """Point every module that re-reads params at the in-memory override copy."""
    def patched(name):
        if name in _OVERRIDDEN:
            return copy.deepcopy(_OVERRIDDEN[name])
        return _ORIGINAL_LOAD(name)
    import stowage.checker.stability as stab
    import stowage.checker.visibility as vis
    import stowage.checker.strength as strg
    import stowage.linear.physics as lin
    fleet.load_ship_params = patched
    stab.load_ship_params = patched
    # stability._context may read the TOML bytes directly (src changes over time);
    # wrap it so an overridden ship gets the in-memory tree and its own geometry key.
    if not getattr(stab._context, "_design_override", False):
        original_context = stab._context
        def context(vessel):
            name = vessel.slice_of or vessel.id
            if name not in _OVERRIDDEN:
                return original_context(vessel)
            params = copy.deepcopy(_OVERRIDDEN[name])
            geometry = stab._geometry(name, json.dumps(params, sort_keys=True))
            return geometry, params
        context._design_override = True
        stab._context = context
    vis.load_ship_params = patched
    lin.load_ship_params = patched
    for module in (strg, vis):
        if hasattr(module._geometry, "cache_clear"):
            module._geometry.cache_clear()
    try:  # keep the linear-model disk cache inside this experiment folder
        import stowage.linear.cache as lc
        lc._DEFAULT_ROOT = HERE / ".cache" / "linear"
    except Exception:
        pass


def _tilt_distribution(blocks, mass, lcg):
    """Rescale lightship blocks to `mass` and exponentially reweight to `lcg`.

    Same family as the fleet generator (README: 'exponentially reweighted').
    """
    from scipy.optimize import brentq
    mids = np.array([(a + b) / 2 for a, b, _ in blocks])
    base = np.array([m for _, _, m in blocks])
    base = base / base.sum()
    def lcg_of(k):
        w = base * np.exp(k * (mids - mids.mean()) / (mids.max() - mids.min()))
        w /= w.sum()
        return float((w * mids).sum()), w
    k = brentq(lambda k: lcg_of(k)[0] - lcg, -60, 60, xtol=1e-14)
    w = lcg_of(k)[1]
    return [(float(a), float(b), float(mass * wi)) for (a, b, _), wi in zip(blocks, w)]


def apply_overrides(name: str, ov: dict | None):
    """Return (vessel, params) with design overrides applied in memory.

    Supported keys (all optional):
      lightship: {mass_kg, lcg_m, vcg_m, vcg_frac_depth}
      ballast:   {double_bottom_top_m: z, scale_y: f, replace: bool}
                 raises/extends the declared WB rectangular solids (capacity
                 is recomputed from the solid); add_tanks: [tank records]
      consumable_scale: f      scales FO/DO/FW solid heights (capacity)
      strength:  {z_margin: f (default 1.10), shear_area_scale: f}
      stack_mass_scale: {deck: f, hold: f}
      max_deck_tiers: n        drops deck cells above tier n
    Principal dimensions are NOT overridable here (needs a full hull rebuild).
    """
    from stowage.vessel.tanks import build_tanks
    ov = ov or {}
    params = _ORIGINAL_LOAD(name)
    vessel = fleet.load_vessel(name, with_hull=True)
    hull = vessel.hull
    updates, hull_updates = {}, {}
    ph = params["hull"]
    D = ph["depth_m"]
    if "lightship" in ov:
        ls = ov["lightship"]
        mass = ls.get("mass_kg", ph["lightship_mass_kg"])
        lcg = ls.get("lcg_m", ph["lightship_lcg_m"])
        vcg = ls.get("vcg_m", ph["lightship_vcg_m"])
        if "vcg_frac_depth" in ls:
            vcg = ls["vcg_frac_depth"] * D
        dist = ph["lightship_distribution"]
        if not (math.isclose(mass, ph["lightship_mass_kg"]) and math.isclose(lcg, ph["lightship_lcg_m"])):
            dist = _tilt_distribution(dist, mass, lcg)
        ph.update(lightship_mass_kg=mass, lightship_lcg_m=lcg, lightship_vcg_m=vcg,
                  lightship_distribution=[list(b) for b in dist])
        hull_updates.update(lightship_mass_kg=mass, lightship_lcg_m=lcg, lightship_vcg_m=vcg,
                            lightship_distribution=[tuple(b) for b in dist])
    tanks_changed = False
    if "ballast" in ov:
        b = ov["ballast"]
        if b.get("replace"):
            params["tanks"] = [t for t in params["tanks"] if t["kind"] != "ballast"]
        for t in params["tanks"]:
            if t["kind"] != "ballast":
                continue
            g = t["geometry"]
            if "double_bottom_top_m" in b:
                g["z_max_m"] = float(b["double_bottom_top_m"])
            if "scale_y" in b:
                if g["y_min_m"] >= 0:
                    g["y_max_m"] = g["y_min_m"] + (g["y_max_m"] - g["y_min_m"]) * b["scale_y"]
                else:
                    g["y_min_m"] = g["y_max_m"] - (g["y_max_m"] - g["y_min_m"]) * b["scale_y"]
            t["capacity_m3"] = (g["x_max_m"] - g["x_min_m"]) * (g["y_max_m"] - g["y_min_m"]) * (g["z_max_m"] - g["z_min_m"])
            tanks_changed = True
        for rec in b.get("add_tanks", []):
            g = rec["geometry"]
            rec = dict(rec)
            rec.setdefault("assumption", "APPROXIMATION: design-review override tank (experiments/physics-review/design)")
            rec["capacity_m3"] = (g["x_max_m"] - g["x_min_m"]) * (g["y_max_m"] - g["y_min_m"]) * (g["z_max_m"] - g["z_min_m"])
            rec["x_aft_m"], rec["x_fore_m"] = g["x_min_m"], g["x_max_m"]
            params["tanks"].append(rec)
            tanks_changed = True
    if "consumable_scale" in ov:
        f = ov["consumable_scale"]
        for t in params["tanks"]:
            if t["kind"] in ("fuel_oil", "diesel_oil", "fresh_water"):
                g = t["geometry"]
                g["z_max_m"] = g["z_min_m"] + (g["z_max_m"] - g["z_min_m"]) * f
                t["capacity_m3"] = (g["x_max_m"] - g["x_min_m"]) * (g["y_max_m"] - g["y_min_m"]) * (g["z_max_m"] - g["z_min_m"])
        tanks_changed = True
    if tanks_changed:
        for t in params["tanks"]:
            t.pop("table", None)  # tables are rebuilt from the solid by build_tanks
        updates["tanks"] = build_tanks(params)
    if "strength" in ov:
        import stowage.checker.strength_limits as sl
        s = ov["strength"]
        saved_margin, saved_fn = sl.SECTION_MODULUS_DESIGN_MARGIN, sl._assumed_scantlings
        sl.SECTION_MODULUS_DESIGN_MARGIN = s.get("z_margin", saved_margin)
        scale = s.get("shear_area_scale", 1.0)
        sl._assumed_scantlings = lambda kw: (lambda z_a: (z_a[0], z_a[1] * scale))(saved_fn(kw))
        try:
            hull_updates["stations"] = sl.permissible_stations(params, hull)
        finally:
            sl.SECTION_MODULUS_DESIGN_MARGIN, sl._assumed_scantlings = saved_margin, saved_fn
    if "stack_mass_scale" in ov or "max_deck_tiers" in ov:
        sm = ov.get("stack_mass_scale", {})
        nt = ov.get("max_deck_tiers")
        stacks, keep = [], set()
        for s in vessel.stacks:
            f = sm.get("deck" if s.on_deck else "hold", 1.0)
            cids = list(s.cell_ids)
            if nt is not None and s.on_deck:
                cids = cids[:nt]
            keep.update(cids)
            stacks.append(s.model_copy(update={
                "max_mass_kg": s.max_mass_kg * f,
                "max_mass_20_kg": (s.max_mass_20_kg * f) if s.max_mass_20_kg else s.max_mass_20_kg,
                "max_mass_40_kg": (s.max_mass_40_kg * f) if s.max_mass_40_kg else s.max_mass_40_kg,
                "cell_ids": cids}))
        updates["stacks"] = stacks
        updates["cells"] = [c for c in vessel.cells if c.id in keep]
    if hull_updates:
        updates["hull"] = hull.model_copy(update=hull_updates)
    vessel = vessel.model_copy(update=updates) if updates else vessel
    if tanks_changed or "strength" in ov:
        # Lightship-only overrides live on the Vessel (checkers read hull.lightship_*),
        # so the expensive per-params stability geometry cache stays valid.
        _OVERRIDDEN[name] = params
    else:
        _OVERRIDDEN.pop(name, None)
    _redirect_param_readers()
    return vessel, params


def fit_tank(params, x_min, x_max, z_min, z_max, *, side, inner_y=0.02, margin=0.02):
    """Largest rectangular wing/double-bottom solid inside the declared hull.

    Half-breadth = minimum over hull sections in [x_min, x_max] (plus the two
    neighbouring stations) and over z in [z_min, z_max] of the section's
    horizontal extent, less `margin`. Returns a geometry dict for add_tanks.
    APPROXIMATION: sampled containment, as in the fleet's 20 mm fit audit.
    """
    from stowage.vessel.hydrostatics import HullGeometry
    g = HullGeometry.from_params(params)
    xs = np.asarray(g.x_m)
    idx = [i for i, x in enumerate(xs) if x_min <= x <= x_max]
    if idx:
        idx = sorted(set(idx) | {max(0, idx[0] - 1), min(len(xs) - 1, idx[-1] + 1)})
    else:
        idx = [int(np.argmin(abs(xs - (x_min + x_max) / 2)))]
    zs = np.linspace(z_min, z_max, 9)
    half = math.inf
    for i in idx:
        poly = np.asarray(g.sections[i][0])
        nxt = np.roll(poly, -1, axis=0)
        for z in zs:
            lo, hi = np.minimum(poly[:, 1], nxt[:, 1]), np.maximum(poly[:, 1], nxt[:, 1])
            m = (lo <= z) & (z <= hi) & (hi - lo > 1e-12)
            if not m.any():
                return None
            a, b = poly[m], nxt[m]
            ys = a[:, 0] + (z - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
            half = min(half, float(np.max(np.abs(ys))))
    y_out = half - margin
    if y_out <= inner_y + 0.1:
        return None
    y0, y1 = (inner_y, y_out) if side > 0 else (-y_out, -inner_y)
    return dict(shape="rectangular_prism", x_min_m=float(x_min), x_max_m=float(x_max),
                y_min_m=float(y0), y_max_m=float(y1), z_min_m=float(z_min), z_max_m=float(z_max))


# --------------------------------------------------------------------------- conditions
def _fill_order(vessel, hull):
    """Cells grouped as (stack, tier index) in the designer's fill order."""
    lcb = hull.lpp_m * 0.485  # KCS LCB ≈ 0.485 Lpp (derived_hydrostatics)
    stacks = {s.id: s for s in vessel.stacks}
    cells = {c.id: c for c in vessel.cells}
    entries = []
    for s in vessel.stacks:
        for i, cid in enumerate(s.cell_ids):
            c = cells[cid]
            if not c.accepts_20:
                continue
            entries.append(((0 if not s.on_deck else 1, i, round(abs(c.lcg_m - lcb), 3),
                             round(abs(c.tcg_m), 3), c.tcg_m), s.id, cid))
    entries.sort()
    return entries, stacks, cells


def place(vessel, masses):
    """Greedy heaviest-first placement respecting stack mass/height and tier order."""
    entries, stacks, cells = _fill_order(vessel, vessel.hull)
    mass_in = {s: 0.0 for s in stacks}
    height_in = {s: 0.0 for s in stacks}
    next_tier = {s: 0 for s in stacks}
    taken = set()
    placements = []
    start = 0
    remaining = sorted(masses, reverse=True)
    failed = None  # descending masses: once m failed, an equal mass fails too
    for m in remaining:
        if failed is not None and m >= failed:
            continue
        while start < len(entries) and entries[start][2] in taken:
            start += 1
        for key, sid, cid in entries[start:]:
            if cid in taken:
                continue
            s = stacks[sid]
            if s.on_deck and m >= RULE_POLICY["expert.e6.heavy_under_deck"]["heavy_threshold"]:
                continue
            tier = key[1]
            if tier != next_tier[sid]:
                continue
            limit = s.max_mass_20_kg or s.max_mass_kg
            if mass_in[sid] + m > limit + 1e-6 or height_in[sid] + BOX_H > s.max_height_m + 1e-9:
                continue
            taken.add(cid)
            mass_in[sid] += m
            height_in[sid] += BOX_H
            next_tier[sid] += 1
            placements.append((cid, m))
            break
        else:
            failed = m  # no cell accepts this box; it is left ashore (reported)
    return placements


def _consumables(vessel, frac):
    return {t.id: (frac * t.capacity_m3 * t.content_density_kg_m3 if not t.adjustable else 0.0)
            for t in vessel.tanks}


def _displacement_at(params, draught, rho):
    from stowage.vessel.hydrostatics import HullGeometry
    key = (params["id"], draught, rho)
    if key not in _DISP:
        g = HullGeometry.from_params(params)
        _DISP[key] = g.submerged(draught, draught, density_kg_m3=rho).displacement_kg
    return _DISP[key]


_DISP: dict = {}


def export_mass_sampler(seed):
    p = load_param_file("cargo", "feeder_export")
    e = next(x for x in p["equipment"] if x.get("iso_size_type", "").startswith("22G"))
    lw = e["loaded_weight_kg"]
    lo, mode, hi = (lw[k]["value"] for k in ("min", "mode", "max"))
    empty_share = e["empty_share"]["value"]
    rng = Random(seed)
    def draw():
        if rng.random() < empty_share:
            return TARE
        return float(min(rng.triangular(lo, hi, mode), MAX_20FT_GROSS))
    return draw, dict(equipment=e["id"], min=lo, mode=mode, max=hi, empty_share=empty_share,
                      source="params/cargo/feeder_export.toml (synthetic)")


def build_condition(name, vessel, params, cond, *, seed=7):
    payload = build_instance().model_dump(mode="python")
    rho = payload["port"]["water_density_kg_m3"]
    h = vessel.hull
    design_disp = _displacement_at(params, h.design_draught_m, rho)
    scant_disp = _displacement_at(params, h.scantling_draught_m, rho)
    cons_frac = 0.10 if cond == "C4" else 0.97
    tanks = _consumables(vessel, cons_frac)
    base = h.lightship_mass_kg + sum(m for *_, m in vessel.fixed_loads) + sum(tanks.values())
    ncells = sum(1 for c in vessel.cells if c.accepts_20)
    info = dict(condition=cond, consumables_fraction=cons_frac, design_displacement_kg=design_disp,
                scantling_displacement_kg=scant_disp, base_mass_kg=base, cells=ncells)
    if cond == "C1":
        budget = design_disp - base
        n = max(0, min(ncells, int(budget // 14000.0)))
        placements = place(vessel, [14000.0] * n)
        info.update(t_per_teu=14.0, requested=n, cargo_budget_to_design_kg=budget)
    elif cond == "C2":
        # Cargo vs t/box is NOT monotonic (deck stack limits drop whole tiers, E6
        # keeps >=25 t boxes off deck), so scan t/box on a 0.1 t grid and keep the
        # heaviest homogeneous condition at or below scantling displacement.
        budget = scant_disp - base
        best, best_tot, max_tot = None, -1.0, 0.0
        for m in np.arange(2300.0, MAX_20FT_GROSS + 1, 100.0):
            pl = place(vessel, [float(m)] * ncells)
            tot = sum(x for _, x in pl)
            max_tot = max(max_tot, tot)
            if best_tot < tot <= budget:
                best, best_tot = (float(m), pl), tot
        mid, placements = best
        info.update(t_per_teu=mid / 1000, requested=len(placements),
                    scantling_reached=best_tot >= budget - 30480.0,
                    max_homogeneous_cargo_kg=max_tot, cargo_budget_to_scantling_kg=budget)
    elif cond == "C3":
        draw, src = export_mass_sampler(seed)
        budget = design_disp - base
        masses, tot = [], 0.0
        while len(masses) < ncells:
            m = draw()
            if tot + m > budget:
                break
            masses.append(m)
            tot += m
        placements = place(vessel, masses)
        info.update(export_mix=src, requested=len(masses), seed=seed)
    elif cond == "C4":
        placements = []
        info.update(requested=0)
    else:
        raise ValueError(cond)
    containers, onboard = [], []
    for serial, (cid, m) in enumerate(placements, 1):
        box = make_container_id("QZXU", serial)
        containers.append(dict(id=box, iso_type="22G1", length_ft=20, height_m=BOX_H, tare_kg=TARE,
                               gross_mass_kg=m, pol="THLCH", pod="THBKK"))
        onboard.append(dict(slot=cid, container_id=box))
    payload.update(id=f"design_{cond}_{name}", vessel=vessel, containers=containers, load_list=[],
                   discharge_list=[], onboard=dict(vessel_id=vessel.id, placements=onboard,
                                                   tank_masses_kg=tanks))
    payload["yard"]["stacks"] = []
    payload["rule_overrides"] = {**payload.get("rule_overrides", {}), **copy.deepcopy(RULE_POLICY)}
    payload["port"]["max_draught_m"] = h.max_draught_m
    payload["port"]["quay_crane"]["max_on_deck_tiers"] = max(len(s.cell_ids) for s in vessel.stacks if s.on_deck)
    payload["port"]["quay_crane"]["max_lift_height_m"] = 60
    for c in payload["rotation"]["calls"]:
        c["max_on_deck_tiers"] = None
    inst = Instance.model_validate(payload)
    cargo = sum(m for _, m in placements)
    info["requested"] = max(info["requested"], len(placements))
    hold = sum(1 for cid, _ in placements if not next(s for s in vessel.stacks if cid in s.cell_ids).on_deck)
    info.update(boxes=len(placements), boxes_hold=hold, boxes_deck=len(placements) - hold,
                left_ashore=info["requested"] - len(placements), cargo_mass_kg=cargo,
                displacement_kg=base + cargo, mean_box_kg=cargo / max(1, len(placements)))
    return inst, info


# --------------------------------------------------------------------------- run + record
def _part_summary(part):
    return dict(ran=part.ran, error=part.error, metrics=dict(part.metrics), margins=dict(part.margins),
                violations=[dict(rule=v.rule_id, measured=v.measured, limit=v.limit, unit=v.unit,
                                 message=(v.message or "")[:200]) for v in part.violations])


def run_condition(name, vessel, params, cond, *, finisher_s=60.0, extra_ballast_probe=True):
    from stowage.checker.full import check
    from stowage.checker.stability import check_stability
    from stowage.solver.ballast import finish_ballast
    t0 = time.perf_counter()
    inst, info = build_condition(name, vessel, params, cond)
    info["build_s"] = time.perf_counter() - t0
    plan = MovePlan(id=f"{inst.id}.design", instance_id=inst.id, call_index=inst.call_index)
    t0 = time.perf_counter()
    outcome = finish_ballast(inst, plan, time_limit_s=finisher_s, seed=0)
    info["finisher"] = dict(status=outcome.status, iterations=outcome.iterations,
                            wall_s=time.perf_counter() - t0, proof=(outcome.proof or "")[:600])
    if outcome.plan is not None:
        final = outcome.plan
    else:  # judge the finisher's best-known attempt is not exposed; judge empty ballast explicitly
        final = plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="planner")})
    info["ballast_kg"] = dict(final.ballast.tank_masses_kg) if final.ballast else {}
    t0 = time.perf_counter()
    report = check(inst, final)
    info["check_s"] = time.perf_counter() - t0
    info["passed"] = report.passed
    info["parts"] = {k: _part_summary(p) for k, p in report.parts.items()}
    info["part_s"] = {}
    if extra_ballast_probe and not report.passed:
        # Diagnostic: every ballast tank pressed full (max GM from ballast; FSM zero).
        full = {t.id: t.capacity_m3 * t.content_density_kg_m3 for t in vessel.tanks if t.adjustable}
        ship = inst.onboard.model_copy(update={"tank_masses_kg": {**inst.onboard.tank_masses_kg, **full}})
        t0 = time.perf_counter()
        st = check_stability(inst, ship, call_index=0)
        info["probe_all_wb_full"] = dict(stability=_part_summary(st), seconds=time.perf_counter() - t0,
                                         ballast_total_kg=sum(full.values()))
    return info


def run(ships, conds, overrides=None, *, finisher_s=60.0, label="before", out=None):
    results = []
    out = Path(out) if out else HERE / f"results_{label}.json"
    done = {}
    if out.exists():
        for r in json.loads(out.read_text()):
            done[(r["vessel"], r["condition"])] = r
    for name in ships:
        t0 = time.perf_counter()
        vessel, params = apply_overrides(name, (overrides or {}).get(name, (overrides or {}).get("*")))
        load_s = time.perf_counter() - t0
        for cond in conds:
            if (name, cond) in done:
                results.append(done[(name, cond)])
                continue
            info = run_condition(name, vessel, params, cond, finisher_s=finisher_s)
            info.update(vessel=name, label=label, vessel_load_s=load_s,
                        overrides=(overrides or {}).get(name, (overrides or {}).get("*")))
            results.append(info)
            done[(name, cond)] = info
            out.write_text(json.dumps(list(done.values()), indent=1, default=float) + "\n")
            print(f"[{label}] {name} {cond}: passed={info['passed']} finisher={info['finisher']['status']} "
                  f"check={info['check_s']:.1f}s finisher={info['finisher']['wall_s']:.1f}s", flush=True)
    return list(done.values())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ships", default=",".join(FLEET))
    ap.add_argument("--conditions", default="C1,C2,C3,C4")
    ap.add_argument("--overrides", help="JSON file: {ship or '*': {override dict}}")
    ap.add_argument("--label", default="before")
    ap.add_argument("--finisher-s", type=float, default=60.0)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    ov = json.loads(Path(a.overrides).read_text()) if a.overrides else None
    run(a.ships.split(","), a.conditions.split(","), ov, finisher_s=a.finisher_s, label=a.label, out=a.out)


if __name__ == "__main__":
    sys.exit(main())

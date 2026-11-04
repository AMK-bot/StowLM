"""Convergence run: loading-manual conditions C1-C3 (+C5) on PURE production code.

Adapted from experiments/physics-review/design/harness.py. Differences:
- No design overrides and no module monkeypatches that change results. The only
  patches are passive *spies* on stability_criteria.evaluate_criteria and
  stability_curve.build_stability_curve that record the GZ curve the production
  checker already computes (inputs/outputs are passed through unchanged), so the
  IS Code Part A 2.2.1-2.2.3 set can be evaluated on the same curve.
- Ballast via stowage.planner.finalize (production 4.2 finisher, hard deadline).
- FixedLoad objects (.mass_kg) instead of the old tuple fixed_loads.
- C5: generator's full-rung realistic export (generate_random_v2 "full"),
  departure condition built by placing its load list (see build_c5).

Condition definitions are those of the design harness (labelled ASSUMPTIONS there):
dry 20ft 22G1 boxes, heaviest-first designer fill order, E6 >= 25 t under deck,
departure consumables 97 %, port draught limit = ship max draught.
Must be run with cwd = repo root (production stability table caches are cwd-relative).
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

import numpy as np  # noqa: E402

from stowage.conventions import make_container_id  # noqa: E402
from stowage.model import Instance, MovePlan, PlannerResult  # noqa: E402
from stowage.model.fixtures import build_instance  # noqa: E402
from stowage.params import load_param_file  # noqa: E402
import stowage.vessel.fleet as fleet  # noqa: E402
import stowage.checker.stability_criteria as _crit  # noqa: E402
import stowage.checker.stability_curve as _curve  # noqa: E402

FLEET = ("feeder_500", "feeder_1000", "feeder_1700")
BOX_H = 2.591
TARE = 2200.0
MAX_20FT_GROSS = 30480.0
RULE_POLICY = {"expert.e6.heavy_under_deck": {"heavy_threshold": 25000.0},
               "expert.e1.heavy_bottom": {"heavy_threshold": 25000.0},
               "expert.e2.weight_order": {"tolerance": 0.0}}

# ----------------------------------------------------------------- passive spies
SPY: dict = {}
_orig_eval = _crit.evaluate_criteria
_orig_curve = _curve.build_stability_curve


def _spy_eval(angles, gz, **kw):
    out = _orig_eval(angles, gz, **kw)
    SPY["eval"] = dict(angles=np.array(angles, float), gz=np.array(gz, float),
                       phi_f=float(kw["phi_f_rad"]), gm=float(kw["gm_m"]),
                       peak_angle=kw.get("peak_angle_rad"), peak_gz=kw.get("peak_gz_m"),
                       metrics=dict(out[0]), margins=dict(out[1]), failures=list(out[2]))
    return out


def _spy_curve(*a, **kw):
    curve = _orig_curve(*a, **kw)
    SPY["at_angle"] = curve.at_angle
    return curve


_crit.evaluate_criteria = _spy_eval
_curve.build_stability_curve = _spy_curve


# ----------------------------------------------------------------- conditions
def _fill_order(vessel, hull):
    lcb = hull.lpp_m * 0.485
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
    entries, stacks, cells = _fill_order(vessel, vessel.hull)
    mass_in = {s: 0.0 for s in stacks}
    height_in = {s: 0.0 for s in stacks}
    next_tier = {s: 0 for s in stacks}
    taken, placements, start, failed = set(), [], 0, None
    for m in sorted(masses, reverse=True):
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
            if key[1] != next_tier[sid]:
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
            failed = m
    return placements


def _consumables(vessel, frac):
    return {t.id: (frac * t.capacity_m3 * t.content_density_kg_m3 if not t.adjustable else 0.0)
            for t in vessel.tanks}


_DISP: dict = {}


def _displacement_at(params, draught, rho):
    from stowage.vessel.hydrostatics import HullGeometry
    key = (params["id"], draught, rho)
    if key not in _DISP:
        g = HullGeometry.from_params(params)
        _DISP[key] = g.submerged(draught, draught, density_kg_m3=rho).displacement_kg
    return _DISP[key]


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


def _wrap(name, vessel, cond, placements, tanks, payload=None):
    payload = payload or build_instance().model_dump(mode="python")
    h = vessel.hull
    containers, onboard = [], []
    for serial, (cid, m) in enumerate(placements, 1):
        box = make_container_id("QZXU", serial)
        containers.append(dict(id=box, iso_type="22G1", length_ft=20, height_m=BOX_H, tare_kg=TARE,
                               gross_mass_kg=m, pol="THLCH", pod="THBKK"))
        onboard.append(dict(slot=cid, container_id=box))
    payload.update(id=f"conv_{cond}_{name}", vessel=vessel, containers=containers, load_list=[],
                   discharge_list=[], onboard=dict(vessel_id=vessel.id, placements=onboard,
                                                   tank_masses_kg=tanks))
    payload["yard"]["stacks"] = []
    payload["rule_overrides"] = {**payload.get("rule_overrides", {}), **copy.deepcopy(RULE_POLICY)}
    payload["port"]["max_draught_m"] = h.max_draught_m
    payload["port"]["quay_crane"]["max_on_deck_tiers"] = max(len(s.cell_ids) for s in vessel.stacks if s.on_deck)
    payload["port"]["quay_crane"]["max_lift_height_m"] = 60
    for c in payload["rotation"]["calls"]:
        c["max_on_deck_tiers"] = None
    return Instance.model_validate(payload)


def build_condition(name, vessel, params, cond, *, seed=7):
    payload = build_instance().model_dump(mode="python")
    rho = payload["port"]["water_density_kg_m3"]
    h = vessel.hull
    design_disp = _displacement_at(params, h.design_draught_m, rho)
    scant_disp = _displacement_at(params, h.scantling_draught_m, rho)
    tanks = _consumables(vessel, 0.97)
    fixed = sum(load.mass_kg for load in vessel.fixed_loads)
    base = h.lightship_mass_kg + fixed + sum(tanks.values())
    ncells = sum(1 for c in vessel.cells if c.accepts_20)
    info = dict(condition=cond, consumables_fraction=0.97, design_displacement_kg=design_disp,
                scantling_displacement_kg=scant_disp, base_mass_kg=base, fixed_loads_kg=fixed,
                cells=len(vessel.cells), cells_20=ncells,
                hold_cells=sum(len(s.cell_ids) for s in vessel.stacks if not s.on_deck))
    if cond == "C1":
        budget = design_disp - base
        n = max(0, min(ncells, int(budget // 14000.0)))
        placements = place(vessel, [14000.0] * n)
        info.update(t_per_teu=14.0, requested=n, cargo_budget_to_design_kg=budget)
    elif cond == "C2":
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
    else:
        raise ValueError(cond)
    inst = _wrap(name, vessel, cond, placements, tanks, payload)
    _summarise(info, vessel, placements, base)
    return inst, info


def _summarise(info, vessel, placements, base):
    cargo = sum(m for _, m in placements)
    deck_cells = {c for s in vessel.stacks if s.on_deck for c in s.cell_ids}
    hold = sum(1 for cid, _ in placements if cid not in deck_cells)
    info["requested"] = max(info.get("requested", 0), len(placements))
    info.update(boxes=len(placements), boxes_hold=hold, boxes_deck=len(placements) - hold,
                left_ashore=info["requested"] - len(placements), cargo_mass_kg=cargo,
                displacement_before_ballast_kg=base + cargo,
                mean_box_kg=cargo / max(1, len(placements)))


def build_c5(name, *, seed=11):
    """Generator 'full' rung realistic export; departure = its load list stowed.

    The generator produces an empty-ship instance with a yard and load list.
    Planning a full rung with the solver is out of scope here; this builds the
    loading-manual-style departure the generator *sizes for*: the generated
    load-list boxes (actual masses, sizes) placed onboard heaviest-first with the
    same placer (20 ft boxes only; non-20 ft boxes are reported and skipped),
    with the instance's own tank masses (booking_tank_masses: 97 % consumables +
    the policy's bounded export ballast; the finisher may then change ballast).
    """
    from stowage.generate.instances_v2_random import generate_random_v2
    t0 = time.perf_counter()
    gen = generate_random_v2("full", seed=seed, noise=0.2, vessel_name=name, port_name="laem_chabang_like")
    gen_s = time.perf_counter() - t0
    gi = gen if isinstance(gen, Instance) else gen[0]
    vessel = gi.vessel
    boxes = {c.id: c for c in gi.containers}
    load = [boxes[b] for b in gi.load_list]
    lengths = {}
    for b in load:
        lengths[b.length_ft] = lengths.get(b.length_ft, 0) + 1
    masses = [b.gross_mass_kg for b in load if b.length_ft == 20]
    placements = place(vessel, masses)
    tanks = dict(gi.onboard.tank_masses_kg)
    base = vessel.hull.lightship_mass_kg + sum(l.mass_kg for l in vessel.fixed_loads) + sum(tanks.values())
    inst = _wrap(name, vessel, "C5", placements, tanks)
    info = dict(condition="C5", generator="generate_random_v2('full', seed=%d, noise=0.2)" % seed,
                generator_s=gen_s, load_list=len(load), load_list_by_length=lengths,
                load_list_mass_kg=sum(b.gross_mass_kg for b in load),
                initial_ballast_kg=sum(v for k, v in tanks.items()
                                       if next(t for t in vessel.tanks if t.id == k).adjustable),
                requested=len(masses), base_mass_kg=base, cells=len(vessel.cells))
    _summarise(info, vessel, placements, base)
    return inst, info


# ----------------------------------------------------------------- A 2.2 evaluation
def _trap(x, y, lo, hi):
    if hi <= lo:
        return 0.0
    q = np.r_[lo, x[(x > lo) & (x < hi)], hi]
    return float(np.trapezoid(np.interp(q, x, y), q))


def part_a_2_2(spy, *, fine=True):
    """IS Code A 2.2.1-2.2.3 on the production (free-surface-corrected) GZ curve."""
    from scipy.optimize import minimize_scalar
    x, y, phi_f = spy["angles"], spy["gz"], spy["phi_f"]
    at = SPY.get("at_angle")
    r15, r25, r30, r40 = (math.radians(a) for a in (15, 25, 30, 40))
    end = min(phi_f, float(x[-1]))
    up = min(r40, end)
    res = dict(phi_f_deg=math.degrees(phi_f), grid_step_deg=float(np.degrees(np.diff(x)).max()))
    grid = dict(a030=_trap(x, y, 0, min(r30, end)), a040=_trap(x, y, 0, up), a3040=_trap(x, y, r30, up))
    res["grid"] = grid
    # Hand cross-check: Simpson's rule on the same samples (uniform 2 deg grid + end point by interpolation)
    from scipy.integrate import simpson
    def simp(lo, hi):
        if hi <= lo:
            return 0.0
        n = max(2, int(round((hi - lo) / math.radians(1.0))))
        n += n % 2
        q = np.linspace(lo, hi, n + 1)
        return float(simpson(np.interp(q, x, y), x=q))
    res["simpson_1deg_on_linear_interp"] = dict(a030=simp(0, min(r30, end)), a040=simp(0, up), a3040=simp(r30, up))
    # Refined: re-evaluate the production curve function (same equilibrium solver) on 0.5 deg
    if fine and at is not None:
        t0 = time.perf_counter()
        xf = np.radians(np.arange(0.0, math.degrees(up) + 1e-9, 0.5))
        if xf[-1] < up - 1e-9:
            xf = np.r_[xf, up]
        yf = np.array([float(at(float(p))[0]) for p in xf])
        res["fine_eval_s"] = time.perf_counter() - t0
        res["fine"] = dict(a030=_trap(xf, yf, 0, min(r30, end)), a040=_trap(xf, yf, 0, up),
                           a3040=_trap(xf, yf, r30, up),
                           max_abs_diff_vs_grid_m=float(np.max(np.abs(yf - np.interp(xf, x, y)))))
        areas = res["fine"]
    else:
        areas = grid
    # Peak: grid argmax within [0, end], refined by bounded minimisation of production GZ
    mask = x <= end + 1e-12
    xi, yi = x[mask], y[mask]
    i = int(np.argmax(yi))
    pk_a, pk_gz = float(xi[i]), float(yi[i])
    if at is not None and 0 < i < len(xi) - 1:
        o = minimize_scalar(lambda p: -float(at(float(p))[0]), bounds=(float(xi[i - 1]), float(xi[i + 1])),
                            method="bounded", options={"xatol": 1e-4})
        if o.success and -o.fun >= pk_gz - 1e-9:
            pk_a, pk_gz = float(o.x), float(-o.fun)
    res.update(peak_angle_deg=math.degrees(pk_a), peak_gz_m=pk_gz, grid_peak_deg=math.degrees(float(xi[i])))
    gz30 = max([float(np.interp(r30, x, y)), *yi[xi >= r30]]) if end >= r30 else 0.0
    if pk_a >= r30:
        gz30 = max(gz30, pk_gz)
    crit = {
        "A2.2.1 area 0-30 >= 0.055": (areas["a030"], 0.055),
        "A2.2.1 area 0-40/phi_f >= 0.09": (areas["a040"], 0.09),
        "A2.2.1 area 30-40/phi_f >= 0.03": (areas["a3040"], 0.03),
        "A2.2.2 GZ >= 0.20 at >= 30 deg": (gz30, 0.20),
        "A2.2.3 max GZ angle >= 25 deg": (math.degrees(pk_a), 25.0),
    }
    if end < r30:
        crit["A2.2.1 phi_f >= 30 deg (area 0-30 defined)"] = (math.degrees(end), 30.0)
    res["criteria"] = {k: dict(value=v, limit=l, passed=bool(v >= l - 1e-12)) for k, (v, l) in crit.items()}
    res["passed_count"] = sum(c["passed"] for c in res["criteria"].values())
    res["total"] = len(crit)
    res["info_alt_15deg_peak"] = dict(value=math.degrees(pk_a), limit=15.0, passed=bool(pk_a >= r15),
        note="Not in A 2.2.3 text; 15 deg appears only in B 2.4.5.2.4 (offshore supply vessel equivalent criteria)")
    res["A2.2.4_gm"] = dict(value=spy["gm"], limit=0.15, passed=bool(spy["gm"] >= 0.15))
    return res


# ----------------------------------------------------------------- run + record
def _part_summary(part):
    return dict(ran=part.ran, error=part.error, metrics=dict(part.metrics), margins=dict(part.margins),
                violations=[dict(rule=v.rule_id, measured=v.measured, limit=v.limit, unit=v.unit,
                                 message=(v.message or "")[:200]) for v in part.violations])


def strength_extremes(vessel, part):
    m = part.metrics
    out = {}
    for kind, key, up, lo in (("sf", "sf_n@", "max_shear_n", "min_shear_n"),
                              ("bm", "bm_nm@", "max_bending_nm", "min_bending_nm")):
        pos, neg = (0.0, None), (0.0, None)
        for st in vessel.hull.stations:
            label = format(st.x_m, ".12g")
            v = m.get(key + label)
            if v is None:
                continue
            if v >= 0:
                r = v / getattr(st, up)
                if r > pos[0]:
                    pos = (r, st.x_m)
            else:
                r = v / getattr(st, lo)
                if r > neg[0]:
                    neg = (r, st.x_m)
        names = ("hog", "sag") if kind == "bm" else ("pos", "neg")
        out[f"{kind}_{names[0]}_ratio"], out[f"{kind}_{names[0]}_x_m"] = pos
        out[f"{kind}_{names[1]}_ratio"], out[f"{kind}_{names[1]}_x_m"] = neg
    return out


def run_condition(name, cond, *, finisher_s=30.0, fine=False):
    from stowage.checker.full import check
    from stowage.planner import finalize
    t0 = time.perf_counter()
    if cond == "C5":
        inst, info = build_c5(name)
    else:
        vessel = fleet.load_vessel(name, with_hull=True)
        params = fleet.load_ship_params(name)
        inst, info = build_condition(name, vessel, params, cond)
    vessel = inst.vessel
    info["build_s"] = time.perf_counter() - t0
    plan = MovePlan(id=f"{inst.id}.conv", instance_id=inst.id, call_index=inst.call_index)
    if cond == "C5":
        # Keep the generator's initial ballast as a declared starting point; finisher still owns ballast.
        pass
    pr = PlannerResult(status="ok", plan=plan, planner="physics-review.convergence", planner_version="1", seed=0)
    t0 = time.perf_counter()
    fin = finalize(inst, pr, time_limit_s=finisher_s)
    info["finisher"] = dict(status=fin.status, ballast_status=fin.metrics.get("ballast_status"),
                            message=(fin.message or "")[:600], wall_s=time.perf_counter() - t0,
                            time_limit_s=finisher_s,
                            found=bool(fin.plan is not None and fin.plan.ballast is not None))
    final = fin.plan if (fin.plan is not None and fin.plan.ballast is not None) else plan
    info["judged_ballast"] = "finisher" if final is not plan else "none (arrival tanks, ballast=None)"
    info["ballast_kg"] = dict(final.ballast.tank_masses_kg) if final.ballast else {}
    info["ballast_total_kg"] = sum(info["ballast_kg"].values())
    SPY.clear()
    t0 = time.perf_counter()
    report = check(inst, final)
    info["check_s"] = time.perf_counter() - t0
    info["passed"] = report.passed
    info["parts"] = {k: _part_summary(p) for k, p in report.parts.items()}
    if "strength" in report.parts and report.parts["strength"].ran:
        info["strength_extremes"] = strength_extremes(vessel, report.parts["strength"])
    if "eval" in SPY:
        spy = SPY["eval"]
        info["gz_curve"] = dict(angles_deg=np.degrees(spy["angles"]).tolist(), gz_m=spy["gz"].tolist(),
                                phi_f_deg=math.degrees(spy["phi_f"]))
        t0 = time.perf_counter()
        info["a2_2"] = part_a_2_2(spy, fine=fine)
        info["a2_2_s"] = time.perf_counter() - t0
        st = info["parts"]["stability"]
        b = {k: dict(margin=v) for k, v in st["margins"].items() if k.startswith("containership_")}
        info["b2_3"] = dict(criteria=b, passed_count=sum(v["margin"] >= -1e-12 for v in b.values()),
                            total=len(b), C=st["metrics"].get("containership_form_factor"))
    return info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ships", default=",".join(FLEET))
    ap.add_argument("--conditions", default="C1,C2,C3,C5")
    ap.add_argument("--finisher-s", type=float, default=30.0)
    ap.add_argument("--out", default=str(HERE / "results.json"))
    ap.add_argument("--fine", action="store_true", help="re-evaluate GZ on 0.5 deg (slow without tables)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    done = {}
    if out.exists():
        for r in json.loads(out.read_text()):
            done[(r["vessel"], r["condition"])] = r
    for name in a.ships.split(","):
        for cond in a.conditions.split(","):
            if (name, cond) in done:
                continue
            t0 = time.perf_counter()
            try:
                info = run_condition(name, cond, finisher_s=a.finisher_s, fine=a.fine)
            except Exception as exc:  # record, don't hide
                import traceback
                info = dict(condition=cond, error=f"{type(exc).__name__}: {exc}",
                            traceback=traceback.format_exc()[-3000:])
            info.update(vessel=name, total_s=time.perf_counter() - t0)
            done[(name, cond)] = info
            out.write_text(json.dumps(list(done.values()), indent=1, default=float) + "\n")
            print(f"{name} {cond}: passed={info.get('passed')} fin={info.get('finisher', {}).get('ballast_status')} "
                  f"fin_s={info.get('finisher', {}).get('wall_s', 0):.1f} check_s={info.get('check_s', 0):.1f} "
                  f"total={info['total_s']:.1f} err={info.get('error')}", flush=True)


if __name__ == "__main__":
    sys.exit(main())

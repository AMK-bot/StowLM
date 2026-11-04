"""Run C1-C4 with the after.json design-value set (in memory; rules untouched).

after.json extends harness overrides with design values the harness cannot express:
  remove_tanks         ids dropped before the new tanks are built (old 6 m3 FO tanks)
  strength_stations    {Z, A_ref, profile_u, profile_p}: hull.stations from strength-design.md
  envelope             {vent_dz_m, er_dz_m, coaming_dz_m} opening/coaming heights (design values)
  ballast_plan         {cond: [tank-id prefixes pressed full]}: a declared loading-manual ballast
                       (cargo budget is reduced by it, so the draught limit still holds)

Each condition: production finisher first (harness.run_condition semantics). If it returns
not_found and the condition declares a ballast_plan, the declared ballast is judged by the full
checker instead and reported as source="loading_manual" (never mixed up with the finisher).

Run: OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python \
     experiments/physics-review/design/after_run.py --label after [--ships ..] [--conditions ..]
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import sys
import time
from pathlib import Path

import harness as H
import after_design as AD
from stowage.model import MovePlan
from stowage.model.plan import BallastCondition


def apply(name, ov):
    from stowage.vessel.tanks import build_tanks
    from stowage.model.vessel import HeatSource
    ov = copy.deepcopy(ov or {})
    base = {k: ov[k] for k in ("lightship", "ballast", "consumable_scale", "max_deck_tiers", "stack_mass_scale") if k in ov}
    vessel, params = H.apply_overrides(name, base)
    D = params["hull"]["depth_m"]
    updates = {}
    rm = set(ov.get("remove_tanks", []))
    if rm:
        params["tanks"] = [t for t in params["tanks"] if t["id"] not in rm]
        params["heat_sources"] = [h for h in params.get("heat_sources", []) if h["id"] not in {f"heat-{i}" for i in rm}]
        hs = [h for h in vessel.heat_sources if h.id not in {f"heat-{i}" for i in rm}]
        for t in params["tanks"]:
            if t.get("heated") and not any(h["id"] == f"heat-{t['id']}" for h in params["heat_sources"]):
                g = t["geometry"]
                rec = dict(id=f"heat-{t['id']}", kind="heated_tank",
                           **{k: g[k] for k in ("x_min_m", "x_max_m", "y_min_m", "y_max_m", "z_min_m", "z_max_m")})
                params["heat_sources"].append(rec)
                hs.append(HeatSource.model_validate({k: v for k, v in rec.items() if k in HeatSource.model_fields}))
        updates["heat_sources"] = hs
    env = ov.get("envelope")
    if env:
        for o in params["envelope"]["openings"]:
            o["z_m"] = float(o["z_m"]) + (env.get("er_dz_m", 0.0) if o["kind"] == "engine_room_air_inlet"
                                           else env.get("vent_dz_m", 0.0))
        cdz = env.get("coaming_dz_m", 0.0)
        if cdz:
            for c in params["envelope"]["hatch_coamings"]:
                c["z_max_m"] = float(c["z_max_m"]) + cdz
            for c in params["cells"]:
                if c["vcg_base_m"] >= D - 1e-9:
                    c["vcg_base_m"] += cdz; c["vcg_m"] += cdz
            updates["cells"] = [c.model_copy(update={"vcg_base_m": c.vcg_base_m + cdz})
                                if c.vcg_base_m >= D - 1e-9 else c for c in (updates.get("cells") or vessel.cells)]
            updates["stacks"] = [st.model_copy(update={"base_z_m": st.base_z_m + cdz}) if st.on_deck else st
                                 for st in vessel.stacks]
            for r in params.get("windage", {}).get("projected_rectangles", []):
                if r["id"].startswith("HC-"):
                    r["z_max_m"] += cdz
    if rm or env:
        for t in params["tanks"]:
            t.pop("table", None)
        updates["tanks"] = build_tanks(params)
    s = ov.get("strength_stations")
    if s:
        AD.PROFILE_U, AD.PROFILE_P = s.get("profile_u", AD.PROFILE_U), s.get("profile_p", AD.PROFILE_P)
        updates["hull"] = vessel.hull.model_copy(update={"stations": AD.stations(params, vessel.hull, s["Z"], s["A_ref"])})
    if updates:
        vessel = vessel.model_copy(update=updates)
    H._OVERRIDDEN[name] = params
    H._redirect_param_readers()
    return vessel, params


def _pattern(vessel, prefixes):
    """'DB' presses every DB-* tank full; 'APT@0.5' fills APT-* to 50 % (slack: free surface counted)."""
    out = {}
    for spec in prefixes:
        p, _, f = spec.partition("@")
        frac = float(f) if f else 1.0
        for t in vessel.tanks:
            if t.adjustable and t.id.startswith(p):
                out[t.id] = frac * t.capacity_m3 * t.content_density_kg_m3
    return out


@contextlib.contextmanager
def condition_context(declared, draught_margin_m=0.0):
    """Declared ballast joins the deadweight (cargo budget shrinks) and the cargo budget is taken
    at (T_max - margin) mean draught, so a residual trim keeps both end draughts <= T_max.
    Loading-manual convention (condition definition, not a criterion)."""
    orig_c, orig_d = H._consumables, H._displacement_at
    if declared:
        H._consumables = lambda v, f: {**orig_c(v, f), **declared}
    if draught_margin_m:
        H._displacement_at = lambda p, T, rho: orig_d(p, T - draught_margin_m, rho)
    try:
        yield
    finally:
        H._consumables, H._displacement_at = orig_c, orig_d


def run_condition(name, vessel, params, cond, *, finisher_s, plan_prefixes=None, draught_margin_m=0.0):
    from stowage.checker.full import check
    from stowage.checker.stability import check_stability
    from stowage.solver.ballast import finish_ballast
    declared = _pattern(vessel, plan_prefixes) if plan_prefixes else {}
    t0 = time.perf_counter()
    with condition_context(declared, draught_margin_m):
        inst, info = H.build_condition(name, vessel, params, cond)
    info["build_s"] = time.perf_counter() - t0
    info["draught_margin_m"] = draught_margin_m
    if declared:  # onboard state carries no ballast; the plan's ballast condition sets it
        inst = inst.model_copy(update={"onboard": inst.onboard.model_copy(update={
            "tank_masses_kg": {**inst.onboard.tank_masses_kg, **{k: 0.0 for k in declared}}})})
    info["declared_ballast_prefixes"] = plan_prefixes
    info["declared_ballast_kg"] = sum(declared.values())
    plan = MovePlan(id=f"{inst.id}.design", instance_id=inst.id, call_index=inst.call_index)
    t0 = time.perf_counter()
    outcome = finish_ballast(inst, plan, time_limit_s=finisher_s, seed=0)
    info["finisher"] = dict(status=outcome.status, iterations=outcome.iterations,
                            wall_s=time.perf_counter() - t0, proof=(outcome.proof or "")[:600])
    if outcome.plan is not None:
        final, src = outcome.plan, "finisher"
    elif declared:
        final, src = plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg=declared, source="planner")}), "loading_manual"
    else:
        final, src = plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="planner")}), "empty"
    info["ballast_source"] = src
    info["ballast_kg"] = dict(final.ballast.tank_masses_kg) if final.ballast else {}
    t0 = time.perf_counter()
    report = check(inst, final)
    info["check_s"] = time.perf_counter() - t0
    info["passed"] = report.passed
    info["parts"] = {k: H._part_summary(p) for k, p in report.parts.items()}
    info["part_timings_note"] = "full check wall time only; parts are not timed separately by check()"
    if not report.passed:
        full = {t.id: t.capacity_m3 * t.content_density_kg_m3 for t in vessel.tanks if t.adjustable}
        ship = inst.onboard.model_copy(update={"tank_masses_kg": {**inst.onboard.tank_masses_kg, **full}})
        t0 = time.perf_counter()
        st = check_stability(inst, ship, call_index=0)
        info["probe_all_wb_full"] = dict(stability=H._part_summary(st), seconds=time.perf_counter() - t0,
                                         ballast_total_kg=sum(full.values()))
    return info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ships", default=",".join(H.FLEET))
    ap.add_argument("--conditions", default="C1,C2,C3,C4")
    ap.add_argument("--overrides", default=str(H.HERE / "after.json"))
    ap.add_argument("--label", default="after")
    ap.add_argument("--finisher-s", type=float, default=45.0)
    a = ap.parse_args(argv)
    ovs = json.loads(Path(a.overrides).read_text())
    out = H.HERE / f"results_{a.label}.json"
    done = {(r["vessel"], r["condition"]): r for r in json.loads(out.read_text())} if out.exists() else {}
    for name in a.ships.split(","):
        ov = ovs.get(name, ovs.get("*"))
        t0 = time.perf_counter()
        vessel, params = apply(name, ov)
        load_s = time.perf_counter() - t0
        for cond in a.conditions.split(","):
            if (name, cond) in done:
                continue
            prefixes = (ov.get("ballast_plan") or {}).get(cond)
            t0 = time.perf_counter()
            info = run_condition(name, vessel, params, cond, finisher_s=a.finisher_s, plan_prefixes=prefixes,
                                 draught_margin_m=(ov.get("draught_margin_m") or {}).get(cond, 0.0))
            info.update(vessel=name, label=a.label, vessel_load_s=load_s, total_s=time.perf_counter() - t0,
                        overrides={k: v for k, v in ov.items() if k not in ("ballast",)} | {
                            "ballast_tank_ids": [t["id"] for t in ov.get("ballast", {}).get("add_tanks", [])]})
            done[(name, cond)] = info
            out.write_text(json.dumps(list(done.values()), indent=1, default=float) + "\n")
            st = info["parts"]["stability"]["metrics"]
            print(f"[{a.label}] {name} {cond}: passed={info['passed']} fin={info['finisher']['status']} "
                  f"src={info['ballast_source']} WB={info['declared_ballast_kg']/1e3:.0f}t "
                  f"GM={st.get('gm_m')} trim={st.get('trim_m')} "
                  f"fails={sorted({v['rule'] for p in info['parts'].values() for v in p['violations']})} "
                  f"check={info['check_s']:.1f}s fin={info['finisher']['wall_s']:.1f}s total={info['total_s']:.1f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())

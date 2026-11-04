"""Checker-backed screens and stage-page physics frames; no UI dependencies."""
from __future__ import annotations

import math

from stowage.checker.rules import check_static_rules
from stowage.checker.stability import _loading_policy, _minimum_loading_gm
from stowage.checker.stability_loading import CONSUMABLE_KINDS, loading_condition
from stowage.model import CheckReport, Instance, ShipState

FAST_LABEL = "Fast screen, full check running"
FAST_NOTE = (
    "APPROXIMATION: upright hydrostatic-table interpolation, actual cargo/lightship/fixed-load "
    "mass moments, tabulated tank centres and free surfaces; linear MCT trim and small-angle "
    "list. No finite-angle GZ, hull strength, lashing, visibility or intermediate-lift check. "
    "A clear fast screen is provisional; only the full checker decides."
)


def _broken(v):
    return {"rule_id": v.rule_id, "message": v.message, "measured": v.measured,
            "limit": v.limit, "unit": v.unit, "boxes": list(v.containers),
            "slots": list(v.slots), "severity": v.severity}


def _number(v):
    return float(v) if isinstance(v, (float, int)) and math.isfinite(v) else None


def _header(instance, note):
    hull = instance.vessel.hull
    stations = hull.stations if hull else []
    return {"stations_x_m": [s.x_m for s in stations],
            "sf_min_n": [s.min_shear_n for s in stations],
            "sf_max_n": [s.max_shear_n for s in stations],
            "bm_min_nm": [s.min_bending_nm for s in stations],
            "bm_max_nm": [s.max_bending_nm for s in stations],
            "keyframes": "final-only", "note": note, "frames": []}


def _empty_frame(instance):
    hull = instance.vessel.hull
    limits = [x for x in (getattr(hull, "max_draught_m", None), instance.port.max_draught_m)
              if x is not None]
    return {"step": 0, "criteria": None, "gm_m": None, "gm_min_m": None,
            "heel_deg": None, "trim_m": None, "draught_aft_m": None,
            "draught_fwd_m": None, "draught_mean_m": None,
            "max_draught_m": min(limits) if limits else None,
            "displacement_t": None, "sf_n": None, "bm_nm": None,
            "max_sf_ratio": None, "max_bm_ratio": None,
            "gz": [], "gz_error": None, "stability_passed": None,
            "strength_passed": None, "stability_violations": [],
            "strength_violations": [], "error": None}


def _interpolate(rows, x, axis, field):
    """Linear interpolation with explicit coverage; never clamp/extrapolate."""
    rows = sorted(rows, key=lambda row: getattr(row, axis))
    if not rows or not getattr(rows[0], axis) <= x <= getattr(rows[-1], axis):
        raise ValueError(f"{axis} outside declared table coverage")
    for left, right in zip(rows, rows[1:]):
        a, b = getattr(left, axis), getattr(right, axis)
        if a <= x <= b:
            if b <= a:
                raise ValueError(f"non-increasing {axis} table")
            f = (x-a)/(b-a)
            return (1-f)*getattr(left, field)+f*getattr(right, field)
    return getattr(rows[0], field)


def _rough_frame(instance, ship):
    h = instance.vessel.hull
    if h is None:
        raise ValueError("missing hull/lightship data")
    # The checker computes actual cargo heights (including 40-foot supports),
    # fixed loads and hatch-cover mass changes. Its geometry-only diagnostics
    # below are expected: this screen deliberately uses the declared tank tables.
    condition = loading_condition(instance, ship, free_surface_method=2)
    errors = [e for e in condition.errors if not any(
        marker in e for marker in ("exact liquid geometry unavailable",
            "exact filling-range geometry unavailable", "exact slack free-surface geometry unavailable",
            "exact free-surface geometry unavailable"))]
    if errors:
        raise ValueError("; ".join(errors))
    carried = {tank.kind for tank in instance.vessel.tanks
               if ship.tank_masses_kg.get(tank.id, 0) > 0 and tank.kind in CONSUMABLE_KINDS}
    fsm = 0.
    for tank in instance.vessel.tanks:
        volume = ship.tank_masses_kg.get(tank.id, 0)/tank.content_density_kg_m3
        if tank.kind in carried:
            if not tank.table:
                raise ValueError(f"tank {tank.id}: missing sounding table")
            inertia = max(row.fsm_m4 for row in tank.table)
        elif 0 < volume < .98*tank.capacity_m3:
            inertia = _interpolate(tank.table, volume, "volume_m3", "fsm_m4")
        else:
            inertia = 0.
        fsm += inertia*tank.content_density_kg_m3
    rho = instance.port.water_density_kg_m3
    equivalent_mass = condition.mass_kg*h.water_density_kg_m3/rho
    at = lambda field: _interpolate(h.hydrostatics, equivalent_mass, "displacement_kg", field)
    gm = at("kmt_m")-condition.kg_m-fsm/condition.mass_kg
    mct = at("mct_nm_per_cm")*rho/h.water_density_kg_m3
    if mct <= 0:
        raise ValueError("nonpositive moment to change trim")
    trim = -.01*condition.mass_kg*9.80665*(condition.lcg_m-at("lcb_m"))/mct
    draught, lcf = at("draught_m"), at("lcf_m")
    ta = draught+trim*lcf/h.lpp_m
    tf = draught-trim*(h.lpp_m-lcf)/h.lpp_m
    # atan2 avoids reporting a neutral upright condition for negative GM.
    heel = math.atan2(condition.tcg_m, gm) if condition.tcg_m else (0. if gm > 0 else math.pi/2)
    frame = _empty_frame(instance)
    frame.update(criteria="rough upright-table fast screen (not a checker verdict)",
        gm_m=gm, gm_min_m=_minimum_loading_gm(instance), heel_deg=math.degrees(heel),
        trim_m=trim, draught_aft_m=ta, draught_fwd_m=tf,
        draught_mean_m=(ta+tf)/2, displacement_t=condition.mass_kg/1000,
        gz_error="not computed by the fast screen", covers_off=sorted(ship.covers_off),
        tank_state="candidate ship tank masses; tabulated centres and free surfaces")
    return frame


def fast_check(instance: Instance, ship: ShipState) -> tuple[list[dict], dict]:
    """Screen all public static rules plus approximate loading limits."""
    broken = []
    try:
        static = check_static_rules(instance, ship, call_index=instance.call_index)
        broken.extend(_broken(v) for v in static.violations if v.severity == "hard")
    except Exception as exc:
        broken.append({"rule_id": "checker.internal_error", "message": f"Fast static screen unavailable: {exc}",
                       "measured": None, "limit": None, "boxes": [], "severity": "hard"})
    block = _header(instance, FAST_NOTE)
    block.update(label=FAST_LABEL, approximation=True, full_check_pending=True)
    try:
        frame = _rough_frame(instance, ship)
        minimum_trim, maximum_trim, minimum_mass, maximum_mass = _loading_policy(instance)
        h = instance.vessel.hull
        checks = [("iscode.loading.gm", frame["gm_m"], frame["gm_min_m"], True, "m"),
            ("iscode.loading.trim", frame["trim_m"], minimum_trim, True, "m"),
            ("iscode.loading.trim", frame["trim_m"], maximum_trim, False, "m"),
            ("iscode.loading.list", abs(math.radians(frame["heel_deg"])), h.max_list_rad, False, "rad"),
            ("iscode.loading.draft", max(frame["draught_aft_m"], frame["draught_fwd_m"]), h.max_draught_m, False, "m"),
            ("iscode.loading.draft", frame["draught_fwd_m"], h.min_draught_fwd_m, True, "m"),
            ("iscode.loading.displacement", frame["displacement_t"]*1000, minimum_mass, True, "kg"),
            ("iscode.loading.displacement", frame["displacement_t"]*1000, maximum_mass, False, "kg")]
        if instance.port.max_draught_m is not None:
            checks.append(("port.call.draft", max(frame["draught_aft_m"], frame["draught_fwd_m"]),
                           instance.port.max_draught_m, False, "m"))
        for rule, value, limit, lower, unit in checks:
            if (value < limit-1e-10) if lower else (value > limit+1e-10):
                broken.append({"rule_id": rule, "message": f"Rough fast-screen estimate exceeds {'minimum' if lower else 'maximum'} loading limit.",
                    "measured": value, "limit": limit, "unit": unit,
                    "boxes": sorted(p.container_id for p in ship.placements),
                    "severity": "hard", "approximation": True})
        frame["stability_violations"] = sorted({b["rule_id"] for b in broken if b.get("approximation")})
    except Exception as exc:
        frame = _empty_frame(instance)
        frame["error"] = f"Fast physics screen unavailable: {exc}"
        broken.append({"rule_id": "checker.missing_vessel_data", "message": frame["error"],
                       "measured": None, "limit": None, "boxes": [], "severity": "hard", "approximation": True})
    # A fast screen never sets stability_passed or strength_passed green.
    block["frames"] = [frame]
    return broken, block


def _departure_part(report, name):
    for key, part in report.parts.items():
        if key.startswith("condition:") and key.endswith(":"+name) and part.metrics.get("condition.phase: departure"):
            return part
    return report.parts.get(name)


def _passed(part):
    return bool(part and part.ran and not part.error and not any(v.severity == "hard" for v in part.violations))


def full_gauges(instance: Instance, ship: ShipState, report: CheckReport) -> dict:
    """Extract departure metrics already computed by the supplied full checker."""
    if isinstance(report, dict):
        report = CheckReport.model_validate(report)
    stab, strength = _departure_part(report, "stability"), _departure_part(report, "strength")
    m, s = (stab.metrics if stab else {}), (strength.metrics if strength else {})
    frame = _empty_frame(instance)
    for field in ("gm_m", "trim_m", "draught_aft_m", "draught_fwd_m", "draught_mean_m", "max_sf_ratio", "max_bm_ratio"):
        frame[field] = _number((s if field.startswith("max_") else m).get(field))
    try:
        frame["gm_min_m"] = _minimum_loading_gm(instance)
    except (ValueError, TypeError, AttributeError):
        pass
    frame.update(criteria="departure: full checker sea-going IS Code",
        heel_deg=math.degrees(m["list_rad"]) if _number(m.get("list_rad")) is not None else None,
        displacement_t=m["displacement_kg"]/1000 if _number(m.get("displacement_kg")) is not None else None,
        stability_passed=_passed(stab), strength_passed=_passed(strength),
        stability_violations=sorted({v.rule_id for v in stab.violations}) if stab else [],
        strength_violations=sorted({v.rule_id for v in strength.violations}) if strength else [],
        gz_error="GZ samples are not stored in the checker report; no curve recomputed",
        error="; ".join(e for e in ((stab.error if stab else "stability not run"),
                                     (strength.error if strength else "strength not run")) if e) or None,
        tank_state="declared departure ballast", covers_off=sorted(ship.covers_off))
    if instance.vessel.hull and strength and strength.ran:
        xs = [station.x_m for station in instance.vessel.hull.stations]
        frame["sf_n"] = [_number(s.get(f"sf_n@{x:.12g}")) for x in xs]
        frame["bm_nm"] = [_number(s.get(f"bm_nm@{x:.12g}")) for x in xs]
    block = _header(instance, "Measured departure metrics from the full checker report. GZ curve unavailable in report.")
    block.update(frames=[frame], label="Full checker", approximation=False,
                 full_check_pending=False, checker_passed=report.passed)
    return block

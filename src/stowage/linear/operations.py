"""Fast mass algebra and conservative ballast linear programs.

The GM numerator is a stability mass moment (kg m). Reserving the maximum
free-surface moment of every tank keeps every row affine, including at empty
and full tanks; this deliberately sacrifices feasible solutions rather than
assuming that a chosen tank remains pressed full. List uses the small-angle
wall-sided relation tan(phi) = transverse moment / stability moment.
APPROXIMATION: the list relation and fixed tank arms are local approximations;
measured errors and explicit band rows restrict their use.

Constraint margins use measured error in the unsafe direction of each bound.
For residual ``linear - exact``, a lower bound needs its positive maximum and
an upper bound needs its negative minimum. A safe-side prediction bias remains
in the absolute error report but does not consume the opposite safety margin.
Models without signed measurements retain their absolute-error margins.
Certified family models instead default to the measured directional policy's
10 percent allowance plus its explicit reference-transfer reserve. The public
``margin_scale`` is an additional caller multiplier on those default reserves.
An explicitly qualified boundary LIST policy instead reserves the measured
outward mass-moment error at the full operating angle. It retains the GM row
and maximum-FSM restoring proxy; it never silently caps an exhausted angle.

Builder-created models retain their physical identity and endpoint draught
limits through dataclasses.replace. A manually constructed model without
endpoint limits uses its public validity band; its supplier must make that
mean-draught band conservative for operational endpoint limits.
Sources: IMO 2008 IS Code, Part B 3.1 (free surfaces); scipy.optimize.linprog
HiGHS documentation (linear inequalities and bounded continuous variables).
"""
from __future__ import annotations

from collections.abc import Mapping
from numbers import Real
import warnings
import hashlib
import json
import numpy as np
from scipy.optimize import OptimizeWarning, linprog

from stowage.conventions import cells_of_slot


_LOADING_FIELDS = ("min_trim_m", "max_trim_m", "min_displacement_kg", "max_displacement_kg")
_LOADING_RULES = {"iscode.loading.trim": ("min_trim_m", "max_trim_m"),
                  "iscode.loading.displacement": ("min_displacement_kg", "max_displacement_kg")}


def _field(record, name, default=None):
    return record.get(name, default) if isinstance(record, Mapping) else getattr(record, name, default)


def _loading_number(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name}: explicit finite numeric loading policy is required")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{name}: explicit finite numeric loading policy is required") from None
    if not np.isfinite(number):
        raise ValueError(f"{name}: explicit finite numeric loading policy is required")
    if "displacement" in name and number <= 0:
        raise ValueError(f"{name}: loading displacement must be positive")
    return number


def _validated_loading_policy(policy):
    if not isinstance(policy, Mapping) or set(policy) != set(_LOADING_FIELDS):
        raise ValueError("loading policy requires all four independently declared endpoints")
    result = {key: _loading_number(policy[key], key) for key in _LOADING_FIELDS}
    for lower, upper in _LOADING_RULES.values():
        if result[lower] > result[upper]:
            raise ValueError(f"loading policy {lower} must not exceed {upper}")
    return result


def public_loading_policy(instance):
    """D40 vessel-manual endpoints with instance overrides that only tighten.

    Calibration coverage is not an operating permit. A real HullModel needs
    independent values/evidence for both signed trim and displacement bounds.
    Legacy minimal algebra fixtures without D40 fields carry no such policy.
    """
    from stowage.model.vessel import HullModel, VesselFieldProvenance

    overrides = instance.rule_overrides
    if not isinstance(overrides, Mapping):
        raise ValueError("rule_overrides: loading policy must be a mapping")
    for rule, values in overrides.items():
        if values and rule.startswith(("iscode.", "iacs.", "port.call.draft")) and rule not in _LOADING_RULES:
            raise ValueError(f"{rule}: linear safety overrides must be supplied as vessel/port limits")
    missing = object()
    hull = _field(instance.vessel, "hull", missing)
    if hull is None:
        raise ValueError("linear loading policy requires vessel hull")
    if hull is missing or (not isinstance(hull, HullModel) and all(
            _field(hull, key, missing) is missing for key in
            ("min_trim_m", "min_displacement_kg", "max_displacement_kg"))):
        if any(overrides.get(rule) for rule in _LOADING_RULES):
            raise ValueError("loading policy overrides require declared vessel hull endpoints")
        return None
    policy = _validated_loading_policy({key: _field(hull, key) for key in _LOADING_FIELDS})
    for key in _LOADING_FIELDS:
        evidence = _field(hull, key+"_provenance")
        texts = (evidence.source, evidence.assumption) if isinstance(evidence, VesselFieldProvenance) else ()
        supplied = [text for text in texts if text is not None]
        if not supplied or not all(isinstance(text, str) and text.strip() for text in supplied):
            raise ValueError(f"{key}_provenance: declared vessel loading policy evidence is required")
    for rule, (lower, upper) in _LOADING_RULES.items():
        values = overrides.get(rule, {})
        if not isinstance(values, Mapping) or set(values) - {"minimum", "maximum"}:
            raise ValueError(f"{rule}: loading policy permits only minimum/maximum overrides")
        for bound, field in (("minimum", lower), ("maximum", upper)):
            if bound not in values:
                continue
            number = _loading_number(values[bound], field)
            if (bound == "minimum" and number < policy[field]) or (bound == "maximum" and number > policy[field]):
                raise ValueError(f"{rule}.{bound}: must not weaken vessel loading policy")
            policy[field] = number
    return _validated_loading_policy(policy)


def vessel_physics_identity(vessel):
    """Bind model use to the physical inputs/limits, independent of vessel ID.

    Recompute from current values: Vessel and its child lists are mutable, so
    object identity or a memoized hash cannot detect a changed same-ID hull.
    Include the coefficient inputs and the stack height/deck contract used by
    instance assembly, without reading/reintegrating hull files.
    """
    from .cache import _canonical, vessel_physics
    physical = vessel_physics(vessel)
    def stack_inputs(stack):
        if isinstance(stack, dict):
            return {key: stack[key] for key in ("id", "max_height_m", "on_deck") if key in stack}
        return {key: getattr(stack, key) for key in ("id", "max_height_m", "on_deck")
                if hasattr(stack, key)}
    physical["assembly_stack_limits"] = [stack_inputs(stack) for stack in vessel.stacks]
    hull = _field(vessel, "hull")
    physical["loading_manual_endpoints"] = {key: _field(hull, key) for key in _LOADING_FIELDS}
    return hashlib.sha256(_canonical(physical)).hexdigest()


def departure_manifest_identity(instance):
    """Fingerprint every property of the known fixed departure cargo.

    Equal cargo mass does not imply equal stability error: swapping a high cube
    or OOG box changes stack heights. Narrow models therefore belong to these
    exact box records, as well as their tank and displacement windows.
    """
    from .bands import departure_manifest
    boxes = departure_manifest(instance)
    content = [box.model_dump(mode="json") for box in boxes]
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return tuple(box.id for box in boxes), hashlib.sha256(encoded).hexdigest()


def actual_fsm_kgm(model, instance, tank_masses):
    """Known-fill FSM with the exact sounding geometry used by checker 3.1.

    Rectangular tanks have constant upright slack FSM, so their stored maximum
    is also their exact point correction. Section tanks have fill-dependent
    waterplane inertia: obtain that point from TankGeometry.sounding, never a
    bounding box. Carried consumable kinds retain the conservative maximum in
    every tank of that kind. The affine LP separately reserves all tank maxima.
    """
    from stowage.checker.stability_loading import CONSUMABLE_KINDS

    records = getattr(instance.vessel, "tanks", None)
    if records is None:
        return float(model.tank_fsm_max_kgm.sum())
    tanks = {tank.id: tank for tank in records}
    masses = {
        key: float(tank_masses[i] if model.tank_adjustable[i] else
                   instance.onboard.tank_masses_kg.get(key, 0.))
        for i, key in enumerate(model.tank_ids)
    }
    carried = {tanks[key].kind for key, mass in masses.items()
               if mass > 0 and tanks[key].kind in CONSUMABLE_KINDS}
    params = getattr(model, "_reference_params", None)
    resolved = params is not None
    total = 0.
    lightship = getattr(getattr(instance.vessel, "hull", None), "lightship_mass_kg", None)
    def charged(moment):
        # Production 3.1 applies the B3.1 small-tank exemption to each actual
        # correction, using lightship as its conservative displacement basis.
        return 0. if lightship and moment/lightship < .01 else moment
    for i, key in enumerate(model.tank_ids):
        tank, mass = tanks[key], masses[key]
        if tank.kind in carried:
            total += charged(model.tank_fsm_max_kgm[i])
            continue
        # Use the public checker's volume threshold when real Tank records are
        # present. Lightweight algebra fixtures expose only id/kind and retain
        # the equivalent mass threshold and rectangular constant-FSM semantics.
        if hasattr(tank, "content_density_kg_m3") and hasattr(tank, "capacity_m3"):
            volume = mass/tank.content_density_kg_m3
            slack = 0 < volume < .98*tank.capacity_m3
        else:
            volume = None
            slack = 0 < mass < .98*model.tank_capacity_kg[i]
        if not slack:
            continue
        if not resolved:
            if hasattr(instance.vessel, "model_dump"):
                from .physics import _params_for
                _, params = _params_for(instance)
            resolved = True
        if params is None:
            total += charged(model.tank_fsm_max_kgm[i])
            continue
        raw = next((record for record in params.get("tanks", ()) if record["id"] == key), None)
        if raw is None:
            raise ValueError(f"exact tank geometry unavailable for known fill: {key}")
        geometry = raw.get("geometry", raw)
        if geometry.get("shape", "rectangular_prism") == "rectangular_prism":
            total += charged(model.tank_fsm_max_kgm[i])
            continue
        if volume is None:
            raise ValueError(f"tank density/capacity unavailable for exact sounding: {key}")
        from stowage.vessel.tanks import TankGeometry
        solid = TankGeometry.from_params(raw, ship_params=params)
        volume = min(volume, solid.capacity_m3)
        from stowage.checker.stability_sounding import sounding
        total += charged(sounding(solid,volume).fsm_m4*tank.content_density_kg_m3)
    return float(total)


def mass_vectors(model, ship, instance, *, ignore_adjustable=False):
    """Split spanning boxes into physical cells; fixed tanks live in constants."""
    from stowage.conventions import canonical_vessel_id
    if (canonical_vessel_id(model.vessel_id) != canonical_vessel_id(instance.vessel.id)
            or canonical_vessel_id(ship.vessel_id) != canonical_vessel_id(model.vessel_id)):
        raise ValueError("linear model and ship must belong to the instance vessel")
    if model.water_density_kg_m3 != instance.port.water_density_kg_m3:
        raise ValueError("linear model water density differs from instance port")
    expected_physics = getattr(model, "_vessel_physics_fingerprint", None)
    if expected_physics is not None and expected_physics != vessel_physics_identity(instance.vessel):
        raise ValueError("vessel geometry or limits differ from model build; rebuild linear model")
    expected_loads = getattr(model, "_fixed_loads_fingerprint", None)
    if expected_loads is not None:
        from .cache import fixed_loads_fingerprint
        if expected_loads != fixed_loads_fingerprint(instance):
            raise ValueError("fixed loads differ from model constants; rebuild linear model")
    current_policy = public_loading_policy(instance)
    diagnostics = getattr(model, "measurement_diagnostics", {})
    if not isinstance(diagnostics, Mapping):
        raise ValueError("invalid loading policy diagnostics")
    saved_policy = diagnostics.get("loading_policy")
    if current_policy is not None or saved_policy is not None:
        if saved_policy is None or current_policy != _validated_loading_policy(saved_policy):
            raise ValueError("loading policy differs from model build; rebuild linear model")
    current_port_max = getattr(instance.port, "max_draught_m", None)
    saved_max = getattr(model, "hull_max_draught_m", None)
    if current_port_max is not None and saved_max is not None and current_port_max < saved_max:
        raise ValueError("port draught limit is stricter than model build; rebuild linear model")
    if ship.covers_off:
        raise ValueError("linear model requires all hatch covers in their departure positions")
    expected_fixed = getattr(model, "_fixed_tank_masses", None)
    if expected_fixed is not None:
        for key, mass in expected_fixed.items():
            if not np.isclose(instance.onboard.tank_masses_kg.get(key, 0.0), mass, rtol=0, atol=1e-6):
                raise ValueError(f"fixed tank mass differs from model build: {key}")
    expected_ids = getattr(model, "_manifest_ids", None)
    expected_fingerprint = getattr(model, "_manifest_fingerprint", None)
    if expected_ids is not None or expected_fingerprint is not None:
        current_ids, current_fingerprint = departure_manifest_identity(instance)
        if tuple(expected_ids or ()) != current_ids or expected_fingerprint != current_fingerprint:
            raise ValueError("departure manifest differs from model calibration; rebuild linear model")
        if sorted(placement.container_id for placement in ship.placements) != list(current_ids):
            raise ValueError("ship cargo differs from calibrated departure manifest")
    ci = {key: i for i, key in enumerate(model.cell_ids)}
    boxes = {box.id: box for box in instance.containers}
    cargo = np.zeros(len(ci), dtype=np.float64)
    for placement in ship.placements:
        cells = cells_of_slot(placement.slot)
        mass = boxes[placement.container_id].gross_mass_kg / len(cells)
        for cell in cells:
            if cell not in ci:
                raise ValueError(f"placement occupies unknown model cell {cell}")
            cargo[ci[cell]] += mass
    tanks = np.zeros(len(model.tank_ids), dtype=np.float64)
    unknown = set(ship.tank_masses_kg) - set(model.tank_ids)
    if unknown:
        raise ValueError(f"unknown tank ids: {sorted(unknown)}")
    for i, key in enumerate(model.tank_ids):
        if ignore_adjustable and model.tank_adjustable[i]:
            continue
        value = float(ship.tank_masses_kg.get(key, 0.0))
        if not np.isfinite(value) or value < 0 or value > model.tank_capacity_kg[i] + 1e-6:
            raise ValueError(f"tank mass outside capacity: {key}")
        if model.tank_adjustable[i]:
            tanks[i] = value
        elif not np.isclose(value, instance.onboard.tank_masses_kg.get(key, 0.0), rtol=0, atol=1e-6):
            raise ValueError(f"fixed tank mass changed since model build: {key}")
    return cargo, tanks


def evaluate(model, ship, instance):
    from .model import LinearCondition
    c, t = mass_vectors(model, ship, instance)
    disp = float(model.const_mass_kg + c.sum() + t.sum())
    if disp <= 0:
        raise ValueError("displacement must be positive")
    mx = float(model.const_moment_x_kgm + model.cell_lcg_m @ c + model.tank_lcg_m @ t)
    my = float(model.const_moment_y_kgm + model.cell_tcg_m @ c + model.tank_tcg_m @ t)
    mz = float(model.const_moment_z_kgm + model.cell_vcg_m @ c + model.tank_vcg_m @ t)
    a, b = model.km_line
    gm = (a + b*disp - mz - actual_fsm_kgm(model, instance, t)) / disp
    c0, cx, cd = model.trim_line
    trim = float(c0 + cx*mx + cd*disp)
    d0, dd = model.draught_line
    draught = float(d0 + dd*disp)
    heel = float(np.arctan2(my, gm*disp))
    band = model.band
    inside = (band.displacement_min_kg <= disp <= band.displacement_max_kg
              and band.draught_min_m <= draught <= band.draught_max_m
              and abs(trim) <= band.max_abs_trim_m and abs(heel) <= band.max_abs_heel_rad)
    manifest_mass = getattr(model, "_cargo_mass_kg", None)
    if manifest_mass is not None:
        inside = inside and np.isclose(c.sum(), manifest_mass, rtol=1e-10, atol=1e-5)
    for key, (lower, upper) in getattr(model, "_tank_mass_bounds_kg", {}).items():
        mass = t[model.tank_ids.index(key)]
        inside = inside and lower-1e-6 <= mass <= upper+1e-6
    return LinearCondition(disp, mx/disp, my/disp, mz/disp, float(gm), trim, heel,
                           draught, model.const_sf_n + model.sf_per_kg_cell @ c + model.sf_per_kg_tank @ t,
                           model.const_bm_nm + model.bm_per_kg_cell @ c + model.bm_per_kg_tank @ t, bool(inside))


def _unsafe_error(model, metric, side, *, fallback=None):
    """Read an empirical one-sided envelope, falling back to the frozen maximum.

    ``LinearErrors`` remains the absolute prediction-error contract. Builder
    diagnostics additionally distinguish optimistic error from safe bias; old
    or manually constructed models need no new fields. Invalid signed metadata
    fails closed rather than silently weakening a constraint.
    """
    absolute = np.asarray(getattr(model.errors, metric) if fallback is None else fallback, dtype=float)
    diagnostics = getattr(model, "measurement_diagnostics", {})
    if not isinstance(diagnostics, dict):
        raise ValueError("invalid signed error diagnostics")
    signed = diagnostics.get("signed_errors")
    if signed is None:
        value = absolute
    else:
        if not isinstance(signed, dict) or not isinstance(signed.get(side), dict):
            raise ValueError("invalid signed error diagnostics")
        directional = signed[side]
        if metric not in directional and fallback is None:
            raise ValueError(f"missing {side} error envelope for {metric}")
        try:
            value = np.asarray(directional.get(metric, absolute), dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {side} error envelope for {metric}") from exc
    if (value.shape != absolute.shape or not np.isfinite(value).all() or
            np.any(value < 0)):
        raise ValueError(f"invalid {side} error envelope for {metric}")
    return float(value) if value.ndim == 0 else value


def _constraint_cell_vcg(model):
    """Conservative cargo-height arms for affine constraints, when supplied.

    Reusable loading families can reserve per-cell height uncertainty directly
    in the vertical moment, while point predictions retain their nominal arms.
    The builder is responsible for proving these bounds for its loading family;
    numerical validation here prevents malformed bounds entering solver rows.
    """
    try:
        stored = getattr(model, "_constraint_cell_vcg_m", None)
        arms = np.asarray(model.cell_vcg_m if stored is None else stored, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid conservative cell VCG arms") from exc
    if arms.shape != (len(model.cell_ids),) or not np.isfinite(arms).all():
        raise ValueError("invalid conservative cell VCG arms")
    return arms


def constraints(model, margin_scale, *, direction):
    from .model import LinearConstraintSet
    from .margin_policy import default_reserves
    if not isinstance(direction, str) or direction not in ("necessary", "conservative"):
        raise ValueError("direction must be necessary or conservative")
    if not np.isfinite(margin_scale) or margin_scale < 1:
        raise ValueError("margin_scale must be finite and at least one")
    band = model.band
    diagnostics = getattr(model, "measurement_diagnostics", {})
    loading = diagnostics.get("loading_policy") if isinstance(diagnostics, Mapping) else None
    loading = _validated_loading_policy(loading) if loading is not None else None
    cell_vcg = _constraint_cell_vcg(model)
    policy = default_reserves(model, direction=direction)
    bound_sign = 1. if direction == "conservative" else -1.
    if direction == "necessary" and (policy is None or "list_moment_kgm" not in policy):
        raise ValueError("necessary rows require independently qualified opposite residuals and LIST boundary evidence")
    def error(name, side, *, fallback=None):
        return policy[side][name] if policy is not None else _unsafe_error(model, name, side, fallback=fallback)
    lower = {name: margin_scale*error(name, "lower_unsafe")
             for name in ("gm_m", "trim_m", "list_rad", "draught_m", "sf_n", "bm_nm")}
    upper = {name: margin_scale*error(name, "upper_unsafe")
             for name in ("trim_m", "list_rad", "draught_m", "sf_n", "bm_nm")}
    ones_c, ones_t = np.ones(len(model.cell_ids)), np.ones(len(model.tank_ids))
    # Fixed tank columns are identically zero: their masses are in constants.
    ones_t *= model.tank_adjustable
    tx = model.tank_lcg_m * model.tank_adjustable
    ty = model.tank_tcg_m * model.tank_adjustable
    tz = model.tank_vcg_m * model.tank_adjustable
    rows = []
    def add(name, c, t, k, lo=-np.inf, hi=np.inf):
        rows.append((name, np.asarray(c), np.asarray(t), k, lo, hi))
    a, b = model.km_line
    fsm = float(model.tank_fsm_max_kgm.sum())
    reserve = bound_sign*lower["gm_m"]
    g = model.gm_min_m + reserve
    add("gm", cell_vcg+(g-b)*ones_c, tz+(g-b)*ones_t,
        model.const_moment_z_kgm+(g-b)*model.const_mass_kg+fsm-a, hi=0.)
    c0, cx, cd = model.trim_line
    if loading is None:
        trim = min(model.max_trim_m, band.max_abs_trim_m)
        trim_min, trim_max = -trim, trim
    else:
        trim_min = max(loading["min_trim_m"], -band.max_abs_trim_m)
        trim_max = min(loading["max_trim_m"], band.max_abs_trim_m)
    add("trim", cx*model.cell_lcg_m+cd*ones_c, cx*tx+cd*ones_t,
        c0+cx*model.const_moment_x_kgm+cd*model.const_mass_kg,
        trim_min+bound_sign*lower["trim_m"], trim_max-bound_sign*upper["trim_m"])
    heel = min(model.max_list_rad, band.max_abs_heel_rad)
    boundary_moments = policy.get("list_moment_kgm") if policy is not None else None
    heel_lower = heel if boundary_moments is not None else heel-lower["list_rad"]
    heel_upper = heel if boundary_moments is not None else heel-upper["list_rad"]
    # An exhausted angular margin makes the LP infeasible, even for zero TCG.
    if min(heel_lower, heel_upper) < 0:
        add("list_margin", 0*ones_c, 0*ones_t, 0., 1., np.inf)
    # Necessary boundary residuals are measured on the unchanged raw affine
    # restoring moment, including maximum FSM and upper cargo VCG. Its opposite
    # GM residual widens the GM row only; it is not a second LIST correction.
    list_gm_reserve = reserve if direction == "conservative" else 0.
    restore_c = (b-list_gm_reserve)*ones_c-cell_vcg
    restore_t = (b-list_gm_reserve)*ones_t-tz
    restore_k = a+(b-list_gm_reserve)*model.const_mass_kg-model.const_moment_z_kgm-fsm
    for sign, name, angle in ((1., "list_port", heel_upper), (-1., "list_starboard", heel_lower)):
        tangent = float(np.tan(max(0., angle)))
        # Optional independently qualified boundary evidence uses kg m directly:
        # q_exact - q_affine = tan(limit)*R - signed exact boundary capacity.
        # Never mix this reserve with the blanket interior angular envelope.
        moment_reserve = (bound_sign*margin_scale*boundary_moments[
            "upper_unsafe" if sign > 0 else "lower_unsafe"]
            if boundary_moments is not None else 0.)
        add(name, sign*model.cell_tcg_m-tangent*restore_c,
            sign*ty-tangent*restore_t,
            sign*model.const_moment_y_kgm-tangent*restore_k+moment_reserve, hi=0.)
    add("displacement", ones_c, ones_t, model.const_mass_kg,
        max(band.displacement_min_kg, loading["min_displacement_kg"]) if loading else band.displacement_min_kg,
        min(band.displacement_max_kg, loading["max_displacement_kg"]) if loading else band.displacement_max_kg)
    manifest_mass = getattr(model, "_cargo_mass_kg", None)
    if manifest_mass is not None:
        add("cargo_mass", ones_c, np.zeros_like(ones_t), 0., manifest_mass, manifest_mass)
    for key, (tank_lower, tank_upper) in sorted(getattr(model, "_tank_mass_bounds_kg", {}).items()):
        column = np.zeros_like(ones_t)
        column[model.tank_ids.index(key)] = 1.
        add(f"tank_mass@{key}", np.zeros_like(ones_c), column, 0., tank_lower, tank_upper)
    d0, dd = model.draught_line
    add("draught", dd*ones_c, dd*ones_t, d0+dd*model.const_mass_kg,
        band.draught_min_m+bound_sign*lower["draught_m"],
        band.draught_max_m-bound_sign*upper["draught_m"])
    if getattr(model, "hull_max_draught_m", None) is not None:
        for sign, name in ((1., "draught_aft"), (-1., "draught_forward")):
            # Aft = mean + trim/2; forward = mean - trim/2. Reversing
            # trim's sign exchanges its lower/upper error directions.
            end_lower = lower["draught_m"]+.5*(lower["trim_m"] if sign > 0 else upper["trim_m"])
            end_upper = upper["draught_m"]+.5*(upper["trim_m"] if sign > 0 else lower["trim_m"])
            end_lower = margin_scale*error(name+"_m", "lower_unsafe", fallback=end_lower/margin_scale)
            end_upper = margin_scale*error(name+"_m", "upper_unsafe", fallback=end_upper/margin_scale)
            add(name, dd*ones_c + sign*.5*(cx*model.cell_lcg_m+cd*ones_c),
                dd*ones_t + sign*.5*(cx*tx+cd*ones_t),
                d0+dd*model.const_mass_kg+sign*.5*(c0+cx*model.const_moment_x_kgm+cd*model.const_mass_kg),
                getattr(model, "hull_min_draught_fwd_m", 0.)+bound_sign*end_lower if sign < 0 else -np.inf,
                model.hull_max_draught_m-bound_sign*end_upper)
    for i, x in enumerate(model.station_x_m):
        add(f"sf@{x:g}", model.sf_per_kg_cell[i], model.sf_per_kg_tank[i], model.const_sf_n[i],
            model.sf_min_n[i]+bound_sign*lower["sf_n"][i], model.sf_max_n[i]-bound_sign*upper["sf_n"][i])
        add(f"bm@{x:g}", model.bm_per_kg_cell[i], model.bm_per_kg_tank[i], model.const_bm_nm[i],
            model.bm_min_nm[i]+bound_sign*lower["bm_nm"][i], model.bm_max_nm[i]-bound_sign*upper["bm_nm"][i])
    return LinearConstraintSet(tuple(r[0] for r in rows), np.stack([r[1] for r in rows]),
        np.stack([r[2] for r in rows]), np.array([r[3] for r in rows]),
        np.array([r[4] for r in rows]), np.array([r[5] for r in rows]))


def _highs(cost, matrix, rhs, bounds):
    # SciPy forwards the HiGHS-specific thread option, but emits a wrapper warning.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=OptimizeWarning, message="Unrecognized options detected.*")
        return linprog(cost, A_ub=matrix, b_ub=rhs, bounds=bounds, method="highs",
                       options={"threads": 1, "random_seed": 0,
                                "primal_feasibility_tolerance": 1e-9,
                                "dual_feasibility_tolerance": 1e-9})


def solve(model, ship, instance, margin_scale, objective, *, direction):
    """Minimise ballast kg, or maximise conservative GM margin in metres.

    The latter is a linear-fractional objective: Dinkelbach iterations solve
    ordinary LPs until (stability moment / displacement) stops changing.
    Infeasible diagnostics minimise the sum of nonnegative row violations in
    their original SI units (a mixed-unit diagnostic, never a physical score).
    """
    from .model import BallastLPResult
    if not isinstance(direction, str) or direction not in ("necessary", "conservative"):
        raise ValueError("direction must be necessary or conservative")
    if objective not in ("min_total_ballast", "max_gm_margin"):
        raise ValueError("unknown ballast LP objective")
    cargo, _ = mass_vectors(model, ship, instance, ignore_adjustable=True)
    cs = constraints(model, margin_scale, direction=direction)
    idx = np.flatnonzero(model.tank_adjustable)
    fixed = cs.a_cell @ cargo + cs.const
    matrix, rhs, names = [], [], []
    for i, name in enumerate(cs.names):
        if np.isfinite(cs.ub[i]):
            matrix.append(cs.a_tank[i, idx]); rhs.append(cs.ub[i]-fixed[i]); names.append(name)
        if np.isfinite(cs.lb[i]):
            matrix.append(-cs.a_tank[i, idx]); rhs.append(fixed[i]-cs.lb[i]); names.append(name)
    matrix, rhs = np.asarray(matrix), np.asarray(rhs)
    windows = getattr(model, "_tank_mass_bounds_kg", {})
    bounds = [tuple(windows.get(model.tank_ids[i], (0., float(model.tank_capacity_kg[i])))) for i in idx]
    # Scale inequalities, preserving original slacks for diagnostics below.
    scale = np.maximum(1., np.maximum(np.abs(rhs), np.max(np.abs(matrix), axis=1) if idx.size else 0.))
    aa, bb = matrix/scale[:, None], rhs/scale
    if not idx.size:
        violations = np.maximum(-rhs, 0.)
        if violations.max(initial=0.) > 1e-6:
            return BallastLPResult("infeasible", {}, None, float(violations.sum()),
                                   tuple(sorted(set(names[i] for i in np.flatnonzero(violations > 1e-6)))))
        result_x = np.zeros(0)
    else:
        answer = _highs(np.ones(idx.size), aa, bb, bounds)
        if answer.status == 2:
            count = len(rhs)
            relaxed = np.column_stack((aa, -np.eye(count)))
            repair = _highs(np.r_[np.zeros(idx.size), scale], relaxed, bb,
                            bounds+[(0., None)]*count)
            if not repair.success:
                return BallastLPResult("error", {}, None, None, ())
            slack = repair.x[idx.size:] * scale
            return BallastLPResult("infeasible", {}, None, float(slack.sum()),
                tuple(sorted(set(names[i] for i in np.flatnonzero(slack > 1e-6)))))
        if not answer.success:
            return BallastLPResult("error", {}, None, None, ())
        result_x = answer.x
    if objective == "max_gm_margin" and idx.size:
        a, b = model.km_line
        base_d = model.const_mass_kg+cargo.sum()
        base_n = a+b*base_d-model.const_moment_z_kgm-_constraint_cell_vcg(model)@cargo-model.tank_fsm_max_kgm.sum()
        ncoef = b-model.tank_vcg_m[idx]
        for _ in range(64):
            ratio = float((base_n+ncoef@result_x)/(base_d+result_x.sum()))
            answer = _highs(ratio-ncoef, aa, bb, bounds)
            if not answer.success:
                return BallastLPResult("error", {}, None, None, ())
            result_x = answer.x
            new_ratio = float((base_n+ncoef@result_x)/(base_d+result_x.sum()))
            if abs(new_ratio-ratio) <= 1e-10:
                break
        else:
            return BallastLPResult("error", {}, None, None, ())
        value = new_ratio-model.gm_min_m
    elif objective == "max_gm_margin":
        disp = model.const_mass_kg+cargo.sum()
        a, b = model.km_line
        value = ((a+b*disp-model.const_moment_z_kgm-_constraint_cell_vcg(model)@cargo
                  -model.tank_fsm_max_kgm.sum())/disp-model.gm_min_m)
    else:
        value = float(result_x.sum())
    residual = rhs-matrix@result_x
    binding = tuple(sorted(set(names[i] for i in np.flatnonzero(np.abs(residual) <= 1e-7*scale))))
    return BallastLPResult("optimal", {model.tank_ids[i]: float(max(0., x)) for i, x in zip(idx, result_x)},
                           float(value), None, binding)

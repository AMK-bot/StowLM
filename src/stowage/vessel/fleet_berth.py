"""D64 rev3 actual arrival fills, independent of booked/witness ballast.

APPROXIMATION: the forward-first arrival policy is a synthetic operating recipe,
not an approved ballast manual. Its mass/trim solve uses the actual hull and
finite-angle liquid geometry. This is the condition bunkered before cargo
operations (the fleet initializer declares 97 percent consumables). Declared
consumables are preserved: no refuelling occurs during cargo operations.
"""
from __future__ import annotations

import json
import math

import numpy as np
from scipy.optimize import root

from stowage.model import Instance, Vessel
from stowage.rules.catalogue import rule_freeze_v1_enabled

ARRIVAL_VERSION = "D64-rev3.1-actual-arrival-v2"
ARRIVAL_PREFIX = "stowage.arrival_condition:"


def anti_heeling_prefill(vessel: Vessel) -> dict[str, float]:
    """Half-fill the explicitly declared anti-heeling pair, at current capacity.

    Geometry-only diagnostic vessels have no tanks to initialize. A real hull
    with tanks must declare its pair; names such as HEEL are never inferred.
    """
    if not vessel.tanks:
        return {}
    tanks = {tank.id: tank for tank in vessel.tanks}
    pairs = [pair for pair in vessel.tank_pairs if pair.role == "anti_heeling"]
    if not pairs:
        raise ValueError("D64 arrival requires an explicit anti-heeling TankPair")
    masses = {}
    for pair in pairs:
        for tid in (pair.port_tank, pair.starboard_tank):
            tank = tanks[tid]
            if not tank.adjustable or tank.kind != "ballast":
                raise ValueError("D64 anti-heeling pair must contain adjustable ballast")
            masses[tid] = .5 * tank.capacity_m3 * tank.content_density_kg_m3
    return masses


def _forward_groups(vessel, excluded):
    """Forepeak, then forward deep groups, identified by physical geometry.

    Mirrored tanks share longitudinal bounds/full-load centres. Grouping them
    preserves equal fractional fills; all anti-heeling members are excluded.
    """
    groups = {}
    for tank in vessel.tanks:
        if (tank.id in excluded or not tank.adjustable or tank.kind != "ballast"
                or tank.table[-1].lcg_m <= .5 * vessel.hull.lpp_m):
            continue
        key = tuple(round(value, 7) for value in
                    (tank.x_aft_m, tank.x_fore_m, tank.table[-1].lcg_m,
                     tank.table[-1].vcg_m))
        groups.setdefault(key, []).append(tank)
    return [groups[key] for key in sorted(groups, key=lambda key: -key[2])]


def _trim_solver(vessel, density):
    """Build one exact hull/liquid context for all bounded bisection trials."""
    from stowage.vessel.fleet import load_ship_params
    from stowage.vessel.hydrostatics import HullGeometry
    from stowage.vessel.tanks import TankGeometry

    params = load_ship_params(vessel.slice_of or vessel.id)
    geometry = HullGeometry.from_params(params)
    hull = vessel.hull
    if not np.allclose([geometry.lpp_m, geometry.beam_m, geometry.depth_m],
                       [hull.lpp_m, hull.beam_m, hull.depth_m], rtol=1e-9):
        raise ValueError("D64 arrival hull disagrees with parameter geometry")
    raw = {tank["id"]: tank for tank in params.get("tanks", [])}
    solids = {}
    for tank in vessel.tanks:
        if tank.id not in raw:
            raise ValueError(f"D64 arrival missing parameter tank geometry: {tank.id}")
        solid = TankGeometry.from_params(raw[tank.id], ship_params=params)
        if (not math.isclose(solid.capacity_m3, tank.capacity_m3, rel_tol=1e-9)
                or not np.allclose([solid.x_min_m, solid.x_max_m],
                                   [tank.x_aft_m, tank.x_fore_m], rtol=1e-9)):
            raise ValueError("D64 arrival tank geometry disagrees with parameter geometry")
        solids[tank.id] = solid
    base = [(hull.lightship_lcg_m, hull.lightship_tcg_m, hull.lightship_vcg_m,
             hull.lightship_mass_kg)]
    base.extend((load.x_m, load.y_m, load.z_m, load.mass_kg)
                for load in vessel.fixed_loads)
    tanks = {tank.id: tank for tank in vessel.tanks}

    def solve(masses):
        liquids = []
        for tid, mass in masses.items():
            if not math.isfinite(mass) or mass < 0:
                raise ValueError("D64 arrival tank mass must be finite and nonnegative")
            if tid not in tanks:
                if mass:
                    raise ValueError(f"D64 arrival unknown tank {tid}")
                continue
            tank = tanks[tid]
            capacity = tank.capacity_m3 * tank.content_density_kg_m3
            if mass > capacity:
                raise ValueError(f"D64 arrival tank exceeds capacity: {tid}")
            if mass:
                liquids.append((solids[tid], mass / tank.content_density_kg_m3, mass))
        total = sum(point[3] for point in base) + sum(mass for _, _, mass in liquids)
        constant = np.sum([np.array(point[:3]) * point[3] for point in base], axis=0)

        def actual_cg(heel=0., trim=0.):
            return (constant + sum((np.asarray(solid.liquid(volume, heel, trim).centroid_m) * mass
                                   for solid, volume, mass in liquids), start=np.zeros(3))) / total

        def residual(cg):
            equilibrium = geometry.equilibrium(total, *cg, density)
            actual = actual_cg(equilibrium.heel_rad,
                               math.atan2(equilibrium.trim_m, hull.lpp_m))
            return actual - cg, equilibrium

        cg = actual_cg()
        for _ in range(6):
            error, equilibrium = residual(cg)
            if np.linalg.norm(error) < 1e-7:
                return equilibrium.trim_m
            cg += error
        solution = root(lambda value: residual(value)[0], cg,
                        options={"xtol": 1e-9, "maxfev": 80})
        error, equilibrium = residual(solution.x)
        if not np.all(np.isfinite(solution.x)) or np.linalg.norm(error) >= 1e-7:
            raise ValueError("D64 arrival liquid/hull equilibrium did not converge")
        return equilibrium.trim_m
    return solve


def prepare_arrival(instance: Instance) -> Instance:
    """Return ON actual onboard fills; OFF is the identical input object.

    Loaded slices retain their booked fixed ballast, except the declared HEEL
    prefill. Only a Stage A ship without decision or fixed background cargo is
    trimmed forward. Failure to find a capacity-bounded recipe raises ValueError;
    the caller must still run the independent in-port P1--P6 checker.
    """
    if not rule_freeze_v1_enabled():
        return instance
    vessel = instance.vessel
    if vessel.hull is None or not vessel.tanks:
        raise ValueError("D64 arrival preparation requires physical hull and tanks")
    from stowage.vessel.fleet_conditions import departure_tank_masses
    masses = departure_tank_masses(vessel)
    masses.update(instance.onboard.tank_masses_kg)
    pair_masses = anti_heeling_prefill(vessel)
    masses.update(pair_masses)
    full_empty = (instance.size_class == "full" and instance.stage == "A" and not instance.onboard.placements
                  and not any(load.mass_kg > 0 and load.kind != "non_cargo"
                              for load in vessel.fixed_loads))
    audit = dict(version=ARRIVAL_VERSION,
                 consumables_timing="bunkered before cargo operations",
                 consumables="preserved actual declaration; no refuelling during cargo operations",
                 anti_heeling_tank_masses_kg=pair_masses, full_empty_trim=full_empty,
                 water_density_kg_m3=instance.port.water_density_kg_m3)
    if full_empty:
        solve = _trim_solver(vessel, instance.port.water_density_kg_m3)
        target = .5 * vessel.hull.lpp_m * math.tan(math.radians(1.))
        initial_trim = trim = solve(masses)
        used = []
        if trim < -target:
            raise ValueError("D64 full-empty arrival is already beyond forward trim target")
        for group in _forward_groups(vessel, pair_masses):
            if abs(trim) <= target:
                break
            start = {tank.id: masses.get(tank.id, 0.) for tank in group}
            end = {tank.id: tank.capacity_m3 * tank.content_density_kg_m3 for tank in group}
            trial = dict(masses, **end)
            end_trim = solve(trial)
            if end_trim >= trim:
                continue
            used.append([tank.id for tank in group])
            if end_trim > target:
                masses, trim = trial, end_trim
                continue
            lo, hi = 0., 1.
            # One centimetre inside the half-limit also leaves room for the
            # checker's independently qualified geometry interpolation error.
            aim = target - .01
            for _ in range(28):
                fraction = .5 * (lo + hi)
                trial = dict(masses)
                trial.update({tid: start[tid] + fraction * (end[tid] - start[tid]) for tid in start})
                trial_trim = solve(trial)
                if trial_trim > aim:
                    lo = fraction
                else:
                    hi = fraction
                if hi - lo < 1e-7:
                    break
            masses.update({tid: start[tid] + hi * (end[tid] - start[tid]) for tid in start})
            trim = solve(masses)
        if abs(trim) > target:
            raise ValueError(f"D64 forward ballast cannot reach half in-port trim limit: {trim:g} m")
        audit.update(initial_trim_m=initial_trim, trim_m=trim,
                     half_trim_limit_m=target, forward_groups_used=used)
    audit["tank_masses_kg"] = masses
    parents = [parent for parent in instance.provenance.parents if not parent.startswith(ARRIVAL_PREFIX)]
    parents.append(ARRIVAL_PREFIX + json.dumps(audit, sort_keys=True, separators=(",", ":")))
    return instance.model_copy(update={
        "onboard": instance.onboard.model_copy(update={"tank_masses_kg": masses}),
        "provenance": instance.provenance.model_copy(update={"parents": sorted(parents)}),
    })

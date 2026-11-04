"""Mass moments and tank free surfaces for intact stability.

A centre of gravity is a mass-weighted mean of the centres of everything
aboard. Lightship already includes closed covers (model.vessel); removed covers
are subtracted and covers resting aboard are relocated. Cargo height is measured
from each stack's base using actual box heights, so high cubes raise boxes above.

IMO MSC.267(85), Part B 3.1.3–3.1.9 distinguishes the initial GM correction
(sum of density times upright liquid-surface inertia / displacement) from the
finite-angle liquid-transfer correction. Fixed ballast uses its actual fill.
ASSUMPTION: ballast is not filled, discharged or transferred at sea. The frozen
ShipState has no voyage filling range; a future voyage-transfer field must use
the most onerous transitory fill under Part B 3.1.5 instead.
Consumables use the maximum throughout their envisaged filling range, including
at least the most adverse transverse pair or centre tank per carried liquid type.
ASSUMPTION: the frozen model has no operational filling limits, so the whole
usable range is taken for every tank of each carried consumable type. For the
explicitly rectangular tanks, upright inertia is constant for all slack fills;
finite-angle transfer is greatest at half fill by central symmetry and concavity
of the box's liquid potential. Hull-section tanks instead use the whole-range
upright FSM supremum from their polygon slice moments and a separately bounded
finite-angle transfer envelope. All variable tanks may be slack simultaneously;
thus summing their maxima covers every transverse pair and every centre tank.
This simultaneous all-tank envelope is conservative relative to the Code's
minimum one transverse pair or centre tank. Production method .1 may omit individually small tanks under 3.1.12.
The minimum no-cargo service displacement is absent from the frozen model;
lightship mass is a conservative lower bound. Requiring rho*I0/lightship_mass
< 0.01 m is stricter than the 30-degree moment test. Never use current loaded
mass to justify the exemption.

The rectangular centroid helper integrates a box clipped by a plane analytically
(inclusion-exclusion of simplices); only the plane-level scalar root is numerical.
It uses the same solids and angle convention as vessel.tanks.TankGeometry.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import product
from math import cos, factorial, sin, tan
from typing import Any

import numpy as np
from scipy.optimize import brentq

from stowage.checker.slots import cells_of_slot
from stowage.vessel.tanks import TankGeometry

CONSUMABLE_KINDS = frozenset({'fuel_oil', 'diesel_oil', 'fresh_water', 'lube_oil'})


@dataclass(frozen=True)
class CorrectionTank:
    id: str
    density_kg_m3: float
    volume_m3: float
    fsm_kgm: float
    geometry: TankGeometry | None
    variable: bool = False


@dataclass(frozen=True)
class LoadingCondition:
    mass_kg: float
    lcg_m: float
    tcg_m: float
    kg_m: float
    fsc_m: float
    fsm_kgm: float
    tanks: tuple[CorrectionTank, ...] = ()
    actual_tanks: tuple[CorrectionTank, ...] = ()
    errors: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    omitted_tanks: tuple[str, ...] = ()
    precomputed_soundings: bool = False
    liquid_tables: Mapping[str, Any] | None = None
    liquid_usage: dict[str, int] = field(default_factory=dict, compare=False)
    upright_cache: dict = field(default_factory=dict, compare=False)
    cg_cache: dict = field(default_factory=dict, compare=False)
    liquid_cg_groups: tuple | None = None


def _interpolate(tank, volume):
    return tuple(float(np.interp(volume, [r.volume_m3 for r in tank.table],
                                 [getattr(r, key) for r in tank.table]))
                 for key in ('lcg_m', 'tcg_m', 'vcg_m'))


def loading_condition(instance, ship, *, geometry=None,
                      ship_params: Mapping[str, Any] | None = None,
                      free_surface_method: int = 1, precomputed_soundings: bool = False,
                      small_tank_exemption: bool | None = None) -> LoadingCondition:
    """Accumulate SI mass moments; invalid references are returned in ``errors``.

    ``geometry`` is accepted for a common checker context; no hull integration is
    needed here. The passed ship state is authoritative for every tank, including
    fixed consumables: a missing tank is empty, as ShipState specifies.
    """
    if free_surface_method not in (1, 2):
        raise ValueError('free-surface method must be 1 or 2')
    precomputed_soundings = precomputed_soundings or free_surface_method == 2
    if small_tank_exemption is None:
        small_tank_exemption = free_surface_method == 2
    vessel, hull = instance.vessel, instance.vessel.hull
    if hull is None:
        return LoadingCondition(0, 0, 0, 0, 0, 0, errors=('missing hull/lightship data',))
    weights = [(hull.lightship_mass_kg, hull.lightship_lcg_m,
                hull.lightship_tcg_m, hull.lightship_vcg_m)]
    weights.extend((mass, x, y, z) for x, y, z, mass in vessel.fixed_loads)
    errors, assumptions = [], []
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in vessel.cells}
    occupancy = {}
    for p in ship.placements:
        if p.container_id not in boxes:
            errors.append(f'unknown box {p.container_id}')
            continue
        for cid in cells_of_slot(p.slot):
            if cid not in cells:
                errors.append(f'unknown physical cell {cid}')
            else:
                occupancy[cid] = boxes[p.container_id]
    base = {}
    for stack in vessel.stacks:
        z = stack.base_z_m
        for cid in stack.cell_ids:
            base[cid] = z
            if cid in occupancy:
                z += occupancy[cid].height_m
    for p in ship.placements:
        if p.container_id not in boxes:
            continue
        box = boxes[p.container_id]
        ids = cells_of_slot(p.slot)
        if any(cid not in cells for cid in ids):
            continue
        supports = [base[cid] for cid in ids]
        if max(supports)-min(supports) > 1e-6:
            errors.append(f'box {box.id} has unequal support heights')
        weights.append((box.gross_mass_kg,
                        sum(cells[cid].lcg_m for cid in ids)/len(ids),
                        sum(cells[cid].tcg_m for cid in ids)/len(ids),
                        sum(supports)/len(ids)+box.height_m/2))
    covers = {c.id: c for c in vessel.hatch_covers}
    for cid, destination in sorted(ship.covers_off.items()):
        if cid not in covers or (destination != 'quay' and destination not in covers):
            errors.append(f'unknown hatch cover {cid} or destination {destination}')
            continue
        cover = covers[cid]
        weights.append((-cover.mass_kg, cover.lcg_m, cover.tcg_m, cover.vcg_m))
        if destination != 'quay':
            target = covers[destination]
            # APPROXIMATION: cover thickness and stacking contact geometry are
            # absent from the frozen schema; colocate centres on target cover.
            weights.append((cover.mass_kg, target.lcg_m, target.tcg_m, target.vcg_m))
            assumptions.append('APPROXIMATION: relocated cover centre uses destination cover centre; cover thickness unavailable')
    raw = {r['id']: r for r in (ship_params or {}).get('tanks', [])}
    tanks = {t.id: t for t in vessel.tanks}
    for tid in ship.tank_masses_kg:
        if tid not in tanks:
            errors.append(f'unknown tank {tid}')
    carried = {t.kind for t in vessel.tanks
               if ship.tank_masses_kg.get(t.id, 0) > 0 and t.kind in CONSUMABLE_KINDS}
    corrections, actual_tanks, omitted = [], [], []
    for tank in vessel.tanks:
        mass = ship.tank_masses_kg.get(tank.id, 0.0)
        density = tank.content_density_kg_m3
        if mass == 0 and tank.kind not in carried:
            continue  # No physical moment or envisaged free surface for dry types.
        volume = mass/density
        if not 0 <= volume <= tank.capacity_m3*(1+1e-12):
            errors.append(f'tank {tank.id} exceeds capacity')
            continue
        volume = min(volume, tank.capacity_m3)
        solid = TankGeometry.from_params(raw[tank.id], ship_params=ship_params) if tank.id in raw else None
        if solid:
            volume = min(volume, solid.capacity_m3)
            if precomputed_soundings:
                from stowage.checker.stability_sounding import sounding
                row = sounding(solid, volume)
            else:
                row = solid.sounding(volume)
            centre = (row.lcg_m, row.tcg_m, row.vcg_m)
        else:
            centre = _interpolate(tank, volume)
        weights.append((mass, *centre))
        if mass > 0:
            if solid is None:
                errors.append(f'tank {tank.id}: exact liquid geometry unavailable')
            else:
                actual_tanks.append(CorrectionTank(tank.id, density, volume, 0., solid))
        variable = tank.kind in carried
        if variable:
            if solid is None:
                errors.append(f'tank {tank.id}: exact filling-range geometry unavailable')
                continue
            if precomputed_soundings:
                from stowage.checker.stability_sounding import maximum_fsm
                maximum, correction_volume = maximum_fsm(solid)
            else:
                from stowage.checker.stability_tank_envelope import max_fsm
                maximum, correction_volume = max_fsm(solid)
            fsm = maximum*density
        elif 0 < volume < .98*tank.capacity_m3:
            if not solid:
                errors.append(f'tank {tank.id}: exact slack free-surface geometry unavailable')
            fsm = (row.fsm_m4 if solid else
                   float(np.interp(volume, [r.volume_m3 for r in tank.table],
                                   [r.fsm_m4 for r in tank.table])))*density
            correction_volume = volume
        else:
            continue
        if not solid:
            errors.append(f'tank {tank.id}: exact free-surface geometry unavailable')
        if small_tank_exemption and fsm/hull.lightship_mass_kg < .01:
            omitted.append(tank.id)
            continue
        corrections.append(CorrectionTank(tank.id, density, correction_volume, fsm, solid, variable))
    if carried:
        assumptions.append('ASSUMPTION: consumable filling limits are full usable range; all tanks of carried liquid types may be slack simultaneously')
    if any(tank.kind == 'ballast' for tank in vessel.tanks):
        # ASSUMPTION: ShipState specifies only this fixed fill, not a voyage
        # transfer range; Part B 3.1.5 transitory stages require that future field.
        assumptions.append('ASSUMPTION: ballast is not filled, discharged or transferred at sea; fixed state fills apply throughout the voyage')
    mass = sum(w[0] for w in weights)
    if mass <= 0:
        return LoadingCondition(mass, 0, 0, 0, 0, 0, errors=tuple(errors+['nonpositive displacement']))
    x, y, z = (sum(w[0]*w[i] for w in weights)/mass for i in (1, 2, 3))
    fsm = sum(t.fsm_kgm for t in corrections)
    return LoadingCondition(mass, x, y, z, fsm/mass, fsm, tuple(corrections), tuple(actual_tanks),
                            tuple(errors), tuple(sorted(set(assumptions))), tuple(omitted), precomputed_soundings)


def liquid_centroid(solid: TankGeometry, volume: float, heel_rad: float,
                    trim_rad: float = 0.0) -> np.ndarray:
    """Actual liquid centroid, with an analytic acceleration for explicit boxes.

    Hull-section bounds are only extents: neither capacity nor the empty/full
    centres follow from their bounding prism. Delegate every fill and angle to
    that solid's section integration (IS Code Part B 3.1.9 liquid transfer).
    Unknown shapes fail rather than silently replacing a solid with a box.
    """
    shape = getattr(solid, 'shape', None)
    if shape == 'hull_sections':
        from stowage.checker.stability_tank_transfer import liquid_centroid as exact_centroid
        return exact_centroid(solid,volume,heel_rad,trim_rad)
    if shape != 'rectangular_prism':
        raise ValueError(f'unsupported exact tank geometry shape {shape!r}')
    lengths = np.array([getattr(solid, f'{a}_max_m')-getattr(solid, f'{a}_min_m') for a in 'xyz'])
    lo = np.array([getattr(solid, f'{a}_min_m') for a in 'xyz'])
    capacity = float(np.prod(lengths))
    if volume <= 0:
        return np.array([solid.sounding(0).lcg_m, solid.sounding(0).tcg_m, solid.z_min_m])
    if volume >= capacity:
        return lo+lengths/2
    normal = np.array([tan(trim_rad)*cos(heel_rad), -sin(heel_rad), cos(heel_rad)])
    q = abs(normal)*lengths
    active = np.flatnonzero(q > 0)
    if min(q[active])/max(q[active]) < 1e-5:
        return np.array(solid.liquid(volume, heel_rad, trim_rad).centroid_m)
    qa = q[active]
    n = len(active)
    bits = np.array(list(product((0., 1.), repeat=n)))
    shifts = bits @ qa
    signs = (-1.)**bits.sum(axis=1)
    denom = float(np.prod(qa))*factorial(n)
    # Integrate the smaller complement to avoid cancellation near pressed full.
    fraction = min(volume/capacity, 1-volume/capacity)
    def cdf(level):
        h = np.maximum(level-shifts, 0.)
        return float(signs @ (h**n))/denom
    midpoint = float(qa.sum()/2)
    # Central symmetry puts half the volume below the midpoint. Independent
    # capacity/mass arithmetic and inclusion-exclusion may round in opposite
    # directions near half fill; clamp to that exact symmetry limit when the
    # numerical CDF cannot bracket the requested fraction there.
    level = (midpoint if cdf(midpoint) <= fraction else
             brentq(lambda level: cdf(level)-fraction, 0., midpoint, xtol=1e-13))
    h = np.maximum(level-shifts, 0.)
    integrals = np.array([float(signs @ (bits[:, i]*h**n + h**(n+1)/((n+1)*qa[i])))/denom
                          for i in range(n)])
    means = np.full(3, .5)
    if volume/capacity <= .5:
        means[active] = integrals/fraction
    else:
        means[active] = (.5-fraction+integrals)/(1-fraction)
    means = np.where(normal < 0, 1-means, means)
    return lo+lengths*means


def curve_correction(condition: LoadingCondition, angles_rad: Sequence[float],
                     trim_rad: float | Sequence[float] = 0.0, *,
                     method: int = 1) -> np.ndarray:
    """GZ lever to subtract, using real liquid shifts (IS B3.1.9.1).

    Each angle may use its own hull free-trim angle. Upright reference liquid
    centres remain those used in the loading moments; trim transfer therefore
    contributes when projecting into the heel plane. The variable consumable
    envelope replaces mass/volume only in the transfer correction, not in ship
    displacement or its actual centre of gravity.
    """
    angles = np.asarray(angles_rad, dtype=float)
    if method == 2:
        # MSC.267(85) B3.1.9.2: inertia at zero heel, "modified at each
        # angle of heel calculated". The Code supplies no modification equation.
        # Selected booklet interpretation: constant virtual KG rise FSC,
        # hence delta-GZ = FSC*sin(phi). Code-permitted method .2, not an
        # approximation to the distinct actual-transfer method .1.
        return condition.fsc_m*np.sin(angles)
    if method != 1:
        raise ValueError('free-surface method must be 1 or 2')
    trims = np.broadcast_to(np.asarray(trim_rad, dtype=float), angles.shape)
    result = np.zeros_like(angles)
    shaped_variables = sum(t.variable and getattr(t.geometry, 'shape', None) == 'hull_sections'
                           for t in condition.tanks)
    if shaped_variables and condition.liquid_tables:
        from stowage.checker.stability_liquid_batch import transfer_envelopes
        envelopes = transfer_envelopes(condition, angles, trims, shaped_variables)
    else:
        envelopes = {}
    for tank in condition.tanks:
        if tank.geometry is None:
            raise ValueError(f'tank {tank.id}: exact free-surface geometry unavailable')
        solid = tank.geometry
        if tank.variable and getattr(solid, 'shape', None) == 'hull_sections':
            from stowage.checker.stability_tank_transfer import max_transfer
            # APPROXIMATION: sum of conservative numerical envelope excesses
            # is at most 0.1 mm GZ per angle (and <=0.000105 m rad to 60deg).
            # This is not an allowance on a hard stability criterion. The
            # geometry oracle/root and declared section quadrature retain their
            # own documented numerical error. A failed bound is a hard error.
            tolerance = 1e-4*condition.mass_kg/(tank.density_kg_m3*shaped_variables)
            for i, (angle, trim) in enumerate(zip(angles, trims, strict=True)):
                table = (condition.liquid_tables or {}).get(tank.id)
                if (tank.id, i) in envelopes:
                    moment = envelopes[tank.id, i]
                    condition.liquid_usage['table'] = condition.liquid_usage.get('table',0)+1
                elif table is not None:
                    try:
                        moment = table.max_transfer(float(angle),float(trim),tolerance_m4=tolerance)
                        condition.liquid_usage['table'] = condition.liquid_usage.get('table',0)+1
                    except ValueError:
                        condition.liquid_usage['direct'] = condition.liquid_usage.get('direct',0)+1
                        moment = max_transfer(solid,float(angle),float(trim),tolerance_m4=tolerance)
                else:
                    condition.liquid_usage['direct'] = condition.liquid_usage.get('direct',0)+1
                    moment = max_transfer(solid, float(angle), float(trim), tolerance_m4=tolerance)
                result[i] += (-1. if angle < 0 else 1.)*tank.density_kg_m3/condition.mass_kg*moment
            continue
        upright = _upright(condition, tank)
        for i, (angle, trim) in enumerate(zip(angles, trims, strict=True)):
            centre = _centroid(condition, tank, float(angle), float(trim))
            reference = np.array([upright.lcg_m, upright.tcg_m, upright.vcg_m])
            normal = np.array([tan(trim)*cos(angle), -sin(angle), cos(angle)])
            normal /= np.linalg.norm(normal)
            longitudinal = np.array([1., 0., -tan(trim)])
            longitudinal /= np.linalg.norm(longitudinal)
            transverse = np.cross(normal, longitudinal)
            # The waterplane transverse direction has an x component when heel
            # and trim coexist: retain the complete three-dimensional shift.
            result[i] += tank.density_kg_m3*tank.volume_m3/condition.mass_kg*float((centre-reference) @ transverse)
    return result


def liquid_cg(condition: LoadingCondition, heel_rad: float,
              trim_rad: float = 0.0) -> tuple[float, float, float]:
    """Actual total mass centre for the equilibrium iteration at heel and trim.

    The returned KG is the physical centre, without the regulatory virtual-rise
    correction; adding FSC to it would count liquid motion twice.
    """
    key = (float(heel_rad),float(trim_rad))
    if key in condition.cg_cache:
        return condition.cg_cache[key]
    if condition.liquid_cg_groups is not None:
        from stowage.checker.stability_liquid_batch import evaluate
        try:
            result=evaluate(condition.liquid_cg_groups,
                (condition.lcg_m,condition.tcg_m,condition.kg_m),heel_rad,trim_rad)
            condition.liquid_usage['table']=condition.liquid_usage.get('table',0)+len(condition.actual_tanks)
            if len(condition.cg_cache)>=512:
                condition.cg_cache.clear()
            condition.cg_cache[key]=result
            return result
        except ValueError:
            pass  # An uncovered group uses the individual/direct fallback below.
    if not condition.liquid_tables and condition.actual_tanks:
        from stowage.checker.stability_direct_liquid_batch import evaluate as direct_batch
        try:
            result = direct_batch(condition,heel_rad,trim_rad)
            if len(condition.cg_cache)>=512:
                condition.cg_cache.clear()
            condition.cg_cache[key]=result
            return result
        except ValueError:
            pass  # Unsupported/adaptive solids retain the individual exact path.
    centre = np.array([condition.lcg_m, condition.tcg_m, condition.kg_m])
    for tank in condition.actual_tanks:
        solid = tank.geometry
        assert solid is not None
        row = _upright(condition, tank)
        reference = np.array([row.lcg_m, row.tcg_m, row.vcg_m])
        moved = _centroid(condition, tank, heel_rad, trim_rad)
        centre += tank.density_kg_m3*tank.volume_m3/condition.mass_kg*(moved-reference)
    result = tuple(float(value) for value in centre)
    if len(condition.cg_cache)>=512:
        condition.cg_cache.clear()
    condition.cg_cache[key]=result
    return result


def _upright(condition, tank):
    key=(id(tank.geometry),tank.volume_m3)
    if key not in condition.upright_cache:
        if condition.precomputed_soundings:
            from stowage.checker.stability_sounding import sounding
            row=sounding(tank.geometry,tank.volume_m3)
        else:
            row=tank.geometry.sounding(tank.volume_m3)
        condition.upright_cache[key]=row
    return condition.upright_cache[key]


def _centroid(condition, tank, heel, trim):
    table = (condition.liquid_tables or {}).get(tank.id)
    if table is not None:
        try:
            result = table.centroid(tank.volume_m3,heel,trim)
            condition.liquid_usage['table'] = condition.liquid_usage.get('table',0)+1
            return np.asarray(result)
        except ValueError:
            pass  # Outside a certified grid: actual geometry, never extrapolate.
    condition.liquid_usage['direct'] = condition.liquid_usage.get('direct',0)+1
    return liquid_centroid(tank.geometry,tank.volume_m3,heel,trim)

"""Batch actual-fill mass moments and independent filling-range envelopes.

IS Code B3.1.9.1 uses the actual fluid-transfer moment. At a fixed loading
condition each tank's fill is fixed; both mass-weighted addition and the
certified tensor interpolation are linear. We can therefore interpolate fill
once, sum compatible grids, then interpolate heel/trim once per grid group.
This is algebraic regrouping of the individual table queries, with no new
physics approximation. Bounds and prismatic-table restrictions are unchanged.
Variable-fill corrections share spline preparation while retaining a separate
fill maximum for every tank and angle; those maxima are never combined first.
"""
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import CubicSpline

from stowage.checker.stability_liquid_tables import _stencil


@dataclass
class CGGroup:
    heels: np.ndarray
    trims: np.ndarray
    shift_grid: np.ndarray
    prismatic: bool = False


def prepare(condition):
    """Return grouped CG-shift grids, or None if any actual tank lacks a table.

    The caller must retain its ordinary actual-geometry path when preparation
    is unavailable, or when a later angular query leaves these certified grids.
    """
    from stowage.checker.stability_loading import _upright

    tables = condition.liquid_tables or {}
    if any(tank.id not in tables for tank in condition.actual_tanks):
        return None
    groups = {}
    for tank in condition.actual_tanks:
        table = tables[tank.id]
        volume = float(tank.volume_m3)
        if not np.isfinite(volume) or not 0 <= volume <= table.capacity:
            return None
        heels = np.asarray(table.heels,dtype=float)
        trims = np.asarray(table.trims,dtype=float)
        key = (heels.shape,heels.tobytes(),trims.shape,trims.tobytes())
        if key not in groups:
            groups[key] = CGGroup(heels,trims,np.zeros((len(heels),len(trims),3)))
        group = groups[key]
        group.prismatic |= bool(table.prismatic)
        # Even the endpoint queries in LiquidTable validate angle bounds and
        # prismatic trim, so keep their groups when the shift is identically 0.
        if volume == 0:
            continue
        row = _upright(condition,tank)
        reference = np.array([row.lcg_m,row.tcg_m,row.vcg_m])
        if volume == table.capacity:
            moment = np.broadcast_to(table.moments[0,0,-1],group.shift_grid.shape)
        else:
            fi,fw = _stencil(table.fills,volume/table.capacity)
            moment = np.einsum('k,ijkd->ijd',fw,table.moments[:,:,fi,:],optimize=False)
        group.shift_grid += (tank.density_kg_m3/condition.mass_kg)*(
            moment-volume*reference)
    return tuple(groups.values())


def evaluate(groups, base_xyz, heel_rad, trim_rad=0.):
    """Evaluate prepared actual mass CG; ValueError requests ordinary fallback."""
    heel,trim = float(heel_rad),float(trim_rad)
    centre = np.array(base_xyz,dtype=float,copy=True)
    for group in groups:
        if group.prismatic and abs(trim) > 1e-14:
            raise ValueError('prismatic adaptive longitudinal quadrature requires direct method')
        hi,hw = _stencil(group.heels,heel)
        ti,tw = _stencil(group.trims,trim)
        centre += np.einsum('i,j,ijd->d',hw,tw,
            group.shift_grid[hi[:,None],ti[None,:]],optimize=False)
    return tuple(float(value) for value in centre)


def transfer_envelopes(condition, angles, trims, shaped_variables):
    """Batch spline preparation, retaining every tank's independent maximum.

    IS Code B3.1.9.1, PDF p.40: "actual moment of fluid transfer";
    B3.1.3.2, PDF p.39 requires the "maximum value attainable" between
    each tank's filling limits. CubicSpline solves the same interpolation system
    for multiple right-hand sides here. Each tank/angle still gets its own
    stationary points and maximum; maximizing the sum would change physics.
    Missing/custom tables and uncovered queries are omitted for the caller's
    unchanged per-tank table/direct path. Qualification and angular restrictions
    remain those of LiquidTable._at_angles; no extrapolation is introduced.
    """
    from stowage.checker.stability_liquid_tables import LiquidTable

    groups, directions = {}, {}
    for tank in condition.tanks:
        if not tank.variable or getattr(tank.geometry, 'shape', None) != 'hull_sections':
            continue
        table = (condition.liquid_tables or {}).get(tank.id)
        if type(table) is not LiquidTable:
            continue  # A custom provider keeps its own max_transfer semantics.
        tolerance = 1e-4*condition.mass_kg/(tank.density_kg_m3*shaped_variables)
        if not np.isfinite(tolerance) or tolerance <= 0:
            continue
        key = (table.fills.shape, table.fills.tobytes())
        for index, (heel, trim) in enumerate(zip(angles, trims, strict=True)):
            try:
                tilted = table._at_angles(float(heel), float(trim))
                upright = table._at_angles(0., 0.)
            except ValueError:
                continue
            if index not in directions:
                normal = np.array([np.tan(trim)*np.cos(heel), -np.sin(heel), np.cos(heel)])
                normal /= np.linalg.norm(normal)
                along = np.array([1., 0., -np.tan(trim)])
                along /= np.linalg.norm(along)
                directions[index] = np.cross(normal, along)*(-1 if heel < 0 else 1)
            values = (tilted-upright) @ directions[index]
            if key not in groups:
                groups[key] = (table.fills, [])
            groups[key][1].append((tank.id, index, values))

    result = {}
    for fills, queries in groups.values():
        if len(queries) < 2:
            continue  # One query cannot amortize batched spline preparation.
        try:
            values = np.stack([query[2] for query in queries])
            spline = CubicSpline(fills, values, axis=1)
            roots = spline.derivative().roots(extrapolate=False)
        except ValueError:
            continue  # A rejected batch retains each query's normal fallback.
        for column, ((tank_id, index, _), stationary) in enumerate(zip(queries, roots, strict=True)):
            stationary = stationary[np.isfinite(stationary)]
            intervals = np.clip(np.searchsorted(fills, stationary, side='right')-1, 0, len(fills)-2)
            delta = stationary-fills[intervals]
            coefficients = spline.c[:, intervals, column]
            extrema = ((coefficients[0]*delta+coefficients[1])*delta+coefficients[2])*delta+coefficients[3]
            # Knot values and every real in-domain derivative root are the
            # same complete candidate set used by LiquidTable.max_transfer.
            maximum = max(0., float(np.max(values[column])))
            if len(extrema):
                maximum = max(maximum, float(np.max(extrema)))
            result[tank_id, index] = maximum
    return result

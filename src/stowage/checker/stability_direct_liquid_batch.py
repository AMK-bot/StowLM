"""Batch the direct liquid-centroid roots of all actual hull-section tanks.

Every root clips the original polygons and retains their Gauss longitudinal
weights. Crossing-edge widths give the exact derivative of volume with respect
to the liquid plane level. A safeguarded Newton step remains inside a bisected
bracket; the geometry provider's volume residual tolerance is checked explicitly.
This is direct geometric integration, with no table or fill/angle interpolation.
Prismatic tanks requiring adaptive longitudinal quadrature use the existing
individual direct reference instead. Source: IS Code B 3.1.9.1 actual transfer.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
from math import cos, sin, tan

import numpy as np

from stowage.checker.stability_budget import check_budget

_CACHE = OrderedDict()
_CACHE_SIZE = 8
_CENTROID_CACHE = OrderedDict()
_CENTROID_GROUPS = 8
_CENTROIDS_PER_GROUP = 512


@dataclass(frozen=True)
class Batch:
    p: np.ndarray
    q: np.ndarray
    x: np.ndarray
    weights: np.ndarray
    tank_indices: np.ndarray
    volumes: np.ndarray
    capacities: np.ndarray
    low_corners: np.ndarray
    coefficients: np.ndarray
    references: np.ndarray
    base: np.ndarray
    seeds: dict = field(default_factory=dict, compare=False)
    root_identity: tuple | None = None
    geometry_refs: tuple = field(default=(), compare=False)


def prepare(condition):
    """Prepare reusable concatenated polygon records for one loading condition."""
    from stowage.checker.stability_loading import _upright
    from stowage.vessel.hull_tanks import SectionTankGeometry
    key = id(condition)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key][1]
    partial = []
    for tank in condition.actual_tanks:
        solid = tank.geometry
        if solid is None:
            raise ValueError('direct liquid batch requires tank geometry')
        if tank.volume_m3 <= 0 or tank.volume_m3 >= solid.capacity_m3:
            continue  # Empty/full liquids retain their upright moments exactly.
        if getattr(solid, 'shape', None) != 'hull_sections' or solid._prismatic:
            raise ValueError('direct liquid batch requires non-prismatic hull sections')
        partial.append(tank)
    records, references, coefficients, corners = [], [], [], []
    for i, tank in enumerate(partial):
        solid = tank.geometry
        p, q, xs, weights, _ = solid._batch
        for x, w, polygon in zip(xs, weights, p, strict=True):
            # Source section polygons retain contour subdivision and padding.
            # Removing exact duplicate/collinear vertices changes neither their
            # boundary nor their shoelace integrals; curved offsets stay intact.
            polygon = polygon[np.any(polygon != np.roll(polygon, 1, axis=0), axis=1)]
            before = polygon-np.roll(polygon, 1, axis=0)
            after = np.roll(polygon, -1, axis=0)-polygon
            cross = before[:, 0]*after[:, 1]-before[:, 1]*after[:, 0]
            polygon = polygon[cross != 0.]
            records.append((i, x, w, polygon))
        row = _upright(condition, tank)
        references.append([row.lcg_m, row.tcg_m, row.vcg_m])
        coefficients.append(tank.density_kg_m3*tank.volume_m3/condition.mass_kg)
        corners.append([[x, y, z] for x in (solid.x_min_m, solid.x_max_m)
                        for y in (solid.y_min_m, solid.y_max_m)
                        for z in (solid.z_min_m, solid.z_max_m)])
    size = max((len(p) for _, _, _, p in records), default=1)
    polygons = np.array([np.concatenate((p, np.repeat(p[-1:], size-len(p), axis=0)))
                         for _, _, _, p in records]).reshape(-1, size, 2)
    batch = Batch(polygons, np.roll(polygons, -1, axis=1),
        np.array([x for _, x, _, _ in records]), np.array([w for _, _, w, _ in records]),
        np.array([i for i, _, _, _ in records], dtype=int),
        np.array([t.volume_m3 for t in partial]),
        np.array([t.geometry.capacity_m3 for t in partial]),
        np.asarray(corners).reshape(-1, 8, 3), np.array(coefficients),
        np.asarray(references).reshape(-1, 3),
        np.array([condition.lcg_m, condition.tcg_m, condition.kg_m]),
        # SectionTankGeometry is frozen and owns read-only physical arrays.
        # Retaining references below prevents object-id reuse while a root lives.
        root_identity=(tuple((id(t.geometry), float(t.volume_m3)) for t in partial)
                       if all(isinstance(t.geometry, SectionTankGeometry) for t in partial) else None),
        geometry_refs=tuple(t.geometry for t in partial))
    _CACHE[key] = (condition, batch)
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return batch


def _integrate(batch, levels, normal):
    """All tank volumes/moments and their exact plane-level derivatives."""
    p, q, ids = batch.p, batch.q, batch.tank_indices
    h = levels[ids]-normal[0]*batch.x
    py, pz = p[..., 0], p[..., 1]
    dy, dz = q[..., 0]-py, q[..., 1]-pz
    d = py*normal[1]+pz*normal[2]-h[:, None]
    dq = np.roll(d, -1, axis=1)
    inside, next_inside = d <= 0, dq <= 0
    crossing = inside != next_inside
    fraction = np.divide(d, d-dq, out=np.zeros_like(d), where=crossing)
    a, b = np.where(inside, 0., fraction), np.where(next_inside, 1., fraction)
    origin_scale = h/(normal[1]**2+normal[2]**2)
    oy, oz = origin_scale*normal[1], origin_scale*normal[2]
    # Shift the shoelace origin onto the clipping plane. Newly closing
    # waterline edges then have exactly zero cross product, so integrating
    # original clipped edges requires no successor gathering or compaction.
    cross = (b-a)*(py*q[..., 1]-q[..., 0]*pz-oy[:, None]*dz+oz[:, None]*dy)
    area = cross.sum(axis=1)/2
    endpoint_sum = a+b
    ymoment = (((2*(py-oy[:, None])+endpoint_sum*dy)*cross).sum(axis=1)/6+oy*area)
    zmoment = (((2*(pz-oz[:, None])+endpoint_sum*dz)*cross).sum(axis=1)/6+oz*area)
    n = len(batch.volumes)
    moments = np.column_stack([np.bincount(ids, weights=value*batch.weights, minlength=n)
              for value in (area, area*batch.x, ymoment, zmoment)])
    transverse_norm = np.hypot(normal[1], normal[2])
    across = (normal[2]*(py+fraction*dy)-normal[1]*(pz+fraction*dz))/transverse_norm
    width = np.where(crossing, np.sign(dq-d)*across, 0.).sum(axis=1)
    derivative = np.bincount(ids, weights=width*batch.weights/transverse_norm, minlength=n)
    return moments, derivative


def centroids(batch, heel, trim):
    """Solve all partial-tank volume roots simultaneously without approximations."""
    check_budget('direct liquid batch entry')
    if not np.isfinite([heel, trim]).all() or max(abs(heel), abs(trim)) >= np.pi/2:
        raise ValueError('tank angles must be finite and below 90 degrees')
    if not len(batch.volumes):
        return np.empty((0, 3))
    query = (float(heel), float(trim))
    roots = None
    if batch.root_identity is not None:
        if batch.root_identity not in _CENTROID_CACHE:
            _CENTROID_CACHE[batch.root_identity] = (batch.geometry_refs, OrderedDict())
            while len(_CENTROID_CACHE) > _CENTROID_GROUPS:
                _CENTROID_CACHE.popitem(last=False)
        _CENTROID_CACHE.move_to_end(batch.root_identity)
        roots = _CENTROID_CACHE[batch.root_identity][1]
        if query in roots:
            roots.move_to_end(query)
            centre, levels = roots[query]
            if not np.isfinite(centre).all() or not np.isfinite(levels).all():
                raise ArithmeticError('nonfinite direct liquid batch cached result')
            if len(batch.seeds) >= 128 and heel not in batch.seeds:
                batch.seeds.pop(next(iter(batch.seeds)))
            batch.seeds[heel] = levels.copy()
            check_budget('direct liquid batch exit')
            return centre.copy()
    normal = np.array([tan(trim)*cos(heel), -sin(heel), cos(heel)])
    normal /= np.linalg.norm(normal)
    # D114: explicit fixed-order products avoid the defective batched BLAS path.
    projections = (batch.low_corners[...,0]*normal[0]
                   +batch.low_corners[...,1]*normal[1]
                   +batch.low_corners[...,2]*normal[2])
    low, high = projections.min(axis=1), projections.max(axis=1)
    levels = low+(high-low)*batch.volumes/batch.capacities
    if batch.seeds:
        closest = min(batch.seeds, key=lambda phi: abs(phi-heel))
        if abs(closest-heel) <= .05:
            levels = np.clip(batch.seeds[closest], low, high)
    from stowage.vessel.hull_tanks import _TINY_LIQUID_VOLUME_M3
    from stowage.checker.stability_tank_transfer import _direct_liquid
    tiny = batch.volumes <= _TINY_LIQUID_VOLUME_M3
    tiny_centres = np.empty((len(batch.volumes), 3))
    for index in np.flatnonzero(tiny):
        # The scalar kernel retains support-relative levels and translated
        # moments at sub-ULP depths. Keep ordinary members on the unchanged
        # batch arithmetic and preserve their original summation order.
        centre, level = _direct_liquid(batch.geometry_refs[index],
            float(batch.volumes[index]), heel, trim, polynomial=False)
        if not np.isfinite(centre).all() or not np.isfinite(level):
            raise ArithmeticError('nonfinite direct liquid batch tiny result')
        tiny_centres[index] = centre
        levels[index] = level
    tolerance = np.maximum(1e-10, 1e-11*batch.volumes)
    for _ in range(80):
        check_budget('direct liquid batch volume refinement')
        moments, derivative = _integrate(batch, levels, normal)
        residual = moments[:, 0]-batch.volumes
        done = (np.abs(residual) <= tolerance) | tiny
        if done.all():
            if len(batch.seeds) >= 128 and heel not in batch.seeds:
                batch.seeds.pop(next(iter(batch.seeds)))
            batch.seeds[heel] = levels.copy()
            result = np.empty_like(moments[:, 1:])
            np.divide(moments[:, 1:], moments[:, :1], out=result,
                      where=~tiny[:, None])
            result[tiny] = tiny_centres[tiny]
            if not np.isfinite(result).all() or not np.isfinite(levels).all():
                raise ArithmeticError('nonfinite direct liquid batch result')
            if roots is not None:
                # IS Code B3.1.9.1, PDF p.40: "actual moment of fluid
                # transfer for each angle of heel calculated". Only an exactly
                # repeated geometry/fill/heel/trim may reuse this solved physical
                # centroid. Ship mass, base CG and density remain fresh inputs
                # to evaluate(); no condition/report or rounded state is cached.
                roots[query] = (result.copy(), levels.copy())
                while len(roots) > _CENTROIDS_PER_GROUP:
                    roots.popitem(last=False)
            check_budget('direct liquid batch exit')
            return result
        low = np.where((residual < 0) & ~done, levels, low)
        high = np.where((residual >= 0) & ~done, levels, high)
        step = np.divide(residual, derivative, out=np.zeros_like(residual), where=derivative > 0)
        proposed = levels-step
        safe = (derivative > 0) & (proposed > low) & (proposed < high) & (proposed != levels)
        levels = np.where(done, levels, np.where(safe, proposed, (low+high)/2))
    raise ValueError('direct liquid batch failed geometry volume tolerance')


def evaluate(condition, heel, trim):
    """Return actual ship mass centre; unsupported solids request individual fallback."""
    batch = prepare(condition)
    moved = centroids(batch, float(heel), float(trim))
    centre = batch.base+np.sum(batch.coefficients[:, None]*(moved-batch.references), axis=0)
    condition.liquid_usage['direct'] = condition.liquid_usage.get('direct', 0)+len(batch.volumes)
    return tuple(float(v) for v in centre)

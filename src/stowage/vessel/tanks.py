"""Declared rectangular and hull-following tank liquid geometry (plan 2.1).

``build_tanks(params)`` builds strict model records; ``TankGeometry.from_params``
keeps the solid that the model deliberately does not store. ``sounding(volume)``
uses exact section polygon moments and the declared longitudinal quadrature,
including the discontinuous free-surface inertia at
empty/full: never linearly interpolate FSM between an endpoint and a slack row.
``liquid(volume, heel_rad=0, trim_rad=0)`` clips the solid against a horizontal
liquid plane and solves its level (rectangular bisection; section solids use
safeguarded Brent roots). Positive heel lowers port;
positive trim is aft down. The upward plane normal in ship axes is
normalise((tan(trim)*cos(heel), -sin(heel), cos(heel))).
Here trim_rad=atan((draught_aft-draught_fore)/L), matching HullGeometry.

The returned centroid is in ship axes; free_surface_inertia_m4 is the central
second area moment about the longitudinal direction (1,0,-tan(trim)) in the liquid
plane, matching HullGeometry waterplane axes. Multiplying by density gives the small-rotation free-surface mass moment.
For finite-angle stability use the liquid centroid itself, rather than adding
an upright correction again (IMO 2008 IS Code Part B 3.1 free-surface treatment).
Rectangular polyhedron tetrahedra and polygon moments are integrated analytically;
their liquid-level root has numerical error. Hull-following sections also retain
the explicitly labelled longitudinal Gauss/loft approximation. No voxel geometry
is used.

APPROXIMATION: each declared tank is unbaffled and hydraulically connected; disjoint
section pieces share the liquid head. Separate compartments require separate Tank
IDs. Rectangular solids use exact polyhedron geometry. Hull-following solids use
actual section polygons and recorded Gauss weights (see hull_tanks.py). No internal
structure or permeability is inferred. Bounding extents never replace a section solid.
``TankGeometry.from_params(record, ship_params=...)`` optionally builds bounds-only
hull intersections through hull_tank_sections.py; embedded arrays need no context.
``shape`` and ``is_rectangular`` explicitly distinguish these representations.
Hull-following geometry exposes ``section_liquid_areas`` for weight quadrature;
recorded nodes can have gaps/missing Gauss mates and are not continuous blocks.
Empty-tank centroid is the bottom centre by convention (zero mass).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import product
from math import cos, isclose, isfinite, sin, tan
from typing import Any, ClassVar

import numpy as np
from scipy.spatial import ConvexHull

from stowage.model import Tank, TankSoundingPoint


def _number(value: Any) -> float:
    return float(value['value'] if isinstance(value, Mapping) else value)


@dataclass(frozen=True)
class LiquidCondition:
    volume_m3: float
    centroid_m: tuple[float, float, float]
    free_surface_area_m2: float
    free_surface_inertia_m4: float
    plane_level_m: float

    @property
    def fsm_m4(self) -> float:
        return self.free_surface_inertia_m4


@dataclass(frozen=True)
class TankGeometry:
    shape: ClassVar[str] = 'rectangular_prism'
    is_rectangular: ClassVar[bool] = True
    x_min_m: float
    x_max_m: float
    y_min_m: float
    y_max_m: float
    z_min_m: float
    z_max_m: float

    def __post_init__(self) -> None:
        for axis in 'xyz':
            lo, hi = getattr(self, f'{axis}_min_m'), getattr(self, f'{axis}_max_m')
            if not isfinite(lo) or not isfinite(hi) or hi <= lo:
                raise ValueError('tank bounds must be finite with positive extents')

    @classmethod
    def from_params(cls, tank_params: Mapping[str, Any], *, ship_params=None):
        g = tank_params.get('geometry', tank_params)
        if g.get('shape') in ('hull_sections','hull_intersection'):
            if not all(key in g for key in ('x_m','integration_weights_m','sections_yz_m')):
                if ship_params is None:
                    raise ValueError('bounds-only hull tank requires ship_params context')
                from stowage.vessel.hull_tank_sections import build_hull_tank_geometry
                g=build_hull_tank_geometry(ship_params,g,nx=int(_number(g.get('nx',48))),nz=int(_number(g.get('nz',64))),
                                           inner_skin_m=_number(g['inner_skin_m']) if 'inner_skin_m' in g else None)
            from stowage.vessel.hull_tanks import section_geometry_from_params
            return section_geometry_from_params(g)
        if g.get('shape', 'rectangular_prism') != 'rectangular_prism':
            raise ValueError('unsupported tank geometry; requires rectangular_prism or hull_sections')
        return cls(**{f'{a}_{s}_m': _number(g[f'{a}_{s}_m']) for a in 'xyz' for s in ('min', 'max')})

    @property
    def capacity_m3(self) -> float:
        return (self.x_max_m-self.x_min_m)*(self.y_max_m-self.y_min_m)*(self.z_max_m-self.z_min_m)

    def _validate_volume(self, volume_m3: float) -> None:
        if not isfinite(volume_m3) or not 0 <= volume_m3 <= self.capacity_m3:
            raise ValueError('tank volume must be finite and between zero and capacity')

    def sounding(self, volume_m3: float) -> TankSoundingPoint:
        """Exact upright sounding at any volume; endpoints alone have zero FSM."""
        self._validate_volume(volume_m3)
        length, breadth = self.x_max_m-self.x_min_m, self.y_max_m-self.y_min_m
        return TankSoundingPoint(volume_m3=volume_m3,
            lcg_m=(self.x_min_m+self.x_max_m)/2, tcg_m=(self.y_min_m+self.y_max_m)/2,
            vcg_m=self.z_min_m+volume_m3/(2*length*breadth),
            fsm_m4=length*breadth**3/12 if 0 < volume_m3 < self.capacity_m3 else 0.0)

    def liquid(self, volume_m3: float, heel_rad: float = 0.0, trim_rad: float = 0.0) -> LiquidCondition:
        """Fixed-volume finite-angle liquid centroid and surface area moment.

        Angles must describe an upward normal (abs(heel/trim) < pi/2).
        Bisection stops after 64 iterations; volume residual is checked to 1e-9
        of capacity. The surface inertia is about the longitudinal axis through
        the surface centroid, parallel to the centreline plane.
        """
        self._validate_volume(volume_m3)
        if not all(isfinite(a) and abs(a) < np.pi/2 for a in (heel_rad, trim_rad)):
            raise ValueError('heel and trim must be finite and less than 90 degrees')
        s = self.sounding(volume_m3)
        normal = np.array([tan(trim_rad)*cos(heel_rad), -sin(heel_rad), cos(heel_rad)])
        normal /= np.linalg.norm(normal)
        # Work relative to the box centre to avoid precision loss at large x.
        centre = np.array([(getattr(self, f'{a}_min_m')+getattr(self, f'{a}_max_m'))/2 for a in 'xyz'])
        half = np.array([(getattr(self, f'{a}_max_m')-getattr(self, f'{a}_min_m'))/2 for a in 'xyz'])
        if heel_rad == trim_rad == 0 or volume_m3 in (0.0, self.capacity_m3):
            area = ((self.x_max_m-self.x_min_m)*(self.y_max_m-self.y_min_m)
                    if 0 < volume_m3 < self.capacity_m3 else 0.0)
            level = self.z_min_m+volume_m3/((self.x_max_m-self.x_min_m)*(self.y_max_m-self.y_min_m))
            if volume_m3 in (0.0, self.capacity_m3):
                level = float(centre @ normal + (-1 if volume_m3 == 0 else 1)*(half @ np.abs(normal)))
            return LiquidCondition(volume_m3, (s.lcg_m, s.tcg_m, s.vcg_m), area, s.fsm_m4, level)
        corners = np.array(list(product((-1., 1.), repeat=3)))*half
        edges = [(i,j) for i in range(8) for j in range(i+1,8) if np.count_nonzero(corners[i] != corners[j]) == 1]
        projections = corners @ normal
        lo, hi = float(min(projections)), float(max(projections))
        for _ in range(64):
            level = (lo+hi)/2
            vol, centroid, surface = _clip(corners, edges, projections, level)
            if vol < volume_m3:
                lo = level
            else:
                hi = level
        if abs(vol-volume_m3) > 1e-9*self.capacity_m3:
            raise ArithmeticError('tank liquid root failed volume tolerance')
        area, inertia = _surface(surface, normal, trim_rad)
        return LiquidCondition(volume_m3, tuple(float(x) for x in centroid+centre), area, inertia,
                               level+float(centre @ normal))


def _clip(corners: np.ndarray, edges: list, projections: np.ndarray, level: float):
    surface = []
    for i,j in edges:
        if (projections[i] < level) != (projections[j] < level):
            surface.append(corners[i]+(corners[j]-corners[i])*(level-projections[i])/(projections[j]-projections[i]))
    vertices = np.array([*corners[projections <= level], *surface])
    if len(vertices) < 4:
        return 0., np.zeros(3), np.array(surface)
    hull = ConvexHull(vertices)
    interior = vertices.mean(axis=0)
    volume, moment = 0., np.zeros(3)
    for triangle in hull.simplices:
        a,b,c = vertices[triangle]
        dv = abs(float(np.linalg.det(np.array([a-interior,b-interior,c-interior]))))/6
        volume += dv
        moment += dv*(interior+a+b+c)/4
    return volume, moment/volume, np.array(surface)


def _surface(vertices: np.ndarray, normal: np.ndarray, trim_rad: float) -> tuple[float, float]:
    longitudinal = np.array([1.,0.,-tan(trim_rad)])
    longitudinal /= np.linalg.norm(longitudinal)
    transverse = np.cross(normal, longitudinal)
    uv = np.column_stack((vertices @ longitudinal, vertices @ transverse))
    uv = np.unique(uv, axis=0)
    uv -= uv.mean(axis=0)  # Stable central moments for very small corner wedges.
    uv = uv[ConvexHull(uv).vertices]
    u,v = uv[:,0], uv[:,1]
    un,vn = np.roll(u,-1),np.roll(v,-1)
    cross = u*vn-un*v
    area = cross.sum()/2
    cy = ((v+vn)*cross).sum()/(6*area)
    inertia = ((v*v+v*vn+vn*vn)*cross).sum()/12-area*cy*cy
    return float(area), max(0.,float(inertia))


def build_tanks(ship_params: Mapping[str, Any]) -> list[Tank]:
    """Build upright capacity tables from declared tank solids, sorted by ID.

    Reject capacity/extent discrepancies rather than silently changing inputs.
    Soundings at quarters retain the params convention; consumers at arbitrary
    fills must use TankGeometry.sounding for the endpoint FSM discontinuity.
    """
    tanks = []
    for record in ship_params.get('tanks', []):
        g = TankGeometry.from_params(record, ship_params=ship_params)
        capacity = _number(record['capacity_m3'])
        if not isclose(capacity, g.capacity_m3, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError(f"tank {record['id']} capacity differs from geometry")
        if not all(isclose(_number(record[key]), value, abs_tol=1e-9) for key,value in
                   (('x_aft_m',g.x_min_m),('x_fore_m',g.x_max_m))):
            raise ValueError(f"tank {record['id']} longitudinal extent differs from geometry")
        if 'table' in record:
            rows=record['table']
            volumes=[_number(row['volume_m3']) for row in rows]
            if not volumes or volumes[0]!=0. or volumes[-1]!=capacity or any(a>=b for a,b in zip(volumes,volumes[1:])):
                raise ValueError(f"tank {record['id']} supplied table must ascend from zero to capacity")
            # The generator's 42 bisection iterations leave small roundoff; this
            # 1e-9 relative/absolute numerical tolerance is not a design allowance.
            for row,volume in zip(rows,volumes):
                expected=g.sounding(volume)
                if not all(isclose(_number(row[key]),getattr(expected,key),rel_tol=1e-9,abs_tol=1e-9)
                           for key in ('lcg_m','tcg_m','vcg_m','fsm_m4')):
                    raise ValueError(f"tank {record['id']} supplied sounding differs from geometry")
        # Use the declared capacity for exact strict-model endpoint equality.
        table = [g.sounding(g.capacity_m3*f) for f in (0.,.25,.5,.75,1.)]
        table[-1] = table[-1].model_copy(update={'volume_m3':capacity})
        tanks.append(Tank(id=record['id'], kind=record['kind'],
            content_density_kg_m3=_number(record['content_density_kg_m3']), capacity_m3=capacity,
            table=table, heated=record.get('heated',False), x_aft_m=g.x_min_m,
            x_fore_m=g.x_max_m, adjustable=record.get('adjustable',False)))
    ids = [t.id for t in tanks]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate tank IDs')
    return sorted(tanks, key=lambda t:t.id)

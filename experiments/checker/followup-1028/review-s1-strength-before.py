"""Still-water hull-girder strength (IACS UR S11A; S1 read-out stations).

SF is the integrated upward buoyancy minus weight; BM is minus its integral,
so hogging is positive, following the frozen Station convention. Closed covers
are already lightship. APPROXIMATION: cargo masses act at 20-foot cell centres
(40/45-foot boxes split between supporting cells), rather than corner castings.
Fixed loads act at declared centres. APPROXIMATION: lightship is uniform within
each declared block, with mass outside AP/FP folded inside these blocks. Liquid loads
use the actual clipped tank at the solved trim and heel. Rectangular tanks have
exact piecewise-polynomial loads. APPROXIMATION: hull-following tanks use their
authoritative sectional quadrature as concentrated masses (wet area times
longitudinal weight times density). This preserves liquid mass and longitudinal
moment without inventing continuous geometry across gaps or missing Gauss mates;
SF therefore jumps at the tank quadrature stations (vessel.hull_tanks).
The beam begins/ends at the geometry's outermost source panels, retaining
buoyant overhangs outside AP/FP. SF and BM at AP/FP need not vanish; only the
actual free ends close. This includes all loads aft of a section in its readout.

Buoyancy uses HullGeometry's trimmed equilibrium and composite Gauss stations.
APPROXIMATION: between each pair of Gauss stations the sectional load is
reconstructed linearly. This preserves both quadrature force and first moment
exactly, rather than treating Gauss stations as uniformly spaced or point loads.
A small affine distributed-load correction closes residual end SF and BM (the
usual force/moment balancing of an integrated load curve); raw errors and the
correction are exposed. Gross numerical residuals prevent a strength verdict.
APPROXIMATION: the beam uses vertical forces in ship coordinates. At trim,
HullGeometry balances moments in the waterplane axes, including the KG/KB
height lever. The exact physical raw end moment is -W*(KG-KB)*tan(theta),
where tan(theta)=(draught_aft-draught_forward)/L,
which is removed before testing numerical closure. Its longitudinal shape is
still replaced by an affine closing load rather than the actual distributed
trim couple. APPROXIMATION: the 0.1% numerical force/moment closure guard is a
synthetic checker tolerance, not a class-rule tolerance, scaled by W or W*L.
APPROXIMATION: this function checks only the supplied loading condition against
seagoing limits. Departure/arrival/worst intermediate coverage and separate
harbour limits belong to caller orchestration (S11A 2.2.2 and S1/Rec.48).
A departure-only caller does not cover those required additional conditions.

Metrics: max_sf_ratio, max_bm_ratio; sf_n@<x>, bm_nm@<x> at each prescribed
station. Ratios also include both sides of concentrated loads and all interior
extrema against the piecewise-linear permissible envelope; assessment_cut_count
reports the assessed cuts. Raw_end_shear_n/raw_end_bending_nm and end_shear_n/end_bending_nm;
closure_force_fraction, closure_moment_fraction (numerical only);
raw_closure_moment_fraction, predicted_trim_end_bending_nm,
numerical_end_bending_nm; correction_a_n_m and
correction_b_n_m2; displacement_kg, draught_aft_m, draught_fwd_m, trim_m,
list_rad. Margins sf_ratio and bm_ratio are one minus their worst ratio.
All computations use SI and g=9.80665. See HullGeometry's documented synthetic
hull approximation and strength_limits for the assumed S11A design envelope.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath
import math

import numpy as np
from scipy.optimize import brentq, root

from stowage.checker.slots import cells_of_slot
from stowage.checker.strength_assessment import assessment_cuts
from stowage.frozen import frozen
from stowage.model import Instance, PartReport, ShipState, Violation
from stowage.vessel.hydrostatics import HullGeometry, FloatingCondition, _clip, _moments
from stowage.vessel.tanks import TankGeometry, LiquidCondition

G = 9.80665


@dataclass(frozen=True)
class StrengthCurves:
    x_m: np.ndarray
    shear_n: np.ndarray
    bending_nm: np.ndarray
    raw_end_shear_n: float
    raw_end_bending_nm: float
    correction_a_n_m: float
    correction_b_n_m2: float
    # Retain the already reconstructed beam for continuous assessment without
    # rebuilding loads or changing the supplied-station array contract.
    load_segments: tuple = ()
    point_forces: tuple = ()


def _integrate_segments(stations, segments, points):
    """Integrate local polynomials q=a+b*(x-left)+c*(x-left)^2 exactly."""
    x = np.asarray(stations, dtype=float)
    sf, bm = np.zeros_like(x), np.zeros_like(x)
    for left, right, a, b, c in segments:
        d = np.clip(x-left, 0, right-left)
        force = a*d+b*d*d/2+c*d**3/3
        moment = a*d*d/2+b*d**3/3+c*d**4/4
        sf += force
        bm -= (x-left)*force-moment
    for position, force in points:
        sf += force*(x >= position)
        bm -= force*np.maximum(x-position, 0)
    return sf, bm


def integrate_strength(x_m, integration_weights_m, buoyancy_n_m, weight_blocks,
                       point_loads, stations_m, *, lpp_m, close=True,
                       distributed_segments=()):
    """Pure Gauss-buoyancy/block/point integration for independent analytic tests.

    Blocks and points carry mass in kg; buoyancy and optional polynomial
    distributed_segments carry signed net upward N/m. A segment is
    (left, right, a, b, c), its local quadratic coefficients. The Gauss grid
    must be consecutive two-point panels, as supplied by HullGeometry. Its
    outer panel boundaries define the free beam ends, including AP/FP
    overhangs. Lpp remains the rule length, not the integration extent.
    """
    x, w, q = map(lambda v: np.asarray(v, dtype=float),
                  (x_m, integration_weights_m, buoyancy_n_m))
    L = float(lpp_m)
    if L <= 0 or len(x) == 0 or len(x) % 2 or x.shape != w.shape or x.shape != q.shape:
        raise ValueError('invalid two-point Gauss load grid')
    if not all(np.isfinite(v).all() for v in (x,w,q)) or np.any(w <= 0):
        raise ValueError('nonfinite load grid or nonpositive Gauss weights')
    segments = list(distributed_segments)
    aft = float((x[0]+x[1])/2-w[0])
    last = aft
    for i in range(0, len(x), 2):
        midpoint = (x[i]+x[i+1])/2
        left, right = midpoint-w[i], midpoint+w[i]
        if not (np.isclose(w[i],w[i+1]) and np.isclose(left,last)
                and np.isclose(x[i+1]-x[i],2*w[i]/math.sqrt(3))):
            raise ValueError('grid is not consecutive two-point Gauss panels')
        slope = (q[i+1]-q[i])/(x[i+1]-x[i])
        segments.append((left,right,q[i]+slope*(left-x[i]),slope,0.))
        last = right
    fore = float(last)
    span = fore-aft
    if aft > 1e-8 or fore < L-1e-8:
        raise ValueError('Gauss panels must include AP to FP and retain their overhangs')
    for left,right,mass in weight_blocks:
        if not (aft-1e-8 <= left < right <= fore+1e-8 and np.isfinite(mass)):
            raise ValueError('invalid longitudinal weight block')
        segments.append((left,right,-mass*G/(right-left),0.,0.))
    points = []
    for position,mass in point_loads:
        if not (aft-1e-8 <= position <= fore+1e-8 and np.isfinite(mass)):
            raise ValueError('invalid point load')
        points.append((position,-mass*G))
    raw_sf,raw_bm = _integrate_segments([fore],segments,points)
    # Close at the actual free bow, using u=x-aft over the complete span.
    a,b = (np.linalg.solve([[span,span*span/2],[span*span/2,span**3/6]],
                          [-raw_sf[0],raw_bm[0]]) if close else (0.,0.))
    segments.append((aft,fore,float(a),float(b),0.))
    stations = np.asarray(stations_m,dtype=float)
    if not np.isfinite(stations).all() or np.any(stations < aft-1e-8) or np.any(stations > fore+1e-8):
        raise ValueError('read-out stations must lie inside the complete buoyant envelope')
    sf,bm = _integrate_segments(stations,segments,points)
    return StrengthCurves(stations,sf,bm,float(raw_sf[0]),float(raw_bm[0]),float(a),float(b),
                          tuple(segments),tuple(points))


@lru_cache(maxsize=8)
def _geometry(name):
    from stowage.vessel.fleet import load_ship_params
    params = load_ship_params(name)
    geometry = HullGeometry.from_params(params)
    # Repeated root evaluations have exactly equal float arguments. Cache the
    # immutable-in-use section results; this changes no sampling or tolerance.
    # These private geometries never escape this module, and their returned
    # arrays are read only throughout the checker.
    geometry.submerged = lru_cache(maxsize=128)(geometry.submerged)
    return params, geometry


def _tank_segments(tank, liquid, heel, trim, density):
    """Exact piecewise-quadratic sectional liquid area of a tilted box."""
    normal = np.array([math.tan(trim)*math.cos(heel),-math.sin(heel),math.cos(heel)])
    normal /= np.linalg.norm(normal)
    polygon = np.array([[tank.y_min_m,tank.z_min_m],[tank.y_max_m,tank.z_min_m],
                        [tank.y_max_m,tank.z_max_m],[tank.y_min_m,tank.z_max_m]])
    edges = [tank.x_min_m,tank.x_max_m]
    if abs(normal[0]) > 1e-14:
        edges.extend((liquid.plane_level_m-normal[1]*y-normal[2]*z)/normal[0]
                     for y,z in polygon)
    edges = sorted(set(float(np.clip(v,tank.x_min_m,tank.x_max_m)) for v in edges))
    segments = []
    for left,right in zip(edges,edges[1:]):
        if right-left < 1e-12:
            continue
        def area(xx):
            return _moments(_clip(polygon,normal[1],normal[2],liquid.plane_level_m-normal[0]*xx))[0]
        # Every change in the clipped polygon's vertices is a breakpoint;
        # between them section area is exactly quadratic in longitudinal x.
        length = right-left
        v0,vm,v1 = (area(xx) for xx in (left,(left+right)/2,right))
        c = 2*(v1-2*vm+v0)/length**2
        b = (v1-v0-c*length**2)/length
        segments.append((left,right,-density*G*v0,-density*G*b,-density*G*c))
    return segments


def _tank_liquid(tank, volume, heel=0., trim=0.):
    """Exact sliced-box liquid volume/centroid, avoiding repeated 3-D hulls.

    Two-point Gauss integrates the piecewise quadratic areas and cubic first
    moments exactly. Breakpoints are every crossing of a tank edge by the
    liquid plane; the level is the only numerical root.
    """
    if not tank.is_rectangular or heel == trim == 0 or volume in (0.,tank.capacity_m3):
        return tank.liquid(volume,heel,trim)
    normal = np.array([math.tan(trim)*math.cos(heel),-math.sin(heel),math.cos(heel)])
    normal /= np.linalg.norm(normal)
    poly = np.array([[tank.y_min_m,tank.z_min_m],[tank.y_max_m,tank.z_min_m],
                     [tank.y_max_m,tank.z_max_m],[tank.y_min_m,tank.z_max_m]])
    def integrals(level):
        edges = [tank.x_min_m,tank.x_max_m]
        if abs(normal[0]) > 1e-14:
            edges.extend((level-normal[1]*y-normal[2]*z)/normal[0] for y,z in poly)
        edges = sorted(set(np.clip(edges,tank.x_min_m,tank.x_max_m)))
        total = np.zeros(4)
        for left,right in zip(edges,edges[1:]):
            half = (right-left)/2
            for xx in ((left+right)/2-half/math.sqrt(3),(left+right)/2+half/math.sqrt(3)):
                area,ym,zm = _moments(_clip(poly,normal[1],normal[2],level-normal[0]*xx))
                total += half*np.array([area,xx*area,ym,zm])
        return total
    levels = [normal[0]*xx+normal[1]*y+normal[2]*z
              for xx in (tank.x_min_m,tank.x_max_m) for y,z in poly]
    level = brentq(lambda h:integrals(h)[0]-volume,min(levels),max(levels),xtol=1e-11)
    total = integrals(level)
    return LiquidCondition(volume,tuple(total[1:]/total[0]),0.,0.,level)


def _section_tank_point_loads(tank, volume, heel, trim, density):
    """Equivalent kg loads of the tank's authoritative liquid quadrature.

    Do not pair adjacent samples: excluded hull regions can leave gaps and
    unpaired Gauss points. The upstream liquid solver supplies the same weights
    for volume and centroid, so these loads retain both exactly.
    """
    x, weights, areas = tank.section_liquid_areas(volume,heel,trim)
    return [(float(xx),float(density*weight*area))
            for xx,weight,area in zip(x,weights,areas) if area > 0.]


def _condition_loads(instance,ship,geometry,params):
    vessel,hull = instance.vessel,instance.vessel.hull
    blocks = list(hull.lightship_distribution)
    if not blocks or not math.isclose(sum(b[2] for b in blocks),hull.lightship_mass_kg,rel_tol=1e-8):
        raise ValueError('lightship weight blocks do not sum to lightship mass')
    block_lcg = sum((a+b)/2*m for a,b,m in blocks)/hull.lightship_mass_kg
    if abs(block_lcg-hull.lightship_lcg_m) > 1e-6*hull.lpp_m:
        raise ValueError('lightship weight blocks disagree with declared LCG')
    masses = [(hull.lightship_mass_kg,hull.lightship_lcg_m,hull.lightship_tcg_m,hull.lightship_vcg_m)]
    points = []
    boxes = {box.id:box for box in instance.containers}
    cells = {cell.id:cell for cell in vessel.cells}
    stacks = {stack.id:stack for stack in vessel.stacks}
    occupied = {}
    for placement in ship.placements:
        for cid in cells_of_slot(placement.slot):
            occupied[cid] = boxes[placement.container_id]
    for placement in ship.placements:
        box = boxes[placement.container_id]
        supports = [cells[cid] for cid in cells_of_slot(placement.slot)]
        for cell in supports:
            stack = stacks[cell.stack_id]
            lower = stack.cell_ids[:stack.cell_ids.index(cell.id)]
            floor = stack.base_z_m+sum(occupied[cid].height_m+(
                occupied[cid].oog.over_height_m if occupied[cid].oog else 0.)
                for cid in lower if cid in occupied)
            m = box.gross_mass_kg/len(supports)
            points.append((cell.lcg_m,m))
            masses.append((m,cell.lcg_m,cell.tcg_m,floor+box.height_m/2))
    covers = {cover.id:cover for cover in vessel.hatch_covers}
    for cid,destination in ship.covers_off.items():
        cover = covers[cid]
        points.append((cover.lcg_m,-cover.mass_kg))
        masses.append((-cover.mass_kg,cover.lcg_m,cover.tcg_m,cover.vcg_m))
        if destination != 'quay':
            target = covers[destination]
            points.append((target.lcg_m,cover.mass_kg))
            masses.append((cover.mass_kg,target.lcg_m,target.tcg_m,target.vcg_m))
    for x,y,z,m in vessel.fixed_loads:
        points.append((x,m)); masses.append((m,x,y,z))
    tank_models = {t.id:t for t in vessel.tanks}
    tank_params = {t['id']:t for t in params.get('tanks',[])}
    tanks = []
    for tid,mass in sorted(ship.tank_masses_kg.items()):
        model = tank_models[tid]
        volume = mass/model.content_density_kg_m3
        solid = TankGeometry.from_params(tank_params[tid],ship_params=params)
        # Public validation supports both rectangular and hull-following tanks,
        # including the latter's tolerance for roundoff at capacity.
        solid.liquid(volume)
        if mass:
            tanks.append((solid,volume,mass,model.content_density_kg_m3))
    total_mass = sum(m[0] for m in masses)+sum(t[2] for t in tanks)
    def cg(liquids):
        moment = sum((m*np.array([x,y,z]) for m,x,y,z in masses),np.zeros(3))
        moment += sum((t[2]*np.array(liquid.centroid_m) for t,liquid in zip(tanks,liquids)),np.zeros(3))
        return moment/total_mass
    liquids = [_tank_liquid(solid,volume) for solid,volume,_,_ in tanks]
    density = instance.port.water_density_kg_m3
    lcg,tcg,vcg = cg(liquids)
    condition = geometry.equilibrium(total_mass,lcg,tcg,vcg,density)
    if tanks:
        # Warm-start from task 2.1 equilibrium, then solve precisely the same
        # volume and waterplane-axis moment equations with tank CG evaluated
        # *inside* the residual. This avoids repeatedly restarting a full hull
        # solve during an outer fixed-point liquid-centroid iteration.
        target_volume = total_mass/density
        def residual(v):
            ta,tf,heel = v
            trim = math.atan((ta-tf)/hull.lpp_m)
            liquids = [_tank_liquid(solid,volume,heel,trim) for solid,volume,_,_ in tanks]
            centre = cg(liquids)
            submerged = geometry.submerged(ta,tf,heel,density)
            _,along,across = geometry._axes(ta,tf,heel)
            diff = np.array(submerged.centroid_m)-centre
            return [(submerged.volume_m3-target_volume)/target_volume,
                    float(diff@along)/hull.lpp_m,float(diff@across)/hull.beam_m]
        solved = root(residual,[condition.t_aft_m,condition.t_fwd_m,condition.heel_rad],tol=1e-9)
        error = float(np.linalg.norm(residual(solved.x)))
        if not solved.success or error > 2e-6:
            raise ValueError('coupled tank/hull floating equilibrium did not converge')
        ta,tf,heel = map(float,solved.x)
        condition = FloatingCondition(ta,tf,heel,geometry.submerged(ta,tf,heel,density),error)
    trim = math.atan(condition.trim_m/hull.lpp_m)
    updated = [_tank_liquid(solid,volume,condition.heel_rad,trim) for solid,volume,_,_ in tanks]
    segments = []
    for (solid,volume,_,rho),liquid in zip(tanks,updated):
        if solid.is_rectangular:
            segments.extend(_tank_segments(solid,liquid,condition.heel_rad,trim,rho))
        else:
            points.extend(_section_tank_point_loads(solid,volume,condition.heel_rad,trim,rho))
    return condition,total_mass,blocks,points,segments,cg(updated)


@frozen("checker.check_strength", owner="3.2", implemented=True)
def check_strength(instance: Instance, ship: ShipState, *, call_index: int) -> PartReport:
    """Shear force and bending moment of one loading condition against the permissible values."""
    def failure(message, *, ran=False, rule='checker.missing_vessel_data'):
        return PartReport(name='strength',ran=ran,error=message if not ran else None,
            violations=[Violation(rule_id=rule,severity='hard',call_index=call_index,message=message)])
    vessel,hull = instance.vessel,instance.vessel.hull
    if hull is None or not hull.stations or not hull.hull_offsets_ref:
        return failure('Strength requires hull geometry, lightship blocks and permissible read-out stations.')
    if ship.vessel_id != vessel.id:
        return failure('Strength state references a different vessel.')
    if hull.beam_m > 32.26:
        return failure('S11A torsional strength is required above 32.26 m breadth; torsion is not modelled.')
    if any(getattr(station, 'max_torsion_nm', None) is not None for station in hull.stations):
        return failure('S1 requires the explicitly declared station torsion readouts; '
                       'torsion is not modelled.', rule='iacs.s1.loading_instrument')
    try:
        reference = hull.hull_offsets_ref.split('#')[0]
        if not reference.startswith('params/fleet/') or not reference.endswith('.toml'):
            return failure('Unsupported hull geometry reference for strength.')
        params,geometry = _geometry(PurePosixPath(reference).stem)
        if not np.allclose([hull.lpp_m,hull.beam_m,hull.depth_m],
                           [geometry.lpp_m,geometry.beam_m,geometry.depth_m],rtol=1e-10):
            return failure('Hull geometry reference disagrees with vessel dimensions.')
        condition,mass,blocks,points,segments,centre = _condition_loads(instance,ship,geometry,params)
        if ship.covers_off:
            # A dry cover only relocates weight. Closed-coaming solids cannot
            # represent immersed open coamings and a potentially flooded hold.
            coamings = {c['id']:c for c in params.get('envelope',{}).get('hatch_coamings',[])}
            normal = geometry.waterplane_normal(condition.t_aft_m,condition.t_fwd_m,condition.heel_rad)
            level = condition.t_aft_m*normal[2]
            for cover_id in ship.covers_off:
                coaming = coamings.get(cover_id)
                if coaming is None:
                    return failure('Open cover lacks coaming geometry needed to confirm a dry opening.')
                lip = min(float(normal@np.array([x,y,coaming['z_max_m']]))
                    for x in (coaming['x_min_m'],coaming['x_max_m'])
                    for y in (coaming['y_min_m'],coaming['y_max_m']))
                if lip <= level:
                    return failure('Open hatch coaming lip is immersed; flooded/open-hold buoyancy is unavailable.')
        s = condition.submerged
        rho = instance.port.water_density_kg_m3
        stations = [station.x_m for station in hull.stations]
        buoyant_aft = float((s.x_m[0]+s.x_m[1])/2-s.integration_weights_m[0])
        buoyant_fore = float((s.x_m[-2]+s.x_m[-1])/2+s.integration_weights_m[-1])
        curve = integrate_strength(s.x_m,s.integration_weights_m,s.sectional_areas_m2*rho*G,
            blocks,points,[*stations,buoyant_fore],lpp_m=hull.lpp_m,distributed_segments=segments)
    except (ValueError,KeyError,ArithmeticError,TypeError) as exc:
        return failure(f'Strength input/equilibrium error: {exc}')
    # The integrator receives every prescribed station plus the free bow.
    # Validate that boundary before using an end sample or constructing S1
    # readouts: a truncated/misaligned/nonfinite numerical result cannot prove
    # that each required station was calculated.
    try:
        requested = np.asarray([*stations, buoyant_fore], dtype=float)
        sampled = tuple(np.asarray(values, dtype=float)
                        for values in (curve.x_m, curve.shear_n, curve.bending_nm))
        if (any(values.shape != requested.shape for values in sampled)
                or any(not np.isfinite(values).all() for values in sampled)
                or not np.array_equal(sampled[0], requested)):
            return failure('S1 requires finite SF/BM readouts at every prescribed station '
                           'and the free bow; numerical readouts are incomplete or misaligned.',
                           rule='iacs.s1.loading_instrument')
    except (ValueError,TypeError,AttributeError,ArithmeticError) as exc:
        return failure(f'S1 required strength readouts are invalid: {exc}',
                       rule='iacs.s1.loading_instrument')
    force_error = abs(curve.raw_end_shear_n)/(mass*G)
    # Exact equilibrium along-axis is normalized [1, 0, (Tf-Ta)/L]. Thus
    # XB-XG = -(KG-KB)*tan(trim), even when heel is nonzero. BM(L)=W*(XB-XG).
    trim_end_bending = -mass*G*(centre[2]-s.vcb_m)*condition.trim_m/hull.lpp_m
    numerical_end_bending = curve.raw_end_bending_nm-trim_end_bending
    moment_error = abs(numerical_end_bending)/(mass*G*hull.lpp_m)
    metrics = dict(raw_end_shear_n=curve.raw_end_shear_n,raw_end_bending_nm=curve.raw_end_bending_nm,
        end_shear_n=float(curve.shear_n[-1]),end_bending_nm=float(curve.bending_nm[-1]),
        closure_force_fraction=force_error,closure_moment_fraction=moment_error,
        raw_closure_moment_fraction=abs(curve.raw_end_bending_nm)/(mass*G*hull.lpp_m),
        predicted_trim_end_bending_nm=trim_end_bending,numerical_end_bending_nm=numerical_end_bending,
        correction_a_n_m=curve.correction_a_n_m,correction_b_n_m2=curve.correction_b_n_m2,
        buoyant_x_min_m=buoyant_aft,buoyant_x_max_m=buoyant_fore,
        displacement_kg=mass,draught_aft_m=condition.t_aft_m,draught_fwd_m=condition.t_fwd_m,
        trim_m=condition.trim_m,list_rad=condition.heel_rad)
    violations = []
    if max(force_error,moment_error) > 1e-3:
        report = failure('Numerical load-curve imbalance exceeds the synthetic 0.1% closure tolerance '
                         'after subtracting the physical trim lever; no strength verdict is available.')
        report.metrics = metrics
        return report
    # Preserve prescribed readouts for existing consumers. The safety verdict
    # additionally covers the entire represented beam (S11A 2.2.1, PDF p.8),
    # including its discontinuities, as derived in strength_assessment.
    for station,sf,bm in zip(hull.stations,curve.shear_n[:-1],curve.bending_nm[:-1],strict=True):
        label = format(station.x_m,'.12g')
        metrics[f'sf_n@{label}'] = float(sf); metrics[f'bm_nm@{label}'] = float(bm)
    try:
        cuts = assessment_cuts(curve.load_segments,curve.point_forces,hull.stations)
    except (ValueError,ArithmeticError,TypeError) as exc:
        return failure(f'Strength permissible-envelope error: {exc}')
    ordered = sorted(hull.stations,key=lambda station:station.x_m)
    xs = [station.x_m for station in ordered]
    limits = np.column_stack([np.interp([cut[0] for cut in cuts],xs,
        [getattr(station,name) for station in ordered]) for name in
        ('min_shear_n','max_shear_n','min_bending_nm','max_bending_nm')])
    sf_ratios,bm_ratios = [],[]
    metrics['assessment_cut_count'] = len(cuts)
    for (x,sf,bm,side),(sf_min,sf_max,bm_min,bm_max) in zip(cuts,limits):
        label = format(x,'.12g')
        for value,lower,upper,unit,kind,ratios in (
            (sf,sf_min,sf_max,'N','shear',sf_ratios),
            (bm,bm_min,bm_max,'N m','bending',bm_ratios)):
            ratio = float(value/upper if value >= 0 else value/lower)
            ratios.append(ratio)
            if value < lower-1e-6 or value > upper+1e-6:
                violations.append(Violation(rule_id='iacs.s11a.longitudinal_strength',severity='hard',
                    call_index=call_index,measured=float(value),limit=upper if value >= 0 else lower,
                    unit=unit,message=f'Still-water {kind} exceeds its permissible value at x={label} m '
                                      f'({side} cut).'))
    metrics['max_sf_ratio'] = max(sf_ratios); metrics['max_bm_ratio'] = max(bm_ratios)
    return PartReport(name='strength',metrics=metrics,violations=violations,
        margins={'sf_ratio':1-max(sf_ratios),'bm_ratio':1-max(bm_ratios)})

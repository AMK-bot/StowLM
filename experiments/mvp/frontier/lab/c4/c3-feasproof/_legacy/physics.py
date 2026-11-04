"""Necessary whole-map physics and securing inequalities.

This module never accepts a stow. In particular, the lashing GM and the
stability GM are deliberately distinct quantities: their source providers use
different liquid-centroid and free-surface conventions.
"""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from functools import lru_cache
import hashlib
import math
from pathlib import Path, PurePosixPath


LASHING_SOURCE_SHA256 = '6bf6a1ba92cf17687716fbb6eb8ac7bf528971356614134b84d5bb89f1ecfdb8'
FORCE_GUARD = 1. - 1.e-10


def _down(value):
    """Round an exact rational down to a binary float."""
    value = Fraction(value)
    result = float(value)
    if Fraction(result) > value:
        result = math.nextafter(result, -math.inf)
    return result


def _up(value):
    value = Fraction(value)
    result = float(value)
    if Fraction(result) < value:
        result = math.nextafter(result, math.inf)
    return result


def _sqrt_lower(value):
    value = Fraction(value)
    if value < 0:
        raise ValueError('negative square root')
    result = math.sqrt(float(value))
    while Fraction(result)**2 > value:
        result = math.nextafter(result, -math.inf)
    return Fraction(result)


def _sqrt_upper(value):
    value = Fraction(value)
    result = math.sqrt(float(value))
    while Fraction(result)**2 < value:
        result = math.nextafter(result, math.inf)
    return Fraction(result)


@lru_cache(maxsize=4)
def _hull_reference(ref, lpp, beam, depth, scantling):
    """Pinned checker's exact scantling-CB provider; no loaded equilibrium."""
    from stowage.checker import lashing, visibility
    if hashlib.sha256(Path(lashing.__file__).read_bytes()).hexdigest() != LASHING_SOURCE_SHA256:
        raise ValueError('unreviewed lashing source')
    path = PurePosixPath(ref.split('#')[0])
    if str(path.parent) != 'params/fleet' or path.suffix != '.toml':
        raise ValueError('unsupported hull reference')
    geometry, params = visibility._geometry(path.stem)
    actual = geometry.lpp_m, geometry.beam_m, geometry.depth_m
    if any(not math.isclose(a,b,rel_tol=1e-9) for a,b in zip(actual,(lpp,beam,depth))):
        raise ValueError('hull dimensions disagree with checker geometry')
    volume = geometry.submerged(scantling,scantling).volume_m3
    cb = volume/(lpp*beam*scantling)
    if not 0 < cb <= 1:
        raise ValueError('nonphysical scantling CB')
    return geometry, params, cb


def hull_reference(instance):
    from stowage.frozen import active_stubs
    if active_stubs():
        raise ValueError('active frozen-interface override')
    h = instance.vessel.hull
    return _hull_reference(h.hull_offsets_ref,h.lpp_m,h.beam_m,h.depth_m,h.scantling_draught_m)


def motion_floor(instance, *, gm_lower=0., z_lower=-math.inf):
    """Outward motion floor for *lashing* GM >= gm_lower, any draught.

    For z below depth/2 cancellation is possible, so only the immutable
    surge/sway/heave terms are retained. At elevated z, the rotational lever
    is nonnegative. No stability draught or GM bound is borrowed here.
    Sine is bounded below by x-x^3/6 using rational arithmetic, rather than
    assuming endpoint sampling encloses the nonlinear expression.
    """
    from stowage.checker import lashing
    h = instance.vessel.hull
    if not math.isfinite(gm_lower) or gm_lower < 0:
        raise ValueError('nonnegative finite lashing GM lower bound required')
    _,_,cb = hull_reference(instance)
    reference = lashing._motions(h,h.scantling_draught_m/4,1.,cb)
    F = Fraction
    gravity, pi = F(lashing.G), F(math.pi)
    surge, sway, heave = map(F,(abs(reference.surge),abs(reference.sway),abs(reference.heave)))
    al, at, av = F(.7)*surge, sway, heave
    period_upper = F(30.)
    if gm_lower > 0:
        denominator = _sqrt_lower(gravity*F(gm_lower))
        period_upper = min(period_upper,F(2.3)*pi*F(.35)*F(h.beam_m)/denominator)
    factor = F(1. if h.bilge_keel else 1.2)
    phi_degree_lower = F(9000.)*(F(1.25)-F(.025)*period_upper)*factor/((F(h.beam_m)+75)*pi)
    phi_degree_upper = F(9000.)*F(1.25)*factor/((F(h.beam_m)+75)*pi)
    if h.beam_m < 32.2:
        phi_degree_lower = max(F(25.),phi_degree_lower)
        phi_degree_upper = max(F(25.),phi_degree_upper)
    if not 0 <= phi_degree_lower <= phi_degree_upper < 90:
        raise ValueError('roll-angle monotonicity not established')
    phi_lower = phi_degree_lower*pi/180
    theta = F(reference.theta)
    if not 0 <= theta < pi/2:
        raise ValueError('pitch-angle sine bound not established')
    if math.isfinite(z_lower) and z_lower >= h.depth_m/2:
        lever = F(z_lower)-F(h.depth_m)/2
        sin_phi_lower = phi_lower-phi_lower**3/6
        sin_theta_lower = theta-theta**3/6
        roll_lower = phi_lower*(2*pi/period_upper)**2
        horizontal = gravity*sin_phi_lower+roll_lower*lever
        at = _sqrt_lower(sway*sway+horizontal*horizontal)
        # Pitch coefficient can approach zero for unbounded lashing draught.
        al = F(.7)*_sqrt_lower(surge*surge+F(max(h.lpp_m,110.))/325*(gravity*sin_theta_lower)**2)
    return dict(al_m_s2=max(0.,_down(al)*FORCE_GUARD),
                at_m_s2=max(0.,_down(at)*FORCE_GUARD),
                av_m_s2=max(0.,_down(av)*FORCE_GUARD),
                gm_lashing_lower_m=gm_lower,rotation_upper_m=h.depth_m/2,
                period_upper_s=_up(period_upper),phi_lower_rad=_down(phi_lower),
                source_sha256=LASHING_SOURCE_SHA256)


def securing_coefficients(instance, box, *, on_deck, z_lower, gm_lower=0.):
    """Necessary own-box and bay aggregate coefficients for one allocation.

    Use a representative real box with the largest OOG asymmetry in a class
    only if the class is exact in those attributes. Otherwise pass every
    member and take coefficient minima / ratio minima: minimum preserves all
    class members. The root model does this explicitly.
    """
    from stowage.checker import lashing
    m = max(box.gross_mass_kg,2500. if box.length_ft == 20 else 3500.)
    oog = box.oog
    height = box.height_m+(oog.over_height_m if oog else 0.)
    length = lashing.LENGTH[box.length_ft]
    expanded_l = length+(oog.over_length_fore_m+oog.over_length_aft_m if oog else 0.)
    expanded_w = lashing.WIDTH+(oog.over_width_port_m+oog.over_width_starboard_m if oog else 0.)
    dx = (oog.over_length_fore_m-oog.over_length_aft_m)/2 if oog else 0.
    securing_length = 12.192 if box.length_ft == 45 else length
    shares = [abs(.5-dx/securing_length),abs(.5+dx/securing_length)]
    motion = motion_floor(instance,gm_lower=gm_lower,z_lower=z_lower)
    al,at,av = (motion[k] for k in ('al_m_s2','at_m_s2','av_m_s2'))
    wind_t = max(15700. if box.length_ft == 20 else 31600.,expanded_l*height*1000.) if on_deck else 0.
    wind_l = max(6400.,expanded_w*height*1000.) if on_deck else 0.
    transverse = (m*at+wind_t)*FORCE_GUARD
    longitudinal = (m*al+wind_l)*FORCE_GUARD
    # Own longitudinal DAG shear is >= whole own horizontal force / 2 at
    # either end; dividing by 2 corners gives own horizontal force / 4.
    if on_deck:
        own_fitting = math.hypot(max(shares)*transverse/2,longitudinal/4)/(.85*lashing.LOCK_SHEAR_SWL)
    else:
        own_fitting = math.hypot(m*at,m*al)/(4*.85*lashing.LOCK_SHEAR_SWL)
    # An upper eccentric box can apply a negative mean end reaction to one
    # of a lower long box's two supporting 20-foot columns. A whole-graph
    # positivity audit could recover own compression floors, but an own-box
    # test alone cannot establish that audit. Relax compression here.
    own_corner = 0.
    return dict(**motion,transverse_force_n=transverse,longitudinal_force_n=longitudinal,
                # For a long box, its physical-column share can be smaller
                # than 1/parts with asymmetric OOG. This factor restores a
                # lower coefficient valid for either physical column.
                per_column_share_factor=min(1.,2*min(shares)),
                own_fitting_ratio=own_fitting*FORCE_GUARD,
                own_corner_ratio=own_corner*FORCE_GUARD,
                own_mass_ratio=box.gross_mass_kg/30500.,
                deck_force_capacity_per_physical_column_n=4*.85*lashing.LOCK_SHEAR_SWL,
                deck_longitudinal_capacity_per_root_n=2*.85*lashing.LOCK_SHEAR_SWL)


def physical_bounds(instance):
    """Static mass/tank/weather bounds independent of assignment and tanks.

    Virtual KG >= physical upright KG because FSC is nonnegative. The weather
    table applicability row forces virtual KG <= 1.5*d. This directly gives a
    useful physical-KG upper envelope without confusing the two GM providers.
    """
    from stowage.checker import stability
    from stowage.checker.visibility import _fixed_loads
    h = instance.vessel.hull
    geometry,params,_ = hull_reference(instance)
    trim_lo,trim_hi,mass_lo,mass_hi = stability._loading_policy(instance)
    draft_hi = min(h.max_draught_m,instance.port.max_draught_m or h.max_draught_m)
    # Weather B/d < 3.5 is strict; closed lower endpoint is outward-safe.
    draft_lo = max(0.,h.beam_m/3.5)
    # Total mass ignores the fixed cargo's geometric boxes: FixedLoad already
    # carries precisely that mass and adding its box records would double it.
    boxes = {b.id:b for b in instance.containers}
    fixed_mass = h.lightship_mass_kg+math.fsum(q.mass_kg for q in _fixed_loads(instance.vessel))
    fixed_zmoment = h.lightship_mass_kg*h.lightship_vcg_m+math.fsum(q.mass_kg*q.z_m for q in _fixed_loads(instance.vessel))
    cargo_mass = math.fsum(boxes[bid].gross_mass_kg for bid in instance.load_list)
    raw = {q['id']:q for q in params.get('tanks',[])}
    tanks = []
    for t in instance.vessel.tanks:
        g = raw[t.id].get('geometry',raw[t.id])
        # Declared bounding box is an enclosure of every liquid centroid,
        # independent of heel/trim/volume. Actual tilted-liquid arms are not
        # replaced by a half-fill sounding or tangent.
        def number(value):
            return float(value['value'] if isinstance(value,dict) else value)
        zlo,zhi = number(g['z_min_m']),number(g['z_max_m'])
        tanks.append(dict(id=t.id,capacity_kg=t.capacity_m3*t.content_density_kg_m3,
                          z_lower_m=zlo,z_upper_m=zhi))
    return dict(fixed_mass_kg=fixed_mass,fixed_zmoment_kg_m=fixed_zmoment,
                cargo_mass_kg=cargo_mass,mass_lower_kg=mass_lo,mass_upper_kg=mass_hi,
                trim_lower_m=trim_lo,trim_upper_m=trim_hi,
                draft_stability_lower_m=draft_lo,draft_stability_upper_m=draft_hi,
                physical_kg_upper_m=1.5*draft_hi,
                gm_stability_lower_m=max(.15,float(instance.rule_overrides.get('iscode.a2_2.initial_gm',{}).get('minimum',.15))),
                gm_lashing_lower_m=0.,gm_lashing_upper_m=float(h.max_gm_lashing_m),
                tanks=tanks,weather_applicability_coupling='KGphysical <= KGvirtual <= 1.5*d_stability',
                finite_angle_area_envelope='not implemented; no finite-angle proof claim')


def _width_min(polygon,lo,hi):
    """Exact minimum horizontal waterline length on a polygon, all heights.

    Between adjacent vertex heights every active edge intersection is affine
    in height; their sorted order is fixed for a simple polygon. Sum wet
    segment lengths and evaluate both limiting endpoints of every piece.
    A minimum of zero at a jump remains safely zero. All arithmetic is exact
    rational arithmetic on the pinned polygon's binary input coordinates.
    """
    p = [(Fraction(float(y)),Fraction(float(z))) for y,z in polygon]
    lo,hi = Fraction(lo),Fraction(hi)
    if hi < lo:
        raise ValueError('empty height interval')
    knots = sorted({lo,hi,*[z for _,z in p if lo < z < hi]})
    if lo == hi:
        # Evaluate both adjacent limits by a small exact interval whose cuts
        # include lo. This only weakens the result at horizontal topology cuts.
        eps = Fraction(1,10**12)
        return _width_min(polygon,lo-eps,hi+eps)
    minimum = None
    for a,b in zip(knots,knots[1:]):
        mid = (a+b)/2
        edges = []
        for (y,z),(v,w) in zip(p,p[1:]+p[:1]):
            if min(z,w) < mid < max(z,w):
                slope = (v-y)/(w-z)
                edges.append((y+(mid-z)*slope,y-z*slope,slope))
        edges.sort()
        if len(edges)%2:
            raise ValueError('polygon waterline has odd intersections')
        intercept = sum((edges[j+1][1]-edges[j][1] for j in range(0,len(edges),2)),Fraction(0))
        slope = sum((edges[j+1][2]-edges[j][2] for j in range(0,len(edges),2)),Fraction(0))
        value = min(intercept+slope*a,intercept+slope*b)
        minimum = value if minimum is None else min(minimum,value)
    return max(Fraction(0),minimum or Fraction(0))


def _clipped_area(polygon,zmax):
    p = [(Fraction(float(y)),Fraction(float(z))) for y,z in polygon]
    zmax = Fraction(zmax)
    q = []
    for a,b in zip(p,p[1:]+p[:1]):
        da,db = zmax-a[1],zmax-b[1]
        if da >= 0:
            q.append(a)
        if (da >= 0) != (db >= 0):
            t = da/(da-db)
            q.append((a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])))
    if len(q) < 3:
        return Fraction(0)
    area = sum((a[0]*b[1]-b[0]*a[1] for a,b in zip(q,q[1:]+q[:1])),Fraction(0))/2
    if area < 0:
        raise ValueError('clockwise hull polygon')
    return area


def _depth_prime_exact(hull,params):
    """The checker's coaming-depth arithmetic, exactly on binary inputs."""
    F = Fraction
    def number(value):
        return F(float(value['value'] if isinstance(value,dict) else value))
    boxes = [{k:number(v) for k,v in box.items() if k.endswith('_m')}
             for box in params['envelope'].get('hatch_coamings',[])]
    L,B,D = map(F,(hull.lpp_m,hull.beam_m,hull.depth_m))
    lo,hi = L/4,3*L/4
    breaks = sorted({lo,hi,*[max(lo,min(hi,b[k])) for b in boxes
                             for k in ('x_min_m','x_max_m')]})
    length,width_length,height_length = F(0),F(0),F(0)
    for a,z in zip(breaks,breaks[1:]):
        active = [b for b in boxes if b['x_min_m'] < (a+z)/2 < b['x_max_m']]
        if not active:
            continue
        unions = []
        for u,v in sorted((b['y_min_m'],b['y_max_m']) for b in active):
            if unions and u <= unions[-1][1]:
                unions[-1][1] = max(unions[-1][1],v)
            else:
                unions.append([u,v])
        width = sum((v-u for u,v in unions),F(0))
        heights = {b['z_max_m']-b['z_min_m'] for b in active}
        if len(heights) != 1:
            raise ValueError('unequal coaming heights')
        length += z-a
        width_length += width*(z-a)
        height_length += heights.pop()*(z-a)
    if not length:
        return D
    b,h = width_length/length,height_length/length
    return D+h*(2*b-B)/B*(2*length/L)


def righting_area_universal_upper(instance):
    """All-pose finite-angle lever/area upper bounds, deliberately very weak.

    A buoyancy centroid lies inside the convex hull of the declared polygon
    vertices. An upright mass CG lies inside its positive mass-point envelope.
    Cauchy--Schwarz bounds their projection onto any unit heeling axis by the
    enclosing diagonal. Liquid-transfer projection can change by no more than
    tank diagonal times liquid mass (or conservative consumable envelope mass).
    This avoids any assumed finite-angle trim bound or sampled KN envelope.
    """
    from stowage.checker.visibility import _fixed_loads
    F = Fraction
    h = instance.vessel.hull
    geometry,params,_ = hull_reference(instance)
    def number(value):
        return F(float(value['value'] if isinstance(value,dict) else value))
    hull_x = [F(float(x)) for x in geometry.x_m]
    hull_y = [F(float(p[0])) for ps in geometry.sections for poly in ps for p in poly]
    hull_z = [F(float(p[1])) for ps in geometry.sections for poly in ps for p in poly]
    mass_x = [F(h.lightship_lcg_m)]
    mass_z = [F(h.lightship_vcg_m)]
    fixed = _fixed_loads(instance.vessel)
    for load in fixed:
        if load.mass_kg:
            mass_x.append(F(load.x_m)); mass_z.append(F(load.z_m))
    mass_x.extend(F(c.lcg_m) for c in instance.vessel.cells)
    mass_z.extend(F(s.base_z_m) for s in instance.vessel.stacks)
    mass_z.extend(F(s.base_z_m)+F(s.max_height_m) for s in instance.vessel.stacks)
    rho = {t.id:F(t.content_density_kg_m3) for t in instance.vessel.tanks}
    liquid_projection_moment_upper = F(0)
    for tank in params.get('tanks',[]):
        if tank['id'] not in rho:
            continue
        g = tank.get('geometry',tank)
        lo = [number(g[f'{a}_min_m']) for a in 'xyz']
        hi = [number(g[f'{a}_max_m']) for a in 'xyz']
        mass_x.extend((lo[0],hi[0])); mass_z.extend((lo[2],hi[2]))
        extents = [b-a for a,b in zip(lo,hi)]
        # The prism encloses hull-section tanks and therefore upper-bounds
        # every variable-consumable envelope volume as well as actual fill.
        prism_volume = extents[0]*extents[1]*extents[2]
        diagonal = _sqrt_upper(sum((v*v for v in extents),F(0)))
        liquid_projection_moment_upper += prism_volume*rho[tank['id']]*diagonal
    dx = max(abs(min(hull_x)-max(mass_x)),abs(max(hull_x)-min(mass_x)))
    dy = max(map(abs,hull_y))  # source raw curve uses reference TCG zero
    dz = max(abs(min(hull_z)-max(mass_z)),abs(max(hull_z)-min(mass_z)))
    raw_lever_upper = _sqrt_upper(dx*dx+dy*dy+dz*dz)
    boxes = {b.id:b for b in instance.containers}
    mass_lower = (F(h.lightship_mass_kg)+sum((F(q.mass_kg) for q in fixed),F(0))
                  +sum((F(boxes[bid].gross_mass_kg) for bid in instance.load_list),F(0)))
    if mass_lower <= 0:
        raise ValueError('nonpositive total-mass lower bound')
    correction_upper = liquid_projection_moment_upper/mass_lower
    # Source subtracts abs(TCG)*across_y, nonnegative on [0,pi/2], so ignoring
    # it only increases this upper bound. Method-2 FSC*sin is nonnegative too.
    lever = raw_lever_upper+correction_upper
    lever_upper = _up(lever)*(1.+1e-10)
    return dict(gz_universal_upper_m=lever_upper,
                raw_projection_upper_m=_up(raw_lever_upper),
                liquid_projection_correction_upper_m=_up(correction_upper),
                total_area_universal_upper_mrad=lever_upper*math.nextafter(math.pi/2,math.inf),
                area_0_40_universal_upper_mrad=lever_upper*math.nextafter(math.radians(40),math.inf),
                strength='universal geometric enclosure; intentionally loose')


def second_check_envelope(instance, *, draft_lower=None,draft_upper=None,trim_lower=None,trim_upper=None):
    """Certified geometric upper bound on the second-check form factor C.

    This returns a necessary *target* for righting area, not an upper GZ
    envelope. The finite-angle curve, free-trim and liquid-transfer correction
    remain relaxed. No sampled hydrostatic tangent supplies a proof here.
    """
    bounds = physical_bounds(instance)
    h = instance.vessel.hull
    if h.lpp_m <= 100:
        return dict(active=False)
    geometry,params,_ = hull_reference(instance)
    F = Fraction
    dlo = F(bounds['draft_stability_lower_m'] if draft_lower is None else draft_lower)
    dhi = F(bounds['draft_stability_upper_m'] if draft_upper is None else draft_upper)
    tlo = F(bounds['trim_lower_m'] if trim_lower is None else trim_lower)
    thi = F(bounds['trim_upper_m'] if trim_upper is None else trim_upper)
    if not 0 < dlo <= dhi or tlo > thi:
        raise ValueError('invalid draft/trim interval')
    L = F(h.lpp_m)
    area_lower,volume_upper = F(0),F(0)
    breadth_lower = F(0)
    for x,w,polygons in zip(geometry.x_m,geometry.integration_weights_m,geometry.sections):
        arm = F(.5)-F(float(x))/L
        local_lo = dlo+min(arm*tlo,arm*thi)
        local_hi = dhi+max(arm*tlo,arm*thi)
        # ta and tf individually cannot exceed the legal draft upper. For
        # stations between perpendiculars, their interpolation cannot either.
        if 0 <= x <= h.lpp_m:
            local_hi = min(local_hi,dhi)
        bare = polygons[0]
        area_lower += F(float(w))*_width_min(bare,local_lo,local_hi)
        breadth_lower = max(breadth_lower,_width_min(bare,dlo/2,dhi/2))
        for polygon in polygons:
            volume_upper += F(float(w))*_clipped_area(polygon,local_hi)
    if area_lower <= 0 or breadth_lower <= 0:
        return dict(active=True,status='unknown',reason='zero outward waterplane/breadth lower bound')
    # C = dp*V^2/(bm^2*Awp^2*d) * sqrt(d/max(KGv,d))*sqrt(100/L).
    # The KG factor is at most 1; use upper dp rounded outwards. Polygon
    # waterplane area includes a trim Jacobian >=1, so horizontal widths are
    # a valid area lower bound. No displacement equality is assumed here.
    dp = _depth_prime_exact(h,params)
    C = dp*volume_upper**2/(breadth_lower**2*area_lower**2*dlo)*_sqrt_upper(F(100)/L)
    cupper = _up(C)*(1.+1e-10)
    universal = righting_area_universal_upper(instance)
    return dict(active=True,status='bounded',form_factor_upper=cupper,
                waterplane_area_lower_m2=_down(area_lower),
                hull_breadth_at_half_draft_lower_m=_down(breadth_lower),
                upright_volume_upper_m3=_up(volume_upper),
                draft_lower_m=float(dlo),draft_upper_m=float(dhi),
                trim_lower_m=float(tlo),trim_upper_m=float(thi),
                required_area_0_30_lower_mrad=.009/cupper,
                required_area_0_40_lower_mrad=.016/cupper,
                required_area_30_40_lower_mrad=.006/cupper,
                required_total_area_lower_mrad=.029/cupper,
                finite_angle_area_upper=universal)

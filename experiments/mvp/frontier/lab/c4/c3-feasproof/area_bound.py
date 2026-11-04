"""Finite-angle outward polygon/centroid envelopes for the actual GZ area.

This module bounds buoyancy moments of every pose in a box, rather than fitting
a GM surrogate or sampling a reference KN curve. Native cubic hull/liquid
providers are separate: direct-only results are explicitly ineligible as an
unchanged-checker infeasibility certificate. See area_notes.md.
"""
from __future__ import annotations

try:
    # Optional experiment-local accelerator installed under C4/vendor by the
    # executive. mpq(float) preserves the exact binary input, like Fraction.
    from gmpy2 import mpq as F
    RATIONAL_BACKEND = 'gmpy2.mpq'
except ImportError:
    from fractions import Fraction as F
    RATIONAL_BACKEND = 'fractions.Fraction'
from functools import lru_cache
import heapq
import math


RESIDUAL_TOLERANCE = 2.e-6


def _down(value):
    value = F(value)
    result = float(value)
    return math.nextafter(result,-math.inf) if F(result) > value else result


def _up(value):
    value = F(value)
    result = float(value)
    return math.nextafter(result,math.inf) if F(result) < value else result


def _iv(lo,hi=None):
    return (float(lo),float(lo if hi is None else hi))


def _add(a,b):
    return _down(F(a[0])+F(b[0])),_up(F(a[1])+F(b[1]))


def _neg(a):
    return -a[1],-a[0]


def _sub(a,b):
    return _add(a,_neg(b))


def _mul(a,b):
    values = [F(x)*F(y) for x in a for y in b]
    return _down(min(values)),_up(max(values))


def _div(a,b):
    if b[0] <= 0 <= b[1]:
        return (-math.inf,math.inf)
    values = [F(x)/F(y) for x in a for y in b]
    return _down(min(values)),_up(max(values))


def _square(a):
    if a[0] <= 0 <= a[1]:
        return 0.,_up(max(F(a[0])**2,F(a[1])**2))
    return _down(min(F(a[0])**2,F(a[1])**2)),_up(max(F(a[0])**2,F(a[1])**2))


def _sqrt(a):
    low = math.sqrt(max(0.,a[0])); high = math.sqrt(max(0.,a[1]))
    while F(low)**2 > F(max(0.,a[0])):
        low = math.nextafter(low,-math.inf)
    while F(high)**2 < F(max(0.,a[1])):
        high = math.nextafter(high,math.inf)
    return low,high


def _abs_upper(a):
    return max(abs(a[0]),abs(a[1]))


@lru_cache(maxsize=16384)
def _sin_point(x):
    """Alternating Taylor enclosure on [-pi/2,pi/2], using exact rationals."""
    sign = -1 if x < 0 else 1
    x = abs(F(x)); term = x; total = x; lower = F(0); upper = x
    for j in range(1,32):
        term *= -x*x/F((2*j)*(2*j+1))
        total += term
        if j%2:
            lower = total
        else:
            upper = total
        if j > 8 and abs(term) < F(1,10**28):
            break
    return (_down(lower),_up(upper)) if sign > 0 else (-_up(upper),-_down(lower))


@lru_cache(maxsize=16384)
def _cos_point(x):
    x = abs(F(x)); term = F(1); total = F(1); lower = F(0); upper = F(1)
    for j in range(1,32):
        term *= -x*x/F((2*j-1)*(2*j))
        total += term
        if j%2:
            lower = total
        else:
            upper = total
        if j > 8 and abs(term) < F(1,10**28):
            break
    return max(0.,_down(lower)),min(1.,_up(upper))


def angle_intervals(angle,*,nonnegative=False):
    lo,hi = map(float,angle)
    if lo > hi or lo < -math.pi/2-1.e-10 or hi > math.pi/2+1.e-10:
        raise ValueError('angle interval must lie in [-pi/2,pi/2]')
    sine = max(-1.,_sin_point(lo)[0]),min(1.,_sin_point(hi)[1])
    # A tiny beyond-endpoint floating domain extension covers true pi/2.
    if lo <= -math.pi/2:
        sine = (-1.,sine[1])
    if hi >= math.pi/2:
        sine = (sine[0],1.)
    cosine = (min(_cos_point(lo)[0],_cos_point(hi)[0]),
              1. if lo <= 0 <= hi else max(_cos_point(lo)[1],_cos_point(hi)[1]))
    if nonnegative and lo < -1.e-14:
        raise ValueError('nonnegative heel required')
    return sine,cosine


def pose_axes(heel_rad,alpha_rad):
    """Full free-trim pose; alpha=atan((tf-ta)/L), never upright trim bounds."""
    sp,cp = angle_intervals(heel_rad,nonnegative=True)
    sa,ca = angle_intervals(alpha_rad)
    m = (_neg(_mul(sa,cp)),_neg(_mul(ca,sp)),_mul(ca,cp))
    norm = _sqrt(_add(_square(cp),_mul(_square(ca),_square(sp))))
    if norm[0] <= 0:
        across = ((-1.,1.),(0.,1.),(0.,1.))
    else:
        bx = _div(_neg(_mul(_mul(sa,ca),sp)),norm)
        by = _div(cp,norm)
        bz = _div(_mul(_square(ca),sp),norm)
        across = ((max(-1.,bx[0]),min(1.,bx[1])),
                  (max(0.,by[0]),min(1.,by[1])),
                  (max(0.,bz[0]),min(1.,bz[1])))
    return dict(normal=m,normal_norm=norm,along=(ca,sa),across=across,
                sin_heel=sp,cos_heel=cp,sin_alpha=sa,cos_alpha=ca)


def _clip(poly,a,b,c):
    """Exact rational clipping by a*y+b*z <= c."""
    if not poly:
        return []
    values = [c-a*y-b*z for y,z in poly]
    if min(values) >= 0:
        return poly
    if max(values) < 0:
        return []
    result = []
    for p,q,dp,dq in zip(poly,poly[1:]+poly[:1],values,values[1:]+values[:1]):
        if dp >= 0:
            result.append(p)
        if (dp >= 0) != (dq >= 0):
            t = dp/(dp-dq)
            result.append((p[0]+t*(q[0]-p[0]),p[1]+t*(q[1]-p[1])))
    return result


def _moments(poly):
    if len(poly) < 3:
        return F(0),F(0),F(0)
    area,my,mz = F(0),F(0),F(0)
    for p,q in zip(poly,poly[1:]+poly[:1]):
        cross = p[0]*q[1]-q[0]*p[1]
        area += cross; my += (p[0]+q[0])*cross; mz += (p[1]+q[1])*cross
    area /= 2; my /= 6; mz /= 6
    if area < 0:
        raise ValueError('clockwise hull polygon')
    return area,my,mz


def _number(value):
    return float(value['value'] if isinstance(value,dict) else value)


def _covers_heel_domain(intervals):
    """Exact binary endpoint coverage of the native closed heel domain."""
    covered = 0.
    for lo,hi in sorted(intervals):
        if lo > hi or lo > covered:
            return False
        covered = max(covered,hi)
        if covered >= math.pi/2:
            return True
    return False


class AreaOracle:
    """Prepared exact hull parts and physical tank enclosures, no checker run."""
    def __init__(self,instance,*,density_kg_m3=None,geometry=None,params=None):
        from stowage.checker import stability
        from stowage.frozen import active_stubs
        if active_stubs():
            raise ValueError('active frozen-interface override')
        self.instance = instance
        self.geometry,self.params = (stability._context(instance.vessel) if geometry is None else (geometry,params))
        if self.params is None:
            raise ValueError('raw parameter tree required with custom geometry')
        self.rho = float(instance.port.water_density_kg_m3 if density_kg_m3 is None else density_kg_m3)
        if not math.isfinite(self.rho) or self.rho <= 0:
            raise ValueError('positive finite phase density required')
        self.L = float(self.geometry.lpp_m)
        self.parts = []
        coordinates = []
        for xx,ww,polygons in zip(self.geometry.x_m,self.geometry.integration_weights_m,self.geometry.sections):
            x,w = F(float(xx)),F(float(ww))
            if w <= 0:
                raise ValueError('positive hull quadrature weights required')
            for polygon in polygons:
                p = [(F(float(y)),F(float(z))) for y,z in polygon]
                coordinates.extend((x,y,z) for y,z in p)
                # Each sign quadrant makes the coefficient-wise min/max
                # linear. Therefore one inner/outer halfplane encloses every
                # uncertain plane within the coefficient box, exactly.
                for sy in (-1,1):
                    py = _clip(p,F(-sy),F(0),F(0))
                    for sz in (-1,1):
                        part = _clip(py,F(0),F(-sz),F(0))
                        moments = _moments(part)
                        if moments[0] > 0:
                            self.parts.append((x,w,sy,sz,part,moments))
        self.bounds = [(min(p[k] for p in coordinates),max(p[k] for p in coordinates)) for k in range(3)]
        self.full_volume = sum((w*m[0] for x,w,sy,sz,poly,m in self.parts),F(0))
        self.integral_queries = 0
        raw = {t['id']:t for t in self.params.get('tanks',[])}
        self.tanks = []
        for tank in instance.vessel.tanks:
            record = raw[tank.id]; g = record.get('geometry',record)
            lows = [_number(g[f'{a}_min_m']) for a in 'xyz']
            highs = [_number(g[f'{a}_max_m']) for a in 'xyz']
            self.tanks.append(dict(id=tank.id,kind=tank.kind,
                shape=g.get('shape','rectangular_prism'),capacity_kg=tank.capacity_m3*tank.content_density_kg_m3,
                extents=[b-a for a,b in zip(lows,highs)]))

    def offset_domain(self,heel_rad,alpha_rad):
        normal = pose_axes(heel_rad,alpha_rad)['normal']
        projections = [_mul(c,(_down(lo),_up(hi))) for c,(lo,hi) in zip(normal,self.bounds)]
        value = _add(_add(projections[0],projections[1]),projections[2])
        return value

    def direct_integral_bounds(self,heel_rad,alpha_rad,offset_m,*,only_volume=False):
        """Definite/possible wet shell enclosure for volume and first moments."""
        self.integral_queries += 1
        normal = pose_axes(heel_rad,alpha_rad)['normal']
        nx,ny,nz = [(F(lo),F(hi)) for lo,hi in normal]
        hlo,hhi = map(F,offset_m)
        low = [F(0)]*4; high = [F(0)]*4
        for x,w,sy,sz,poly,full in self.parts:
            px = sorted((nx[0]*x,nx[1]*x))
            # Outer set: min possible n.y*y+n.z*z <= max possible offset.
            outer_polygon = _clip(poly,ny[0 if sy > 0 else 1],nz[0 if sz > 0 else 1],hhi-px[0])
            inner_polygon = _clip(poly,ny[1 if sy > 0 else 0],nz[1 if sz > 0 else 0],hlo-px[1])
            outer = full if outer_polygon is poly else _moments(outer_polygon)
            inner = full if inner_polygon is poly else _moments(inner_polygon)
            vl,vu = w*inner[0],w*outer[0]
            low[0] += vl; high[0] += vu
            low[1] += min(x*vl,x*vu); high[1] += max(x*vl,x*vu)
            for k,sign in ((2,sy),(3,sz)):
                ql,qu = w*inner[k-1],w*outer[k-1]
                low[k] += ql if sign > 0 else qu
                high[k] += qu if sign > 0 else ql
        result = {name:(_down(a),_up(b)) for name,a,b in zip(('volume','mx','my','mz'),low,high)}
        result.update(physical_outward=True,checker_outward=False,
                      arithmetic='exact rational clip and shoelace on native polygon/quadrature inputs',
                      normal_coefficients=[list(v) for v in normal],offset_m=list(offset_m))
        return result

    def mass_volume_window(self,mass_kg):
        return (_down(F(mass_kg[0])/F(self.rho)*(1-F(RESIDUAL_TOLERANCE))),
                _up(F(mass_kg[1])/F(self.rho)*(1+F(RESIDUAL_TOLERANCE))))

    def contract_offset(self,heel_rad,alpha_rad,offset_m,*,mass_kg,steps=4,integral_provider=None):
        """Discard whole heave halves by volume enclosures, no monotonicity fit.

        Direct wet volume is monotone, but native cubics need not be. Querying
        entire half intervals also proves the contraction for a correctly
        unioned nonmonotone native interpolation provider.
        """
        lo,hi = map(float,offset_m)
        target = self.mass_volume_window(mass_kg)
        queries = 0
        checker = integral_provider is not None
        def bounds(offset):
            nonlocal queries,checker
            direct = self.direct_integral_bounds(heel_rad,alpha_rad,offset,only_volume=True)
            queries += 1
            result = direct if integral_provider is None else integral_provider(heel_rad,alpha_rad,offset,direct)
            checker &= bool(result.get('checker_outward',False))
            return result
        for repeat in range(max(0,int(steps))):
            old = lo,hi
            mid = (lo+hi)/2
            lower = bounds((lo,mid))
            if lower['volume'][1] < target[0]:
                lo = mid
            mid = (lo+hi)/2
            upper = bounds((mid,hi))
            if upper['volume'][0] > target[1]:
                hi = mid
            if (lo,hi) == old:
                break
        return dict(offset_m=(lo,hi),integral_queries=queries,
                    physical_outward=True,checker_outward=checker)

    def tank_envelopes(self,tank_masses_kg,mass_kg,axes):
        """Signed direct-liquid lower correction and callback CG movement."""
        supplied = tank_masses_kg or {}
        mass_low = float(mass_kg[0])
        if mass_low <= 0:
            raise ValueError('positive phase mass lower endpoint required')
        dx,dz,negative = F(0),F(0),F(0)
        components = []
        from stowage.checker.stability_loading import CONSUMABLE_KINDS
        ca,sa,sp,N = axes['cos_alpha'],axes['sin_alpha'],axes['sin_heel'],axes['normal_norm']
        for tank in self.tanks:
            interval = supplied.get(tank['id'],(0.,tank['capacity_kg']))
            if isinstance(interval,(int,float)):
                interval = (float(interval),float(interval))
            mlo,mhi = map(float,interval)
            if not 0 <= mlo <= mhi <= tank['capacity_kg']*(1+1.e-12):
                raise ValueError('tank interval outside declared physical capacity')
            xspan,yspan,zspan = tank['extents']
            dx += F(mhi)*F(xspan)/F(mass_low)
            dz += F(mhi)*F(zspan)/F(mass_low)
            if tank['kind'] in CONSUMABLE_KINDS and tank['shape'] == 'hull_sections':
                # max transfer over the whole fill range includes the empty
                # or full zero-transfer endpoint. This mathematical lower
                # bound is not silently extended to a native cubic table.
                components.append(dict(tank=tank['id'],correction_lower_m=0.,reason='whole-fill direct maximum includes zero'))
                continue
            correction_mass = tank['capacity_kg'] if tank['kind'] in CONSUMABLE_KINDS else mhi
            general = sum((F(_abs_upper(v))*F(span) for v,span in zip(axes['across'],tank['extents'])),F(0))
            if ca[0] > 0 and sp[0] > 0:
                variational = F(N[1])*F(_abs_upper(sa))/F(ca[0])/F(sp[0])*F(xspan)
                lever = min(general,variational)
            else:
                lever = general
            amount = F(correction_mass)/F(mass_low)*lever
            negative += amount
            components.append(dict(tank=tank['id'],correction_lower_m=-_up(amount),
                                   reason='signed potential/bounding-box enclosure'))
        return dict(callback_delta_x_abs_m=_up(dx),callback_delta_z_m=(0.,_up(dz)),
                    correction_lower_m=-_up(negative),components=components,
                    physical_outward=True,checker_outward=False)

    def classify_pose(self,heel_rad,alpha_rad,offset_m,*,mass_kg,kg_m,lcg_m,
                      tank_masses_kg=None,integral_provider=None,correction_lower_provider=None):
        """Retain or refute a complete pose box using accepted residual bands.

        integral_provider(phi,alpha,offset,direct) must UNION native-table and
        direct fields. correction_lower_provider(phi,alpha,tanks,direct_lower)
        must lower-bound the native/direct signed correction, not its absolute
        value. Each provider explicitly supplies checker_outward=True only
        after covering its native interpolation and floating arithmetic.
        """
        axes = pose_axes(heel_rad,alpha_rad)
        integrals = self.direct_integral_bounds(heel_rad,alpha_rad,offset_m)
        if integral_provider is not None:
            integrals = integral_provider(heel_rad,alpha_rad,offset_m,integrals)
        desired = self.mass_volume_window(mass_kg)
        volume = (max(integrals['volume'][0],math.nextafter(desired[0],-math.inf)),
                  min(integrals['volume'][1],math.nextafter(desired[1],math.inf)))
        common = dict(heel_rad=list(heel_rad),alpha_rad=list(alpha_rad),offset_m=list(offset_m),
                      physical_outward=True,integrals=integrals)
        if volume[0] > volume[1] or volume[1] <= 0:
            return dict(common,possible=False,reason='mass residual interval disjoint',gz_upper_m=-math.inf,
                        checker_outward=bool(integrals.get('checker_outward',False)))
        if volume[0] <= 0:
            raise ValueError('nonpositive intersected mass/volume interval')
        B = [_div(integrals[name],volume) for name in ('mx','my','mz')]
        # Cap only physical/direct intervals. A cubic native interpolant can
        # overshoot the convex hull and must not be silently clamped here.
        if not integrals.get('native_union',False):
            B = [(max(a,_down(lo)),min(b,_up(hi))) for (a,b),(lo,hi) in zip(B,self.bounds)]
        liquids = self.tank_envelopes(tank_masses_kg,mass_kg,axes)
        corrected = None
        callback_certified = integral_provider is None
        if correction_lower_provider is not None:
            corrected = correction_lower_provider(heel_rad,alpha_rad,tank_masses_kg,liquids['correction_lower_m'])
            callback_certified = bool(corrected.get('callback_physical_enclosed',False))
            if 'callback_delta_x_m' in corrected and 'callback_delta_z_m' in corrected:
                liquids['callback_delta_x_abs_m'] = max(liquids['callback_delta_x_abs_m'],_abs_upper(corrected['callback_delta_x_m']))
                liquids['callback_delta_z_m'] = (min(liquids['callback_delta_z_m'][0],corrected['callback_delta_z_m'][0]),
                                                 max(liquids['callback_delta_z_m'][1],corrected['callback_delta_z_m'][1]))
                callback_certified = bool(corrected.get('callback_enclosure_outward',False))
        cx = (math.nextafter(lcg_m[0]-liquids['callback_delta_x_abs_m'],-math.inf),
              math.nextafter(lcg_m[1]+liquids['callback_delta_x_abs_m'],math.inf))
        cz = _add(kg_m,liquids['callback_delta_z_m'])
        residual = _add(_mul(_sub(B[0],cx),axes['along'][0]),
                        _mul(_sub(B[2],cz),axes['along'][1]))
        residual_allowance = math.nextafter(RESIDUAL_TOLERANCE*self.L+1.e-10,math.inf)
        if callback_certified and (residual[0] > residual_allowance or residual[1] < -residual_allowance):
            return dict(common,possible=False,reason='longitudinal residual interval disjoint',
                        residual_m=list(residual),gz_upper_m=-math.inf,
                        checker_outward=bool(integrals.get('checker_outward',False) and corrected is not None
                                             and corrected.get('callback_enclosure_outward',corrected.get('callback_physical_enclosed',False))))
        raw = _add(_add(_mul(_sub(B[0],lcg_m),axes['across'][0]),
                        _mul(B[1],axes['across'][1])),
                   _mul(_sub(B[2],kg_m),axes['across'][2]))
        raw_upper = raw[1]
        # Eliminate the poorly bounded longitudinal buoyancy coordinate using
        # the actual callback equilibrium, while preserving its liquid shift.
        # Native liquid CG interpolation may violate deltaZ>=0; reduced form
        # is physical-only unless the correction provider explicitly certifies
        # its callback movement as well.
        if axes['normal_norm'][0] > 0 and callback_certified:
            numerator = _add(_mul(axes['cos_heel'],B[1]),
                             _mul(axes['sin_heel'],_sub(B[2],kg_m)))
            error = (F(_abs_upper(axes['sin_alpha']))*F(axes['sin_heel'][1])*F(residual_allowance)
                     +F(_abs_upper(_mul(axes['sin_alpha'],axes['cos_alpha'])))*F(axes['sin_heel'][1])*F(liquids['callback_delta_x_abs_m'])
                     +F(_square(axes['sin_alpha'])[1])*F(axes['sin_heel'][1])*F(max(0.,-liquids['callback_delta_z_m'][0])))
            numerator = _add(numerator,(0.,_up(error)))
            reduced = _div(numerator,axes['normal_norm'])
            raw_upper = min(raw_upper,reduced[1])
        correction_lower = liquids['correction_lower_m'] if corrected is None else min(liquids['correction_lower_m'],float(corrected['lower_m']))
        gz_upper = math.nextafter(raw_upper-correction_lower,math.inf)
        checker = bool(integrals.get('checker_outward',False) and corrected is not None
                       and corrected.get('checker_outward',False) and callback_certified)
        return dict(common,possible=True,gz_upper_m=gz_upper,raw_gz_upper_m=raw_upper,
                    correction_lower_m=correction_lower,buoyancy_centroid_m=[list(v) for v in B],
                    volume_intersection_m3=list(volume),residual_m=list(residual),
                    residual_allowance_m=residual_allowance,liquid_envelopes=liquids,
                    checker_outward=checker)


def build_area_envelope(instance,*,mass_kg,kg_m,lcg_m,tank_masses_kg=None,
                        density_kg_m3=None,heel_step_deg=2.,max_pose_boxes=128,max_depth=12,
                        integral_provider=None,correction_lower_provider=None,oracle=None,
                        alpha_intervals=None,offset_contraction_steps=2,draught_m=None,trim_m=None,
                        heel_intervals=None):
    """Count-bounded spatial enclosure of total positive PL GZ area.

    The default alpha partition covers all finite free-trim slopes. Narrower
    caller partitions are guidance only and explicitly lose completeness.
    Every unexpanded possible pose box remains in the upper bound. No timeout
    or unresolved interval is called infeasible. Flooding/first vanishing are
    relaxed by integrating positive upper values through90 degrees.
    """
    if not 0 < heel_step_deg <= 2. or max_pose_boxes < 1 or max_depth < 0:
        raise ValueError('positive heel step<=2 and finite work counts required')
    oracle = oracle or AreaOracle(instance,density_kg_m3=density_kg_m3)
    halfpi = math.nextafter(math.pi/2,math.inf)
    default_partition = [-halfpi,-.35,-.08,-.02,0.,.02,.08,.35,halfpi]
    complete = alpha_intervals is None
    domains = list(zip(default_partition,default_partition[1:])) if complete else list(alpha_intervals)
    # Source base knots are multiples of2 degrees plus designated special
    # knots. The slabs include every such knot even at a smaller requested
    # step; each source PL segment's endpoint values are covered by the slab
    # that contains them. See the conservative neighbour dilation below.
    count = int(math.ceil(90./heel_step_deg))
    edges = [min(halfpi,math.radians(i*heel_step_deg)) for i in range(count+1)]
    edges[-1] = halfpi
    heel_domains = list(zip(edges,edges[1:])) if heel_intervals is None else list(heel_intervals)
    global_complete = complete and _covers_heel_domain(heel_domains)
    slabs = []
    all_checker = True
    initial_query_count = oracle.integral_queries
    for index,(lo,hi) in enumerate(heel_domains):
        phi = (max(0.,math.nextafter(lo,-math.inf)),min(halfpi,math.nextafter(hi,math.inf)))
        queue = []; leaves = []; examined = 0; rejected = 0; serial = 0; local_checker = True
        def assess(alpha,offset,depth,contract=False):
            nonlocal examined,rejected,serial,local_checker
            if contract and offset_contraction_steps:
                contraction = oracle.contract_offset(phi,alpha,offset,mass_kg=mass_kg,
                    steps=offset_contraction_steps,integral_provider=integral_provider)
                offset = contraction['offset_m']
                local_checker &= contraction['checker_outward']
            result = oracle.classify_pose(phi,alpha,offset,mass_kg=mass_kg,kg_m=kg_m,lcg_m=lcg_m,
                tank_masses_kg=tank_masses_kg,integral_provider=integral_provider,
                correction_lower_provider=correction_lower_provider)
            examined += 1
            local_checker &= bool(result.get('checker_outward',False))
            if not result['possible']:
                rejected += 1
                return
            serial += 1
            if depth >= max_depth or result['gz_upper_m'] <= 0:
                leaves.append(result)
            else:
                heapq.heappush(queue,(-result['gz_upper_m'],serial,depth,alpha,offset,result))
        for alpha in domains:
            assess(alpha,oracle.offset_domain(phi,alpha),0,contract=True)
        while queue and examined+2 <= max_pose_boxes:
            _,_,depth,alpha,offset,result = heapq.heappop(queue)
            # Match halfspace uncertainty scales: alpha moves longitudinal
            # clipping over a ship-length lever; offset moves it oneforone.
            angular = (alpha[1]-alpha[0])*oracle.L/2
            vertical = offset[1]-offset[0]
            if angular >= vertical:
                mid = (alpha[0]+alpha[1])/2
                for sub in ((alpha[0],mid),(mid,alpha[1])):
                    assess(sub,offset,depth+1,contract=True)
            else:
                mid = (offset[0]+offset[1])/2
                for sub in ((offset[0],mid),(mid,offset[1])):
                    assess(alpha,sub,depth+1)
        leaves.extend(item[-1] for item in queue)
        upper = max((r['gz_upper_m'] for r in leaves),default=0.)
        checker = local_checker and all(r.get('checker_outward',False) for r in leaves)
        all_checker &= checker
        governing = max(leaves,key=lambda r:r['gz_upper_m']) if leaves else None
        slabs.append(dict(heel_rad=[lo,hi],gz_upper_m=max(0.,upper),
                          pose_boxes_examined=examined,pose_boxes_rejected=rejected,
                          surviving_boxes=len(leaves),checker_outward=checker,
                          governing_pose=governing))
    # The source integrates its piecewise-linear knot curve, not the exact
    # continuous curve. A segment can cross our smaller slab boundaries. The
    # maximum of all physical slab envelopes within one source2degree span
    # covers both segment endpoints and therefore its entire linear segment.
    enlarged = []
    source_span = math.nextafter(math.radians(2.),math.inf)
    for i,slab in enumerate(slabs):
        lo,hi = slab['heel_rad']
        enlarged.append(max(s['gz_upper_m'] for s in slabs
                            if s['heel_rad'][1] >= lo-source_span and s['heel_rad'][0] <= hi+source_span))
    area = F(0)
    for slab,upper in zip(slabs,enlarged):
        slab['pl_gz_upper_m'] = upper
        area += F(upper)*(F(slab['heel_rad'][1])-F(slab['heel_rad'][0]))
    return dict(status=('guidance_subdomain' if not global_complete else ('bounded_direct' if not all_checker else 'bounded_checker')),
                area_upper_mrad=_up(area),metric='containership_area_0_flood_mrad',
                physical_outward=global_complete,checker_outward=bool(all_checker and global_complete),
                full_free_trim_domain=complete,slab_bounds=slabs,
                full_heel_domain=_covers_heel_domain(heel_domains),
                phase_inputs=dict(mass_kg=list(mass_kg),kg_m=list(kg_m),lcg_m=list(lcg_m),
                                  density_kg_m3=oracle.rho,tank_masses_kg=tank_masses_kg,
                                  upright_draught_m=draught_m,upright_trim_m=trim_m),
                residual_tolerance=RESIDUAL_TOLERANCE,
                work_limits=dict(max_pose_boxes_per_heel_slab=max_pose_boxes,max_depth=max_depth,
                                 offset_contraction_steps=offset_contraction_steps),
                direct_integral_queries=oracle.integral_queries-initial_query_count,
                rational_backend=RATIONAL_BACKEND,
                limitations=['flooding and first vanishing relaxed to positive-part integration through90degrees',
                             'unexpanded pose boxes retained in upper envelope',
                             'direct-only bounds cannot refute the default native table checker',
                             'native hull and liquid providers must enclose interpolation and arithmetic uniformly'])


def build_knot_area_envelope(instance,*,mass_kg,kg_m,lcg_m,tank_masses_kg=None,
                             density_kg_m3=None,heel_step_deg=2.,max_pose_boxes=128,max_depth=12,
                             integral_provider=None,correction_lower_provider=None,oracle=None,
                             alpha_intervals=None,offset_contraction_steps=2,draught_m=None,trim_m=None,
                             flood_pose_boxes=None,knot_radius_rad=1.e-12):
    """Bound the actual native PL area from tight exact-knot pose envelopes.

    Nonnegative upper endpoints cover every zero-valued inserted vanishing
    root. The only arbitrary positive extra knot is the flooding endpoint;
    charge a SINGLE maximum gap bonus, not a whole uncertain heel slab at
    every knot. Its upper comes from complete pose boxes over that gap.
    The old whole-slab method remains available as an independent outer lane.
    """
    if heel_step_deg != 2.:
        raise ValueError('knot mode follows the native fixed two-degree grid')
    if knot_radius_rad < 0:
        raise ValueError('nonnegative knot roundoff radius required')
    oracle = oracle or AreaOracle(instance,density_kg_m3=density_kg_m3)
    step = math.radians(2.)
    end = math.radians(89.9)
    # np.arange(0,end,step) uses the same native binary step. Designated
    # degree values and89.9 are all fixed possible source end/special knots.
    knots = sorted({*[i*step for i in range(45) if i*step < end],
                    *[math.radians(a) for a in (25.,30.,35.,40.,50.,89.9)]})
    queries = [(max(0.,p-knot_radius_rad),min(math.pi/2,p+knot_radius_rad)) for p in knots]
    kwargs = dict(mass_kg=mass_kg,kg_m=kg_m,lcg_m=lcg_m,tank_masses_kg=tank_masses_kg,
                  density_kg_m3=density_kg_m3,heel_step_deg=2.,max_depth=max_depth,
                  integral_provider=integral_provider,correction_lower_provider=correction_lower_provider,
                  oracle=oracle,alpha_intervals=alpha_intervals,
                  offset_contraction_steps=offset_contraction_steps,draught_m=draught_m,trim_m=trim_m)
    point_result = build_area_envelope(instance,max_pose_boxes=max_pose_boxes,heel_intervals=queries,**kwargs)
    point_bounds = [max(0.,r['gz_upper_m']) for r in point_result['slab_bounds']]
    base = sum((F(b-a)*(F(ua)+F(ub))/2 for a,b,ua,ub in
                zip(knots,knots[1:],point_bounds,point_bounds[1:])),F(0))
    gaps = list(zip(knots,knots[1:]))
    flood_budget = max(8,min(max_pose_boxes,32)) if flood_pose_boxes is None else int(flood_pose_boxes)
    flood_result = build_area_envelope(instance,max_pose_boxes=flood_budget,heel_intervals=gaps,**kwargs)
    bonuses = []
    for i,((a,b),row) in enumerate(zip(gaps,flood_result['slab_bounds'])):
        # The partial final source trapezoid can occupy at most this entire
        # gap. Subtract the nonnegative next-knot upper already credited by
        # the full-gap baseline; all other future baseline areas only help.
        excess_height = max(0.,row['gz_upper_m']-point_bounds[i+1])
        bonus = F(b-a)*F(excess_height)/2
        bonuses.append(dict(heel_rad=[a,b],gz_gap_upper_m=row['gz_upper_m'],
                            next_knot_upper_m=point_bounds[i+1],extra_area_upper_mrad=_up(bonus),
                            pose_bound=row))
    flood_bonus = max((F(r['extra_area_upper_mrad']) for r in bonuses),default=F(0))
    # This reserve concerns source knot-location/linear-integration arithmetic,
    # not an empirical hull/table qualification error. Default checker proof
    # remains disabled pending a uniform direct-arithmetic enclosure.
    pl_reserve = F(1.e-8)
    total = _up(base+flood_bonus+pl_reserve)
    complete = alpha_intervals is None
    all_provider_flags = all(r.get('checker_outward',False) for r in point_result['slab_bounds']+flood_result['slab_bounds'])
    return dict(status='bounded_knot_direct' if complete else 'guidance_knot_subdomain',
                metric='containership_area_0_flood_mrad',area_upper_mrad=total,
                base_positive_knot_area_upper_mrad=_up(base),flood_endpoint_bonus_upper_mrad=_up(flood_bonus),
                pl_arithmetic_reserve_mrad=float(pl_reserve),physical_outward=complete,
                checker_outward=False,provider_checker_flags=all_provider_flags,
                full_free_trim_domain=complete,
                knots=[dict(heel_rad=p,gz_upper_m=u,pose_bound=r) for p,u,r in
                       zip(knots,point_bounds,point_result['slab_bounds'])],
                flood_gap_bounds=bonuses,phase_inputs=point_result['phase_inputs'],
                residual_tolerance=RESIDUAL_TOLERANCE,
                direct_integral_queries=point_result['direct_integral_queries']+flood_result['direct_integral_queries'],
                rational_backend=RATIONAL_BACKEND,
                work_limits=dict(max_pose_boxes_per_knot=max_pose_boxes,max_pose_boxes_per_flood_gap=flood_budget,
                                 max_depth=max_depth,offset_contraction_steps=offset_contraction_steps),
                limitations=['default checker proof disabled until direct arithmetic is uniformly enclosed',
                             'all full-slope tails and unexpanded pose boxes retained',
                             'one conservative arbitrary flooding-endpoint bonus retained',
                             'zero inserted vanishing roots covered by nonnegative endpoint trapezoids'])

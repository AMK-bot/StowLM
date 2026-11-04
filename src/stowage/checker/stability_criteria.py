"""IS Code 2008 GZ and wind criteria, separated from loading arithmetic.

Source: MSC.267(85), Part A 2.2–2.3 and Part B 2.3.2 (local
``data/raw/is-code``; formula transcription checked against the IMO PDF at
https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.267%2885%29.pdf).
An area under GZ measures the work available to resist a heeling disturbance.
Weather compares that work with the gust's work after a windward roll.
Integrals and crossings below are exact for the supplied piecewise linear GZ
interpolant; hull quadrature and angular sampling remain numerical accuracy
limits. The supplied GZ and KG must already include liquid corrections.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np


_X1 = ([2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3., 3.1, 3.2, 3.4, 3.5],
       [1., .98, .96, .95, .93, .91, .90, .88, .86, .82, .80])
_X2 = ([.45, .50, .55, .60, .65, .70], [.75, .82, .89, .95, .97, 1.])
_K = ([0., 1., 1.5, 2., 2.5, 3., 3.5, 4.], [1., .98, .95, .88, .79, .74, .72, .70])
_S = ([6., 7., 8., 12., 14., 16., 18., 20.], [.100, .098, .093, .065, .053, .044, .038, .035])


def _integral(x, y, lo, hi):
    """Signed integral with exact insertion of the two end points."""
    if hi <= lo:
        return 0.
    if lo < x[0] - 1e-12 or hi > x[-1] + 1e-12:
        raise ValueError('GZ samples do not cover the integration interval')
    q = np.r_[lo, x[(x > lo) & (x < hi)], hi]
    v = np.interp(q, x, y)
    return float(np.sum(np.diff(q) * (v[1:] + v[:-1]) / 2))


def _crossings(x, y, level):
    """Crossings, including knots at which the curve meets a heeling lever."""
    out = list(x[np.isclose(y, level, rtol=0., atol=1e-12)])
    for i in range(len(x)-1):
        if (y[i]-level)*(y[i+1]-level) < 0:
            out.append(x[i] + (level-y[i])*(x[i+1]-x[i])/(y[i+1]-y[i]))
    return sorted(set(float(a) for a in out))


def _number(value):
    return float(value['value'] if isinstance(value, Mapping) else value)


def _breadth(geometry, z):
    """Maximum waterline breadth, obtained from the hull section polygons."""
    widths = []
    for polys in geometry.sections:
        p = polys[0]
        q = np.roll(p, -1, axis=0)
        mask = (np.minimum(p[:, 1],q[:, 1]) <= z) & (z <= np.maximum(p[:, 1],q[:, 1]))
        a,b = p[mask],q[mask]
        if len(a):
            flat = a[:, 1] == b[:, 1]
            ys = a[:,0]+np.divide((z-a[:,1])*(b[:,0]-a[:,0]),b[:,1]-a[:,1],
                                  out=np.zeros(len(a)),where=~flat)
            ys = np.r_[ys,b[flat,0]]
            widths.append(float(np.max(ys)-np.min(ys)))
    if not widths or max(widths) <= 0:
        raise ValueError('no positive hull breadth at required waterline')
    return max(widths)


def _waterline_length(geometry, ta, tf, lpp):
    """Waterplane's longitudinal span on the actual ruled section loft.

    Recover each panel's end contours from its two Gauss stations. Contour
    vertices are linear in x, so inserting every waterplane/vertex crossing
    gives intervals on which waterline intersection topology is constant.
    Overhangs outside the perpendiculars are included (A 2.3.4's L_WL).
    """
    x = geometry.x_m
    weights = geometry.integration_weights_m
    if len(x) % 2:
        raise ValueError('waterline length requires paired Gauss stations')
    wet = []
    for j in range(0, len(x), 2):
        p, q = geometry.sections[j][0], geometry.sections[j+1][0]
        if p.shape != q.shape:
            raise ValueError('waterline length requires corresponding panel contours')
        mid = (x[j]+x[j+1])/2
        a, b = mid-weights[j], mid+weights[j]
        p0 = p+(a-x[j])/(x[j+1]-x[j])*(q-p)
        p1 = p+(b-x[j])/(x[j+1]-x[j])*(q-p)
        za, zb = ta+(tf-ta)*a/lpp, ta+(tf-ta)*b/lpp
        d0, d1 = p0[:, 1]-za, p1[:, 1]-zb
        cuts = [0., 1.]
        crossing = d0*d1 < 0
        cuts.extend((-d0[crossing]/(d1[crossing]-d0[crossing])).tolist())
        cuts = sorted(set(cuts))
        for lo, hi in zip(cuts, cuts[1:]):
            t = (lo+hi)/2
            contour = p0+t*(p1-p0)
            z = za+t*(zb-za)
            following = np.roll(contour,-1,axis=0)
            mask = ((np.minimum(contour[:,1],following[:,1]) <= z) &
                    (z <= np.maximum(contour[:,1],following[:,1])))
            u,v = contour[mask],following[mask]
            if len(u):
                flat = u[:,1] == v[:,1]
                ys = u[:,0]+np.divide((z-u[:,1])*(v[:,0]-u[:,0]),v[:,1]-u[:,1],
                                     out=np.zeros(len(u)),where=~flat)
                ys = np.r_[ys,v[flat,0]]
                if np.max(ys)-np.min(ys) > 1e-10:
                    wet.append((a+lo*(b-a), a+hi*(b-a)))
    if not wet:
        raise ValueError('no positive hull length at required waterline')
    return float(max(b for a, b in wet)-min(a for a, b in wet))


def _sharp_bilge(geometry):
    """Credit sharp-bilge k only for demonstrably rectangular bare sections.

    Conservative classification: chines/rounded offset lofts whose bilge shape
    is not an axis-aligned rectangle receive no sharp-bilge reduction (k=1).
    Collinear subdivisions do not affect classification.
    """
    for section in geometry.sections:
        p = section[0]
        steps = np.roll(p, -1, axis=0)-p
        if np.any((np.abs(steps[:, 0]) > 1e-9) & (np.abs(steps[:, 1]) > 1e-9)):
            return False
        if len(np.unique(np.round(p[:, 0], 9))) != 2:
            return False
    return True


def _coaming_depth(hull, hull_params):
    """Code D-prime; merge adjacent port/starboard coaming solids by slab."""
    if hull_params is None:
        raise ValueError('B 2.3 requires envelope coaming dimensions')
    boxes = hull_params['envelope'].get('hatch_coamings', [])
    boxes = [{k: _number(v) for k, v in b.items() if k.endswith('_m')} for b in boxes]
    lo, hi = hull.lpp_m/4, 3*hull.lpp_m/4
    breaks = sorted({lo, hi, *[max(lo, min(hi, b[k])) for b in boxes
                                   for k in ('x_min_m', 'x_max_m')]})
    length = width_length = height_length = 0.
    for a, z in zip(breaks, breaks[1:]):
        active = [b for b in boxes if b['x_min_m'] < (a+z)/2 < b['x_max_m']]
        if not active:
            continue
        # Union coamings laterally, so split P/S halves count as one hatch.
        intervals = sorted((b['y_min_m'], b['y_max_m']) for b in active)
        unions = []
        for u, v in intervals:
            if unions and u <= unions[-1][1]:
                unions[-1][1] = max(unions[-1][1], v)
            else:
                unions.append([u, v])
        width = sum(v-u for u, v in unions)
        heights = {b['z_max_m']-b['z_min_m'] for b in active}
        if len(heights) != 1:
            raise ValueError('unequal coaming heights require defined Code mean-height input')
        length += z-a
        width_length += width*(z-a)
        height_length += heights.pop()*(z-a)
    if length == 0:
        return hull.depth_m
    b, h = width_length/length, height_length/length
    return hull.depth_m + h*(2*b-hull.beam_m)/hull.beam_m*(2*length/hull.lpp_m)


def evaluate_criteria(angles, gz, *, gm_m, hull, geometry, displacement_kg,
                      kg_m, draught_m, phi_f_rad, windage_area_m2=None,
                      windage_lever_m=None, hull_params=None,
                      weather_bilge_keel_area_m2=None, weather_roll_period_s=None,
                      draught_aft_m=None, draught_fwd_m=None, negative_gz=None, rule_overrides=None,
                      deck_edge_angle_rad=None, peak_angle_rad=None, peak_gz_m=None):
    """Return metrics, signed pass margins, and catalogue-tagged failures.

    ``kg_m`` includes the virtual KG rise used for the supplied liquid-corrected
    GZ. Optional measured roll period replaces the Code's approximate formula.
    ``negative_gz`` contains signed GZ at minus each supplied positive angle;
    omission assumes a transversely symmetric loading condition.
    D21 applies B 2.3 instead of A 2.2.1–2.2.3 above 100 m; initial GM
    and weather remain mandatory. Numeric flags expose the selected criteria.
    B total positive-stability area stops at the earlier of flooding and the
    first vanishing-stability angle after a positive lever (D21 interpretation).
    An undefined roll period at nonpositive GM is omitted from metrics.
    Missing keel area/coaming dimensions raises ValueError: the caller must
    turn this into a hard missing-vessel-data violation, never a pass.
    Criterion overrides may only tighten Code minima. Part A 2.2 uses
    ``minimum``; weather uses ``minimum_ratio`` and
    ``minimum_steady_heel_margin_rad``. Part B 2.3 uses ``minimum_`` followed
    by the area/lever metric name (without ``containership_``). The shared
    weather/B rule IDs cannot accept a dimensionally ambiguous ``minimum``.
    Angular integration coverage and weather-table applicability are fixed.
    """
    x, y = np.asarray(angles, dtype=float), np.asarray(gz, dtype=float)
    if (x.ndim != 1 or x.shape != y.shape or len(x) < 2 or
            not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or
            x[0] != 0 or np.any(np.diff(x) <= 0)):
        raise ValueError('GZ samples must be finite, increasing and start at zero')
    end = min(float(phi_f_rad), float(x[-1]))
    if end < 0 or draught_m <= 0 or displacement_kg <= 0:
        raise ValueError('invalid flooding angle, draught or displacement')
    positive_indices = np.flatnonzero(y > 0)
    vanishing = None
    if len(positive_indices):
        for i in range(int(positive_indices[0]), len(x)-1):
            if y[i] > 0 and y[i+1] <= 0:
                vanishing = float(x[i] - y[i]*(x[i+1]-x[i])/(y[i+1]-y[i]))
                break
    # ASSUMPTION: B 2.3.2.5's "total area" means the available positive
    # stability area, ending at flooding or first loss of positive stability.
    # Do not subtract the negative post-capsize branch of a sealed envelope.
    total_end = min(end, vanishing) if vanishing is not None else end
    if (phi_f_rad > x[-1] + 1e-12 and hull.lpp_m > 100
            and vanishing is None):
        raise ValueError('GZ samples must reach downflooding or vanishing stability for B 2.3 total area')
    r30, r40 = math.radians(30), math.radians(40)
    if x[-1] < min(r40,float(phi_f_rad))-1e-12:
        raise ValueError('GZ samples must cover 40 degrees or downflooding for the specified area criteria')
    upper = min(r40, end)
    q = np.r_[x[(x >= 0) & (x < end)], end]
    v = np.interp(q, x, y)
    imax = int(np.argmax(v))
    peak_angle, peak_gz = float(q[imax]), float(v[imax])
    # APPROXIMATION: refine a smooth sampled peak near the 25-degree boundary
    # with its local quadratic vertex. This removes grid-knot threshold bias;
    # it is not a fresh hull equilibrium solve. Sparse/kinked curves retain
    # their supplied maximum rather than inventing a wide quadratic arc.
    if (0 < imax < len(q)-1 and abs(peak_angle-math.radians(25)) <= math.radians(3)
            and max(np.diff(q[imax-1:imax+2])) <= math.radians(3)):
        local_x = q[imax-1:imax+2]-peak_angle
        a, b, c = np.polyfit(local_x, v[imax-1:imax+2], 2)
        if a < 0:
            vertex = -b/(2*a)
            if local_x[0] <= vertex <= local_x[-1]:
                peak_angle += float(vertex)
                peak_gz = float(a*vertex**2+b*vertex+c)
    if peak_angle_rad is not None:
        if (peak_gz_m is None or not math.isfinite(peak_angle_rad)
                or not math.isfinite(peak_gz_m) or not 0 <= peak_angle_rad <= end):
            raise ValueError('invalid refined maximum-GZ angle or lever')
        peak_angle, peak_gz = float(peak_angle_rad), float(peak_gz_m)
    gz30 = max([float(np.interp(r30, x, y)), *v[q >= r30]]) if end >= r30 else 0.
    metrics = dict(area_0_30_mrad=_integral(x, y, 0, min(r30, end)),
                   area_0_40_mrad=_integral(x, y, 0, upper),
                   area_30_40_mrad=_integral(x, y, r30, upper),
                   gz_max_m=peak_gz, angle_gz_max_rad=peak_angle,
                   gz_at_or_above_30_m=gz30,
                   criteria_part_a_2_2_1_to_3_active=float(hull.lpp_m <= 100),
                   criteria_part_b_2_3_active=float(hull.lpp_m > 100),
                   criteria_initial_gm_active=1., criteria_weather_active=1.)
    margins, failures = {}, []
    overrides = {} if rule_overrides is None else rule_overrides
    if not isinstance(overrides, Mapping):
        raise ValueError('stability criterion overrides must be a mapping')
    for rule, policy in overrides.items():
        if not isinstance(policy, Mapping):
            raise ValueError(f'stability criterion override {rule} must be a mapping')
    weather = 'iscode.a2_3.weather'
    containership = 'iscode.b2_3.containership_second_check'
    for rule in (weather, containership):
        policy = overrides.get(rule, {})
        if 'minimum' in policy:
            raise ValueError(f'ambiguous stability criterion override minimum for {rule}; use metric-specific minima')
    if 'minimum_table_applicability' in overrides.get(weather, {}):
        raise ValueError('weather table applicability is not overridable')

    def check(rule, key, measured, limit, unit, message, *, override_key='minimum'):
        policy = overrides.get(rule, {})
        if override_key is not None and override_key in policy:
            value = policy[override_key]
            try:
                if isinstance(value, (bool, np.bool_)):
                    raise ValueError('boolean limit')
                proposed = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f'invalid stability criterion override {rule}.{override_key}') from exc
            if not math.isfinite(proposed):
                raise ValueError(f'nonfinite stability criterion override {rule}.{override_key}')
            # IS Code A 1.1.1, PDF p.11: "a set of minimum requirements";
            # A 2.2.4, PDF p.13: "shall not be less than 0.15 m". Instance
            # policy cannot authorize a relaxation of any applicable Code floor.
            if proposed < limit:
                raise ValueError(f'stability criterion override {rule}.{override_key} would weaken Code minimum {limit} {unit}')
            limit = proposed
        margins[key] = float(measured-limit)
        if measured < limit-1e-12:
            failures.append((rule, float(measured), float(limit), unit, message))
    if hull.lpp_m <= 100:
        for rule, key, limit in [('gz_area_0_30','area_0_30_mrad',.055),
                                  ('gz_area_0_40','area_0_40_mrad',.09),
                                  ('gz_area_30_40','area_30_40_mrad',.03)]:
            check('iscode.a2_2.'+rule,key,metrics[key],limit,'m*rad','GZ area below Code minimum')
        # A 2.2.1, PDF p.12: "up to ... 30° angle of heel". An area
        # override in m*rad cannot alter the required angular range in rad.
        check('iscode.a2_2.gz_area_0_30','downflooding_30_margin_rad',end,r30,'rad','Downflooding occurs before required 30 degree integration', override_key=None)
        check('iscode.a2_2.gz_at_30','gz_at_or_above_30_m',gz30,.2,'m','Insufficient GZ at or beyond 30 degrees')
        check('iscode.a2_2.max_gz_angle','angle_gz_max_rad',peak_angle,math.radians(25),'rad','Maximum GZ occurs before 25 degrees')
    check('iscode.a2_2.initial_gm','gm_m',gm_m,.15,'m','Liquid-corrected GM below Code minimum')

    L, B, d = hull.lpp_m, hull.beam_m, draught_m
    ta = d if draught_aft_m is None else draught_aft_m
    tf = d if draught_fwd_m is None else draught_fwd_m
    lwl = _waterline_length(geometry, ta, tf, L)
    submerged = (geometry.full_submerged(ta, tf, 0.) if hasattr(geometry, 'full_submerged')
                 else geometry.submerged(ta, tf, 0.))
    bw = _breadth(geometry, d)
    cb = submerged.volume_m3/(L*bw*d)
    weather_cb = submerged.volume_m3/(lwl*bw*d)
    x1, x2 = float(np.interp(B/d, *_X1)), float(np.interp(weather_cb, *_X2))
    if weather_bilge_keel_area_m2 is not None:
        # A 2.3.4, PDF p.15: Ak is the "overall area of bilge keels";
        # table 2.3.4-3 (PDF p.16) requires a finite physical area, not NaN/inf.
        # D40 makes an explicit Ak authoritative over the legacy keel flag
        # and polygon classification: the table's Ak=0 entry is k=1.0.
        if not math.isfinite(weather_bilge_keel_area_m2) or weather_bilge_keel_area_m2 < 0:
            raise ValueError('bilge/bar keel area must be finite and nonnegative')
        k = float(np.interp(weather_bilge_keel_area_m2*100/(lwl*B), *_K))
    elif hull.bilge_keel:
        raise ValueError('weather criterion needs total bilge/bar keel area Ak')
    else:
        # Backward-compatible standalone analytic calls can omit Ak for a
        # declared bare hull. Production independently requires explicit hull
        # Ak/evidence before calling this helper, including a no-keels zero.
        k = .7 if _sharp_bilge(geometry) else 1.
    roll_c = .373+.023*B/d-.043*lwl/100
    # APPROXIMATION: Code A 2.3.4 expressly permits this roll-period estimate
    # when measured rolling-period information is unavailable.
    period = (weather_roll_period_s if weather_roll_period_s is not None
              else 2*roll_c*B/math.sqrt(gm_m) if gm_m > 0 else math.inf)
    s = float(np.interp(period, *_S))
    r = .73+.6*(kg_m-d)/d
    phi1 = math.radians(109*k*x1*x2*math.sqrt(max(0., r*s)))
    A = hull.windage_area_m2 if windage_area_m2 is None else windage_area_m2
    Z = hull.windage_lever_m if windage_lever_m is None else windage_lever_m
    lw1 = 504*A*Z/(9.81*displacement_kg)
    lw2 = 1.5*lw1
    roots = _crossings(q, v, lw1)
    phi0 = roots[0] if roots else end
    gust_roots = _crossings(q, v, lw2)
    stable_gust = gust_roots[0] if gust_roots else end
    phi_c = gust_roots[1] if len(gust_roots) >= 2 else end
    phi2 = min(end, math.radians(50), phi_c)
    ta = d if draught_aft_m is None else draught_aft_m
    tf = d if draught_fwd_m is None else draught_fwd_m
    deck_angles = [math.atan2(max(0., hull.depth_m-(ta+(tf-ta)*xx/L)),
                             max(abs(p[0][:, 0])))
                   for xx, p in zip(geometry.x_m, geometry.sections, strict=True)] if deck_edge_angle_rad is None else []
    # APPROXIMATION: absent a supplied heeled free-trim immersion root, use
    # upright freeboard/breadth, omitting heel sinkage and free-trim changes.
    deck_edge = min(deck_angles) if deck_edge_angle_rad is None else float(deck_edge_angle_rad)
    negative = -y if negative_gz is None else np.asarray(negative_gz, dtype=float)
    if negative.shape != y.shape or not np.all(np.isfinite(negative)):
        raise ValueError('negative GZ samples must match the positive angle grid')
    # Windward roll is independent of the leeward downflooding angle. Preserve
    # the complete signed curve for area a; phi2 alone truncates area b.
    sx, sy = np.r_[-x[:0:-1], x], np.r_[negative[:0:-1], y]
    start = phi0-phi1
    a = b = 0.
    coverage = start >= sx[0] and stable_gust <= phi2
    if coverage:
        a = lw2*(stable_gust-start)-_integral(sx, sy, start, stable_gust)
        b = _integral(sx, sy, stable_gust, phi2)-lw2*(phi2-stable_gust)
    ratio = b/a if a > 0 else (1. if b >= 0 and coverage else 0.)
    metrics.update(weather_ratio=ratio, weather_area_a_mrad=a, weather_area_b_mrad=b,
                   weather_lw1_m=lw1, weather_lw2_m=lw2, weather_phi0_rad=phi0,
                   weather_phi1_rad=phi1, weather_phi2_rad=phi2, weather_x1=x1,
                   weather_x2=x2, weather_k=k, weather_s=s, weather_r=r,
                   weather_waterline_length_m=lwl, weather_block_coefficient=weather_cb,
                   weather_deck_edge_rad=deck_edge)
    if math.isfinite(period):
        metrics['weather_roll_period_s'] = period
    # A 2.3.1.4, PDF p.13: "area b shall be equal to or greater than area a";
    # A 2.3.1.2 on the same page separately limits heel to "16° or 80%".
    # Ratio and angular safety margin need separate, dimensionally typed policy.
    check(weather,'weather_ratio',ratio,1.,'1','Gust righting work is less than overturning work', override_key='minimum_ratio')
    check(weather,'weather_steady_heel_margin_rad',min(math.radians(16),.8*deck_edge)-phi0,0.,'rad','Steady-wind heel exceeds 16 degrees or 80% deck-edge immersion', override_key='minimum_steady_heel_margin_rad')
    valid = gm_m > 0 and B/d < 3.5 and -.3 <= kg_m/d-1 <= .5 and period < 20 and r >= 0
    # A 2.3.5, PDF p.17: outside the table limits requires "model experiments";
    # no scalar policy override can supply that alternative physical evidence.
    check(weather,'weather_table_applicability',float(valid and coverage and bool(roots)),1.,'1','Weather tables outside A 2.3.5 applicability, no steady equilibrium, or GZ coverage insufficient', override_key=None)

    if L > 100:
        check('iscode.b2_3.containership_second_check','containership_downflooding_30_margin_rad',
              end,r30,'rad','Downflooding occurs before required 30 degree integration', override_key=None)
        bm = _breadth(geometry, d/2)
        cw = submerged.waterplane_area_m2/(L*bw)
        dp = _coaming_depth(hull, hull_params)
        C = d*dp/bm**2*math.sqrt(d/max(kg_m,d))*(cb/cw)**2*math.sqrt(100/L)
        if C <= 0:
            raise ValueError('nonpositive containership form factor')
        metrics.update(containership_form_factor=C, containership_depth_prime_m=dp,
                       containership_vanishing_angle_detected=float(vanishing is not None),
                       containership_vanishing_angle_rad=vanishing if vanishing is not None else float(x[-1]),
                       containership_total_area_end_rad=total_end,
                       containership_total_area_positive_only=1.,
                       area_0_flood_mrad=_integral(x,y,0,total_end))
        for key, coefficient, unit in [('area_0_30_mrad',.009,'m*rad'),
                                      ('area_0_40_mrad',.016,'m*rad'),
                                      ('area_30_40_mrad',.006,'m*rad'),
                                      ('gz_at_or_above_30_m',.033,'m'),
                                      ('gz_max_m',.042,'m'),
                                      ('area_0_flood_mrad',.029,'m*rad')]:
            # B 2.3.2.1–5, PDF p.25, prescribe separate area (m*rad) and
            # lever (m) minima: "should not be less than" / "at least".
            check(containership,'containership_'+key,
                  metrics[key],coefficient/C,unit,'Containership second-check minimum not met',
                  override_key='minimum_'+key)
    return metrics, margins, failures

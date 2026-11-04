"""Conservative full-filling-range finite-angle liquid-transfer envelope.

IS Code Part B 3.1 permits the most adverse transfer over the usable filling
range. For a shaped tank it need not occur at half fill. ``max_transfer`` calls
its actual liquid geometry, never its bounding rectangle. It returns the maximum
outward moment (m4), with outward defined by sign(heel), or positive transverse
at zero heel. The caller restores that sign in the signed GZ correction.

For F(V)=V*(CG_tilt-CG_upright)·outward, F' is the difference of the
transverse means of the two incremental liquid surfaces. Both lie within the
solid's vertex extrema. Thus |F'| is at most that vertex range. Polygon-edge
slabs between endpoint liquid levels give tighter, still conservative derivative
bounds. For non-prismatic section tanks, exact piecewise-polynomial surface
means tighten those derivative bounds further: their extrema are quadratic roots.
An edge-event sweep prepares these polynomials in linear time after sorting;
nearly horizontal edges use their local intervals to prevent cancellation.
A deterministic branch-and-bound returns an UPPER bound, never merely
the largest sample. The returned upper bound minus the sampled lower bound is
at most ``tolerance_m4``; failure to establish that bound raises ValueError.

APPROXIMATION: the bounded numerical envelope has the caller's stated absolute
moment tolerance, in addition to the underlying tank liquid integration/root
roundoff and the section geometry's declared longitudinal quadrature error.
Source: IMO 2008 IS Code, Part B 3.1 (free-surface and liquid-transfer effects).
"""
from __future__ import annotations

from collections import OrderedDict
import heapq
from itertools import product
from hashlib import sha256
from math import cos, sin, tan

from scipy.optimize import brentq

import numpy as np

from stowage.checker.stability_budget import check_budget

_CACHE = OrderedDict()
_CACHE_SIZE = 2048
_MAX_EVALUATIONS = 8193


_CANONICAL_IDENTITIES = OrderedDict()
_CANONICAL_IDENTITIES_SIZE = 256


def _polygon_bytes(polygon, reflected):
    """Exact contour identity independent of its cyclic starting vertex."""
    points = np.asarray(polygon, dtype='<f8').copy()
    if reflected:
        points[:, 0] *= -1
        points = points[::-1].copy()  # Reflection reverses polygon orientation.
    points[points == 0] = 0.  # +0 and -0 denote the same exact coordinate.
    points = points[np.any(points != np.roll(points, 1, axis=0), axis=1)]
    # The least coordinate pair fixes the start; repeated nonadjacent vertices
    # use the least complete rotation. No rounding or tolerance is permitted.
    minimum = min(map(tuple, points))
    starts = np.flatnonzero(np.all(points == minimum, axis=1))
    sequences = [np.roll(points, -int(start), axis=0).tobytes() for start in starts]
    return len(points).to_bytes(8, 'little')+min(sequences)


def _solid_identity(solid, reflected):
    digest = sha256()
    shape = getattr(solid, 'shape', '')
    digest.update(shape.encode())
    bounds = np.array([getattr(solid, f'{a}_{b}_m') for a in 'xyz' for b in ('min', 'max')], dtype='<f8')
    if reflected:
        bounds[2:4] = -bounds[3:1:-1]
    bounds[bounds == 0] = 0.
    digest.update(bounds.tobytes())
    digest.update(np.array([solid.capacity_m3], dtype='<f8').tobytes())
    if shape == 'hull_sections':
        for values in (solid.x_m, solid.integration_weights_m):
            array = np.asarray(values, dtype='<f8').copy()
            array[array == 0] = 0.
            digest.update(len(array).to_bytes(8, 'little'))
            digest.update(array.tobytes())
        for section in solid.sections_yz_m:
            contours = sorted(_polygon_bytes(p, reflected) for p in section)
            digest.update(len(contours).to_bytes(8, 'little'))
            for contour in contours:
                digest.update(len(contour).to_bytes(8, 'little'))
                digest.update(contour)
    elif not getattr(solid, 'is_rectangular', False):
        # Unknown geometries are deliberately never equated by their bounds.
        return None
    return digest.digest()


def _canonical_cache_key(solid, heel, trim, tolerance):
    """Identify true reflected solids and reverse heel/outward direction exactly.

    Reflection y -> -y leaves longitudinal trim and volume invariant. It reverses
    both transverse centroid shift and outward unit direction, so their scalar
    moment product is identical. At zero heel the caller defines outward as +y;
    retain its reflected sign explicitly, rather than conflating +0 with -0.
    """
    identity_key = id(solid)
    if identity_key not in _CANONICAL_IDENTITIES:
        direct, reflected = _solid_identity(solid, False), _solid_identity(solid, True)
        if direct is None or reflected is None:
            result = (('object', identity_key), 1., False)
        else:
            result = (min(direct, reflected), 1. if direct <= reflected else -1., direct == reflected)
        _CANONICAL_IDENTITIES[identity_key] = (solid, result)
        while len(_CANONICAL_IDENTITIES) > _CANONICAL_IDENTITIES_SIZE:
            _CANONICAL_IDENTITIES.popitem(last=False)
    else:
        _CANONICAL_IDENTITIES.move_to_end(identity_key)
    identity, orientation, symmetric = _CANONICAL_IDENTITIES[identity_key][1]
    outward = (-1. if heel < 0 else 1.)*orientation
    return (identity, abs(heel) if symmetric else heel*orientation,
            1. if symmetric else outward, trim, tolerance)


def liquid_centroid(solid, volume_m3, heel_rad=0., trim_rad=0.):
    """Exact direct centroid without calculating an unused free-surface inertia.

    Clip all tank section polygons in a NumPy batch, invert their integrated
    volume with Brent's bracketed root, and evaluate their first moments. This
    calls the same longitudinal quadrature as ``solid.liquid`` (including its
    angle-dependent refinement for prismatic extrusions). No cross-curve grid,
    interpolation, rectangular replacement, or finite-angle approximation is
    involved. The scalar root retains the geometry provider's volume tolerance.
    """
    check_budget('direct tank centroid entry')
    result = _direct_liquid(solid, volume_m3, heel_rad, trim_rad, polynomial=False)[0]
    check_budget('direct tank centroid exit')
    return result


def _direct_liquid(solid, volume_m3, heel_rad, trim_rad, *, polynomial=True):
    if getattr(solid, 'shape', None) != 'hull_sections':
        condition = solid.liquid(volume_m3, heel_rad, trim_rad)
        return np.asarray(condition.centroid_m), float(condition.plane_level_m)
    volume = solid._volume(volume_m3)
    if polynomial and not (solid._prismatic and abs(trim_rad) > 1e-14):
        return _polynomial_liquid(solid, volume, heel_rad, trim_rad)
    normal = solid._normal(heel_rad, trim_rad)
    corners = np.array(list(product(*[(getattr(solid, f'{a}_min_m'),
                                      getattr(solid, f'{a}_max_m')) for a in 'xyz'])))
    projections = corners @ normal
    low, high = float(projections.min()), float(projections.max())
    if volume == 0.:
        return np.array([(solid.x_min_m+solid.x_max_m)/2,
                         (solid.y_min_m+solid.y_max_m)/2, solid.z_min_m]), low
    if volume == solid.capacity_m3:
        return np.asarray(solid._full_centroid), high
    def residual(level):
        if level <= low:
            return -volume
        if level >= high:
            return solid.capacity_m3-volume
        return solid._integrate(level, normal)[0]-volume
    level = brentq(residual, low, high, xtol=5e-14,
                   rtol=4*np.finfo(float).eps, maxiter=150)
    totals = solid._integrate(level, normal)
    if abs(totals[0]-volume) > max(1e-10, 2e-10*volume):
        raise ArithmeticError('direct tank centroid root failed volume tolerance')
    return totals[1:]/totals[0], float(level)


_POLYNOMIAL_CACHE = OrderedDict()
_POLYNOMIAL_CACHE_SIZE = 128


def _section_polynomials(solid, heel, trim):
    """Exact height-piece polynomials, reducing all polygon edges in batches.

    The integration identity is Green's theorem on each polygon slice: signed
    edge intersections give width, transverse first moment and their integrals.
    The slice height breaks are every actual vertex, so these are polynomial
    identities inside each interval, not sampled or fitted curves.
    """
    from types import SimpleNamespace
    key = (id(solid), float(heel), float(trim))
    if key in _POLYNOMIAL_CACHE:
        _POLYNOMIAL_CACHE.move_to_end(key)
        return _POLYNOMIAL_CACHE[key][1]
    c, sn = cos(heel), sin(heel)
    slope = tan(trim)*c
    records, heights = [], []
    origin = np.longdouble((solid.y_min_m+solid.y_max_m)/2)
    for x, weight, section in zip(solid.x_m, solid.integration_weights_m,
                                   solid.sections_yz_m, strict=True):
        for p in section:
            y = c*p[:, 0]+sn*p[:, 1]
            z = -sn*p[:, 0]+c*p[:, 1]+slope*x
            heights.extend(z)
            for i in range(len(p)):
                j = (i+1) % len(p)
                dz = np.longdouble(z[j])-np.longdouble(z[i])
                if dz:
                    records.append((np.longdouble(y[i])-origin, z[i],
                        np.longdouble(y[j])-np.longdouble(y[i]), dz,
                        weight*np.sign(dz), x, z[j]))
    edges = np.asarray(records, dtype=np.longdouble)
    levels = np.unique(heights)
    lows, highs = np.minimum(edges[:, 1], edges[:, 6]), np.maximum(edges[:, 1], edges[:, 6])
    # Sweep vertex-height events: an edge contributes only between its two
    # endpoint heights. Add its global polynomial at entry, subtract at exit,
    # then cumulative-sum once. This avoids testing every edge at every height.
    zorigin = np.longdouble((levels[0]+levels[-1])/2)
    zscale = np.longdouble(levels[-1]-levels[0])
    tleft = (levels[:-1].astype(np.longdouble)-zorigin)/zscale
    spans = np.diff(levels).astype(np.longdouble)
    dt = spans/zscale
    intercept = edges[:, 0]+(zorigin-edges[:, 1])*edges[:, 2]/edges[:, 3]
    slope_t = zscale*edges[:, 2]/edges[:, 3]
    weight = edges[:, 4]
    contribution = np.zeros((len(edges), 4, 3), dtype=np.longdouble)
    contribution[:, 0, 0] = weight*intercept
    contribution[:, 0, 1] = weight*slope_t
    contribution[:, 1, :2] = contribution[:, 0, :2]*edges[:, 5, None]
    contribution[:, 2, 0] = weight*(intercept**2/2+origin*intercept)
    contribution[:, 2, 1] = weight*(intercept*slope_t+origin*slope_t)
    contribution[:, 2, 2] = weight*slope_t**2/2
    contribution[:, 3, 0] = contribution[:, 0, 0]*zorigin
    contribution[:, 3, 1] = contribution[:, 0, 1]*zorigin+contribution[:, 0, 0]*zscale
    contribution[:, 3, 2] = contribution[:, 0, 1]*zscale
    # Very shallow edges have enormous global coefficients even though their
    # support is tiny. Add their exact local polynomials only to the intervals
    # they span; this avoids subtracting enormous almost-equal event values.
    shallow = np.abs(edges[:, 3]) < 1e-4*zscale
    contribution[shallow] = 0.
    changes = np.zeros((len(levels), 4, 3), dtype=np.longdouble)
    np.add.at(changes, np.searchsorted(levels, lows), contribution)
    np.add.at(changes, np.searchsorted(levels, highs), -contribution)
    active = np.cumsum(changes, axis=0)[:-1]
    integral_rows = np.zeros((len(levels)-1, 4, 4), dtype=np.longdouble)
    integral_rows[:, :, 1] = active[:, :, 0]+active[:, :, 1]*tleft[:, None]+active[:, :, 2]*tleft[:, None]**2
    integral_rows[:, :, 2] = dt[:, None]*(active[:, :, 1]+2*active[:, :, 2]*tleft[:, None])/2
    integral_rows[:, :, 3] = dt[:, None]**2*active[:, :, 2]/3
    for edge in edges[shallow]:
        start, stop = np.searchsorted(levels, [min(edge[1], edge[6]), max(edge[1], edge[6])])
        ii = np.arange(start, stop)
        left, span = levels[ii].astype(np.longdouble), spans[ii]
        a = edge[0]+(left-edge[1])*edge[2]/edge[3]
        b = span*edge[2]/edge[3]
        w = edge[4]
        integral_rows[ii, 0, 1] += w*a
        integral_rows[ii, 0, 2] += w*b/2
        integral_rows[ii, 1, 1] += w*edge[5]*a
        integral_rows[ii, 1, 2] += w*edge[5]*b/2
        integral_rows[ii, 2, 1] += w*(a*a/2+origin*a)
        integral_rows[ii, 2, 2] += w*(a*b+origin*b)/2
        integral_rows[ii, 2, 3] += w*b*b/6
        integral_rows[ii, 3, 1] += w*left*a
        integral_rows[ii, 3, 2] += w*(left*b+span*a)/2
        integral_rows[ii, 3, 3] += w*span*b/3
    totals = spans[:, None]*integral_rows.sum(axis=2)
    cumulative = np.vstack((np.zeros(4, dtype=np.longdouble), np.cumsum(totals, axis=0)))
    expected = np.asarray(solid._full_centroid)
    transformed_centroid = np.array([expected[0], c*expected[1]+sn*expected[2],
                                    -sn*expected[1]+c*expected[2]+slope*expected[0]])
    expected_totals = solid.capacity_m3*np.r_[1., transformed_centroid]
    if (not np.allclose(cumulative[-1], expected_totals, rtol=2e-10, atol=1e-9) or
            np.any(np.diff(cumulative[:, 0]) < -1e-10)):
        # Almost-horizontal edges have ill-conditioned global polynomial
        # coefficients. Their local interval representation retains accuracy;
        # use the reference analytic builder rather than cancel large numbers.
        from stowage.checker.stability_sounding import _build
        sections = tuple(tuple(np.column_stack((c*p[:, 0]+sn*p[:, 1],
                           -sn*p[:, 0]+c*p[:, 1]+slope*x)) for p in section)
                         for x, section in zip(solid.x_m, solid.sections_yz_m))
        vertices = np.vstack([p for section in sections for p in section])
        transformed = SimpleNamespace(x_m=solid.x_m, integration_weights_m=solid.integration_weights_m,
            sections_yz_m=sections, capacity_m3=solid.capacity_m3,
            x_min_m=solid.x_min_m, x_max_m=solid.x_max_m,
            y_min_m=float(vertices[:, 0].min()), y_max_m=float(vertices[:, 0].max()),
            z_min_m=float(vertices[:, 1].min()), z_max_m=float(vertices[:, 1].max()),
            sounding=lambda v: SimpleNamespace(lcg_m=0., tcg_m=0., vcg_m=0.))
        robust = _build(transformed, include_maximum=False)
        levels, cumulative, integral_rows = robust.levels, robust.cumulative, robust.integrals
    table = SimpleNamespace(levels=np.asarray(levels), cumulative=np.asarray(cumulative, dtype=float),
                integrals=np.asarray(integral_rows, dtype=float), c=c, sn=sn, slope=slope,
                normal_norm=np.linalg.norm([slope, -sn, c]))
    _POLYNOMIAL_CACHE[key] = (solid, table)
    while len(_POLYNOMIAL_CACHE) > _POLYNOMIAL_CACHE_SIZE:
        _POLYNOMIAL_CACHE.popitem(last=False)
    return table


def _polynomial_liquid(solid, volume, heel, trim):
    table = _section_polynomials(solid, heel, trim)
    if volume <= 1e-5*solid.capacity_m3 or volume >= (1-1e-5)*solid.capacity_m3:
        return _direct_liquid(solid, volume, heel, trim, polynomial=False)
    i = int(np.clip(np.searchsorted(table.cumulative[:, 0], volume, side='right')-1,
                    0, len(table.levels)-2))
    span = table.levels[i+1]-table.levels[i]
    target = volume-table.cumulative[i, 0]
    coefficients = table.integrals[i]
    def integrated(u):
        return span*(coefficients[0, 1]*u+coefficients[0, 2]*u*u+coefficients[0, 3]*u*u*u)
    total = integrated(1.)
    target = min(total, max(0., target))
    u = (0. if target == 0 else 1. if target == total else
         brentq(lambda q: integrated(q)-target, 0., 1., xtol=5e-15))
    moments = table.cumulative[i]+span*(coefficients[:, 1]*u+coefficients[:, 2]*u*u+coefficients[:, 3]*u*u*u)
    x, y, z = moments[1:]/moments[0]
    centroid = np.array([x, table.c*y-table.sn*(z-table.slope*x),
                           table.sn*y+table.c*(z-table.slope*x)])
    level = (table.levels[i]+span*u)/table.normal_norm
    return centroid, float(level)


def angle_moments(solid, heel, trim, fills):
    """Exact first-volume moments for a batch of fills at one liquid plane.

    This is the offline table builder's direct integration kernel. The geometry
    and longitudinal Gauss weights are the same as its previous sounding-based
    construction. Polynomial inversion is vectorised, with 46 bracket halvings
    (height error below 1.5e-14 of one vertex-height interval). No heel, trim or
    fill interpolation occurs in this reference kernel.
    """
    check_budget('direct tank angle moments entry')
    fractions = np.asarray(fills, dtype=float)
    if fractions.ndim != 1 or not np.isfinite(fractions).all() or np.any((fractions < 0) | (fractions > 1)):
        raise ValueError('tank fill fractions must be finite and within zero to one')
    table = _section_polynomials(solid, float(heel), float(trim))
    volumes = fractions*solid.capacity_m3
    indices = np.clip(np.searchsorted(table.cumulative[:, 0], volumes, side='right')-1,
                      0, len(table.levels)-2)
    spans = np.diff(table.levels)[indices]
    target = volumes-table.cumulative[indices, 0]
    coefficients = table.integrals[indices]
    low, high = np.zeros(len(fractions)), np.ones(len(fractions))
    for _ in range(46):
        u = (low+high)/2
        predicted = spans*(coefficients[:, 0, 1]*u+coefficients[:, 0, 2]*u*u+coefficients[:, 0, 3]*u*u*u)
        mask = predicted < target
        low, high = np.where(mask, u, low), np.where(mask, high, u)
    u = (low+high)/2
    moments = table.cumulative[indices]+spans[:, None]*(coefficients[:, :, 1]*u[:, None]+
               coefficients[:, :, 2]*u[:, None]**2+coefficients[:, :, 3]*u[:, None]**3)
    x, across, level = moments[:, 1], moments[:, 2], moments[:, 3]
    actual = np.column_stack((x, table.c*across-table.sn*(level-table.slope*x),
                             table.sn*across+table.c*(level-table.slope*x)))
    actual[fractions == 0] = 0.
    actual[fractions == 1] = solid.capacity_m3*np.asarray(solid._full_centroid)
    check_budget('direct tank angle moments exit')
    return actual


def _edges(solid):
    """Vertices/edges of the represented solid, not its enclosing box."""
    if getattr(solid, 'is_rectangular', False):
        vertices = np.array(list(product(*[(getattr(solid, f'{a}_min_m'),
                                            getattr(solid, f'{a}_max_m')) for a in 'xyz'])))
        pairs = [(i, j) for i in range(8) for j in range(i + 1, 8)
                 if np.count_nonzero(vertices[i] != vertices[j]) == 1]
        return vertices, vertices[[i for i, _ in pairs]], vertices[[j for _, j in pairs]]
    vertices, starts, ends = [], [], []
    # Constant-section extrusions refine x quadrature at liquid-plane corners:
    # their actual solid has longitudinal edges as well as end polygons.
    prismatic = getattr(solid, '_prismatic', False)
    records = ([(solid.x_min_m, solid.sections_yz_m[0]),
                (solid.x_max_m, solid.sections_yz_m[0])] if prismatic else
               list(zip(solid.x_m, solid.sections_yz_m)))
    for x, section in records:
        for polygon in section:
            points = np.column_stack((np.full(len(polygon), x), polygon))
            vertices.extend(points)
            starts.extend(points)
            ends.extend(np.roll(points, -1, axis=0))
    if prismatic:
        for polygon in solid.sections_yz_m[0]:
            starts.extend(np.column_stack((np.full(len(polygon), solid.x_min_m), polygon)))
            ends.extend(np.column_stack((np.full(len(polygon), solid.x_max_m), polygon)))
    return np.asarray(vertices), np.asarray(starts), np.asarray(ends)


def _slab_bounds(vertices, starts, ends, normal, direction, low, high):
    """Linear-coordinate extrema on edges clipped to a free-surface slab."""
    scale = max(1., abs(low), abs(high))
    pad = 1e-10 * scale
    low, high = min(low, high) - pad, max(low, high) + pad
    projection = vertices @ normal
    values = list((vertices @ direction)[(projection >= low) & (projection <= high)])
    a, b = starts @ normal, ends @ normal
    delta = b - a
    for level in (low, high):
        mask = (level >= np.minimum(a, b)) & (level <= np.maximum(a, b)) & (delta != 0)
        fraction = (level - a[mask]) / delta[mask]
        points = starts[mask] + fraction[:, None] * (ends[mask] - starts[mask])
        values.extend(points @ direction)
    if not values:
        # Numerical liquid-level roundoff near empty/full: global extrema stay
        # valid and prevent an empty slab from weakening the upper bound.
        values = vertices @ direction
    return float(np.min(values)), float(np.max(values))


def _project_slab_edges(vertices, starts, ends, normal, direction):
    """Project once per angle; all later slab clipping is scalar arithmetic."""
    return (vertices @ normal, vertices @ direction, starts @ normal,
            ends @ normal, starts @ direction, ends @ direction)


def _projected_slab_bounds(projections, low, high):
    vp, vd, a, b, ad, bd = projections
    pad = 1e-10*max(1., abs(low), abs(high))
    low, high = min(low, high)-pad, max(low, high)+pad
    interior = vd[(vp >= low) & (vp <= high)]
    minimum = float(interior.min()) if len(interior) else np.inf
    maximum = float(interior.max()) if len(interior) else -np.inf
    delta = b-a
    for level in (low, high):
        mask = (level >= np.minimum(a, b)) & (level <= np.maximum(a, b)) & (delta != 0)
        values = ad[mask]+(level-a[mask])/delta[mask]*(bd[mask]-ad[mask])
        if len(values):
            minimum, maximum = min(minimum, float(values.min())), max(maximum, float(values.max()))
    if not np.isfinite(minimum):
        return float(vd.min()), float(vd.max())
    return minimum, maximum


def _surface_projection_polynomials(solid, heel, trim, direction):
    """True incremental liquid-plane transverse means for derivative bounds."""
    table = _section_polynomials(solid, heel, trim)
    dx, dy, dz = direction
    alpha = np.array([dx+dy*table.sn*table.slope-dz*table.c*table.slope,
                      dy*table.c+dz*table.sn, -dy*table.sn+dz*table.c])
    denominator = table.integrals[:, 0, 1:]*np.array([1., 2., 3.])
    numerator = np.einsum('ijk,j->ik', table.integrals[:, 1:, 1:], alpha)*np.array([1., 2., 3.])
    # Area is affine in height between vertices. The projected first moment
    # of an incremental slice is quadratic: extrema of their ratio solve a
    # quadratic derivative, and are therefore included exactly in each slab.
    a = numerator[:, 2]*denominator[:, 1]
    b = 2*numerator[:, 2]*denominator[:, 0]
    c = numerator[:, 1]*denominator[:, 0]-numerator[:, 0]*denominator[:, 1]
    discriminant = b*b-4*a*c
    roots = np.full((len(a), 2), np.nan)
    quadratic = (a != 0) & (discriminant >= 0)
    radical = np.sqrt(np.maximum(0., discriminant[quadratic]))
    q = -.5*(b[quadratic]+np.copysign(radical, b[quadratic]))
    roots[quadratic, 0] = q/a[quadratic]
    roots[quadratic, 1] = np.divide(c[quadratic], q, out=np.zeros_like(q), where=q != 0)
    linear = (a == 0) & (b != 0)
    roots[linear, 0] = -c[linear]/b[linear]
    return table, numerator, denominator, roots


def _surface_mean_bounds(polynomials, low, high):
    table, numerator, denominator, roots = polynomials
    # Caller plane levels use unit normals; table height uses the unnormalised
    # transformed vertical coordinate.
    low, high = sorted((low*table.normal_norm, high*table.normal_norm))
    levels = table.levels
    first = max(0, int(np.searchsorted(levels, low, side='right')-1))
    last = min(len(levels)-2, int(np.searchsorted(levels, high, side='left')))
    indices = np.arange(first, last+1)
    spans = levels[indices+1]-levels[indices]
    lo = np.clip((low-levels[indices])/spans, 0., 1.)
    hi = np.clip((high-levels[indices])/spans, 0., 1.)
    rr = roots[indices]
    candidates = np.column_stack((lo, hi, np.where((rr[:, 0] > lo) & (rr[:, 0] < hi), rr[:, 0], lo),
                                    np.where((rr[:, 1] > lo) & (rr[:, 1] < hi), rr[:, 1], hi)))
    n, d = numerator[indices], denominator[indices]
    # At an empty/full triangular tip both numerator and denominator vanish;
    # cancel their common linear factor analytically (l'Hopital's rule).
    u = candidates
    top = n[:, 0, None]+n[:, 1, None]*u+n[:, 2, None]*u*u
    bottom = d[:, 0, None]+d[:, 1, None]*u
    threshold = 1e-12*np.maximum(1., np.max(np.abs(d), axis=1))[:, None]
    regular = np.abs(bottom) > threshold
    values = np.divide(top, bottom, out=np.zeros_like(top), where=regular)
    limit = np.divide(n[:, 1, None]+2*n[:, 2, None]*u, d[:, 1, None],
                      out=np.zeros_like(top), where=d[:, 1, None] != 0)
    values = np.where(regular, values, limit)
    pad = 1e-8*max(1., float(np.max(np.abs(values))))
    return float(np.min(values))-pad, float(np.max(values))+pad


def max_transfer(solid, heel_rad, trim_rad, *, tolerance_m4):
    """Upper bound on worst outward liquid moment over [0, capacity], in m4.

    Negative heels use the negative transverse direction, so the result is a
    nonnegative magnitude on both sides. Content-based bounded caching also
    shares geometrically verified y reflections at opposite heel, retaining
    exact capacity, trim and tolerance. Coordinate comparisons use no rounding.
    No extrapolation or fixed-fill assumption is made. Input and evaluation
    budget failures raise ValueError; wall-time exhaustion is StabilityTimeout.
    """
    check_budget('direct tank-transfer envelope entry')
    heel, trim, tolerance = map(float, (heel_rad, trim_rad, tolerance_m4))
    if not np.isfinite([heel, trim, tolerance]).all() or tolerance <= 0:
        raise ValueError('finite angles and positive moment tolerance required')
    if max(abs(heel), abs(trim)) >= np.pi / 2:
        raise ValueError('tank angles must be less than 90 degrees')
    if heel == trim == 0:
        return 0.
    capacity = float(solid.capacity_m3)
    if not np.isfinite(capacity) or capacity <= 0:
        raise ValueError('positive finite tank capacity required')
    key = _canonical_cache_key(solid, heel, trim, tolerance)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key][1]
    normal = np.array([tan(trim) * cos(heel), -sin(heel), cos(heel)])
    normal /= np.linalg.norm(normal)
    along = np.array([1., 0., -tan(trim)])
    along /= np.linalg.norm(along)
    direction = np.cross(normal, along) * (-1. if heel < 0 else 1.)
    vertices, starts, ends = _edges(solid)
    global_range = float(np.ptp(vertices @ direction))
    vertical = np.array([0., 0., 1.])
    evaluated = {}
    tilted_projections = _project_slab_edges(vertices, starts, ends, normal, direction)
    upright_projections = _project_slab_edges(vertices, starts, ends, vertical, direction)
    analytic = (getattr(solid, 'shape', None) == 'hull_sections' and
                not (solid._prismatic and abs(trim) > 1e-14))
    if analytic:
        tilted_means = _surface_projection_polynomials(solid, heel, trim, direction)
        upright_means = _surface_projection_polynomials(solid, 0., 0., direction)

    def evaluate(volume):
        tilted, tilted_level = _direct_liquid(solid, volume, heel, trim)
        upright, upright_level = _direct_liquid(solid, volume, 0., 0.)
        value = volume * float((tilted-upright) @ direction)
        evaluated[volume] = (value, tilted_level, upright_level)
        return value

    def interval(a, b):
        fa, ta, ua = evaluated[a]
        fb, tb, ub = evaluated[b]
        tlo, thi = _projected_slab_bounds(tilted_projections, ta, tb)
        ulo, uhi = _projected_slab_bounds(upright_projections, ua, ub)
        if analytic:
            # These are extrema of the actual incremental plane means, rather
            # than the looser vertex envelope. Their difference rigorously
            # bounds F'(V), retaining the original branch-and-bound proof.
            ml, mh = _surface_mean_bounds(tilted_means, ta, tb)
            ul, uh = _surface_mean_bounds(upright_means, ua, ub)
            tlo, thi, ulo, uhi = max(tlo, ml), min(thi, mh), max(ulo, ul), min(uhi, uh)
        lower, upper = max(-global_range, tlo-uhi), min(global_range, thi-ulo)
        width = b-a
        # Intersect endpoint tangent cones; the minimum of the increasing and
        # decreasing lines reaches its maximum at their crossing or an endpoint.
        if upper == lower:
            bound = max(fa, fb)
        else:
            position = np.clip((fb-fa-lower*width)/(upper-lower), 0., width)
            bound = max(fa, fb, min(fa+upper*position, fb-lower*(width-position)))
        # Protect against floating-point subtraction in moment/root calculations.
        bound += 1e-10 * max(1., capacity*global_range, abs(fa), abs(fb))
        return (-float(bound), a, b)

    evaluate(0.)
    evaluate(capacity)
    best = 0.
    heap = [interval(0., capacity)]
    while -heap[0][0] - best > tolerance:
        check_budget('direct tank-transfer envelope refinement')
        if len(evaluated) >= _MAX_EVALUATIONS:
            raise ValueError('liquid-transfer envelope failed its conservative tolerance budget')
        _, a, b = heapq.heappop(heap)
        middle = (a+b)/2
        if middle == a or middle == b:
            raise ValueError('liquid-transfer envelope reached floating-point volume resolution')
        best = max(best, evaluate(middle))
        heapq.heappush(heap, interval(a, middle))
        heapq.heappush(heap, interval(middle, b))
    check_budget('direct tank-transfer envelope exit')
    result = max(0., best, -heap[0][0])
    _CACHE[key] = (solid, result)
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return result

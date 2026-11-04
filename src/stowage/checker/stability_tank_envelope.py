"""Whole-fill upright free-surface maxima from the declared tank solid.

IS Code Part B 3.1 asks for the most adverse free-surface effect over a
consumable's filling range. Half fill need not be adverse in a hull-following
tank. Between consecutive polygon vertex heights every horizontal intersection
edge is affine. Its area, first transverse moment and second transverse moment
are polynomials A, Q and S of degrees one, two and three. Central inertia is
S-Q²/A; all its stationary points are roots of the quartic
S' A² - 2 Q Q' A + Q² A'. Evaluating every real interior root and both
one-sided interval endpoints finds the global supremum without fill sampling.

Recorded section integration weights define the solid, retaining the upstream
longitudinal quadrature approximation. Bounding extents only condition the
coordinates; they never substitute for polygon geometry. At a horizontal ledge
the returned maximum may be a one-sided slack-fill limit; its returned volume
identifies that boundary, rather than claiming the endpoint FSM equals its limit.
"""
from functools import lru_cache

import numpy as np
from numpy.polynomial import polynomial as poly


def max_fsm(solid) -> tuple[float, float]:
    """Return (maximum upright FSM in m⁴, associated liquid volume in m³).

    The complete usable range is considered, including slack-fill limits next
    to empty/full and polygon ledges. Missing/unknown shapes fail explicitly.
    """
    if getattr(solid, 'shape', None) not in ('rectangular_prism', 'hull_sections'):
        raise ValueError('free-surface envelope requires explicit supported tank geometry')
    return _maximum(solid)


@lru_cache(maxsize=128)
def _maximum(solid):
    shape = solid.shape
    if shape == 'rectangular_prism':
        row = solid.sounding(solid.capacity_m3/2)
        return float(row.fsm_m4), float(row.volume_m3)
    if shape != 'hull_sections':
        raise ValueError('free-surface envelope requires explicit supported tank geometry')
    vertices = [p for section in solid.sections_yz_m for p in section]
    yorigin = np.longdouble((solid.y_min_m+solid.y_max_m)/2)
    records = []
    for weight, section in zip(solid.integration_weights_m, solid.sections_yz_m, strict=True):
        for polygon in section:
            for start, end in zip(polygon, np.roll(polygon,-1,axis=0), strict=True):
                dz = np.longdouble(end[1])-np.longdouble(start[1])
                if dz != 0:
                    records.append((start[0]-yorigin,start[1],np.longdouble(end[0])-np.longdouble(start[0]),
                                    dz,weight*np.sign(dz),end[1]))
    edges = np.asarray(records,dtype=np.longdouble)
    # Original endpoints are essential: reconstructing end=start+(end-start)
    # can move a nearly horizontal edge past the next height event by one bit.
    # Its huge slope would then contaminate a whole subsequent interval.
    low = np.minimum(edges[:,1],edges[:,5])
    high = np.maximum(edges[:,1],edges[:,5])
    levels = sorted(set(float(z) for p in vertices for z in p[:,1]))
    volume = np.longdouble(0)
    best, best_volume = 0., 0.
    for left, right in zip(levels[:-1],levels[1:],strict=True):
        span = np.longdouble(right-left)
        # Recompute local edge coefficients from source vertices to avoid
        # cancellation between huge near-horizontal global coefficients.
        active = edges[(low < right) & (high > left)]
        intercept = active[:,0]+(np.longdouble(left)-active[:,1])*active[:,2]/active[:,3]
        slope = span*active[:,2]/active[:,3]
        weight = active[:,4]
        a = np.array([np.sum(weight*intercept),np.sum(weight*slope)],dtype=np.longdouble)
        q = np.array([np.sum(weight*intercept**2)/2,np.sum(weight*intercept*slope),
                      np.sum(weight*slope**2)/2],dtype=np.longdouble)
        s = np.array([np.sum(weight*intercept**3)/3,np.sum(weight*intercept**2*slope),
                      np.sum(weight*intercept*slope**2),np.sum(weight*slope**3)/3],dtype=np.longdouble)
        aa = poly.polymul(a,a)
        derivative = poly.polysub(poly.polymul(poly.polyder(s),aa),
                                 2*poly.polymul(poly.polymul(q,poly.polyder(q)),a))
        derivative = poly.polyadd(derivative,poly.polymul(poly.polymul(q,q),poly.polyder(a)))
        magnitude = float(np.max(np.abs(derivative)))
        candidates = [0.,1.]
        if magnitude > 0:
            coefficients = np.asarray(derivative/magnitude,dtype=float)
            coefficients = np.trim_zeros(coefficients,'b')
            if len(coefficients)>1:
                for root in poly.polyroots(coefficients):
                    if abs(root.imag)<1e-8 and 0 < root.real < 1:
                        candidates.append(float(root.real))
        integrated = poly.polyint(a)
        for u in candidates:
            area = poly.polyval(u,a)
            if area <= 0:
                continue
            first, second = poly.polyval(u,q),poly.polyval(u,s)
            inertia = float(max(0.,second-first*first/area))
            if inertia > best:
                best = inertia
                best_volume = float(volume+span*poly.polyval(u,integrated))
        volume += span*poly.polyval(1.,integrated)
    if not np.isclose(float(volume),solid.capacity_m3,rtol=2e-9,atol=1e-9):
        raise ArithmeticError('tank free-surface envelope slice volume disagrees with solid')
    return best, min(float(solid.capacity_m3),max(0.,best_volume))

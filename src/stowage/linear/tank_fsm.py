"""Maximum upright free-surface inertia of an actual tank solid.

A curved tank's widest free surface need not occur at half fill or at a sounding
quarter. Between successive polygon vertex heights, its waterplane area A is
linear, transverse first moment Q is quadratic, and raw second moment I is
cubic. The central inertia is I-Q²/A. We find stationary points of that rational
function and include both one-sided endpoint limits: a tank just short of full
may have a large free surface although the exactly full tank has none.

A Bernstein polynomial's values lie within the convex hull of its coefficients.
That property supplies an independent upper envelope for I-Q²/A; subdivision
closes the gap to the best evaluated value before returning a conservative
reserve. Numerical roundoff receives a separate small upward guard. This is
continuous geometry integration, not a sampled-fill approximation. Sources:
IMO 2008 IS Code Part B3.1; Farin, Curves and Surfaces for CAGD (2002), Bézier
convex-hull property. Multiplying the returned m^4 by liquid density gives kg m.
"""
from __future__ import annotations

import heapq
from math import comb

import numpy as np
from numpy.polynomial.polynomial import polyadd,polyder,polymul,polyroots,polyval


def _shift(coeff, offset, scale=1.):
    result=np.zeros(len(coeff),dtype=float)
    for power,value in enumerate(coeff):
        for target in range(power+1):
            result[target]+=value*comb(power,target)*offset**(power-target)*scale**target
    return result


def _bernstein_range(coeff, left, right):
    """Convex-hull enclosure on a subinterval, for degree at most three."""
    power=_shift(coeff,left,right-left)
    degree=len(power)-1
    bernstein=np.array([sum(power[k]*comb(j,k)/comb(degree,k) for k in range(j+1))
                        for j in range(degree+1)])
    return float(bernstein.min()),float(bernstein.max())


def _upper(inertia,area,first,left,right):
    _,imax=_bernstein_range(inertia,left,right)
    qmin,qmax=_bernstein_range(first,left,right)
    amax=max(float(polyval(left,area)),float(polyval(right,area)))
    if amax<=0: return 0.
    qabs=0. if qmin<=0<=qmax else min(abs(qmin),abs(qmax))
    return max(0.,imax-qabs*qabs/amax)


def _value(inertia,area,first,u):
    a=float(polyval(u,area))
    if a<=0: return 0.
    return max(0.,float(polyval(u,inertia))-float(polyval(u,first))**2/a)


def _section_coefficients(solid):
    # Import only during a call: physics may itself import this helper while
    # assembling a model. No circular import is needed at module initialisation.
    from stowage.linear.physics import _polygon_upright_polynomials
    sections=solid.sections_yz_m
    knots=np.unique(np.concatenate([poly[:,1] for section in sections for poly in section]))
    left=knots[:-1]; widths=np.diff(knots); mid=(knots[:-1]+knots[1:])/2
    size=len(left)
    inertia=np.zeros((size,4)); area=np.zeros((size,2)); first=np.zeros((size,3))
    y_origin=(solid.y_min_m+solid.y_max_m)/2
    absolute_scale=0.
    for section,weight in zip(sections,solid.integration_weights_m,strict=True):
        for polygon in section:
            local=np.array(polygon,dtype=float,copy=True); local[:,0]-=y_origin
            z,_,_,raw,aw,qw,_,_=_polygon_upright_polynomials(local)
            indices=np.searchsorted(z,left,side='right')-1
            active=(indices>=0)&(indices<len(z)-1)
            if not active.any(): continue
            selected=np.flatnonzero(active)
            for target,coefficients in ((inertia,raw),(area,aw),(first,qw)):
                coefficients=np.asarray(coefficients,dtype=float)
                coefficients=coefficients[indices[selected]]
                delta=left[selected]-z[indices[selected]]
                spans=widths[selected]
                shifted=np.zeros_like(coefficients)
                for power in range(coefficients.shape[1]):
                    for degree in range(power+1):
                        shifted[:,degree]+=coefficients[:,power]*comb(power,degree)*delta**(power-degree)*spans**degree
                target[selected]+=weight*shifted
                if target is inertia:
                    absolute_scale=max(absolute_scale,float(abs(weight)*np.max(np.sum(abs(shifted),axis=1))))
    return inertia,area,first,absolute_scale


def max_slack_fsm_m4(solid, *, relative_tolerance=1e-9, absolute_tolerance=1e-9):
    """Conservative supremum over every upright fill strictly empty-to-full.

    Rectangular tanks have the same inertia at every slack fill. Section tanks
    retain their actual longitudinal quadrature and polygon boundaries. The
    returned upper envelope differs from a lower candidate maximum by at most
    the requested tolerance, plus the stated floating-point roundoff guard.
    An unsupported solid raises ValueError rather than using its bounding box.
    """
    if not np.isfinite([relative_tolerance,absolute_tolerance]).all() or min(relative_tolerance,absolute_tolerance)<=0:
        raise ValueError('FSM tolerances must be finite and positive')
    if getattr(solid,'is_rectangular',False):
        return float(solid.sounding(solid.capacity_m3/2).fsm_m4)
    if not all(hasattr(solid,key) for key in ('sections_yz_m','integration_weights_m','y_min_m','y_max_m')):
        raise ValueError('maximum slack FSM requires rectangular or sectional tank geometry')
    inertia,area,first,scale=_section_coefficients(solid)
    best=0.; heap=[]
    for index,(p,a,q) in enumerate(zip(inertia,area,first,strict=True)):
        if max(a[0],a.sum())<=0: continue
        derivative=polyadd(polymul(polyder(p),polymul(a,a)),
            polyadd(-2*polymul(polymul(q,polyder(q)),a),polymul(polymul(q,q),polyder(a))))
        roots=polyroots(derivative)
        points=[0.,.5,1.,*[r.real for r in roots if abs(r.imag)<1e-8 and 0<r.real<1]]
        best=max(best,*[_value(p,a,q,u) for u in points])
        heapq.heappush(heap,(-_upper(p,a,q,0.,1.),index,0.,1.))
    tolerance=max(absolute_tolerance,relative_tolerance*max(1.,best))
    iterations=0
    while heap and -heap[0][0]>best+tolerance:
        neg_upper,index,left,right=heapq.heappop(heap)
        middle=(left+right)/2
        if middle in (left,right):
            raise ArithmeticError('FSM upper-envelope refinement exhausted floating-point resolution')
        p,a,q=inertia[index],area[index],first[index]
        best=max(best,_value(p,a,q,middle))
        for lo,hi in ((left,middle),(middle,right)):
            upper=_upper(p,a,q,lo,hi)
            if upper>best: heapq.heappush(heap,(-upper,index,lo,hi))
        iterations+=1
        if iterations>200_000:
            raise ArithmeticError('FSM upper-envelope refinement did not reach the requested tolerance')
    upper=max(best,-heap[0][0] if heap else 0.)
    # Lateral recentering limits cancellation in I-Q²/A. Guard the scale of
    # the accumulated raw moments as well as the final central inertia.
    guard=2e-10*max(1.,scale,float(np.max(np.sum(abs(inertia),axis=1))))
    return float(upper+guard)

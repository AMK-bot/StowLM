"""Uniform range enclosures of the native tensor cubic interpolant.

Qualification samples are NOT a uniform interpolation error bound. Instead
we enclose the actual polynomial used by TableGeometry/LiquidTable. Exact
rational basis conversion supplies Bernstein control coefficients; their
convex hull encloses every value, with explicit floating-operation reserves.
No native provider is patched and no table is built by this module.
"""
from __future__ import annotations

from fractions import Fraction
from functools import lru_cache
from itertools import product
import math
import weakref

import numpy as np

EPS = np.finfo(float).eps


def _mul(a,b):
    out = [Fraction(0)]*(len(a)+len(b)-1)
    for i,x in enumerate(a):
        for j,y in enumerate(b):
            out[i+j] += x*y
    return out


@lru_cache(maxsize=8192)
def basis_bernstein(nodes, lo, hi):
    """Enclose each cubic Lagrange basis's four Bernstein coefficients."""
    nodes = tuple(map(Fraction,nodes))
    lo,hi = Fraction(lo),Fraction(hi)
    values = [[Fraction(0)]*4 for _ in range(4)]
    for j in range(4):
        poly = [Fraction(1)]
        for k in range(4):
            if j!=k:
                den = nodes[j]-nodes[k]
                poly = _mul(poly,[(lo-nodes[k])/den,(hi-lo)/den])
        for i in range(4):
            values[i][j] = sum(poly[r]*Fraction(math.comb(i,r),math.comb(3,r))
                               for r in range(i+1))
    centre = np.array([[float(v) for v in row] for row in values])
    radius = np.array([[float(abs(Fraction(float(v))-v)) for v in row] for row in values])
    radius = np.nextafter(radius,math.inf)
    centre.flags.writeable = radius.flags.writeable = False
    return centre,radius


def _contract(centre,radius,weights,weight_radius,axis):
    c = np.tensordot(weights,centre,axes=(1,axis))
    abs_sum = np.tensordot(np.abs(weights),np.abs(centre),axes=(1,axis))
    r = (np.tensordot(np.abs(weights),radius,axes=(1,axis))
         +np.tensordot(weight_radius,np.abs(centre)+radius,axes=(1,axis)))
    # Four terms, at most four products and three additions. gamma(32)
    # reserves additional NumPy contraction/reduction operations as well.
    gamma = 32*EPS/(1-32*EPS)
    r = np.nextafter(r+gamma*(abs_sum+r)+np.finfo(float).tiny,math.inf)
    return c,r


def _basis_source_error(nodes,lo,hi):
    """Absolute error in source's three subtract/divide/product factors.

    The error recursion encloses source floating operations relative to exact
    rational evaluation, including cancellation near an interpolation node.
    The spacing is positive and representable; no relative-error assumption
    is made about the potentially vanishing basis value itself.
    """
    bound,errors = [],[]
    for j in range(4):
        size,error = 1.,0.
        for k in range(4):
            if j==k:
                continue
            den = abs(float(nodes[j])-float(nodes[k]))
            if den<=0:
                raise ValueError('repeated interpolation nodes')
            numerator = max(abs(lo-float(nodes[k])),abs(hi-float(nodes[k])))
            factor = numerator/den
            subtract_error = 2*EPS*(max(abs(lo),abs(hi))+abs(float(nodes[k])))+np.finfo(float).tiny
            denominator_error = 2*EPS*(abs(float(nodes[j]))+abs(float(nodes[k])))
            denlo = den-denominator_error
            if denlo<=0:
                raise ValueError('uncertain interpolation denominator')
            factor_error = ((subtract_error+factor*denominator_error)/denlo
                            +4*EPS*(factor+subtract_error/denlo))
            factor_upper = math.nextafter((numerator+subtract_error)/denlo*(1+8*EPS),math.inf)
            error = math.nextafter((error*(factor_upper+factor_error)+size*factor_error
                    +8*EPS*(size+error)*(factor_upper+factor_error))*(1+8*EPS),math.inf)
            size = math.nextafter(size*factor_upper*(1+8*EPS),math.inf)
        bound.append(size)
        errors.append(math.nextafter(error,math.inf))
    return np.array(bound),np.array(errors)


def _cell_bounds(axes, values, intervals):
    starts,bases,errors,sizes = [],[],[],[]
    for axis,(lo,hi) in zip(axes,intervals):
        middle = (lo+hi)/2
        start = int(np.clip(np.searchsorted(axis,middle)-2,0,len(axis)-4))
        nodes = tuple(map(float,axis[start:start+4]))
        starts.append(start)
        bases.append(basis_bernstein(nodes,float(lo),float(hi)))
        size,error = _basis_source_error(nodes,float(lo),float(hi))
        sizes.append(size); errors.append(error)
    slices = tuple(slice(s,s+4) for s in starts)
    original = np.asarray(values[slices],float)
    if original.ndim==3:
        original = original[...,None]
    centre,radius = original,np.zeros_like(original)
    # Contract axis zero, then original axis one (now index one), then
    # original axis two (now index two). Final axes are reversed but extrema
    # do not depend on their order.
    for index,(weights,weight_radius) in enumerate(bases):
        centre,radius = _contract(centre,radius,weights,weight_radius,index)
    lower = np.min(centre-radius,axis=(0,1,2))
    upper = np.max(centre+radius,axis=(0,1,2))
    # Difference between exact polynomial weights and floating source basis.
    expanded = [a+b for a,b in zip(sizes,errors)]
    total_size = np.einsum('i,j,k,ijkn->n',*expanded,np.abs(original),optimize=False)
    # Positive expansion; avoid subtracting two nearly equal products.
    weight_error = (
        np.einsum('i,j,k,ijkn->n',errors[0],expanded[1],expanded[2],np.abs(original),optimize=False)
        +np.einsum('i,j,k,ijkn->n',sizes[0],errors[1],expanded[2],np.abs(original),optimize=False)
        +np.einsum('i,j,k,ijkn->n',sizes[0],sizes[1],errors[2],np.abs(original),optimize=False))
    gamma = 512*EPS/(1-512*EPS)
    pad = np.nextafter(weight_error+gamma*total_size+np.finfo(float).tiny,math.inf)
    return np.nextafter(lower-pad,-math.inf),np.nextafter(upper+pad,math.inf)


@lru_cache(maxsize=128)
def _axis_lebesgue(axis_tuple,edge_tolerance,clip_edges):
    axis=np.asarray(axis_tuple,float)
    maximum=0.
    domain=list(map(float,axis))
    if not clip_edges:
        domain=[math.nextafter(domain[0]-edge_tolerance,-math.inf),*domain,math.nextafter(domain[-1]+edge_tolerance,math.inf)]
    for lo,hi in zip(domain[:-1],domain[1:]):
        start=int(np.clip(np.searchsorted(axis,(lo+hi)/2)-2,0,len(axis)-4))
        W,R=basis_bernstein(tuple(map(float,axis[start:start+4])),float(lo),float(hi))
        maximum=max(maximum,float(np.max(np.sum(np.abs(W)+R,axis=1))))
    return maximum*(1+1.e-10)


_VALUE_EXTREMA={}
def _value_extrema(values):
    # Native tables are immutable during a run. Keep an identity guard so a
    # freed array's id cannot reuse an unrelated cached maximum.
    key=id(values)
    cached=_VALUE_EXTREMA.get(key)
    if cached is not None and cached[0]() is values:
        return cached[1]
    maximum=np.max(np.abs(values),axis=(0,1,2))
    _VALUE_EXTREMA[key]=(weakref.ref(values),maximum)
    return maximum


def tensor_cubic_bounds(axes, values, box, *, max_cells=1024, edge_tolerance=1.e-10, clip_edges=True):
    """Enclose native cubic values on a box clipped to its actual grid.

    Splitting at every grid knot preserves the source stencil selector.
    Outside-grid poses are not table evaluations; callers must union a direct
    geometry enclosure. A broad-box fallback is explicit and conservative.
    """
    axes = tuple(np.asarray(a,float) for a in axes)
    if len(axes)!=3 or len(box)!=3:
        raise ValueError('three interpolation axes required')
    pieces=[]
    for axis,(lo,hi) in zip(axes,box):
        if len(axis)<4 or np.any(np.diff(axis)<=0) or not np.isfinite(axis).all():
            raise ValueError('invalid cubic grid')
        first,last=float(axis[0]),float(axis[-1])
        lo=max(float(lo),math.nextafter(first-edge_tolerance,-math.inf))
        hi=min(float(hi),math.nextafter(last+edge_tolerance,math.inf))
        if hi<lo:
            return dict(status='outside_grid',bounds=None,cells=0)
        if clip_edges:
            lo,hi=max(first,min(last,lo)),max(first,min(last,hi))
        cuts = [lo,*map(float,axis[(axis>lo)&(axis<hi)]),hi]
        pieces.append([(cuts[i],cuts[i+1]) for i in range(len(cuts)-1)] or [(lo,hi)])
    count=math.prod(map(len,pieces))
    if count>max_cells:
        # The maximum absolute cubic basis on its own grid cell is bounded
        # by the sum of its Bernstein coefficient magnitudes. Precompute the
        # maximum axis Lebesgue enclosure, then use triangle inequality.
        leb=1.
        for axis in axes:
            leb*=_axis_lebesgue(tuple(map(float,axis)),edge_tolerance,clip_edges)
        maximum=_value_extrema(values)*leb
        maximum=np.nextafter(maximum*(1+1.e-8)+1.e-12,math.inf)
        return dict(status='broad_grid_enclosure',bounds=[[-float(v),float(v)] for v in np.atleast_1d(maximum)],cells=count)
    lower=upper=None
    for intervals in product(*pieces):
        lo,hi=_cell_bounds(axes,values,intervals)
        lower=lo if lower is None else np.minimum(lower,lo)
        upper=hi if upper is None else np.maximum(upper,hi)
    return dict(status='bounded_native_cubic',bounds=[[float(a),float(b)] for a,b in zip(lower,upper)],cells=count)


def table_integral_bounds(table,q_interval,trim_interval,heel_interval,*,max_cells=1024):
    result=tensor_cubic_bounds(tuple(table.grid.values()),table.values,
                              (q_interval,trim_interval,heel_interval),max_cells=max_cells)
    if result['bounds'] is not None:
        result['integrals']=dict(zip(('volume','mx','my','mz'),result['bounds']))
    return result

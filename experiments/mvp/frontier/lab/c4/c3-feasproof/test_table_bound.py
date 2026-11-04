"""Actual cubic range checks, including stencil changes and cancellation."""
from fractions import Fraction
import importlib.util
from pathlib import Path

import numpy as np

_spec=importlib.util.spec_from_file_location('c4_table_bound',Path(__file__).with_name('table_bound.py'))
table_bound=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(table_bound)


def _native(axes,values,point):
    from stowage.checker.stability_tables import _basis_reference
    pairs=[_basis_reference(axis,x) for axis,x in zip(axes,point)]
    (i,wi),(j,wj),(k,wk)=pairs
    return np.einsum('i,j,k,ijkn->n',wi,wj,wk,values[i:i+4,j:j+4,k:k+4],optimize=False)


def test_bernstein_basis_coefficients_enclose_exact_rationals():
    nodes=(-1.,0.,1.,2.)
    C,R=table_bound.basis_bernstein(nodes,.1,.9)
    for t in (0.,.125,.5,.875,1.):
        x=Fraction(.1)+(Fraction(.9)-Fraction(.1))*Fraction(t)
        basis=np.array([(1-t)**3,3*t*(1-t)**2,3*t*t*(1-t),t**3])
        for j in range(4):
            exact=Fraction(1)
            for k in range(4):
                if j!=k:
                    exact*= (x-Fraction(nodes[k]))/(Fraction(nodes[j])-Fraction(nodes[k]))
            lo=float(basis@(C[:,j]-R[:,j]))
            hi=float(basis@(C[:,j]+R[:,j]))
            assert lo-1e-15<=float(exact)<=hi+1e-15


def test_native_cubic_enclosure_across_knots():
    rng=np.random.default_rng(19760101)
    axes=(np.array([-2.,-.5,0.,.7,2.,3.]),np.linspace(-1.,1.,5),np.linspace(0.,1.2,5))
    values=rng.normal(size=(6,5,5,4))*np.array([1.,1e6,1e-6,100.])
    for box in (((-.3,.4),(-.6,.4),(.1,.8)),((0.,0.),(0.,0.),(.6,.6)),
                ((-2.,3.),(-1.,1.),(0.,1.2))):
        result=table_bound.tensor_cubic_bounds(axes,values,box)
        for _ in range(64):
            point=[rng.uniform(lo,hi) for lo,hi in box]
            actual=_native(axes,values,point)
            assert all(lo<=v<=hi for v,(lo,hi) in zip(actual,result['bounds']))


def test_broad_fallback_and_outside_grid_are_explicit():
    axes=(np.linspace(0.,1.,6),)*3
    values=np.ones((6,6,6,1))
    result=table_bound.tensor_cubic_bounds(axes,values,((0.,1.),)*3,max_cells=1)
    assert result['status']=='broad_grid_enclosure'
    assert result['bounds'][0][0]<=1<=result['bounds'][0][1]
    result=table_bound.tensor_cubic_bounds(axes,values,((2.,3.),(0.,1.),(0.,1.)))
    assert result['status']=='outside_grid' and result['bounds'] is None
    result=table_bound.tensor_cubic_bounds(axes,values,((-5e-11,-1e-11),(0.,0.),(0.,0.)))
    assert result['bounds'][0][0]<=1<=result['bounds'][0][1]

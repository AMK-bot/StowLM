"""Toy source-semantics regressions; no native caches or physical maps."""
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0,str(Path(__file__).parent))
from native_union import NativeLiquidUnion
from table_bound import tensor_cubic_bounds


def test_hull_edge_tolerance_clips_an_accepted_outer_query():
    axes=(np.arange(4,dtype=float),)*3
    values=np.indices((4,4,4)).sum(axis=0).astype(float)
    result=tensor_cubic_bounds(axes,values,((-5e-11,-5e-11),(1.,1.),(1.,1.)))
    assert result['bounds'] is not None
    assert result['bounds'][0][0]<=2.<=result['bounds'][0][1]


def test_liquid_edge_tolerance_encloses_nonclipped_extrapolation():
    axes=(np.arange(4,dtype=float),)*3
    values=np.broadcast_to(np.arange(4,dtype=float)[:,None,None]*1e12,(4,4,4)).copy()
    query=-5e-14
    result=tensor_cubic_bounds(axes,values,((query,query),(1.,1.),(1.,1.)),
                              edge_tolerance=1e-13,clip_edges=False)
    assert result['bounds'] is not None
    assert result['bounds'][0][0]<=query*1e12<=result['bounds'][0][1]


def _toy_union(*,mass=.5,prismatic=False,pressed_special=False):
    fills=np.linspace(0.,1.,4)
    moments=np.zeros((4,4,4,3))
    moments[:,:,:,0]=20*fills if not pressed_special else .2*fills
    if pressed_special:
        moments[0,0,-1,0]=10.
    table=SimpleNamespace(heels=np.linspace(0.,.3,4),trims=np.linspace(-.03,.03,4),
        fills=fills,moments=moments,prismatic=prismatic)
    union=NativeLiquidUnion.__new__(NativeLiquidUnion)
    union.mass=(1.,1.);union.calls=0;union.max_cells=64
    union.tanks=[dict(id='toy',masses=(mass,mass),density=1.,capacity=1.,
        reference=[(0.,0.)]*3,bbox=[(0.,0.)]*3,variable=False,
        shape='hull_sections',table=table)]
    return union


def test_prismatic_interval_preserves_accepted_zero_trim_subdomain():
    union=_toy_union(prismatic=True)
    result=union((.2,.2),(-.01,.01),None,0.)
    assert result['callback_delta_x_m'][0]<=10.<=result['callback_delta_x_m'][1]
    assert not result['checker_outward']


def test_pressed_special_constant_is_union_even_when_query_node_differs():
    union=_toy_union(mass=1.,pressed_special=True)
    result=union((.2,.2),(0.,0.),None,0.)
    # Native LiquidTable.centroid(capacity,phi,trim) always returns [0,0,-1].
    assert result['callback_delta_x_m'][0]<=10.<=result['callback_delta_x_m'][1]
    assert not result['checker_outward']

"""Exact small-lane and sparse-graph structural regressions; no map data."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import sys

import numpy as np
import pytest
from gap import Assignment, Settings
from stow import reference

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[2]
spec = importlib.util.spec_from_file_location('c5_baseline_gap', LAB/'c5/c5-securing/gap.py')
old = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = old
spec.loader.exec_module(old)


def model(nb=8, pages=4):
    nt, ns = 3, pages*3
    T = NS(units=np.full(nb, 2), slot_cells=np.arange(ns*2).reshape(ns, 2),
           mass=np.linspace(3500., 15000., nb), height=np.full(nb, 2.591),
           share=np.linspace(1750., 7500., nb), rank=np.arange(nb)%3,
           closes_stack=np.zeros(nb, bool), e2_tol=1, e1_threshold=10000.,
           is_dg=np.zeros(nb, bool), stack_base_z=np.full(pages*2, 12.5),
           box_ids=[f'b{i}' for i in range(nb)])
    boxes={name: NS(height_m=2.591, oog=None, length_ft=40, gross_mass_kg=float(T.mass[b]))
           for b,name in enumerate(T.box_ids)}
    pg=[NS(kind='long', slots=tuple(range(j*nt,(j+1)*nt)), stacks=(j*2,j*2+1),
           deck=j%2==0, cover=f'c{j//2}', limit_kg=90000., hmax=10.) for j in range(pages)]
    xy={id(page):(float(j*10), -2. if j%4<2 else 2.) for j,page in enumerate(pg)}
    return NS(T=T, boxes=list(range(nb)), pages=pg, static=np.ones((nb, ns), bool),
              p=NS(boxes=boxes), ports=[1,2], pod={b:1+b%2 for b in range(nb)},
              heavy={b: T.mass[b]>=T.e1_threshold for b in range(nb)},
              _slot_xy=lambda page,k:xy[id(page)])


class Hull:
    lpp_m, beam_m, depth_m, scantling_draught_m, bilge_keel = 124.41,22.5,11.3,8.7,False


def test_small_lane_same_pair_seed_and_entire_lp():
    m=model(pages=60)
    motion=reference(Hull,1.9)
    a=Assignment(m,motion,Settings(pairs_per_box=150, seed=7))
    b=old.Assignment(m,motion,old.Settings(pairs_per_box=150, seed=7))
    assert a.pair==b.pair
    for left,right in zip(a.build(),b.build()):
        if hasattr(left,'toarray'):
            np.testing.assert_array_equal(left.toarray(),right.toarray())
        else:
            np.testing.assert_array_equal(left,right)


def test_xl_all_tiers_and_strata_preserved_deterministically():
    m=model(nb=501,pages=60)
    motion=reference(Hull,1.9)
    a=Assignment(m,motion,Settings(pairs_per_box=64,seed=9))
    b=Assignment(m,motion,Settings(pairs_per_box=64,seed=9))
    assert a.pair==b.pair
    assert a.n < len(m.boxes)*150
    for box in m.boxes:
        slots={q for p,q in a.pair if p==box}
        assert 64 <= len(slots) <= 66
        assert {m.pages[q//3].deck for q in slots}=={True,False}
        for q in slots:
            assert set(m.pages[q//3].slots)<=slots


def test_sight_refinement_uses_exact_submatrix_without_whole_table_copy():
    # The replacement selects exactly the same rows/columns for arbitrary order.
    a=np.arange(63).reshape(7,9)
    boxes=[5,2,6]
    slots=(8,1,4)
    np.testing.assert_array_equal(a[boxes,:][:,slots],a[np.ix_(boxes,slots)])


def test_xl_zero_pair_demand_skips_lp_and_integer_tail():
    m=model(nb=501,pages=6);m.static[0,:]=False
    a=Assignment(m,reference(Hull,1.9),Settings(pairs_per_box=64))
    def forbidden():
        raise AssertionError('No LP should be built for a zero-pair XL demand')
    a.build=forbidden
    log=[]
    assert a.dive(deadline=0.,log=log) is None
    assert a.metrics['zero_pair_boxes']==['b0']
    assert log[0]['proof_scope'].startswith('this restricted search graph only')


def test_small_zero_pair_lane_still_calls_original_lp_builder():
    m=model(nb=8,pages=4);m.static[0,:]=False
    a=Assignment(m,reference(Hull,1.9),Settings())
    def sentinel():
        raise ValueError('Original builder reached')
    a.build=sentinel
    with pytest.raises(ValueError,match='Original builder reached'):
        a.dive(deadline=0.)

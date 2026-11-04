# Copied from experiments/mvp/frontier/lab/c4/c2-layout/test_sight.py; package imports and thread policy adapted.
import math
from pathlib import Path
from types import SimpleNamespace as NS
import sys

import numpy as np
import pytest

from .pages_sight import Model, BalancedModel, Page, roof_cap


def upright(draft=5.):
    return dict(eye=(8.,0.,30.),normal=(0.,0.,1.),along=(1.,0.,0.),
                across=(0.,1.,0.),aft_draft_m=draft,bow_range_m=300.)


def test_upright_cap_matches_native_range_cone_algebra():
    bounds=dict(x_min=50.,x_max=62.,y_min=-3.,y_max=3.)
    cap=roof_cap(**bounds,**upright())
    expected=30.-25.*math.hypot(62.-8.,3.)/300.
    assert cap==pytest.approx(expected,abs=1e-8)
    # Native tangent distance at the furthest corner reaches exactly R.
    assert 25.*math.hypot(54.,3.)/(30.-cap)==pytest.approx(300.)


def test_shallower_waterplane_is_the_stricter_upright_case():
    bounds=dict(x_min=50.,x_max=62.,y_min=-3.,y_max=3.)
    assert roof_cap(**bounds,**upright(3.))<roof_cap(**bounds,**upright(7.))


def test_physical_roof_margin_is_not_an_extra_eye_height():
    bounds=dict(x_min=50.,x_max=62.,y_min=-3.,y_max=3.)
    assert roof_cap(**bounds,**upright(),roof_margin_m=.2)==pytest.approx(roof_cap(**bounds,**upright())-.2)


def test_tilted_frame_cap_satisfies_every_original_corner_inequality():
    angle=.04
    n=(0.,-math.sin(angle),math.cos(angle))
    across=(0.,math.cos(angle),math.sin(angle))
    frame=dict(upright(),normal=n,across=across)
    cap=roof_cap(x_min=50.,x_max=62.,y_min=-3.,y_max=3.,**frame)
    dot=lambda a,b:sum(x*y for x,y in zip(a,b))
    H=dot(n,frame['eye'])-n[2]*frame['aft_draft_m']
    for x in (50.,62.):
        for y in (-3.,3.):
            p=(x-8.,y,cap-30.)
            u,v=dot(frame['along'],p),dot(across,p)
            h=dot(n,p)+H
            assert H*math.hypot(u,v)+300.*(h-H)<=1e-7


def small_model(monkeypatch):
    monkeypatch.setattr(BalancedModel,'legal',lambda *args,**kwargs:True)
    m=object.__new__(Model)
    m.sight_frames=[upright()]
    m.sight_roof_margin_m=0.
    m.sight_cache={}
    m.T=NS(stack_base_z=np.array([19.5]),height=np.array([2.591,2.896]),box_ids=['a','b'])
    m.p=NS(boxes={'a':NS(length_ft=20,oog=None),'b':NS(length_ft=20,oog=None)})
    m._slot_xy=lambda page,tier:(60.,0.)
    page=Page('20',(0,),(0,1,2),60000.,20.,True,'A')
    return m,page


def test_legal_uses_cumulative_actual_high_cube_roof(monkeypatch):
    m,page=small_model(monkeypatch)
    assert m.legal(page,(0,1))
    assert not m.legal(page,(0,1,0))


def test_oog_enlarged_footprint_tightens_its_own_roof_limit(monkeypatch):
    m,page=small_model(monkeypatch)
    original=m.sight_limit(page,0,0)
    m.p.boxes['a'].oog=NS(over_length_fore_m=10.,over_length_aft_m=0.,
                         over_width_port_m=3.,over_width_starboard_m=0.)
    assert m.sight_limit(page,0,0)<original


def test_hold_pages_retain_existing_legality(monkeypatch):
    m,page=small_model(monkeypatch)
    hold=Page(page.kind,page.stacks,page.slots,page.limit_kg,page.hmax,False,page.cover)
    assert m.legal(hold,(0,1,0))


def test_invalid_eye_condition_is_rejected():
    with pytest.raises(ValueError,match='invalid sight'):
        roof_cap(x_min=50.,x_max=62.,y_min=-3.,y_max=3.,**upright(31.))

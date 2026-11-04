# Provenance: copied from experiments/mvp/frontier/lab/c3/c2-layout/test_pages_lp.py.
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np

from .closed_pages_lp import Model, PreviousModel


def test_closed_design_ordinary_heavy_mask_is_a_construction_restriction(monkeypatch):
    monkeypatch.setattr(PreviousModel,'_static',lambda self:np.ones((2,3),dtype=bool))
    m=object.__new__(Model)
    m.closed_hold={'A'};m.boxes=[0,1]
    m.T=SimpleNamespace(e6_threshold=20.,mass=[30.,10.],box_ids=['H','L'],slot_deck=np.array([False,False,True]))
    ordinary=SimpleNamespace(dg=None,oog=None,special_stowage=[])
    m.p=SimpleNamespace(boxes={'H':ordinary,'L':ordinary})
    out=m._static()
    assert out[0,:2].all() and not out[0,2]
    assert out[1,:].all()

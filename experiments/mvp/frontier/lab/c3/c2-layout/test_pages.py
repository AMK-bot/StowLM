from types import SimpleNamespace
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from pages import Model, BaseModel


def test_shut_hold_mask_preserves_deck_and_shared_unary_cache(monkeypatch):
    shared = np.ones((2, 3), dtype=bool)
    monkeypatch.setattr(BaseModel, '_static', lambda self: shared)
    model = object.__new__(Model)
    model.closed_hold = {'A'}
    model.T = SimpleNamespace(slot_ids=['hold_A', 'hold_B', 'deck_A'],
        slot_stacks=np.array([[0,-1],[1,-1],[2,-1]]),
        stack_deck=[False,False,True], stack_cover=['A','B','A'])
    table = model._static()
    assert not table[:,0].any()
    assert table[:,1:].all()
    assert shared.all()


def test_shut_hold_deck_has_no_inherited_port_restriction():
    model = object.__new__(Model)
    model.closed_hold = {'A'}
    model.layout = {'A':1,'B':1,'C':2}
    model.layout_deck = 'no_restow'
    model.ports = [1,2]
    page = lambda cover,deck: SimpleNamespace(cover=cover,deck=deck)
    assert model.port_of(page('A',True)) is None
    assert model.port_of(page('B',True)) == 1
    assert model.port_of(page('C',True)) is None
    assert model.port_of(page('C',False)) == 2

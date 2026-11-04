"""Decoding a joint physical solution must preserve the modeled cargo moments."""
from types import SimpleNamespace
import numpy as np


def test_joint_decode_does_not_resort_light_box_above_heavy():
    from stow import to_assignment
    table = SimpleNamespace(box_ids=['light', 'heavy'], slot_ids=['low', 'high'],
        closes_stack=np.array([False,False]), rank=np.array([0,0]), mass=np.array([1.,2.]))
    model = SimpleNamespace(T=table, pages=[SimpleNamespace(slots=[0,1])],
        heavy=[False,True],pod=[1,1],legal=lambda *a,**k: True)
    solver = SimpleNamespace(slot_page={0:[(0,0)],1:[(0,1)]},
        preserve_prefix=lambda j,boxes: boxes, stack_ratio=lambda page,boxes: 0.)
    placed = {0:0,1:1}
    joint, illegal = to_assignment(model, placed, solver, preserve_order=True)
    regular, _ = to_assignment(model, placed, solver)
    assert not illegal and joint == {'light':'low','heavy':'high'}
    assert regular == {'heavy':'low','light':'high'}

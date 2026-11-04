"""Restricted-master tests; all fixtures are synthetic, with no map data."""
import importlib.util
from pathlib import Path
import sys
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch


_spec = importlib.util.spec_from_file_location('_cgscale_rmp_tests', Path(__file__).with_name('rmp_method.py'))
rmp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rmp
_spec.loader.exec_module(rmp)


def pool(boxes, columns):
    return SimpleNamespace(boxes=boxes, stacks=sorted({stack for _, stack in columns}), banned=set(),
                           pages=[SimpleNamespace(stacks=(stack,)) for _, stack in columns],
                           columns=[SimpleNamespace(page=i, boxes=tuple(contents))
                                    for i, (contents, _) in enumerate(columns)])


def test_rmp_exact_cover_cannot_share_stack():
    model = pool([0, 1], [((0,), 0), ((1,), 0), ((0, 1), 1)])
    chosen, record = rmp.restricted_master(model, {0: 1., 1: 1.}, deadline=monotonic()+2.)
    assert chosen == [2]
    assert record['status'] == 'OPTIMAL'
    assert record['chosen'] == 1


def test_fractional_cover_need_not_have_integer_cover():
    model = pool([0, 1, 2], [((0, 1), 0), ((1, 2), 1), ((0, 2), 2)])
    chosen, record = rmp.restricted_master(model, {0: .5, 1: .5, 2: .5}, deadline=monotonic()+2.)
    assert chosen is None
    assert record['status'] == 'INFEASIBLE'


def test_banned_pattern_and_expired_deadline():
    model = pool([0], [((0,), 0)])
    model.banned.add(0)
    chosen, record = rmp.restricted_master(model, {}, deadline=monotonic()+2.)
    assert chosen is None and record['status'] == 'UNCOVERED_POOL'
    with patch.object(rmp, 'monotonic', return_value=2.):
        chosen, record = rmp.restricted_master(model, {}, deadline=1.)
    assert chosen is None and record['status'] == 'DEADLINE'


def test_rmp_generate_only_at_complete_root():
    model = object.__new__(rmp.Model)
    model.boxes, model.stacks, model.seed_chosen = [0], [0], None
    root = dict(uncovered=0., x={})
    with patch.object(rmp._OriginalModel, 'generate', return_value=root):
        with patch.object(rmp, 'restricted_master', return_value=([3], {'seconds': .1})) as solve:
            model.generate(active_boxes=set(), active_stacks={0}, deadline=10.)
            assert not solve.called
            model.generate(active_boxes={0}, active_stacks={0}, deadline=10.)
            assert model.seed_chosen == [3]
            assert len(model.rmp_history) == 1

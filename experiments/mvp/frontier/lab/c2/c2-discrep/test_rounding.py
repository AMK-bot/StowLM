"""Small structural checks; no instance files, checker patches or heavy jobs."""
from dataclasses import dataclass
import importlib.util
from pathlib import Path
from time import monotonic

import numpy as np
import pytest

_spec = importlib.util.spec_from_file_location(
    '_c2_discrep_rounding_test_module', Path(__file__).with_name('rounding.py'))
rounding = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rounding)


@dataclass(frozen=True)
class Column:
    page: int
    boxes: tuple
    cost: float = 0.


@dataclass(frozen=True)
class Page:
    stacks: tuple


class ToyModel:
    """Either box fits either stack; mirror heel, assignment-invariant height."""
    def __init__(self):
        self.boxes = [0, 1]
        self.stacks = [0, 1]
        self.banned = set()
        self.feature_names = ['heel_tm', 'vertical_tm']
        self.columns = [Column(0, (0,)), Column(0, (1,)),
                        Column(1, (0,)), Column(1, (1,))]
        self.pages = [Page((0,)), Page((1,))]

    def features(self, column):
        mass = 1.+column.boxes[0]
        return np.array([(-1. if column.page == 0 else 1.)*mass, mass])


@pytest.fixture
def root():
    return dict(x={k: .5 for k in range(4)}, uncovered=0.)


def assert_structural(model, point):
    boxes = {b: 0. for b in model.boxes}
    stacks = {j: 0. for j in model.stacks}
    for k, value in point.items():
        assert -1e-7 <= value <= 1.+1e-7
        for b in model.columns[k].boxes:
            boxes[b] += value
        for j in model.pages[model.columns[k].page].stacks:
            stacks[j] += value
    assert all(abs(value-1.) <= 1e-7 for value in boxes.values())
    assert all(value <= 1.+1e-7 for value in stacks.values())


@pytest.mark.parametrize('variant', ['iterated', 'walk', 'cp'])
def test_completion_exact_cover_and_vertical_moment(root, variant):
    model = ToyModel()
    chosen, info = rounding.round_lp(model, root, deadline=monotonic()+2.,
                                     seed=0, variant=variant)
    assert chosen is not None, info
    assert_structural(model, {k: 1. for k in chosen})
    assert info['completion']['status'] in ('OPTIMAL', 'FEASIBLE')
    assert info['completion']['drift']['vertical_tm'] == pytest.approx(0.)
    # Either exact cover has a heel error of one; zero is fractionally possible.
    assert abs(info['completion']['drift']['heel_tm']) == pytest.approx(1.)


@pytest.mark.parametrize('variant', ['iterated', 'walk'])
def test_floating_walk_keeps_structural_and_protected_rows(root, variant):
    model = ToyModel()
    point, info = rounding._floating_walk(model, root,
                                          deadline=monotonic()+2., seed=0,
                                          variant=variant)
    assert_structural(model, point)
    assert info['steps'] == 1
    assert info['dropped'] == ['heel_tm']
    assert 'vertical_tm' in info['protected']
    assert info['walk_drift']['vertical_tm'] == pytest.approx(0.)
    assert info['fractional_remaining'] == 0


def test_karmarkar_karp_recovers_zero_partition():
    values = [8, 7, 6, 5]
    signs = rounding._kk_signs(values)
    assert set(signs) == set(range(len(values)))
    assert all(sign in (-1, 1) for sign in signs.values())
    assert sum(signs[i]*value for i, value in enumerate(values)) == 0


def test_expired_deadline_returns_valid_witness(root):
    model = ToyModel()
    model.rounding_witness = [0, 3]
    # Mandatory columns survive a cap smaller than their root support.
    model.rounding_pool_cap = 3
    chosen, info = rounding.round_lp(model, root, deadline=monotonic()-1.,
                                     seed=0, variant='cp')
    assert chosen == model.rounding_witness
    assert_structural(model, {k: 1. for k in chosen})
    completion = info['completion']
    assert completion['fallback_witness'] is True
    assert completion['pool_columns'] == len(root['x'])
    assert completion['witness_columns'] == 2
    assert completion['drift']['vertical_tm'] == pytest.approx(0.)


@pytest.mark.parametrize('invalid', [[0, 1], [0]])
def test_expired_deadline_rejects_invalid_witness(root, invalid):
    model = ToyModel()
    model.rounding_witness = invalid
    chosen, info = rounding.round_lp(model, root, deadline=monotonic()-1.,
                                     seed=0, variant='cp')
    assert chosen is None
    assert info['completion']['witness_columns'] == 0
    assert info['completion']['fallback_witness'] is False

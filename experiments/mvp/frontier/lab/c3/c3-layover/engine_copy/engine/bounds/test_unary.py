"""Every legal DG footprint is retained without changing native tables."""
from dataclasses import dataclass

import numpy as np
import pytest

from .unary import optimistic_dg_tables


@dataclass
class SampleTables:
    static: np.ndarray
    is_dg: np.ndarray
    length: np.ndarray
    slot_n: np.ndarray
    share: np.ndarray


def test_dg_widening_is_superset_immutable_and_independent():
    tables = SampleTables(
        static=np.array([[False, False, True, False],
                         [False, True, False, False],
                         [True, False, False, False],
                         [False, False, False, False]]),
        is_dg=np.array([True, True, False, True]),
        length=np.array([20, 40, 20, 45]), slot_n=np.array([1, 2, 1, 2]),
        share=np.array([10000., 5000., 10000., 5000.]))
    before = tables.static.copy()
    relaxed = optimistic_dg_tables(tables)
    assert relaxed is not tables
    assert relaxed.static is not tables.static
    assert np.array_equal(tables.static, before)
    assert np.all(relaxed.static | ~before)
    assert relaxed.static[0].tolist() == [True, False, True, False]
    assert relaxed.static[1].tolist() == [False, True, False, True]
    assert relaxed.static[3].tolist() == [False, True, False, True]
    assert np.array_equal(relaxed.static[2], tables.static[2])
    assert relaxed.share is tables.share
    relaxed.static[:] = False
    assert np.array_equal(tables.static, before)


def test_ordinary_only_tables_are_unchanged():
    tables = SampleTables(np.zeros((1, 2), dtype=bool), np.array([False]),
                          np.array([20]), np.array([1, 2]), np.array([10000.]))
    assert optimistic_dg_tables(tables) is tables


@pytest.mark.parametrize('length', [20, 40, 45])
def test_all_production_physical_footprints_included(length):
    from stowage.feasibility.tables import build_tables
    from stowage.model.fixtures import build_instance
    from stowage.conventions import cells_of_slot
    tables = build_tables(build_instance())
    b = 0
    # Simulate the motivating total unary rejection without relying on a
    # specific UN entry or a mutable saved stow. Numeric/geometry arrays keep
    # their production representation; only this test-owned DG row is altered.
    tables.is_dg[b] = True
    tables.length[b] = length
    tables.static[b] = False
    relaxed = optimistic_dg_tables(tables)
    for s, slot in enumerate(tables.slot_ids):
        expected = len(cells_of_slot(slot)) == (1 if length == 20 else 2)
        assert relaxed.static[b, s] == expected
    assert not tables.static[b].any()

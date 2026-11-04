"""Simple matching families must retain exact feasibility without graph-solver overhead."""
import itertools

import numpy as np
import pytest

from stowage.feasibility.capacity import ClassFlow


@pytest.mark.parametrize("compat", [
    [[True, True, False]], [[True], [False], [True]], [[True]],
])
def test_single_family_flow_matches_exhaustive_assignments_without_solver(compat, monkeypatch):
    compat = np.array(compat, dtype=bool)
    flow = object.__new__(ClassFlow)
    flow.compat = compat
    flow.nb, flow.nc = compat.shape

    def unexpected_solver(*args, **kwargs):
        raise AssertionError("one-family matching should use exact sums rather than a graph solver")

    monkeypatch.setattr("stowage.feasibility.capacity.maximum_flow", unexpected_solver)
    for values in itertools.product(range(4), repeat=flow.nb + flow.nc):
        demand, capacity = np.array(values[:flow.nb]), np.array(values[flow.nb:])
        feasible, actual = flow.flow(demand, capacity)
        # Independent exhaustive integer assignment oracle for the small compatibility graph.
        expected = False
        for assignment in itertools.product(range(4), repeat=int(compat.sum())):
            matrix = np.zeros_like(actual)
            matrix[compat] = assignment
            if np.array_equal(matrix.sum(axis=1), demand) and np.all(matrix.sum(axis=0) <= capacity):
                expected = True
                break
        assert bool(feasible) == expected
        assert np.all(actual >= 0) and not actual[~compat].any()
        assert np.all(actual.sum(axis=0) <= capacity)
        assert np.all(actual.sum(axis=1) <= demand)
        if feasible:
            assert np.array_equal(actual.sum(axis=1), demand)

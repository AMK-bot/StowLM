"""The retained v1 necessary screen must not turn solver trouble into a cut."""

from collections import OrderedDict
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from stowage.feasibility import _legacy


def _bounds():
    """One feasible mass variable; no physics cache or qualification is needed."""
    bounds = _legacy.GlobalBounds.__new__(_legacy.GlobalBounds)
    bounds.model = SimpleNamespace(cell_ids=("0010102",))
    bounds.constraints = SimpleNamespace(
        a_cell=np.array([[1.]]), a_tank=np.zeros((1, 0)),
        const=np.zeros(1), lb=np.array([-np.inf]), ub=np.array([10.]),
    )
    bounds.available = True
    bounds._index = {"0010102": 0}
    bounds._tank_idx = np.zeros(0, dtype=int)
    bounds._tank_bounds = []
    bounds._stack_matrix = csr_matrix([[1.]])
    bounds._stack_caps = np.array([10.])
    bounds._boxes = {"box": SimpleNamespace(gross_mass_kg=1., length_ft=20)}
    bounds._discharge = frozenset()
    bounds._cache = OrderedDict()
    bounds.calls = bounds.lp_calls = bounds.lp_indeterminate = 0
    return bounds


@pytest.mark.parametrize("status", [1, 3, 4, 99, None])
def test_legacy_indeterminate_solver_status_does_not_reject(monkeypatch, status):
    result = SimpleNamespace(success=False)
    if status is not None:
        result.status = status
    monkeypatch.setattr(_legacy, "linprog", lambda *args, **kwargs: result)
    bounds = _bounds()
    ship = SimpleNamespace(placements=())

    assert bounds.violations(ship, ("box",)) == ()
    assert bounds.lp_indeterminate == 1
    # Cached answers must not count another solver event.
    assert bounds.violations(ship, ("box",)) == ()
    assert bounds.lp_indeterminate == 1
    assert bounds.lp_calls == 1


@pytest.mark.parametrize("success,status,expected", [
    (True, 0, ()),
    (False, 2, _legacy.GLOBAL_RULE_IDS),
])
def test_legacy_definite_solver_result(monkeypatch, success, status, expected):
    monkeypatch.setattr(_legacy, "linprog", lambda *args, **kwargs:
                        SimpleNamespace(success=success, status=status))
    bounds = _bounds()
    assert bounds.violations(SimpleNamespace(placements=()), ("box",)) == expected
    assert bounds.lp_indeterminate == 0


def test_legacy_diagnostic_counter_starts_at_zero():
    instance = SimpleNamespace(containers=(), discharge_list=(),
                               vessel=SimpleNamespace(hull=None))
    assert _legacy.GlobalBounds(instance).lp_indeterminate == 0

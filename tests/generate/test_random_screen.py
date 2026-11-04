"""D49 regression: generation uses the canonical, necessary C2 relaxation."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.generate import random_screen


def _inputs(monkeypatch, *, cargo_upper=100.0):
    tables = SimpleNamespace(
        cell_ids=["first", "second"], cell_index={"first": 0, "second": 1},
        stack_ids=["stack"], stack_cells=[[0, 1]],
        stack_lim_mixed=np.array([60.0]), stack_lim_20=np.array([80.0]),
        stack_lim_40=np.array([70.0]), share=np.array([50.0]),
    )
    model = SimpleNamespace(
        cell_ids=["second", "first"], tank_ids=["adjustable", "fixed"],
        tank_adjustable=np.array([True, False]), tank_capacity_kg=np.array([20.0, 30.0]),
        _tank_mass_bounds_kg={"adjustable": (2.0, 10.0)},
    )
    instance = SimpleNamespace(vessel=SimpleNamespace(hull=SimpleNamespace(stations=[1])))
    monkeypatch.setattr(random_screen, "band_for_instance", lambda _: "band")
    monkeypatch.setattr(random_screen, "build_linear_model", lambda _, *, band: model)
    calls = []

    def rows(actual_model, *, direction, margin_scale):
        calls.append((actual_model, direction, margin_scale))
        return SimpleNamespace(
            names=("cargo", "moment", "unbounded"),
            a_cell=np.array([[1., 1.], [2., 1.], [7., 9.]]),
            a_tank=np.array([[0., 0.], [1., 0.], [0., 0.]]),
            const=np.array([0., 0., 0.]), lb=np.array([0., 0., -np.inf]),
            ub=np.array([cargo_upper, 1000., np.inf]),
        )

    monkeypatch.setattr(random_screen, "linear_constraints", rows)
    return instance, tables, model, calls


def test_c2_explicitly_requests_necessary_direction_and_canonical_relaxation(monkeypatch):
    instance, tables, model, calls = _inputs(monkeypatch)
    screen = random_screen._necessary_screen(instance, tables)
    assert calls == [(model, "necessary", 1.0)]
    assert screen.names == ("cargo", "moment", "stack_mass@stack")
    np.testing.assert_equal(screen.A, [[1., 1.], [1., 2.], [1., 1.]])
    np.testing.assert_equal(screen.At, [[0.], [1.], [0.]])
    np.testing.assert_equal(screen.tank_lo, [2.])
    np.testing.assert_equal(screen.tank_hi, [10.])
    # A fractional completion is a necessary screen, not a discrete witness.
    assert screen.reference(np.zeros(2), np.ones(2, dtype=bool), 75.) is not None
    assert screen.reference(np.zeros(2), np.ones(2, dtype=bool), 81.) is None


def test_c2_uses_all_linear_rows_and_fixed_onboard_mass(monkeypatch):
    instance, tables, _, _ = _inputs(monkeypatch, cargo_upper=70.)
    screen = random_screen._necessary_screen(instance, tables)
    assert screen.reference(np.array([30., 0.]), np.array([False, True]), 40.) is not None
    assert screen.reference(np.array([30., 0.]), np.array([False, True]), 41.) is None


def test_c2_real_hull_with_unavailable_model_fails_closed(monkeypatch):
    instance, tables, _, _ = _inputs(monkeypatch)
    instance.vessel.hull.stations = []
    engine = SimpleNamespace(T=tables)
    diagnostics = {}
    assert random_screen.necessary_completion_failures(instance, engine=engine,
                                                     diagnostics=diagnostics) == ("c2.unavailable",)
    assert "strength stations" in diagnostics["error"]


def test_c2_hull_free_fixture_fails_closed_when_explicitly_requested():
    instance = SimpleNamespace(vessel=SimpleNamespace(hull=None))
    diagnostics = {}
    assert random_screen.necessary_completion_failures(instance, diagnostics=diagnostics) == ("c2.unavailable",)
    assert "hull" in diagnostics["error"]


@pytest.mark.parametrize("remaining, failed", [(60., ()), (90., ("c2",))])
def test_c2_reuses_engine_departure_state_and_records_lp(monkeypatch, remaining, failed):
    instance, tables, _, _ = _inputs(monkeypatch)
    marker = object()
    seen = []

    def state(ex):
        seen.append(ex)
        return np.zeros(2), np.ones(2, dtype=bool), remaining

    engine = SimpleNamespace(T=tables, _cur=SimpleNamespace(ex=marker), _global_state=state)
    diagnostics = {}
    assert random_screen.necessary_completion_failures(instance, engine=engine,
                                                     diagnostics=diagnostics) == failed
    assert seen == [marker]
    assert diagnostics["direction"] == "necessary"
    assert diagnostics["margin_scale"] == 1.0
    assert diagnostics["lp_calls"] >= 1

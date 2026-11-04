"""D74's rate LP retains source rows and counts internal transfers once."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.linear.model import LinearConstraintSet


def _case(monkeypatch, *, arrival=(200., 0., 50.), bounds=None, rows=None):
    import stowage.linear.model as linear
    import stowage.linear.operations as operations

    model = SimpleNamespace(tank_ids=('a', 'b', 'heel'),
        tank_adjustable=np.ones(3, dtype=bool), tank_capacity_kg=np.full(3, 500.),
        _tank_mass_bounds_kg=bounds or {})
    rows = rows or LinearConstraintSet(('physical_total',), np.zeros((1, 1)),
        np.array([[1., 1., 0.]]), np.zeros(1), np.array([200.]), np.array([200.]))
    monkeypatch.setattr(linear, 'linear_constraints', lambda *a, **kw: rows)
    monkeypatch.setattr(operations, 'mass_vectors', lambda *a, **kw: (np.zeros(1), np.zeros(3)))
    return model, SimpleNamespace(), SimpleNamespace(vessel=SimpleNamespace(tank_pairs=[])), dict(zip(model.tank_ids, arrival))


def _solve(case, capacity, **kwargs):
    from stowage.solver.ballast_rate_lp import solve_rate_ballast_lp
    model, ship, instance, arrival = case
    return solve_rate_ballast_lp(model, ship, instance, arrival_masses=arrival,
        heel_ids={'heel'}, capacity_kg=capacity, **kwargs)


def test_internal_transfer_uses_max_of_intake_and_discharge_not_sum(monkeypatch):
    case = _case(monkeypatch, bounds={'a': (0., 0.), 'b': (200., 200.)})
    result = _solve(case, 200.)
    assert result.status == 'optimal'
    assert result.objective == pytest.approx(200.)
    assert result.tank_masses_kg == pytest.approx({'a': 0., 'b': 200., 'heel': 50.})
    delayed = _solve(case, 199.)
    assert delayed.status == 'optimal'
    assert delayed.objective == pytest.approx(200.)


def test_pumping_delay_does_not_reject_a_physically_required_transfer(monkeypatch):
    case = _case(monkeypatch, bounds={'a': (0., 0.), 'b': (200., 200.)})
    result = _solve(case, 0.)
    assert result.status == 'optimal'
    assert result.objective == pytest.approx(200.)
    assert result.tank_masses_kg == pytest.approx({'a': 0., 'b': 200., 'heel': 50.})


def test_min_p_keeps_feasible_arrival_even_when_less_total_ballast_exists(monkeypatch):
    rows = LinearConstraintSet(('lower_displacement',), np.zeros((1, 1)),
        np.array([[1., 1., 0.]]), np.zeros(1), np.array([100.]), np.array([np.inf]))
    case = _case(monkeypatch, rows=rows)
    result = _solve(case, 500.)
    assert result.status == 'optimal'
    assert result.objective == pytest.approx(0.)
    assert result.tank_masses_kg == pytest.approx({'a': 200., 'b': 0., 'heel': 50.})


def test_pumped_mass_preference_cannot_relax_an_original_physics_row(monkeypatch):
    rows = LinearConstraintSet(('strength_floor', 'draught_ceiling'),
        np.zeros((2, 1)), np.array([[0., 1., 0.], [1., 1., 0.]]),
        np.zeros(2), np.array([201., -np.inf]), np.array([np.inf, 200.]))
    case = _case(monkeypatch, rows=rows)
    result = _solve(case, 500.)
    assert result.status == 'infeasible'
    assert set(result.binding) <= {'strength_floor', 'draught_ceiling'}


def test_heel_contents_are_fixed_and_excluded_from_pumped_mass(monkeypatch):
    case = _case(monkeypatch, arrival=(200., 0., 450.))
    result = _solve(case, 0.)
    assert result.status == 'optimal'
    assert result.objective == pytest.approx(0.)
    assert result.tank_masses_kg['heel'] == pytest.approx(450.)


def test_symmetry_is_an_lp_equality_instead_of_projecting_after_solving(monkeypatch):
    case = _case(monkeypatch)
    result = _solve(case, 100., symmetry_pairs=(('a', 'b'),))
    assert result.status == 'optimal'
    assert result.tank_masses_kg['a'] == pytest.approx(100.)
    assert result.tank_masses_kg['b'] == pytest.approx(100.)
    assert result.objective == pytest.approx(100.)
    delayed = _solve(case, 99., symmetry_pairs=(('a', 'b'),))
    assert delayed.status == 'optimal'
    assert delayed.objective == pytest.approx(100.)


def test_heel_fix_does_not_override_the_original_tank_window(monkeypatch):
    case = _case(monkeypatch, bounds={'heel': (60., 100.)})
    assert _solve(case, 500.).status == 'infeasible'


@pytest.mark.parametrize('capacity', [-1., float('nan'), float('inf')])
def test_invalid_capacity_fails_closed(monkeypatch, capacity):
    with pytest.raises(ValueError, match='capacity'):
        _solve(_case(monkeypatch), capacity)

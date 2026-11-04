"""Read-only joint-incumbent/C2 matrix comparison contracts."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.linear import LinearConstraintSet
from stowage.model.fixtures import build_instance
from stowage.solver.construct_audit import audit_c2_assignment


@pytest.mark.parametrize('ballast,passed', [(1000., True), (30000., False)])
def test_chosen_assignment_ballast_evaluates_existing_c2_rows(monkeypatch, ballast, passed):
    instance = build_instance()
    box = next(box for box in instance.containers if box.length_ft == 20)
    cells = ('0010102', '0030102')
    model = SimpleNamespace(cell_ids=cells, tank_ids=('WB-P',), tank_adjustable=np.array([True]))
    source = LinearConstraintSet(('mass_proxy',), np.array([[1., 0.]]), np.array([[1.]]),
        np.array([0.]), np.array([-np.inf]), np.array([20000.]))
    monkeypatch.setattr('stowage.linear.band_for_instance', lambda *a: None)
    monkeypatch.setattr('stowage.linear.build_linear_model', lambda *a, **kw: model)
    monkeypatch.setattr('stowage.linear.linear_constraints', lambda *a, **kw: source)
    screen = SimpleNamespace(A=np.array([[1., 0.], [1., 1.]]), At=np.array([[1.], [0.]]),
        const=np.zeros(2), lo=np.array([-np.inf, -np.inf]), hi=np.array([20000., 60000.]),
        names=('mass_proxy', 'stack_mass@one'), tank_lo=np.array([0.]), tank_hi=np.array([100000.]),
        u0=30000., _slack=lambda: np.ones(2)*1e-5)
    engine = SimpleNamespace(_global=screen, T=SimpleNamespace(cell_ids=cells,
        cell_index={cell: i for i, cell in enumerate(cells)}),
        _cur=SimpleNamespace(ref=SimpleNamespace(R=box.gross_mass_kg)),
        completion_check=lambda: {'c2': True})
    report = audit_c2_assignment(instance, {box.id: cells[0]}, {'WB-P': ballast}, engine)
    assert report['passed'] is passed
    assert report['source_rows_identical_to_current_necessary_model']
    assert report['cargo_mass_kg'] == box.gross_mass_kg
    assert report['chosen_cargo_fits_cell_cap']
    assert report['prefix_completion'] == {'c2': True}
    assert report['violation_count'] == int(not passed)
    if not passed:
        assert report['violations'][0]['name'] == 'mass_proxy'
        assert report['violations'][0]['value'] == box.gross_mass_kg+ballast


def test_missing_global_screen_is_explicit():
    report = audit_c2_assignment(build_instance(), {}, {}, SimpleNamespace())
    assert report['available'] is False

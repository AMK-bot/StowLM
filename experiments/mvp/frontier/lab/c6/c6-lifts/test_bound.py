"""Independent small controls for the new necessary securing/graph cuts."""
import importlib.util
from pathlib import Path

from ortools.sat.python import cp_model

spec = importlib.util.spec_from_file_location('c6_lifts_bound_test', Path(__file__).with_name('bound.py'))
bound = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bound)


def test_physical_support_components_include_shifted_long_boxes():
    c = bound.components([(0,), (1, 2), (2, 3), (4,)])
    assert c[1] == c[2] == c[3]
    assert c[0] != c[1] != c[4]


def test_active_columns_force_spreading_and_do_not_borrow_remote_capacity():
    model = cp_model.CpModel()
    x = model.new_int_var(0, 2, 'first')
    y = model.new_int_var(0, 2, 'remote')
    model.add(x+y == 2)
    bound.add_active_root_rows(model, [(x, 2, (0,), 400000.), (y, 2, (1,), 400000.)])
    model.minimize(y)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    assert solver.solve(model) == cp_model.OPTIMAL
    assert solver.value(x) == solver.value(y) == 1


def test_joined_columns_count_each_box_force_once():
    model = cp_model.CpModel()
    x = model.new_int_var(3, 3, 'long')
    bound.add_active_root_rows(model, [(x, 3, (0, 1), 400000.)])
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    assert solver.solve(model) == cp_model.OPTIMAL


def test_hall_hand_certificate_and_infeasible_case():
    row = bound.ports.enumerate_cover_masks([18, 18, 8, 8, 4, 4], [37, 14])
    assert row['cost_scaled'] == 18000
    assert bound.ports.enumerate_cover_masks([2], [2, 1])['cost_scaled'] is None

"""Regression tests for the actual helper used by v2's slot constructor.

The reference composition formula is feasibility._engine._slot_features:
short with no long -> stack_lim_20; long with no short -> stack_lim_40;
both -> stack_lim_mixed. Hold mixing remains forbidden independently of mass.
These tiny CP models test capacity branches, not full plan legality.
"""
from pathlib import Path
import importlib.util

import pytest
from ortools.sat.python import cp_model

SPEC = importlib.util.spec_from_file_location('cut_layout_v2', Path(__file__).with_name('cut_layout_v2.py'))
cuts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cuts)


def solve_case(*, short, long, mass_kg, allow_mixed=True,
               initial_kg=0., fixed_short=False, fixed_long=False):
    model = cp_model.CpModel()
    short_var = model.NewBoolVar('short_box')
    long_var = model.NewBoolVar('long_box')
    model.Add(short_var == int(short))
    model.Add(long_var == int(long))
    cuts._add_stack_mass_limits(
        model, mass_terms=[mass_kg // 10], short_terms=[short_var],
        long_terms=[long_var], fixed_short=fixed_short, fixed_long=fixed_long,
        initial_mass_kg=initial_kg, limit20=68000., limit40=34000.,
        limit_mixed=30000., allow_mixed=allow_mixed)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.max_time_in_seconds = 1.
    return solver.Solve(model) in (cp_model.FEASIBLE, cp_model.OPTIMAL)


@pytest.mark.parametrize('short,long,mass_kg', [
    (True, False, 60000), (True, False, 68000), (True, False, 68010),
    (False, True, 34000), (False, True, 34010), (False, True, 60000),
    (True, True, 30000), (True, True, 30010), (True, True, 60000),
])
def test_mass_limit_follows_engine_composition(short, long, mass_kg):
    expected_limit = 30000 if short and long else 68000 if short else 34000
    assert solve_case(short=short, long=long, mass_kg=mass_kg) == (mass_kg <= expected_limit)


def test_sixty_tonnes_short_only_is_not_limited_by_forty_foot_rating():
    # The former min(68t,34t,30t) bound incorrectly rejected this legal mass.
    assert 60000 > min(68000, 34000, 30000)
    assert solve_case(short=True, long=False, mass_kg=60000, allow_mixed=False)


def test_hold_mixing_forbidden_even_below_all_mass_limits():
    assert solve_case(short=True, long=True, mass_kg=20000, allow_mixed=True)
    assert not solve_case(short=True, long=True, mass_kg=20000, allow_mixed=False)


def test_retained_composition_selects_mixed_limit():
    # A new 20-foot box over retained long cargo must use the mixed rating.
    assert solve_case(short=True, long=False, mass_kg=10000,
                      fixed_long=True, initial_kg=20000)
    assert not solve_case(short=True, long=False, mass_kg=10010,
                          fixed_long=True, initial_kg=20000)


def test_retained_mass_consumes_only_matching_short_capacity():
    assert solve_case(short=True, long=False, mass_kg=38000,
                      fixed_short=True, initial_kg=30000)
    assert not solve_case(short=True, long=False, mass_kg=38010,
                          fixed_short=True, initial_kg=30000)


def solve_stack_order(lengths, ranks, *, tolerance=0):
    model = cp_model.CpModel()
    cells = list(range(len(lengths)))
    rank_at, short_at, long_at = {}, {}, {}
    for cell, length, rank in zip(cells, lengths, ranks):
        rank_at[cell] = model.NewIntVar(0, 4, f'r{cell}')
        short_at[cell] = model.NewBoolVar(f'short{cell}')
        long_at[cell] = model.NewBoolVar(f'long{cell}')
        model.Add(rank_at[cell] == rank)
        model.Add(short_at[cell] == int(length == 20))
        model.Add(long_at[cell] == int(length in (40, 45)))
    cuts._add_stack_order_constraints(model, cells=cells, rank_at=rank_at,
                                      short_at=short_at, long_at=long_at,
                                      tolerance=tolerance)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.max_time_in_seconds = 1.
    return solver.Solve(model) in (cp_model.FEASIBLE, cp_model.OPTIMAL)


@pytest.mark.parametrize('lengths,expected', [
    ([40, 20], False), ([45, 20], False), ([20, 40], True),
    ([20, 40, 20], False), ([20, 20, 40, 40], True),
])
def test_mixed_deck_allows_short_below_long_but_never_above(lengths, expected):
    assert solve_stack_order(lengths, [2] * len(lengths)) == expected


def test_nonzero_class_tolerance_cannot_accumulate_up_stack():
    # Each adjacent jump is one, but the top is two classes above the bottom.
    assert not solve_stack_order([20, 20, 20], [1, 2, 3], tolerance=1)
    assert solve_stack_order([20, 20, 20], [1, 2, 2], tolerance=1)


def test_zero_tolerance_preserves_descending_classes():
    assert solve_stack_order([20, 20, 20], [3, 2, 1])
    assert not solve_stack_order([20, 20, 20], [3, 1, 2])

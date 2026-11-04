from fractions import Fraction
import importlib.util
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('c4_necessary_physics_test', HERE/'physics_bound.py')
physics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(physics)


def test_integer_tank_rounding_is_outward_on_both_signs():
    coefficients = [Fraction(-37, 5), Fraction(17, 5), Fraction(-1, 10), Fraction(1, 10)]
    c, ub, guard = physics.rounded_tank_row(coefficients, [Fraction(51, 10)]*4)
    assert ub == [6]*4
    assert guard == 3
    for numerator in range(52):
        m = Fraction(numerator, 10)
        u = math.ceil(m)
        for raw, rounded in zip(coefficients, c):
            assert rounded*u <= raw*m+max(0, rounded)


def test_mass_rows_preserve_every_real_ballast_vector():
    constant = Fraction(12345, 10)
    for masses in [(Fraction(a, 3), Fraction(b, 7)) for a in range(15) for b in range(12)]:
        actual = constant+sum(masses)
        mapped = sum(math.ceil(m) for m in masses)
        assert mapped+math.floor(constant) <= math.ceil(actual)+len(masses)
        assert mapped+math.ceil(constant) >= math.floor(actual)


def test_weather_moment_rounding_preserves_real_points():
    upper_kg = Fraction(17, 2)
    z_lower = [Fraction(1, 3), Fraction(20, 3), Fraction(53, 5)]
    c, _, guard = physics.rounded_tank_row([z-upper_kg for z in z_lower], [20]*3)
    for masses in [(Fraction(a, 3), Fraction(b, 2), Fraction(1, 5))
                   for a in range(20) for b in range(20)]:
        real = sum((z-upper_kg)*m for z,m in zip(z_lower,masses))
        integer = sum(v*math.ceil(m) for v,m in zip(c,masses))
        assert integer <= real+guard


def test_all_four_checked_incumbents_have_feasible_physics_images():
    from collections import Counter
    import json
    from ortools.sat.python import cp_model
    from stowage.feasibility.tables import build_tables
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.moves import apply_plan
    rows = json.loads((HERE.parents[1]/'c3/c2-layout/comparison.json').read_text())['primary']
    assert len(rows) == 4
    for row in rows:
        instance = load_instance(row['input'])
        plan = MovePlan.model_validate(json.loads(Path(row['evidence']).read_text())['plan'])
        replay = apply_plan(instance, plan)
        assert replay.legal
        T = build_tables(instance)
        model = cp_model.CpModel()
        allocations = []
        for k, placement in enumerate(replay.final_state.ship.placements):
            s = T.slot_index[placement.slot]
            sig = tuple(sorted(Counter(int(j) for j in T.slot_stacks[s] if j >= 0).items()))
            allocations.append((model.new_int_var(1, 1, f'actual_{k}'), [placement.container_id], sig))
        meta = physics.attach(model, instance, T, allocations)
        assert meta['lashing_gm_lower_m'] == 0.
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = 3.
        status = solver.solve(model)
        assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE), (row['instance_id'], solver.status_name(status))

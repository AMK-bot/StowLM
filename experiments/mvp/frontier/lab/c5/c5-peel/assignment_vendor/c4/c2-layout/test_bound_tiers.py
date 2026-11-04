"""Necessary prefix-height bounds: arithmetic and real accepted stows."""
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('c4_tier_physics_test', HERE/'physics_bound.py')
physics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(physics)


def test_prefix_floor_rounds_down_and_allows_unequal_stack_bases():
    T = SimpleNamespace(stack_base_z=[2.1, 2.3], cell_stack=[0,1], cell_k=[2,1])
    actual = min(Fraction(2.1)+2*Fraction(2.591), Fraction(2.3)+Fraction(2.591))
    assert Fraction(physics.tier_support_floor(T, (0,1), 2.591)) <= actual
    assert physics.tier_support_floor(T, (0,), 2.591) > 2.1


def test_four_accepted_stows_obey_cell_prefixes_and_support_floors():
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
        boxes = {b.id:b for b in instance.containers}
        hmin = min(boxes[bid].height_m for bid in instance.load_list)
        occupied = {}
        for p in replay.final_state.ship.placements:
            slot = T.slot_index[p.slot]
            for c in T.slot_cells[slot]:
                if c >= 0:
                    assert int(c) not in occupied
                    occupied[int(c)] = p.container_id
        for c in occupied:
            j, tier = int(T.cell_stack[c]), int(T.cell_k[c])
            assert all(lower in occupied for lower in T.stack_cells[j][:tier])
        for p in replay.final_state.ship.placements:
            slot = T.slot_index[p.slot]
            cs = [int(c) for c in T.slot_cells[slot] if c >= 0]
            actual_supports = []
            for c in cs:
                j, tier = int(T.cell_stack[c]), int(T.cell_k[c])
                height = Fraction(float(T.stack_base_z[j]))
                height += sum((Fraction(boxes[occupied[lower]].height_m)
                              for lower in T.stack_cells[j][:tier]), Fraction())
                actual_supports.append(height)
            assert Fraction(physics.tier_support_floor(T, cs, hmin)) <= sum(actual_supports)/len(cs)

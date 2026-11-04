"""Check the cover-restow objective against a tiny explicit D15 scenario."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS

spec = importlib.util.spec_from_file_location('_c4_restow_test_permutation', Path(__file__).with_name('permutation.py'))
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def scenario(deck_aligned):
    from ortools.sat.python import cp_model
    cp = cp_model.CpModel()
    boxes, slots = ['a', 'b', 'c', 'd'], ['hold-A', 'hold-B', 'deck-A', 'deck-B']
    assignment = dict(zip(boxes, slots if not deck_aligned else [*slots[:2], slots[3], slots[2]]))
    use = {(b, s): cp.NewBoolVar(b+'@'+s) for b in boxes for s in slots}
    for (b, s), variable in use.items():
        cp.Add(variable == int(assignment[b] == s))
    t = NS(box_index={b: j for j, b in enumerate(boxes)}, slot_index={s: j for j, s in enumerate(slots)},
           slot_stacks=[(0,), (1,), (2,), (3,)], slot_k=[(0,)]*4,
           slot_deck=[False, False, True, True], stack_cover=['A', 'B', 'A', 'B'])
    objective = NS(future_hatch_cover=1., future_restow=1., future_restow_lifts_per_box=2)
    model = NS(T=t, p=NS(instance=NS(objective=objective)), pod={0: 1, 1: 2, 2: 2, 3: 1})
    work = module._projected_work(cp, use, model, assignment)
    cp.Minimize(work)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    assert solver.Solve(cp) == cp_model.OPTIMAL
    return solver.ObjectiveValue()/1000.


def test_later_box_over_early_hold_costs_two_projected_restow_lifts():
    assert scenario(False) == 6.
    assert scenario(True) == 4.

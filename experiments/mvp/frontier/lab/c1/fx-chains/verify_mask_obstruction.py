"""Reproduce the raw-parameter legacy mask rejection without changing rules."""
import ast
import hashlib
import json
from pathlib import Path


def main():
    import numpy as np
    from alns import Problem
    from sequence import order_legal
    from stowage.actions.space import Place
    from stowage.checker.rules import placement_violations
    from stowage.checker.rules_special import _parameter, _clearance_parameter
    from stowage.io import load_instance

    here = Path(__file__).resolve().parent
    path = Path('experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100034.json')
    module = ast.parse((here/'special_chain.py').read_text())
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'propose_hand_seed')
    assignment = next(ast.literal_eval(n.value) for n in function.body
                      if isinstance(n, ast.Assign) and any(isinstance(v, ast.Name)
                         and v.id == 'slots' for v in n.targets))
    problem = Problem(load_instance(path))
    engine, tables = problem.engine, problem.engine.T
    box = 'QZXU0000009'
    root_slots = list(engine.legal_slots_for_box(box))
    actions, stuck = order_legal(problem, assignment)
    pushed = 0
    try:
        for a in actions:
            assert engine.is_legal(a)
            engine.push(a)
            pushed += 1
        b, s = tables.box_index[box], tables.slot_index[assignment[box]]
        state = engine._cur
        exact = placement_violations(problem.instance, engine._ship(state),
                                     problem.boxes[box], assignment[box])
        fast = bool(engine._e9_batch(state, np.array([b]), np.array([s]), all_boxes=True)[0])
        row = dict(input=str(path), input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   legacy=not engine._freeze, box=box, slot=assignment[box],
                   prefix=[dict(box=a.box, slot=a.slot) for a in actions], stuck=stuck,
                   root_legal_slots=root_slots, slot_is_open=bool(s in engine._open_slots(state)),
                   raw_clearance=_parameter(problem.instance, 'expert.e9.over_width_length',
                                            'oog_clearance', 'minimum_clearance_m'),
                   resolved_clearance=_clearance_parameter(problem.instance, 'expert.e9.over_width_length',
                                                           'oog_clearance', 'minimum_clearance_m'),
                   exact_placement_violations=[v.model_dump(mode='json') for v in exact],
                   native_fast_e9=fast, native_is_legal=bool(engine.is_legal(Place(box, assignment[box]))))
        assert row['legacy'] and row['raw_clearance'] is None and row['resolved_clearance'] == 0.
        assert row['slot_is_open'] and not fast and not row['native_is_legal'] and not exact
        target = here/'results'/'mask-obstruction-probe.json'
        target.write_text(json.dumps(row, indent=2))
        print('PASS: exact placement accepts; legacy native fast path rejects; pinned input unchanged')
    finally:
        for _ in range(pushed):
            engine.pop()


if __name__ == '__main__':
    main()

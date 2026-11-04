"""Evaluate assignment-only MIP rows against an independently saved plan.

Only diagnostics are written. No checker, feasibility or constructor rule is
changed. Auxiliary occupancy/use20 values are computed from final placements.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from cache_runtime import redirect_cache


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    here = Path(__file__).resolve().parent
    if not output.is_relative_to(here):
        parser.error("output must be under construct-1028")
    output.parent.mkdir(parents=True, exist_ok=True)
    redirect_cache(here / "runtime")
    from stowage.checker.rules import check_static_rules
    from stowage.conventions import cells_of_slot
    from stowage.io import load_instance, load_plan
    from stowage.moves import apply_plan
    from stowage.solver.construct import _build_assignment_model

    started = time.monotonic()
    instance, plan = load_instance(args.input), load_plan(args.plan)
    replay = apply_plan(instance, plan)
    assert replay.legal, replay.violations
    ship = replay.final_state.ship
    placements = {p.container_id: p.slot for p in ship.placements}
    boxes = {b.id: b for b in instance.containers}
    occupied = {cell: cid for cid, slot in placements.items() for cell in cells_of_slot(slot)}
    model = _build_assignment_model(instance, physics=False, num_workers=1)
    solver = model.solver
    values = {}
    for (cid, slot), variable in model.x.items():
        values[variable.index()] = float(placements.get(cid) == slot)
    for cell, variable in model.occupied.items():
        values[variable.index()] = float(cell in occupied)
    for j, stack_id in enumerate(model.tables.stack_ids):
        stack_cells = [model.tables.cell_ids[int(ci)] for ci in model.tables.stack_cells[j]]
        use20 = any(cell in occupied and boxes[occupied[cell]].length_ft == 20 for cell in stack_cells)
        variable = solver.LookupVariable(f"use20_{j}")
        values[variable.index()] = float(use20)
    variables = list(solver.variables())
    unknown = [v.name() for v in variables if v.index() not in values]
    if unknown:
        raise ValueError(f"Unknown auxiliary variable values: {unknown}")
    violations = []
    for row in solver.constraints():
        contributions = [(v.name(), row.GetCoefficient(v), values[v.index()]) for v in variables
                         if row.GetCoefficient(v)]
        activity = sum(coefficient * value for _, coefficient, value in contributions)
        tolerance = 1e-7 * max(1., abs(activity))
        if activity < row.lb()-tolerance or activity > row.ub()+tolerance:
            violations.append(dict(name=row.name(), activity=activity, lower=row.lb(), upper=row.ub(),
                                   terms=contributions))
    missing = [(cid, placements.get(cid)) for cid in instance.load_list
               if (cid, placements.get(cid)) not in model.x]
    static = check_static_rules(instance, ship, call_index=instance.call_index)
    for (cid, slot), variable in model.x.items():
        solver.Add(variable == int(placements.get(cid) == slot))
    solver.SetTimeLimit(1000)
    fixed_status = solver.Solve()
    result = dict(input=str(args.input), plan=str(args.plan), instance_id=instance.id,
                  replay_legal=replay.legal, placements=placements, missing_assignments=missing,
                  binary_variables=len(model.x), variables=len(variables), rows=solver.NumConstraints(),
                  violated_rows=violations, fixed_assignment_solve_status=fixed_status,
                  static_hard=[v.model_dump(mode="json") for v in static.violations if v.severity == "hard"],
                  wall_s=time.monotonic()-started)
    output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({key: value for key, value in result.items() if key != "violated_rows"}))
    print(json.dumps(dict(violated_rows=violations)))


if __name__ == "__main__":
    main()

# Provenance: copied lab/c4/c2-layout/constructor_balanced.py:_integer; added engine work caps and certified page cuts.
"""Complete pool selection with soft moment targets, never a map certificate."""
from collections import defaultdict
from time import monotonic

from ortools.sat.python import cp_model

from .advanced_pages import configure_solver


def select(model, *, deadline, seed=0, hint=()):
    start = monotonic()
    cp = cp_model.CpModel()
    use, covering, stacks = {}, defaultdict(list), defaultdict(list)
    for k, column in enumerate(model.columns):
        if k in model.banned:
            continue
        x = cp.NewBoolVar(f'p{k}')
        use[k] = x
        for box in column.boxes:
            covering[box].append(x)
        for stack in model.pages[column.page].stacks:
            stacks[stack].append(x)
        if k in hint:
            cp.AddHint(x, 1)
        if k % 256 == 0 and monotonic() >= deadline:
            return None, dict(status='BUILD_SAFETY_DEADLINE', seconds=monotonic()-start)
    if any(not covering[b] for b in model.boxes):
        return None, dict(status='UNCOVERED_POOL', seconds=monotonic()-start)
    for box in model.boxes:
        cp.AddExactlyOne(covering[box])
    for variables in stacks.values():
        cp.AddAtMostOne(variables)
    for cut in model.page_cuts:
        if cut.get('certified'):
            cp.Add(sum(use[k] for k in cut['columns'] if k in use) <= cut['rhs'])
    objective = sum(int(round(1000*model.column_cost(model.columns[k], 3)))*x for k, x in use.items())
    slacks = []
    for name, coefficients, rhs in model.moment_rows(columns=use):
        scale = 100
        bound = max(1, sum(abs(round(scale*v)) for v in coefficients.values())+abs(round(scale*rhs)))
        slack = cp.NewIntVar(0, bound, 'target_'+name)
        cp.Add(sum(round(scale*v)*use[k] for k, v in coefficients.items()) <= round(scale*rhs)+slack)
        objective += 100*slack
        slacks.append((name, slack))
    cp.Minimize(objective)
    solver = cp_model.CpSolver()
    configure_solver(solver, model.work, deadline, seed)
    status = solver.Solve(cp)
    row = dict(status=solver.StatusName(status), seconds=monotonic()-start, columns=len(use),
               scope='generated restricted pool with soft moment targets; not a map bound')
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, row
    chosen = [k for k, x in use.items() if solver.Value(x)]
    row['moment_slacks_tm'] = {name: solver.Value(x)/100 for name, x in slacks}
    return (chosen if model._seed_columns_valid(chosen) else None), row

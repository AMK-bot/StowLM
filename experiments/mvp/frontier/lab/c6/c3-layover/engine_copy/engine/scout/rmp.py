# Provenance: copied from experiments/mvp/frontier/lab/c2/c2-cgscale/rmp_method.py.
"""Bounded exact cover of the current legal pattern pool; not a lift bound."""
from collections import defaultdict
from time import monotonic
from ortools.sat.python import cp_model

def restricted_master(model, root_x, *, deadline, seconds=2., cuts=()):
    """Find a complete stow in the existing pool; never generate new patterns.

    Returned indices still enter the base dive's legal/lashing/cover checks and
    then its real replay. CP-SAT has one worker and a two-second total allowance.
    """
    started = monotonic()
    stop = min(deadline, started+seconds)
    record = dict(status='DEADLINE', seconds=0., columns=0, chosen=0)

    def done(chosen=None):
        record['seconds'] = round(monotonic()-started, 6)
        record['chosen'] = len(chosen) if chosen is not None else 0
        return chosen, record

    if monotonic() >= stop:
        return done()
    problem = cp_model.CpModel()
    boxes, stacks = set(model.boxes), set(model.stacks)
    cover, stack_use = defaultdict(list), defaultdict(list)
    use = {}
    for k, column in enumerate(model.columns):
        page = model.pages[column.page]
        if (k in model.banned or not set(column.boxes) <= boxes or
                not set(page.stacks) <= stacks):
            continue
        variable = problem.NewBoolVar(f'p{k}')
        use[k] = variable
        for box in column.boxes:
            cover[box].append(variable)
        for stack in page.stacks:
            stack_use[stack].append(variable)
        if root_x.get(k, 0.) > .95:
            problem.AddHint(variable, 1)
        if len(use) % 256 == 0 and monotonic() >= stop:
            return done()
    record['columns'] = len(use)
    if any(not cover[box] for box in boxes):
        record['status'] = 'UNCOVERED_POOL'
        return done()
    for box in sorted(boxes):
        problem.AddExactlyOne(cover[box])
    for variables in stack_use.values():
        problem.AddAtMostOne(variables)
    for cut in cuts:
        if cut.get("certified"):
            problem.Add(sum(use[k] for k in cut["columns"] if k in use) <= cut["rhs"])
    remaining = stop-monotonic()
    if remaining <= 0.:
        return done()
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.max_time_in_seconds = remaining
    status = solver.Solve(problem)
    record['status'] = solver.StatusName(status)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return done()
    return done([k for k, variable in use.items() if solver.Value(variable)])


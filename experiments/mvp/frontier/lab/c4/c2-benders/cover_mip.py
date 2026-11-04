"""Integer recovery of the current-run restricted pattern master with HiGHS.

The page LP often reaches zero shortfall while a short combinatorial CP tail
has no incumbent. This solver keeps that LP relaxation and asks only for a
binary cover. It certifies neither map infeasibility nor minimum legal lifts.
Every selected pattern goes through the original gates and real replay.
"""
from collections import defaultdict
from time import monotonic
import warnings


def recover(model, root_x=None, *, deadline, seconds=30., hard_lcg=False):
    import numpy as np
    from scipy.optimize import milp, Bounds, LinearConstraint
    from scipy.sparse import csc_matrix
    started = monotonic()
    boxes = list(model.boxes)
    stacks = list(model.stacks)
    box_row = {b: k for k, b in enumerate(boxes)}
    stack_row = {j: len(boxes)+k for k, j in enumerate(stacks)}
    columns = []
    rows, cols, data = [], [], []
    cover = defaultdict(list)
    moment = getattr(model, 'lcg_target', None) is not None
    row_count = len(boxes)+len(stacks)+int(moment)
    for k, column in enumerate(model.columns):
        page = model.pages[column.page]
        if (k in model.banned or not set(column.boxes) <= box_row.keys()
                or not set(page.stacks) <= stack_row.keys()):
            continue
        j = len(columns)
        columns.append(k)
        for b in column.boxes:
            rows.append(box_row[b]); cols.append(j); data.append(1.)
            cover[b].append(k)
        for physical in page.stacks:
            rows.append(stack_row[physical]); cols.append(j); data.append(1.)
        if moment:
            rows.append(row_count-1); cols.append(j)
            data.append(float(model.lcg_moment(column)))
    record = dict(solver='scipy.milp/HiGHS', columns=len(columns),
                  rows=row_count, scope='current finite pattern pool only', physical_certificate=False,
                  lcg_mode='hard' if hard_lcg else 'soft')
    if any(not cover[b] for b in boxes):
        return None, dict(record, status='UNCOVERED_POOL', wall_s=monotonic()-started)
    remaining = min(float(seconds), deadline-monotonic())
    if remaining <= .001:
        return None, dict(record, status='DEADLINE', wall_s=monotonic()-started)
    lower = np.r_[np.ones(len(boxes)), np.full(len(stacks)+int(moment), -np.inf)]
    upper = np.ones(row_count)
    if moment:
        upper[-1] = -float(getattr(model, 'fixed_moment', 0.))
    # Native column generation treats its longitudinal target as a soft row.
    # Preserve that relaxation: positive slack is a search diagnostic, never a
    # trim certificate. The exact ballast search decides actual feasibility.
    soft_moment = moment and not hard_lcg
    variables = len(columns)+int(soft_moment)
    if soft_moment:
        rows.append(row_count-1); cols.append(len(columns)); data.append(-1.)
    matrix = csc_matrix((data, (rows, cols)), shape=(row_count, variables))
    objective = np.zeros(variables)
    integrality = np.ones(variables)
    bound_upper = np.ones(variables)
    if soft_moment:
        objective[-1] = 1.
        integrality[-1] = 0
        bound_upper[-1] = np.inf
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        result = milp(objective, integrality=integrality,
            bounds=Bounds(np.zeros(variables), bound_upper),
            constraints=LinearConstraint(matrix, lower, upper),
            options={'time_limit': remaining, 'node_limit': 10000, 'threads': 1})
    chosen = None
    if result.x is not None and np.max(np.abs(result.x[:len(columns)]-np.rint(result.x[:len(columns)]))) <= 1e-6:
        possible = [k for k, x in zip(columns, result.x) if x > .5]
        covered, occupied = set(), set()
        valid = True
        for k in possible:
            column = model.columns[k]; page = model.pages[column.page]
            if (covered.intersection(column.boxes) or occupied.intersection(page.stacks)
                    or not model.legal(page, column.boxes) or not model.lash_ok(page, column.boxes)):
                valid = False
                break
            covered.update(column.boxes); occupied.update(page.stacks)
        if valid and covered == set(boxes):
            chosen = possible
    record.update(status=int(result.status), message=result.message,
                  chosen=len(chosen or ()), wall_s=monotonic()-started,
                  nodes=int(getattr(result, 'mip_node_count', 0) or 0))
    if chosen is not None and moment:
        record['lcg_moment_excess_tm'] = float(model.fixed_moment+sum(
            model.lcg_moment(model.columns[k]) for k in chosen))
    if soft_moment and result.x is not None:
        record['lcg_slack_tm'] = float(result.x[-1])
    return chosen, record

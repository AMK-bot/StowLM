# Copied from experiments/mvp/frontier/lab/c2/c2-benders/benders.py; guards and integration callbacks added locally.
"""Sound fixed-page securing cuts, never map infeasibility claims.

Only pure whole pages on disjoint physical stacks support chain-local cuts.
Mixed patterns deliberately return no cut until their support proof is supplied.
Local ballast LP outcomes never enter this module's physical cut gate.
"""
from collections import defaultdict
from time import monotonic

from ortools.sat.python import cp_model

from ..finishing.early_bound import securing_bound


def alternatives(model, chosen, *, deadline, max_pairs=1200, focus=()):
    """Add whole-page relocations and paired single-box swaps to the pool.

    Pair swaps add both replacement patterns together, so the restricted
    master has immediate alternatives to a rejected page without needing a
    fresh whole-map dive. Feasibility is still decided by exact replay.
    """
    initial_columns = len(model.columns)
    focus = set(focus)
    chosen = sorted(chosen, key=lambda k: k not in focus)
    for k in chosen:
        c = model.columns[k]
        page = model.pages[c.page]
        for target, other_page in enumerate(model.pages):
            if monotonic() >= deadline:
                return len(model.columns)-initial_columns
            if page.kind == other_page.kind and len(c.boxes) <= len(other_page.slots):
                model.add(target, c.boxes)
    pairs = 0
    for ia, a in enumerate(chosen):
        ca = model.columns[a]
        pa = model.pages[ca.page]
        for b in chosen[ia+1:]:
            cb = model.columns[b]
            pb = model.pages[cb.page]
            if pa.kind != pb.kind:
                continue
            for i in range(len(ca.boxes)):
                for j in range(len(cb.boxes)):
                    if monotonic() >= deadline or pairs >= max_pairs:
                        return len(model.columns)-initial_columns
                    pairs += 1
                    one, two = list(ca.boxes), list(cb.boxes)
                    one[i], two[j] = two[j], one[i]
                    if model.legal(pa, one) and model.legal(pb, two):
                        model.add(ca.page, tuple(one))
                        model.add(cb.page, tuple(two))
            # A tall failing chain can also give a box to a shorter page.
            # Both replacement patterns enter the same exact-cover pool.
            for source, target in ((ca, cb), (cb, ca)):
                if len(source.boxes) <= 1 or len(target.boxes) >= len(model.pages[target.page].slots):
                    continue
                for i, moved in enumerate(source.boxes):
                    for j in range(len(target.boxes)+1):
                        if monotonic() >= deadline:
                            return len(model.columns)-initial_columns
                        one = source.boxes[:i]+source.boxes[i+1:]
                        two = target.boxes[:j]+(moved,)+target.boxes[j:]
                        if model.legal(model.pages[source.page], one) and model.legal(model.pages[target.page], two):
                            model.add(source.page, one)
                            model.add(target.page, two)
    return len(model.columns)-initial_columns


def restricted_master(model, cuts, *, seconds, seed, hint=()):
    """Set partitioning: one column per box, at most one per physical stack."""
    started = monotonic()
    master = cp_model.CpModel()
    x = [master.NewBoolVar(f"p{k}") for k in range(len(model.columns))]
    cover, stack_use = defaultdict(list), defaultdict(list)
    for k, c in enumerate(model.columns):
        for b in c.boxes:
            cover[b].append(x[k])
        for stack in model.pages[c.page].stacks:
            stack_use[stack].append(x[k])
    for b in model.boxes:
        if not cover[b]:
            return None, {"status": "missing_column", "box": model.T.box_ids[b]}
        master.AddExactlyOne(cover[b])
    for variables in stack_use.values():
        master.AddAtMostOne(variables)
    for cut in cuts:
        master.Add(sum(x[k] for k in cut["columns"]) <= cut["rhs"])
    # Search guidance only: retain most pages, flatten roofs, then reduce
    # restows. These costs are not reported as checked lift counts.
    hints = set(hint)
    terms = []
    for k, c in enumerate(model.columns):
        page = model.pages[c.page]
        roof = len(c.boxes)**2 if page.deck else 0
        cost = (0 if k in hints else 10000)+10*roof+int(c.cost)
        terms.append(cost*x[k])
        if k in hints:
            master.AddHint(x[k], 1)
    master.Minimize(sum(terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(.001, seconds)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    status = solver.Solve(master)
    result = {"status": solver.StatusName(status), "s": monotonic()-started,
              "columns": len(x), "cuts": len(cuts),
              "scope": "generated restricted pool only"}
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, result
    return [k for k in range(len(x)) if solver.Value(x[k])], result



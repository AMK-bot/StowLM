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
        if k in getattr(model, "banned", ()):
            master.Add(x[k] == 0)
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


def respects_cuts(chosen, cuts):
    """Check model-local append-only column no-goods on a selection."""
    selected = set(chosen)
    return all(len(selected.intersection(cut['columns'])) <= cut['rhs'] for cut in cuts)


def _pure_selection(model, chosen):
    """Refuse mixed, holed, overlapping, duplicated or incomplete patterns."""
    if not chosen or len(set(chosen)) != len(chosen):
        return False
    if model.p.instance.onboard.placements:
        return False
    boxes, stacks = set(), set()
    for index in chosen:
        column = model.columns[index]
        page = model.pages[column.page]
        expected = 1 if page.kind == '20' else 2 if page.kind == 'long' else 0
        if not expected or len(page.stacks) != expected or not column.boxes:
            return False
        if len(column.boxes) > len(page.slots) or stacks.intersection(page.stacks):
            return False
        if boxes.intersection(column.boxes) or len(set(column.boxes)) != len(column.boxes):
            return False
        # A whole page is bottom-contiguous on every physical support stack.
        for tier, slot in enumerate(page.slots[:len(column.boxes)]):
            n = int(model.T.slot_n[slot])
            physical = tuple(int(j) for j in model.T.slot_stacks[slot, :n])
            levels = tuple(int(k) for k in model.T.slot_k[slot, :n])
            if n != expected or set(physical) != set(page.stacks) or any(k != tier for k in levels):
                return False
        boxes.update(column.boxes)
        stacks.update(page.stacks)
    return boxes == set(model.boxes)


def securing_cut(model, chosen, bound):
    """Lift a reviewed rod certificate only across complete disjoint pure pages.

    The caller must supply the securing bound for this selection's exact ship.
    Other securing certificates use a weaker whole-stow no-good. The generated
    pool and fixed vessel/background remain premises, never map infeasibility.
    """
    if bound.get('status') != 'infeasible_fixed_stow' or not bound.get('proof'):
        return None
    if not _pure_selection(model, chosen):
        return None
    T = model.T
    assignment = model.assignment(chosen)
    slots = bound.get('witnesses', {}).get('max_lashing_ratio', [])
    detail = bound.get('witness_details', {}).get('max_lashing_ratio', {})
    ratio = bound.get('lower_ratios', {}).get('max_lashing_ratio', 0.)
    selected = []
    if ratio > 1.+1e-8 and detail and slots and set(slots) <= set(assignment.values()):
        touched = {k for k in chosen if any(
            T.slot_ids[model.pages[model.columns[k].page].slots[tier]] in slots
            for tier in range(len(model.columns[k].boxes)))}
        if touched:
            selected = sorted(touched)
    kind = 'securing_chain_no_good' if selected else 'certified_stow_no_good'
    selected = selected or list(chosen)
    supports = []
    for k in selected:
        column = model.columns[k]
        page = model.pages[column.page]
        supports.append(dict(column=k, page=column.page,
            physical_stacks=[str(T.stack_ids[j]) for j in page.stacks],
            box_slots=[[T.box_ids[b], T.slot_ids[page.slots[tier]]]
                       for tier, b in enumerate(column.boxes)]))
    return dict(kind=kind, certified=True, columns=selected, rhs=len(selected)-1,
        supports=supports, proof=bound['proof'],
        witness_slots=slots if kind == 'securing_chain_no_good' else [],
        scope='Fixed instance/background and complete pure-page patterns on the stated physical stacks; every ballast condition. Other stows and mixed-page families are not excluded.')


def certify_selection(instance, ship, model, chosen):
    """Bind a certificate to the selected assignment before making a cut."""
    if not _pure_selection(model, chosen):
        return None
    assignment = model.assignment(chosen)
    actual = {placement.container_id: placement.slot for placement in ship.placements}
    if actual != assignment or instance is not model.p.instance:
        return None
    return securing_cut(model, chosen, securing_bound(instance, ship))


def page_cut_callback(problem, model, chosen, scored):
    """Layout feedback hook, after real replay and before full finishing."""
    from stowage.moves import apply_plan
    replay = apply_plan(problem.instance, scored.plan)
    if not replay.legal:
        return None
    return certify_selection(problem.instance, replay.final_state.ship, model, chosen)


def page_cut_repair(model, chosen, cuts, deadline):
    """Bounded alternatives then a restricted master obeying every prior cut."""
    before = monotonic()
    remaining = max(0., deadline-before)
    if not remaining:
        return None, dict(status='budget', cuts=len(cuts), seconds=0.)
    added = alternatives(model, chosen, deadline=min(deadline, before+.35*remaining),
                         focus=cuts[-1]['columns'] if cuts else ())
    if monotonic() >= deadline:
        return None, dict(status='budget', cuts=len(cuts), added=added, seconds=monotonic()-before)
    replacement, meta = restricted_master(model, cuts,
        seconds=min(15., max(.001, deadline-monotonic())), seed=0, hint=chosen)
    meta.update(added=added, seconds=monotonic()-before)
    if replacement is not None and not respects_cuts(replacement, cuts):
        raise RuntimeError('restricted master returned selection violating a securing cut')
    return replacement, meta

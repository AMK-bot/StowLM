"""Cold, incumbent-preserving fixed-topology box permutation neighborhoods.

Only a current-process checked stow is accepted as input. CP-SAT guides search
with the port/cover fixed charge and top-access dependency graph. Its physical
rows are hard; replay and the unchanged full checker alone accept an improvement.
"""
from collections import defaultdict
import math
from time import monotonic

import numpy as np


def _columns(model, solver, assignment):
    out = defaultdict(dict)
    for b, q in assignment.items():
        j, k = solver.slot_page[q][0]
        out[j][k] = b
    return {j: [tiers[k] for k in sorted(tiers)] for j, tiers in out.items()}


def _groups(model, assignment):
    """Exchange discharge ports while preserving all geometric/rank categories."""
    T = model.T
    groups = defaultdict(list)
    for b in assignment:
        box = model.p.boxes[T.box_ids[b]]
        # OOG and DG can affect neighbours; leave those concrete boxes fixed.
        if box.oog is not None or bool(T.is_dg[b]):
            continue
        key = (box.length_ft, round(float(box.height_m), 6), int(T.rank[b]),
               bool(model.heavy[b]), bool(T.closes_stack[b]))
        groups[key].append(b)
    useful = [bs for bs in groups.values() if len(bs) >= 2 and len({model.pod[b] for b in bs}) >= 2]
    return sorted(useful, key=lambda bs: (-len(bs)*len({model.pod[b] for b in bs}), min(bs)))


def _topology(T, assignment):
    """Occupied positions and their transitive physical predecessors."""
    by_stack = defaultdict(list)
    for q in assignment.values():
        for stack, tier in zip(T.slot_stacks[q], T.slot_k[q]):
            if stack >= 0:
                by_stack[int(stack)].append((int(tier), q))
    direct = defaultdict(set)
    for members in by_stack.values():
        members.sort()
        for (_, lower), (_, upper) in zip(members, members[1:]):
            direct[upper].add(lower)
    ancestors = {}
    def visit(q, pending):
        if q in ancestors:
            return ancestors[q]
        if q in pending:
            raise ValueError('cyclic occupied-slot dependency')
        before = set(direct[q])
        for lower in direct[q]:
            before.update(visit(lower, pending | {q}))
        ancestors[q] = before
        return before
    for q in assignment.values():
        visit(q, set())
    return ancestors


def _moment(model, solver, b, q):
    j, k = solver.slot_page[q][0]
    page = model.pages[j]
    x, y = model._slot_xy(page, k)
    z = float(model.T.stack_base_z[page.stacks[0]])+(k+.5)*2.6
    mass = float(model.T.mass[b])/1000.
    return np.array((mass*x, mass*y, mass*z))


def _safe_linear(cp, terms, bound, *, scale=1000000.):
    """Round coefficients upward and RHS downward: CP feasibility is sufficient."""
    cp.Add(sum(math.ceil(float(a)*scale-1e-9)*x for a, x in terms)
           <= math.floor(float(bound)*scale+1e-9))


def _neighborhood(instance, assignment, group, *, model, solver, seconds, seed, row):
    from ortools.sat.python import cp_model
    T = model.T
    cp = cp_model.CpModel()
    free = set(group)
    positions = sorted(assignment[b] for b in group)
    occupant = {q: b for b, q in assignment.items()}
    choices, by_box, by_slot = {}, defaultdict(list), defaultdict(list)
    for b in group:
        for q in positions:
            if model.static[b, q]:
                x = cp.NewBoolVar(f'x{b}_{q}')
                choices[b, q] = x
                by_box[b].append(x); by_slot[q].append(x)
                cp.AddHint(x, int(assignment[b] == q))
    for b in group:
        if not by_box[b]:
            return None
        cp.AddExactlyOne(by_box[b])
    for q in positions:
        if not by_slot[q]:
            return None
        cp.AddExactlyOne(by_slot[q])
    # The topology, height at every position, E1 heavy status and E2 rank are
    # unchanged. Thus support, height, visibility and rank rows remain hard.
    cols = _columns(model, solver, assignment)
    securing_guards = {}
    for j, boxes in cols.items():
        page = model.pages[j]
        if not page.deck or not any(b in free for b in boxes):
            continue
        old = solver.stack_ratio(page, boxes)
        heights = tuple(round(float(model.p.boxes[T.box_ids[b]].height_m), 3) for b in boxes)
        A, Bt, Bl, _ = solver.row_cache[(page.slots[0], heights)]
        coefficients = {}
        fixed = np.zeros(A.shape[0])
        for k, b in enumerate(boxes):
            q = assignment[b]
            if b not in free:
                mass, wt, wl = solver.box_force(b)
                fixed += A[:, k]*mass+Bt[:, k]*wt+Bl[:, k]*wl
            else:
                for candidate in group:
                    if (candidate, q) in choices:
                        mass, wt, wl = solver.box_force(candidate)
                        coefficients[candidate, q] = A[:, k]*mass+Bt[:, k]*wt+Bl[:, k]*wl
        guard = max(1., old)+2e-5
        securing_guards[j] = guard
        for r in range(A.shape[0]):
            _safe_linear(cp, [(v[r], choices[key]) for key, v in coefficients.items()], guard-fixed[r])
    # Hard native mass-share and height capacities, also needed for hold pages.
    for j, boxes in cols.items():
        page = model.pages[j]
        if not any(b in free for b in boxes):
            continue
        fixed_mass = sum(float(T.share[b]) for b in boxes if b not in free)
        terms = [(float(T.share[b]), x) for (b, q), x in choices.items()
                 if solver.slot_page[q][0][0] == j]
        _safe_linear(cp, terms, page.limit_kg-fixed_mass, scale=1000.)
    # Stay close to the actual incumbent's side moments, never raising its KG.
    old_moments = sum((_moment(model, solver, b, assignment[b]) for b in group), np.zeros(3))
    moments = {key: _moment(model, solver, *key) for key in choices}
    total = sum(float(T.mass[b])/1000. for b in assignment)
    tolerances = (total*.10, total*.02, 1e-5)
    for axis, tolerance in enumerate(tolerances):
        terms = [(m[axis], choices[key]) for key, m in moments.items()]
        _safe_linear(cp, terms, old_moments[axis]+tolerance, scale=100000000.)
        if axis < 2:
            _safe_linear(cp, [(-a, x) for a, x in terms], -old_moments[axis]+tolerance, scale=100000000.)
    # Hamming radius: at most 32 concrete boxes move in one exact neighborhood.
    cp.Add(sum(choices[b, assignment[b]] for b in group) >= max(0, len(group)-32))
    ports = sorted(model.ports)
    port_cache = {}
    def port_at(q, port, later=False):
        key = q, port, later
        if key in port_cache:
            return port_cache[key]
        b = occupant[q]
        condition = lambda c: model.pod[c] > port if later else model.pod[c] == port
        if b not in free:
            out = int(condition(b))
        else:
            terms = [choices[c, q] for c in group if (c, q) in choices and condition(c)]
            if not terms:
                out = 0
            else:
                out = cp.NewBoolVar(f'port_{q}_{port}_{later}')
                cp.Add(out == sum(terms))
        port_cache[key] = out
        return out
    def union(terms, name):
        if any(isinstance(v, int) and v == 1 for v in terms):
            return 1
        terms = [v for v in terms if not isinstance(v, int)]
        if not terms:
            return 0
        if len(terms) == 1:
            return terms[0]
        out = cp.NewBoolVar(name)
        cp.AddMaxEquality(out, terms)
        return out
    hold = defaultdict(list)
    page_of = {q: model.pages[solver.slot_page[q][0][0]] for q in occupant}
    for q, page in page_of.items():
        if not page.deck and page.cover is not None:
            hold[page.cover].append(q)
    opening = {}
    objective = []
    hatch_cost = round(1000.*2*instance.objective.future_hatch_cover)
    restow_cost = round(1000.*instance.objective.future_restow*instance.objective.future_restow_lifts_per_box)
    for cover, qs in hold.items():
        for port in ports:
            opening[cover, port] = union([port_at(q, port) for q in qs], f'open_{cover}_{port}')
            objective.append(hatch_cost*opening[cover, port])
    ancestors = _topology(T, assignment)
    for q in occupant:
        page = page_of[q]
        for port in ports[:-1]:
            blockers = [port_at(lower, port) for lower in ancestors[q]]
            if page.deck and (page.cover, port) in opening:
                blockers.append(opening[page.cover, port])
            needed = union(blockers, f'need_{q}_{port}')
            later = port_at(q, port, later=True)
            if isinstance(needed, int) and not needed or isinstance(later, int) and not later:
                continue
            if isinstance(needed, int):
                both = later
            elif isinstance(later, int):
                both = needed
            else:
                both = cp.NewBoolVar(f'restow_{q}_{port}')
                cp.Add(both <= needed); cp.Add(both <= later)
                cp.Add(both >= needed+later-1)
            objective.append(restow_cost*both)
    cp.Minimize(sum(objective))
    engine = cp_model.CpSolver()
    engine.parameters.num_workers = 1
    engine.parameters.random_seed = seed
    engine.parameters.max_time_in_seconds = max(.001, seconds)
    status = engine.Solve(cp)
    row.update(status=engine.StatusName(status), boxes=len(group), variables=len(choices),
               cp_s=round(engine.WallTime(), 3))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    found = dict(assignment)
    found.update({b: q for (b, q), x in choices.items() if engine.Value(x)})
    row['moved'] = sum(found[b] != assignment[b] for b in found)
    # Exact floating guards repeat the conservatively rounded CP restrictions.
    new_cols = _columns(model, solver, found)
    for j, boxes in new_cols.items():
        if not model.legal(model.pages[j], tuple(boxes), any_port=True):
            row['status'] = 'column_guard_rejected'
            return None
        if j in securing_guards and solver.stack_ratio(model.pages[j], boxes) > securing_guards[j]+1e-8:
            row['status'] = 'securing_guard_rejected'
            return None
    new_moments = sum((_moment(model, solver, b, found[b]) for b in group), np.zeros(3))
    delta = new_moments-old_moments
    if abs(delta[0]) > tolerances[0]+1e-7 or abs(delta[1]) > tolerances[1]+1e-7 or delta[2] > tolerances[2]+1e-7:
        row['status'] = 'moment_guard_rejected'
        return None
    return found



def _parts(report):
    count = report.count
    return dict(relocations=int(count.relocations), hatch_now=int(count.hatch_cover_lifts),
                hatch_later=sum(int(r.hatch_cover_lifts) for r in count.future),
                restow_now=int(count.restow_lifts),
                restow_later=sum(int(r.restow_lifts) for r in count.future))


def _polish(instance, scored, *, problem, model, solver, deadline, seed, metadata):
    """Return a cheaper ordinary full-checker pass, or the exact input object."""
    from alns import Scored, Sequencer
    from stowage.checker import check
    started = monotonic()
    meta = dict(kind='fixed_topology_port_graph', cold_current_run_only=True,
                incumbent_lifts=float(scored.objective), trials=[], accepted=0,
                proposed=0, guard_rejects=0, full_check_rejects=0)
    metadata['polish'] = meta
    incumbent = scored
    initial = check(instance, incumbent.plan)
    if not initial.passed:
        meta['status'] = 'input_not_a_full_pass'
        return incumbent
    meta['initial_parts'] = _parts(initial)
    T = model.T
    assignment = {int(T.box_index[a.box]): int(T.slot_index[a.slot]) for a in incumbent.actions}
    if len(assignment) != len(model.boxes):
        meta['status'] = 'outside_complete_page_family'
        return incumbent
    seen = {tuple(sorted(assignment.items()))}
    # At most eight exact subproblems; every accepted point gets a new full check.
    for turn, group in enumerate(_groups(model, assignment)[:8]):
        remaining = deadline-monotonic()
        if remaining < 15.:
            break
        row = dict(turn=turn)
        meta['trials'].append(row)
        try:
            found = _neighborhood(instance, assignment, group, model=model, solver=solver,
                                  seconds=min(8., max(.1, remaining-14.)), seed=seed+turn, row=row)
            if found is None:
                meta['guard_rejects'] += int(row.get('status', '').endswith('rejected'))
                continue
            meta['proposed'] += 1
            key = tuple(sorted(found.items()))
            if key in seen:
                row['outcome'] = 'repeat'
                continue
            seen.add(key)
            table = {T.box_ids[b]: T.slot_ids[q] for b, q in found.items()}
            if problem.mixes_hold_stack(table):
                row['outcome'] = 'mixed_hold'
                continue
            actions = Sequencer(problem).order(table)
            candidate = problem.score(actions) if actions is not None else None
            if candidate is None or candidate.objective >= incumbent.objective-1e-9:
                row['outcome'] = 'no_cheaper_replay'
                continue
            candidate = Scored(candidate.actions, candidate.objective,
                               candidate.plan.model_copy(update={'ballast': incumbent.plan.ballast}))
            row['replay_lifts'] = float(candidate.objective)
            checked = check(instance, candidate.plan)
            row.update(full_pass=bool(checked.passed),
                       rules=sorted({v.rule_id for v in checked.violations}),
                       checked_lifts=float(checked.count.weighted_nonproductive))
            if checked.passed and checked.count.weighted_nonproductive < incumbent.objective-1e-9:
                incumbent, assignment = candidate, found
                meta['final_parts'] = _parts(checked)
                meta['accepted'] += 1
                row['outcome'] = 'improved'
            else:
                meta['full_check_rejects'] += 1
                row['outcome'] = 'incumbent_retained'
        except Exception as exc:
            # A polishing failure cannot invalidate an already passing stow.
            row.update(outcome='incumbent_retained_exception', error=f'{type(exc).__name__}: {exc}')
    meta.setdefault('final_parts', meta['initial_parts'])
    meta.update(status='improved' if meta['accepted'] else 'incumbent_retained',
                lifts=float(incumbent.objective), wall_s=round(monotonic()-started, 3))
    return incumbent


def polish(instance, scored, *, problem, model, solver, deadline, seed, metadata):
    """Optional improvement stage: even setup errors return the incoming pass."""
    try:
        return _polish(instance, scored, problem=problem, model=model, solver=solver,
                       deadline=deadline, seed=seed, metadata=metadata)
    except Exception as exc:
        row = metadata.setdefault('polish', {})
        row.update(status='incumbent_retained_setup_exception',
                   error=f'{type(exc).__name__}: {exc}')
        return scored

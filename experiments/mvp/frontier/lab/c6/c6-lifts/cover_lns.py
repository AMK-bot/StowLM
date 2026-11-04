"""Cold cover-changing LNS, with an exact restricted pattern-selection master.

No archived plans, ballast or assignments are read. The input is a concrete
current-run full pass. Pattern gates keep securing and page legality hard;
master rows keep native cargo moments hard. Native replay and the unchanged
full checker alone accept a cheaper complete plan. See COVER_LNS.md.
"""
from collections import Counter, defaultdict
from copy import copy
from dataclasses import dataclass
from fractions import Fraction
from math import floor, gcd, lcm
import random
from time import monotonic

import numpy as np


@dataclass(frozen=True)
class Pattern:
    page: int
    boxes: tuple
    moments: tuple  # mass tonnes, vertical, longitudinal, transverse tonne-metres
    ratio: float


def _columns(model, solver, assignment):
    by_page = defaultdict(dict)
    for b, q in assignment.items():
        j, k = solver.slot_page[q][0]
        by_page[j][k] = b
    columns = {}
    physical, seen = set(), set()
    for j, tiers in by_page.items():
        page = model.pages[j]
        if sorted(tiers) != list(range(len(tiers))):
            raise ValueError('incumbent is outside the contiguous pure-page family')
        boxes = tuple(tiers[k] for k in range(len(tiers)))
        if seen.intersection(boxes) or physical.intersection(page.stacks):
            raise ValueError('incumbent has overlapping physical pages')
        if not model.legal(page, boxes, any_port=True):
            raise ValueError('incumbent is outside the hard page-legal family')
        columns[j] = boxes
        seen.update(boxes); physical.update(page.stacks)
    if seen != set(model.boxes):
        raise ValueError('incumbent is not a complete assignment')
    return columns


def _moments(model, j, boxes):
    """Exact rational body-height geometry on the supplied binary input values."""
    page, T = model.pages[j], model.T
    z = Fraction(max(float(T.stack_base_z[s]) for s in page.stacks))
    out = [Fraction(0) for _ in range(4)]
    for k, b in enumerate(boxes):
        c = model.p.boxes[T.box_ids[b]]
        mass, h = Fraction(float(T.mass[b]))/1000, Fraction(float(c.height_m))
        x, y = map(Fraction, model._slot_xy(page, k))
        for axis, coordinate in enumerate((1, z+h/2, x, y)):
            out[axis] += mass*coordinate
        z += h
    return tuple(out)


def _parts(report):
    c = report.count
    return dict(relocations=int(c.relocations), hatch_now=int(c.hatch_cover_lifts),
                hatch_later=sum(int(r.hatch_cover_lifts) for r in c.future),
                restow_now=int(c.restow_lifts), restow_later=sum(int(r.restow_lifts) for r in c.future))


def _cover_jobs(model, columns):
    """Prefer a physically nearby receiver with shared ports and enough space."""
    hold, all_boxes = defaultdict(list), defaultdict(list)
    for j, boxes in columns.items():
        page = model.pages[j]
        if page.cover is not None:
            all_boxes[page.cover].extend(boxes)
            if not page.deck:
                hold[page.cover].extend(boxes)
    cells = defaultdict(set)
    for page in model.pages:
        if page.cover is not None:
            for q in page.slots:
                cells[page.cover].update(int(c) for c in model.T.slot_cells[q] if c >= 0)
    available = {cover: len(physical) for cover, physical in cells.items()}
    centers = {cover: sum(float(model.p.cells[model.T.cell_ids[c]].lcg_m) for c in physical)/len(physical)
               for cover, physical in cells.items() if physical}
    jobs, seen = [], set()
    for target in sorted(hold, key=lambda c: (len(hold[c]), str(c))):
        ports = {model.pod[b] for b in hold[target]}
        target_demand = sum(float(model.T.units[b]) for b in hold[target])
        def priority(other):
            common = len(ports.intersection(model.pod[b] for b in hold[other]))
            demand = sum(float(model.T.units[b]) for b in all_boxes[other])
            spare = available[other]-demand
            distance = abs(centers[other]-centers[target])
            return (spare < target_demand, -common, distance, -spare, str(other))
        others = sorted((c for c in available if c != target), key=priority)
        for other in others[:2]:
            pair = tuple(sorted((target, other), key=str))
            for close in (target, None):
                key = pair, close
                if key not in seen:
                    jobs.append((pair, close)); seen.add(key)
        if len(jobs) >= 12:
            break
    return jobs[:12]


def _pattern_pool(model, solver, columns, covers, *, deadline, seed, row, closed=None):
    """Generate alternative complete-stack patterns; there is no soft securing."""
    rng = random.Random(seed)
    T = model.T
    moving = {j: boxes for j, boxes in columns.items() if model.pages[j].cover in covers}
    free = {b for boxes in moving.values() for b in boxes}
    fixed_stacks = {s for j in columns if model.pages[j].cover not in covers for s in model.pages[j].stacks}
    available = [j for j, page in enumerate(model.pages) if page.cover in covers
                 and not fixed_stacks.intersection(page.stacks)]
    row['fixed_outside_physical_stacks'] = len(fixed_stacks)
    guards = {j: max(1., solver.stack_ratio(model.pages[j], columns.get(j, ())))+2e-5 for j in available}
    pool, keys, initial = [], {}, []
    checks, rejected = 0, Counter()
    ratio_cache = {}
    def add(j, boxes, *, force_time=False):
        nonlocal checks
        boxes = tuple(boxes)
        key = j, boxes
        if key in keys:
            return keys[key]
        if not boxes or len(set(boxes)) != len(boxes) or not set(boxes) <= free:
            return None
        if not force_time and (monotonic() >= deadline or len(pool) >= 16000):
            return None
        page = model.pages[j]
        checks += 1
        if len(boxes) > len(page.slots) or not model.legal(page, boxes, any_port=True):
            rejected['page'] += 1
            return None
        ratio_key = j, boxes
        if ratio_key not in ratio_cache:
            ratio_cache[ratio_key] = solver.stack_ratio(page, boxes)
        ratio = ratio_cache[ratio_key]
        if ratio > guards[j]+1e-9:
            rejected['securing'] += 1
            return None
        p = Pattern(j, boxes, _moments(model, j, boxes), ratio)
        keys[key] = len(pool); pool.append(p)
        return len(pool)-1
    # Preserve exact original columns as a feasible fallback hint before pricing.
    for j, boxes in moving.items():
        k = add(j, boxes, force_time=True)
        if k is None:
            raise ValueError('passing incumbent rejected by the hard pattern gates')
        initial.append(k)
    # Joint destroy/rebuild seeds produce complementary patterns instead of
    # letting every page independently start with the same heaviest box.
    row.update(greedy_seed_attempts=0, greedy_seed_complete=0)
    seed_deadline = min(deadline, monotonic()+1.6)
    packing_pages = [j for j in available if closed is None or model.pages[j].deck
                     or model.pages[j].cover != closed]
    for attempt in range(4):
        if monotonic() >= seed_deadline:
            break
        row['greedy_seed_attempts'] += 1
        todo, physical, hint = set(free), set(), []
        page_order = list(packing_pages); rng.shuffle(page_order)
        page_order.sort(key=lambda j: (model.pages[j].deck, model.pages[j].kind != 'long',
                                       -len(model.pages[j].slots)))
        for j in page_order:
            if monotonic() >= seed_deadline:
                break
            page = model.pages[j]
            if physical.intersection(page.stacks):
                continue
            eligible = [b for b in todo if (T.units[b] == 1) == (page.kind == '20')]
            counts = Counter(model.pod[b] for b in eligible)
            focus = max(counts, key=counts.get) if counts else None
            noise = {b: rng.random() for b in eligible}
            eligible.sort(key=lambda b: (bool(T.closes_stack[b]), not model.heavy[b],
                model.pod[b] != focus, -int(T.rank[b]),
                -float(T.mass[b]) if attempt == 0 else noise[b]))
            chosen, final = [], None
            for b in eligible:
                if monotonic() >= seed_deadline:
                    break
                k = add(j, chosen+[b])
                if k is not None:
                    chosen.append(b); final = k
                if len(chosen) >= len(page.slots):
                    break
            if final is not None:
                todo.difference_update(chosen); physical.update(page.stacks); hint.append(final)
            if not todo:
                row['greedy_seed_complete'] += 1
                row.setdefault('greedy_hint', hint)
                break
    sources = [(j, boxes) for j, boxes in moving.items()]
    # Relocate whole incumbent stacks, and split them when capacities differ.
    for j in available:
        if monotonic() >= deadline:
            break
        page = model.pages[j]
        for source, boxes in sources:
            if model.pages[source].kind != page.kind:
                continue
            n = min(len(boxes), len(page.slots))
            for size in sorted({1, n, max(1, n-1)}):
                add(j, boxes[:size]); add(j, boxes[-size:])
            for at, b in enumerate(boxes):
                add(j, (b,))
                add(j, boxes[:at]+boxes[at+1:])
        if monotonic() >= deadline:
            break
    # Port-focused greedy stacks, all intermediate prefixes kept. Varied orders
    # and removal/insertion patterns let the exact cover combine different boxes.
    orders = {}
    for kind in ('20', 'long'):
        bs = [b for b in free if (T.units[b] == 1) == (kind == '20')]
        modes = [None]+sorted({model.pod[b] for b in bs})
        orders[kind] = []
        for port in modes:
            here = list(bs); rng.shuffle(here)
            here.sort(key=lambda b: (bool(T.closes_stack[b]), not model.heavy[b],
                                    model.pod[b] != port if port is not None else False,
                                    -int(T.rank[b]), -model.pod[b], -float(T.mass[b])))
            orders[kind].append(here)
        # Mass-first gives a legal capacity backbone when pure-port packing fails.
        orders[kind].append(sorted(bs, key=lambda b: (bool(T.closes_stack[b]), not model.heavy[b],
                                                      -int(T.rank[b]), -float(T.mass[b]), -model.pod[b])))
        for _ in range(2):
            noise = {b: rng.random() for b in bs}
            orders[kind].append(sorted(bs, key=lambda b: (bool(T.closes_stack[b]), not model.heavy[b],
                                                          -int(T.rank[b]), noise[b])))
    rng.shuffle(available)
    for j in available:
        if monotonic() >= deadline:
            break
        page = model.pages[j]
        for order in orders.get(page.kind, []):
            chosen = []
            for b in order:
                if monotonic() >= deadline:
                    break
                k = add(j, chosen+[b])
                if k is not None:
                    chosen.append(b)
                if len(chosen) >= len(page.slots):
                    break
        if monotonic() >= deadline:
            break
        # Exchanges, insertions and deletions around each existing local stack.
        base = moving.get(j, ())
        for b in orders.get(page.kind, [[]])[-1]:
            if monotonic() >= deadline:
                break
            if b in base:
                continue
            for remove in range(-1, len(base)):
                rest = list(base) if remove < 0 else [c for k, c in enumerate(base) if k != remove]
                for at in range(len(rest)+1):
                    add(j, rest[:at]+[b]+rest[at:])
                if monotonic() >= deadline:
                    break
    row.update(pool_patterns=len(pool), pattern_checks=checks, pattern_rejects=dict(rejected),
               free_boxes=len(free), available_pages=len(available))
    return pool, initial, free, guards


def _weights(obj):
    values = (2*Fraction(str(obj.hatch_cover)), 2*Fraction(str(obj.future_hatch_cover)),
              Fraction(str(obj.future_restow))*obj.future_restow_lifts_per_box)
    scale = lcm(*(v.denominator for v in values))
    return scale, [int(v*scale) for v in values]


def _objective(cp, use, pool, model):
    """Fixed port-cover charges and blocker union, adapted from C4 lns.py."""
    scale, (now, later, restow) = _weights(model.p.instance.objective)
    holds, edges = defaultdict(list), defaultdict(list)
    for k, x in use.items():
        c, page = pool[k], model.pages[pool[k].page]
        if not page.deck and page.cover is not None:
            holds[page.cover].append(x)
            for port in {model.pod[b] for b in c.boxes}:
                edges[page.cover, port].append(x)
    terms, opening = [], {}
    for cover, xs in holds.items():
        z = cp.NewBoolVar(f'now_{cover}'); cp.AddMaxEquality(z, xs)
        terms.append(now*z)
    for key, xs in edges.items():
        z = cp.NewBoolVar(f'future_{key}'); cp.AddMaxEquality(z, xs)
        opening[key] = z; terms.append(later*z)
    for k, x in use.items():
        c, page = pool[k], model.pages[pool[k].page]
        lower, internal, cross = set(), 0, Counter()
        for b in c.boxes:
            pod = model.pod[b]
            internal += sum(port < pod for port in lower)
            if page.deck and page.cover is not None:
                for port in model.ports:
                    if port < pod and port not in lower and (page.cover, port) in opening:
                        cross[port] += 1
            lower.add(pod)
        terms.append(restow*internal*x)
        for port, count in cross.items():
            z = cp.NewBoolVar(f'blocked_{k}_{port}')
            cp.AddMinEquality(z, [x, opening[page.cover, port]])
            terms.append(restow*count*z)
    return sum(terms), scale


def _rational_guard(cp, use, pool, axis, sign, bound):
    """Use an exact integer row if int64-safe; otherwise relax then exact-gate.

    Downward coefficient rounding makes the incumbent feasible in the fallback
    row. The caller's exact Fraction guard is mandatory before native scoring.
    """
    coefficients = [Fraction(pool[k].moments[axis])*sign for k in use]
    bound = Fraction(bound)
    denominator = lcm(bound.denominator, *(v.denominator for v in coefficients))
    integers = [v.numerator*(denominator//v.denominator) for v in coefficients]
    rhs = bound.numerator*(denominator//bound.denominator)
    divisor = abs(rhs)
    for v in integers:
        divisor = gcd(divisor, abs(v))
    divisor = max(1, divisor)
    integers, rhs = [v//divisor for v in integers], rhs//divisor
    exact = sum(abs(v) for v in integers)+abs(rhs) < 2**61
    if not exact:
        scale = 100000000
        integers = [floor(v*scale) for v in coefficients]
        rhs = floor(bound*scale)
    cp.Add(sum(v*x for v, x in zip(integers, use.values())) <= rhs)
    return 'exact_integer' if exact else 'outward_relaxation_then_exact_guard'


def _master(pool, initial, free, model, *, closed, deadline, seed, row):
    from ortools.sat.python import cp_model
    cp = cp_model.CpModel()
    use = {}
    boxes, stacks = defaultdict(list), defaultdict(list)
    for k, pattern in enumerate(pool):
        page = model.pages[pattern.page]
        if closed is not None and not page.deck and page.cover == closed:
            continue
        x = cp.NewBoolVar(f'p{k}'); use[k] = x
        for b in pattern.boxes:
            boxes[b].append(x)
        for s in page.stacks:
            stacks[s].append(x)
    if any(not boxes[b] for b in free):
        row['status'] = 'uncovered_restricted_pool'
        return None
    for b in free:
        cp.AddExactlyOne(boxes[b])
    for xs in stacks.values():
        cp.AddAtMostOne(xs)
    baseline = tuple(sum((Fraction(pool[k].moments[a]) for k in initial), Fraction(0)) for a in range(4))
    mass = sum((Fraction(float(model.T.mass[b]))/1000 for b in model.boxes), Fraction(0))
    tolerances = (Fraction(0), Fraction('0.00001'), mass*Fraction('.35'), mass*Fraction('.02'))
    row['moment_row_modes'] = []
    def guard(axis, sign, bound):
        row['moment_row_modes'].append(_rational_guard(cp, use, pool, axis, sign, bound))
    guard(1, 1, baseline[1]+tolerances[1])
    for axis in (2, 3):
        guard(axis, 1, baseline[axis]+tolerances[axis])
        guard(axis, -1, -baseline[axis]+tolerances[axis])
    objective, scale = _objective(cp, use, pool, model)
    cp.Minimize(objective)
    hint = set(row.get('greedy_hint', initial))
    for k, x in use.items():
        cp.AddHint(x, int(k in hint))
    engine = cp_model.CpSolver()
    engine.parameters.num_workers = 1
    engine.parameters.random_seed = seed
    engine.parameters.max_time_in_seconds = max(.001, min(10., deadline-monotonic()))
    status = engine.Solve(cp)
    row.update(status=engine.StatusName(status), cp_s=round(engine.WallTime(), 3),
               objective_scope='restricted pure-page projection; not a whole-map bound')
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    chosen = [k for k, x in use.items() if engine.Value(x)]
    actual = tuple(sum((Fraction(pool[k].moments[a]) for k in chosen), Fraction(0)) for a in range(4))
    if (actual[1] > baseline[1]+tolerances[1] or
            any(abs(actual[a]-baseline[a]) > tolerances[a] for a in (2, 3))):
        row['status'] = 'exact_moment_guard_rejected'
        return None
    seen, physical = set(), set()
    found = {}
    for k in chosen:
        pattern = pool[k]; page = model.pages[pattern.page]
        if seen.intersection(pattern.boxes) or physical.intersection(page.stacks):
            raise AssertionError('restricted master repeated a box or physical stack')
        seen.update(pattern.boxes); physical.update(page.stacks)
        found.update({b: int(page.slots[t]) for t, b in enumerate(pattern.boxes)})
    if seen != free:
        raise AssertionError('restricted master lost complete box coverage')
    row.update(graph_lifts=float(engine.ObjectiveValue())/scale,
               selected_patterns=len(chosen), side_moment_delta_tm=[float(a-b) for a, b in zip(actual, baseline)])
    return found


def _refresh_solver(instance, plan, solver):
    """After ballast changes, no old motion rows/cache may be reused."""
    from stowage.checker.full import departure_condition
    from chainlab import condition_terms, motions
    updated = copy(solver)
    updated.motions = motions(condition_terms(instance, departure_condition(instance, plan)))
    updated.row_cache = {}
    return updated


def _polish(instance, scored, *, problem, model, solver, deadline, seed, metadata):
    from alns import Scored, Sequencer
    from stowage.checker import check
    started = monotonic()
    meta = dict(kind='cold_cover_changing_lns', cold_current_run_only=True,
                initial_lifts=float(scored.objective), trials=[], proposed=0,
                full_check_rejects=0, accepted=0)
    metadata['cover_lns'] = meta
    incumbent = scored
    report = check(instance, incumbent.plan)
    if not report.passed:
        meta['status'] = 'input_not_a_full_pass'
        return incumbent
    meta['initial_parts'] = _parts(report)
    T = model.T
    assignment = {int(T.box_index[a.box]): int(T.slot_index[a.slot]) for a in incumbent.actions}
    columns = _columns(model, solver, assignment)
    jobs = _cover_jobs(model, columns)
    seen = {tuple(sorted(assignment.items()))}
    for turn, (covers, closed) in enumerate(jobs):
        if deadline-monotonic() < 20.:
            break
        row = dict(turn=turn, covers=list(map(str, covers)), closed_hold=str(closed) if closed is not None else None)
        meta['trials'].append(row)
        try:
            t0 = monotonic()
            pool, initial, free, guards = _pattern_pool(model, solver, columns, set(covers),
                deadline=min(deadline-14., monotonic()+4.), seed=seed+turn, row=row, closed=closed)
            row['generation_s'] = round(monotonic()-t0, 3)
            if deadline-monotonic() < 14.:
                break
            found = _master(pool, initial, free, model, closed=closed,
                deadline=min(deadline-12., monotonic()+10.), seed=seed+turn, row=row)
            if found is None:
                continue
            proposed = {b: q for b, q in assignment.items() if b not in free}
            proposed.update(found)
            key = tuple(sorted(proposed.items()))
            if key in seen:
                row['outcome'] = 'repeat'; continue
            seen.add(key); meta['proposed'] += 1
            table = {T.box_ids[b]: T.slot_ids[q] for b, q in proposed.items()}
            if set(table) != set(instance.load_list) or problem.mixes_hold_stack(table):
                row['outcome'] = 'complete_geometry_guard_rejected'; continue
            actions = Sequencer(problem).order(table)
            candidate = problem.score(actions) if actions is not None else None
            if candidate is None or candidate.objective >= incumbent.objective-1e-9:
                row['outcome'] = 'no_cheaper_replay'; continue
            candidate = Scored(candidate.actions, candidate.objective,
                               candidate.plan.model_copy(update={'ballast': incumbent.plan.ballast}))
            checked = check(instance, candidate.plan)
            row.update(replay_lifts=float(candidate.objective), full_pass=bool(checked.passed),
                       rules=sorted({v.rule_id for v in checked.violations}))
            # Ballast only gets a chance when cargo itself is legal and the only
            # remaining breaches are trim/draught. The callback independently checks.
            if not checked.passed and deadline-monotonic() > 12.:
                from cover_ballast import repair, trim_draught_only
                if trim_draught_only(checked):
                    repaired, details = repair(instance, candidate.plan, deadline,
                        incumbent=incumbent.plan, incumbent_lifts=incumbent.objective)
                    row['ballast_repair'] = details
                    if repaired is not None and repaired is not incumbent.plan:
                        checked = check(instance, repaired)
                        candidate = Scored(candidate.actions, float(checked.count.weighted_nonproductive), repaired)
                        row['after_ballast_full_pass'] = bool(checked.passed)
            if checked.passed and checked.count.weighted_nonproductive < incumbent.objective-1e-9:
                new_columns = _columns(model, solver, proposed)
                new_solver = _refresh_solver(instance, candidate.plan, solver)
                incumbent = Scored(candidate.actions, float(checked.count.weighted_nonproductive), candidate.plan)
                assignment, columns, solver = proposed, new_columns, new_solver
                meta['accepted'] += 1; meta['final_parts'] = _parts(checked)
                row.update(outcome='improved', accepted_lifts=float(incumbent.objective))
            else:
                meta['full_check_rejects'] += 1; row['outcome'] = 'incumbent_retained'
        except Exception as exc:
            row.update(outcome='incumbent_retained_exception', error=f'{type(exc).__name__}: {exc}')
    meta.setdefault('final_parts', meta['initial_parts'])
    meta.update(status='improved' if meta['accepted'] else 'incumbent_retained',
                lifts=float(incumbent.objective), wall_s=round(monotonic()-started, 3))
    return incumbent


def polish(instance, scored, *, problem, model, solver, deadline, seed, metadata):
    """Integration API: return a strictly cheaper complete full pass, or scored."""
    try:
        return _polish(instance, scored, problem=problem, model=model, solver=solver,
                       deadline=deadline, seed=seed, metadata=metadata)
    except Exception as exc:
        metadata.setdefault('cover_lns', {}).update(status='incumbent_retained_setup_exception',
            error=f'{type(exc).__name__}: {exc}')
        return scored

"""Fixed-charge page neighbourhoods; every returned assignment needs real replay.

The page pool is a heuristic restriction, so its solver bound is *not* a bound
on the map.  This module imports no mutable engine implementation.
"""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from math import lcm
import random
from time import monotonic

from ortools.sat.python import cp_model


def seed_assignment(model, assignment):
    """Insert an incumbent made of pure pages and return its column indices.

    This deliberately rejects mixed-length pages or holes rather than silently
    changing a known feasible assignment.  Existing cover layout labels do not
    constrain the incumbent or a subsequent neighbourhood.
    """
    T = model.T
    occupied = {T.slot_index[s]: T.box_index[b] for b, s in assignment.items()}
    by_page = []
    seen = set()
    old_layout = model.layout
    try:
        model.layout = {}
        for j, page in enumerate(model.pages):
            boxes = tuple(occupied[s] for s in page.slots if s in occupied)
            if not boxes:
                continue
            actual = tuple(s for s in page.slots if s in occupied)
            if actual != page.slots[:len(boxes)]:
                raise ValueError('incumbent has a page with unsupported tiers')
            if seen.intersection(boxes):
                raise ValueError('incumbent is not a pure-page packing')
            if not model.legal(page, boxes, any_port=True) or not model.lash_ok(page, boxes):
                raise ValueError('incumbent violates this model\'s page gates')
            model.add(j, boxes)
            k = model.pool.get((j, boxes))
            if k is None or k in model.banned:
                raise ValueError('incumbent column is unavailable')
            by_page.append(k)
            seen.update(boxes)
    finally:
        model.layout = old_layout
    if seen != set(model.boxes):
        raise ValueError('incumbent does not cover the complete load list')
    validate_selection(model, by_page)
    return by_page


def validate_selection(model, chosen):
    """Check exact box cover and physical-stack disjointness, without solving."""
    boxes, stacks = set(), set()
    for k in chosen:
        c = model.columns[k]
        page = model.pages[c.page]
        if boxes.intersection(c.boxes) or stacks.intersection(page.stacks):
            raise ValueError('selection repeats a box or a physical stack')
        boxes.update(c.boxes)
        stacks.update(page.stacks)
    if boxes != set(model.boxes):
        raise ValueError('selection is not a complete exact cover')


def _weights(model):
    obj = model.p.instance.objective
    # Decimal input weights are represented exactly, avoiding objective rounding.
    values = [2 * Fraction(str(obj.hatch_cover)),
              2 * Fraction(str(obj.future_hatch_cover)),
              Fraction(str(obj.future_restow)) * obj.future_restow_lifts_per_box]
    scale = lcm(*(v.denominator for v in values))
    return scale, [int(v * scale) for v in values]


def page_lift_cost(model, chosen):
    """Weighted current hatch plus D15 page/hatch restows, excluding yard lifts.

    For pure, disjoint pages this accounts for each blocker once per call,
    including a deck box blocked both by its own page and an opening cover.
    Additional crane-crossing closures are left to real replay.
    """
    scale, (now, later, restow) = _weights(model)
    hold = defaultdict(set)
    for k in chosen:
        c = model.columns[k]
        page = model.pages[c.page]
        if not page.deck and page.cover is not None:
            hold[page.cover].update(model.pod[b] for b in c.boxes)
    total = now * len(hold) + later * sum(len(v) for v in hold.values())
    for k in chosen:
        c = model.columns[k]
        page = model.pages[c.page]
        lower = set()
        for b in c.boxes:
            calls = lower | (hold[page.cover] if page.deck else set())
            total += restow * sum(q < model.pod[b] for q in calls)
            lower.add(model.pod[b])
    return total / scale


def _objective(model, ip, x):
    """Build exact OR charges for the abstract page/cover projection."""
    scale, (now, later, restow) = _weights(model)
    hold, edges = defaultdict(list), defaultdict(list)
    for k in x:
        c = model.columns[k]
        page = model.pages[c.page]
        if not page.deck and page.cover is not None:
            hold[page.cover].append(x[k])
            for port in set(model.pod[b] for b in c.boxes):
                edges[page.cover, port].append(x[k])
    terms, used_edges = [], {}
    for cover, variables in hold.items():
        used = ip.NewBoolVar(f'cover_{cover}')
        ip.AddMaxEquality(used, variables)
        terms.append(now * used)
    for key, variables in edges.items():
        edge = ip.NewBoolVar(f'edge_{key}')
        ip.AddMaxEquality(edge, variables)
        used_edges[key] = edge
        terms.append(later * edge)
    for k in x:
        c, lower, internal = model.columns[k], set(), 0
        page = model.pages[c.page]
        cross_counts = defaultdict(int)
        for b in c.boxes:
            pod = model.pod[b]
            internal += sum(q < pod for q in lower)
            if page.deck and page.cover is not None:
                for q in model.ports:
                    if q < pod and q not in lower and (page.cover, q) in used_edges:
                        cross_counts[q] += 1
            lower.add(pod)
        terms.append(restow * internal * x[k])
        for q, count in cross_counts.items():
            blocked = ip.NewBoolVar(f'blocked_{k}_{q}')
            ip.AddMinEquality(blocked, [x[k], used_edges[page.cover, q]])
            terms.append(restow * count * blocked)
    return sum(terms), scale


def improve(model, chosen, free_covers, *, closed_hold=(), hold_only=(), forbidden_pairs=(),
            seconds=15., pricing_s=4.,
            seed=0, max_rounds=8, hold_full=False, heel_slack_tm=None,
            lcg_slack_tm=None):
    """Repack selected covers, fixing the rest; return result/diagnostics.

    ``closed_hold`` must be a subset of ``free_covers``. Their hold pages cannot
    be used, but deck pages are retained.  Pool generation ignores old pure-port
    labels so mixed covers remain possible when they buy useful capacity.
    ``hold_only`` contains concrete box indices that may not use deck columns.
    This is a construction restriction, not a necessary inequality or bound.
    ``forbidden_pairs`` names exact column-index pairs that cannot coexist;
    a repeated index forbids that column alone. Checker-derived nogoods are
    search constraints, never a checker bypass or a whole-map certificate.

    The return value always has ``chosen`` (None on failure) and diagnostics.
    A returned selection may tie or worsen the incumbent when a forced closure
    makes that necessary. Callers must keep only replay/full-check improvements.
    """
    start = monotonic()
    deadline = start + max(0., seconds)
    validate_selection(model, chosen)
    free_covers, closed_hold = set(free_covers), set(closed_hold)
    hold_only = set(hold_only)
    if not closed_hold <= free_covers:
        raise ValueError('closed hold covers must belong to the neighbourhood')
    fixed = [k for k in chosen if model.pages[model.columns[k].page].cover not in free_covers]
    moving = [k for k in chosen if k not in fixed]
    boxes = {b for k in moving for b in model.columns[k].boxes}
    available_pages = [j for j, p in enumerate(model.pages) if p.cover in free_covers
                       and (p.deck or p.cover not in closed_hold)]
    stacks = {s for j in available_pages for s in model.pages[j].stacks}
    row = dict(chosen=None, status='DEADLINE', free_covers=sorted(free_covers),
               closed_hold=sorted(closed_hold), active_boxes=len(boxes),
               hold_only_boxes=len(hold_only),
               fixed_columns=len(fixed), incumbent_page_lifts=page_lift_cost(model, chosen))

    def done(status=None):
        if status is not None:
            row['status'] = status
        row['seconds'] = round(monotonic() - start, 6)
        return row

    if monotonic() >= deadline:
        return done()
    old_layout, old_moment = model.layout, model.fixed_moment
    greedy_hint = None
    try:
        model.layout = {}
        if model.lcg_target is not None:
            model.fixed_moment = sum(model.lcg_moment(model.columns[k]) for k in fixed)
        # Warm relocation of incumbent patterns to compatible free pages. Prefix
        # and suffix seeds also allow columns to split when hold/deck heights differ.
        for k in moving:
            c = model.columns[k]
            source = model.pages[c.page]
            for j in available_pages:
                p = model.pages[j]
                if p.kind != source.kind:
                    continue
                n = min(len(c.boxes), len(p.slots))
                for part in {c.boxes[:n], c.boxes[-n:]} if n else ():
                    model.add(j, part)
            if monotonic() >= deadline:
                return done()
        # A forced closure makes the old complete solution an infeasible hint.
        # Repack the moving boxes directly, retaining checked partial columns
        # even when first-fit fails. Different ties expose different identities
        # to the subsequent restricted master and avoid an all-slack cold start.
        first_fit = getattr(model, 'first_fit', None)
        row.update(greedy_seed_attempts=0, greedy_seed_complete=0, greedy_hint=False)
        seed_deadline = min(deadline, monotonic() + max(.25, min(pricing_s, .2 * seconds)))
        if boxes and first_fit is not None:
            for attempt in range(3):
                if monotonic() >= seed_deadline:
                    break
                row['greedy_seed_attempts'] += 1
                ending = first_fit(boxes, stacks, rng=random.Random(seed + attempt))
                if ending is None:
                    continue
                candidate = fixed + list(ending)
                validate_selection(model, candidate)
                row['greedy_seed_complete'] += 1
                permitted = all(
                    k in fixed or (model.columns[k].page in available_pages and
                    not (model.pages[model.columns[k].page].deck and
                         hold_only.intersection(model.columns[k].boxes)) and
                    not (hold_full and not model.pages[model.columns[k].page].deck and
                         len(model.columns[k].boxes) != len(model.pages[model.columns[k].page].slots)))
                    for k in candidate)
                if permitted and (greedy_hint is None or
                                  page_lift_cost(model, candidate) < page_lift_cost(model, greedy_hint)):
                    greedy_hint = candidate
                    row['greedy_hint'] = True
        if boxes and pricing_s > 0 and monotonic() < deadline:
            pricing_deadline = min(deadline, monotonic() + pricing_s)
            root = model.generate(active_boxes=boxes, active_stacks=stacks,
                                  deadline=pricing_deadline, phase=1, max_rounds=max_rounds)
            row.update(lp_uncovered=root['uncovered'], pricing_rounds=root['rounds'])
    finally:
        model.layout, model.fixed_moment = old_layout, old_moment
    if monotonic() >= deadline:
        return done()

    ip = cp_model.CpModel()
    available_pages = set(available_pages)
    fixed_set, use = set(fixed), []
    for k, c in enumerate(model.columns):
        if k in fixed_set or (k not in model.banned and c.page in available_pages
                             and set(c.boxes) <= boxes):
            use.append(k)
    x = {k: ip.NewBoolVar(f'p{k}') for k in use}
    by_box, by_stack = defaultdict(list), defaultdict(list)
    for k in use:
        c = model.columns[k]
        p = model.pages[c.page]
        if k in fixed_set:
            ip.Add(x[k] == 1)
        elif hold_full and not p.deck and len(c.boxes) != len(p.slots):
            ip.Add(x[k] == 0)
        if p.deck and hold_only.intersection(c.boxes):
            ip.Add(x[k] == 0)
        for b in c.boxes:
            by_box[b].append(x[k])
        for stack in p.stacks:
            by_stack[stack].append(x[k])
    if any(not by_box[b] for b in model.boxes):
        return done('UNCOVERED_POOL')
    for b in model.boxes:
        ip.AddExactlyOne(by_box[b])
    for variables in by_stack.values():
        ip.AddAtMostOne(variables)
    row['forbidden_pairs_applied'] = 0
    for a, b in forbidden_pairs:
        if a not in x or b not in x:
            continue
        if a == b:
            ip.Add(x[a] == 0)
        else:
            ip.Add(x[a] + x[b] <= 1)
        row['forbidden_pairs_applied'] += 1

    def moment_row(value, bound):
        coeff = {k: round(1000 * value(k)) for k in use}
        ip.Add(sum(coeff[k] * x[k] for k in use) <= round(1000 * bound))

    if heel_slack_tm is not None:
        def heel(k):
            c = model.columns[k]
            page = model.pages[c.page]
            return sum(float(model.T.mass[b]) / 1000 *
                       model.p.cells_tcg(model.T.slot_ids[page.slots[i]])
                       for i, b in enumerate(c.boxes))
        limit = abs(sum(heel(k) for k in chosen)) + heel_slack_tm
        moment_row(heel, limit)
        moment_row(lambda k: -heel(k), limit)
    if lcg_slack_tm is not None and model.lcg_target is not None:
        moment = lambda k: model.lcg_moment(model.columns[k])
        moment_row(moment, sum(moment(k) for k in chosen) + lcg_slack_tm)
    objective, scale = _objective(model, ip, x)
    ip.Minimize(objective)
    hint = set(greedy_hint if greedy_hint is not None else chosen)
    for k in use:
        ip.AddHint(x[k], int(k in hint))
    remaining = deadline - monotonic()
    row['pool_columns'] = len(use)
    if remaining <= 0:
        return done()
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = remaining
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed
    status = solver.Solve(ip)
    row['status'] = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return done()
    result = [k for k in use if solver.Value(x[k])]
    validate_selection(model, result)
    row.update(chosen=result, assignment=model.assignment(result),
               objective_lifts=solver.ObjectiveValue() / scale,
               bound_restricted_pool=solver.BestObjectiveBound() / scale,
               objective_scope='page/cover D15 only; yard and crane crossing excluded')
    if abs(row['objective_lifts'] - page_lift_cost(model, result)) > 1e-6:
        raise AssertionError('IP page objective disagrees with direct page projection')
    return done()

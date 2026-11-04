"""Per-call top/hatch union in the securing box-slot LP, including long bridges.

New code, with the independent closure adapted from C3 graph_model.py. The
copied securing LP supplies assignment/physics rows; no native checker changes.
LP integer exactness concerns the empty-arrival fixed-coordinate D15 top/hatch
scenario only. Crane-crossing closure, yard order and future physical replay
are not certified by this objective. See PORT-MATH.md for proofs and scope.
"""
from collections import defaultdict
from dataclasses import dataclass, replace
from time import monotonic

from frozen_securing.gap import Assignment as SecuringAssignment, Settings


@dataclass
class PortRows:
    start: int
    stop: int
    rows: list
    cost: dict
    required: dict
    opened: dict
    used: dict
    metadata: dict


def slot_geometry(T, slots):
    """Every actual support, rather than a page's single cover attribute."""
    support, tier = {}, {}
    for q in slots:
        support[q] = tuple(sorted({int(j) for j in T.slot_stacks[q] if j >= 0}))
        if not support[q]:
            raise ValueError('box-slot pair has no physical support')
        if len({bool(T.stack_deck[j]) for j in support[q]}) != 1:
            raise ValueError('a footprint spanning deck and hold is unsupported')
        tier[q] = int(T.slot_ids[q][-2:])
    return support, tier


def build_port_rows(pair, calls, support, tier, stack_cover, stack_deck, *,
                    start, hatch_now=1., hatch_later=1., restow=1.,
                    lifts_per_box=2., cliques=()):
    """Append a valid OR relaxation, exact at integral complete assignments.

    pair[v]=(box, reported slot). R[slot,call] means its surviving box must be
    removed at that call. A long box has ONE R, shared by both support columns.
    Each row is (coefficient tuples, upper bound, diagnostic label).
    """
    by_slot = defaultdict(list)
    for v, (b, q) in enumerate(pair):
        by_slot[q].append((v, b))
    ports = sorted(set(calls.values()))
    rows, cost, required, opened, used = [], defaultdict(float), {}, {}, {}
    next_var = start

    def variable():
        nonlocal next_var
        v = next_var
        next_var += 1
        return v

    def add(terms, upper, label):
        merged = defaultdict(float)
        for v, a in terms:
            merged[int(v)] += float(a)
        terms = tuple((v, a) for v, a in sorted(merged.items()) if abs(a) > 1e-12)
        rows.append((terms, float(upper), label))

    alive, target = {}, {}
    for q, candidates in by_slot.items():
        for p in ports:
            a = tuple(v for v, b in candidates if calls[b] >= p)
            if not a:
                continue
            t = tuple(v for v, b in candidates if calls[b] == p)
            alive[q, p], target[q, p] = a, t
            r = required[q, p] = variable()
            add([(r, 1.)]+[(v, -1.) for v in a], 0., 'required_le_surviving_occupancy')
            add([(v, 1.) for v in t]+[(r, -1.)], 0., 'targets_required')
            cost[r] += float(restow)*float(lifts_per_box)
            for v in t:
                cost[v] -= float(restow)*float(lifts_per_box)

    # An opening is the OR of all same-call hold targets touching this cover.
    hold_targets = defaultdict(dict)
    for (q, p), t in target.items():
        if not t or stack_deck[support[q][0]]:
            continue
        for h in {stack_cover[j] for j in support[q]} - {None}:
            hold_targets[h, p][q] = t
    for (h, p), slots in sorted(hold_targets.items()):
        y = opened[h, p] = variable()
        for t in slots.values():
            add([(v, 1.) for v in t]+[(y, -1.)], 0., 'target_opens_every_support_cover')
        all_targets = {v for t in slots.values() for v in t}
        add([(y, 1.)]+[(v, -1.) for v in all_targets], 0., 'opening_requires_hold_target')
        cost[y] += 2.*float(hatch_later)
    for h in sorted({h for h, _ in opened}):
        u = used[h] = variable()
        ys = [y for (cover, _), y in opened.items() if cover == h]
        for y in ys:
            add([(y, 1.), (u, -1.)], 0., 'later_use_requires_current_cover_use')
        add([(u, 1.)]+[(y, -1.) for y in ys], 0., 'current_use_requires_hold_cargo')
        cost[u] += 2.*float(hatch_now)

    # Every lower/upper pair is needed: intermediate departure tiers may have
    # disappeared at this call. Physical shared support induces the arcs.
    on_stack = defaultdict(set)
    for q in by_slot:
        for j in support[q]:
            on_stack[j].add(q)
    arcs = {(lo, hi) for slots in on_stack.values() for lo in slots for hi in slots
            if tier[lo] < tier[hi]}
    for lo, hi in sorted(arcs):
        for p in ports:
            if (lo, p) not in required or (hi, p) not in required:
                continue
            add([(v, 1.) for v in alive[hi, p]]
                +[(required[lo, p], 1.), (required[hi, p], -1.)], 1., 'upward_required_closure')
    for (q, p), a in alive.items():
        if not stack_deck[support[q][0]]:
            continue
        for h in {stack_cover[j] for j in support[q]} - {None}:
            if (h, p) in opened:
                add([(v, 1.) for v in a]+[(opened[h, p], 1.), (required[q, p], -1.)],
                    1., 'deck_clear_for_any_support_cover')

    # A certified circle clique of k boxes sharing one physical stack forces
    # at least k-1 blocker events there. This strengthens the fractional OR
    # relaxation, while keeping its integer closure objective unchanged.
    clique_rows = 0
    for clique in cliques:
        members = set(clique)
        for j, slots in sorted(on_stack.items()):
            occupants = [(v, 1.) for q in slots for v, b in by_slot[q] if b in members]
            if len({b for q in slots for _, b in by_slot[q] if b in members}) < 2:
                continue
            terms = list(occupants)
            for q in slots:
                for p in ports:
                    if (q, p) in required:
                        terms.append((required[q, p], -1.))
                        terms.extend((v, 1.) for v in target[q, p])
            add(terms, 1., 'weight_order_conditioned_circle_clique')
            clique_rows += 1
    for v in range(start, next_var):
        add([(v, 1.)], 1., 'boolean_aux_upper_bound')
    return PortRows(start, next_var, rows, dict(cost), required, opened, used,
                    dict(calls=len(ports), reported_slots=len(by_slot), support_arcs=len(arcs),
                         required_variables=len(required), cover_call_variables=len(opened),
                         cover_use_variables=len(used), circle_rows=clique_rows,
                         scope='empty-arrival fixed-coordinate top/hatch D15 OR relaxation; integral closure exact; crane crossings relaxed'))


def forced_circle_cliques(boxes, rank, calls, tolerance=0., *, limit=8):
    """Certified pairwise-crossing cliques conditioned on enforced E2 order.

    The left chord order is decreasing E2 rank, ties decreasing POD; the right
    endpoints are discharge order, with reversed same-call ties. We retain
    only chains whose consecutive rank gap EXCEEDS tolerance, so each pair's
    heavier-below relation is actually forced, rather than guessed from mass.
    No load-order restriction is introduced. Without E2, use no such rows.
    """
    order = sorted(boxes, key=lambda b: (-float(rank[b]), -calls[b], b))
    chains = []
    best = []
    for i, b in enumerate(order):
        predecessors = [best[j] for j, a in enumerate(order[:i])
                        if float(rank[a]) > float(rank[b])+tolerance and calls[a] < calls[b]]
        prefix = max(predecessors, key=lambda chain: (len(chain), tuple(chain)), default=())
        best.append(prefix+(b,))
        if len(best[-1]) >= 2:
            chains.append(best[-1])
    unique = sorted(set(chains), key=lambda c: (-len(c), tuple(c)))
    selected = []
    for chain in unique:
        if not any(set(chain) <= set(other) for other in selected):
            selected.append(chain)
        if len(selected) >= limit:
            break
    return selected


def exact_closure(calls, assignment, support, tier, stack_cover, stack_deck):
    """Independent least top/hatch closure on one fixed integral departure stow.

    assignment maps box to reported-slot key; surviving long boxes bridge their
    two stacks, and departed ones stop doing so. No future repositioning or
    crane crossing policy is constructed. This is an independent evaluator,
    never a substitute for real move replay or the unchanged full checker.
    """
    active = set(assignment)
    rows = []
    for p in sorted(set(calls.values())):
        target = {b for b in active if calls[b] == p}
        stacks = defaultdict(list)
        for b in active:
            for j in support[assignment[b]]:
                stacks[j].append((tier[assignment[b]], b))
        above = defaultdict(set)
        for entries in stacks.values():
            ordered = [b for _, b in sorted(entries)]
            for lo, hi in zip(ordered, ordered[1:]):
                above[lo].add(hi)
        opened = {stack_cover[j] for b in target for j in support[assignment[b]]
                  if not stack_deck[j] and stack_cover[j] is not None}
        required = set(target)
        for b in active:
            if any(stack_deck[j] and stack_cover[j] in opened for j in support[assignment[b]]):
                required.add(b)
        pending = list(required)
        while pending:
            for b in above[pending.pop()]:
                if b not in required:
                    required.add(b)
                    pending.append(b)
        rows.append(dict(call=p, targets=sorted(target), blockers=sorted(required-target),
                         opened=sorted(opened), required=sorted(required),
                         required_slots=sorted(assignment[b] for b in required)))
        active -= target
    return dict(calls=rows, restow_boxes=sum(len(r['blockers']) for r in rows),
                hatch_now=2*len({h for r in rows for h in r['opened']}),
                hatch_later=2*sum(len(r['opened']) for r in rows),
                scope='exact conditional fixed-coordinate top/hatch count; crane crossings relaxed')


class PortAssignment(SecuringAssignment):
    """The securing LP plus port accounting, present before its first solve."""

    def __init__(self, model, motions, settings=None, layout=None, *, circle_rows=True, circle_limit=8):
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        instance = model.p.instance
        if instance.onboard.placements or instance.onboard.covers_off:
            raise ValueError('PortAssignment supports empty arrival and initially closed covers only')
        # Replace the previous additive adjacent-tier/port-label proxy, while
        # preserving all securing, support, occupancy and physics preferences.
        settings = replace(settings or Settings(), restow_weight=0., cover_port_weight=0., deck_port_weight=0.)
        super().__init__(model, motions, settings=settings, layout=None)
        first = {}
        for p in range(instance.call_index+1, len(instance.rotation.calls)):
            first.setdefault(instance.rotation.calls[p].locode, p)
        self.port_calls = {b: first[model.p.boxes[self.T.box_ids[b]].pod] for b in model.boxes}
        slots = sorted({q for _, q in self.pair})
        self.port_support, self.port_tier = slot_geometry(self.T, slots)
        # T.rank/T.e2_tol describe the legacy CLASS policy. With rule freeze
        # active, native E2 instead uses gross kg and a separate tolerance;
        # do not infer its ordering theorem from these legacy class arrays.
        self.active_rule_freeze = bool(rule_freeze_v1_enabled())
        frozen_gross_policy = bool(self.active_rule_freeze or getattr(self.T, 'extra', {}).get('rule_freeze_v1'))
        self.circle_enabled = bool(circle_rows and self.T.e2_valid and not frozen_gross_policy)
        self.port_cliques = forced_circle_cliques(model.boxes, self.T.rank, self.port_calls,
                                                  float(self.T.e2_tol), limit=circle_limit) if self.circle_enabled else []
        self.port_metadata = dict(circle_scope='conditioned on hard E2 rank/tolerance order; disable when T4 peeled',
                                  circle_enabled=self.circle_enabled,
                                  circle_disabled_for_gross_policy=frozen_gross_policy,
                                  rule_freeze_v1_enabled=self.active_rule_freeze,
                                  exact_for_active_projection=not self.active_rule_freeze,
                                  circle_clique_size_lower_bound=max(map(len, self.port_cliques), default=1 if model.boxes else 0),
                                  circle_cliques=[list(c) for c in self.port_cliques],
                                  port_sequence_alone_global_restow_lower_bound=0,
                                  exact_integral_scope='fixed-coordinate top/hatch only; not physical future optimum',
                                  ignored_legacy_layout=bool(layout))
        self._port_built = False

    def build(self):
        if self._port_built:
            return self._built
        import numpy as np
        from scipy.sparse import coo_matrix, hstack, vstack
        c, A, b, E, e = super().build()
        self.base_columns = len(c)
        weights = self.m.p.instance.objective
        graph = build_port_rows(self.pair, self.port_calls, self.port_support, self.port_tier,
                                self.T.stack_cover, self.T.stack_deck, start=len(c),
                                hatch_now=weights.hatch_cover, hatch_later=weights.future_hatch_cover,
                                restow=weights.future_restow, lifts_per_box=weights.future_restow_lifts_per_box,
                                cliques=self.port_cliques)
        self.port_graph = graph
        extra = graph.stop-len(c)
        c = np.r_[c, np.zeros(extra)]
        for v, a in graph.cost.items():
            c[v] += a
        rr, cc, vv, bb = [], [], [], []
        for i, (terms, upper, _) in enumerate(graph.rows):
            for v, a in terms:
                rr.append(i); cc.append(v); vv.append(a)
            bb.append(upper)
        appended = coo_matrix((vv, (rr, cc)), shape=(len(bb), graph.stop)).tocsc()
        A = vstack([hstack([A, coo_matrix((A.shape[0], extra))]), appended], format='csc')
        E = hstack([E, coo_matrix((E.shape[0], extra))], format='csc')
        self._built = (c, A, np.r_[b, np.array(bb)], E, e)
        self._port_built = True
        self.port_metadata.update(graph.metadata, added_rows=len(graph.rows), added_columns=extra,
                                  base_rows=len(b), total_rows=A.shape[0])
        return self._built

    def solve(self, lower=None, upper=None, time_limit=60.):
        import numpy as np
        c = (self._built or self.build())[0]
        upper = np.full(len(c), np.inf) if upper is None else np.array(upper, copy=True)
        upper[self.port_graph.start:self.port_graph.stop] = np.minimum(
            upper[self.port_graph.start:self.port_graph.stop], 1.)
        return super().solve(lower=lower, upper=upper, time_limit=time_limit)

    def tail(self, fixed, *, seconds=30., seed=0, log=None, hint=None):
        """Integer tail with Boolean graph auxiliaries and continuous soft rows.

        Unlike the copied generic tail, fixed-only rows and auxiliary-to-
        auxiliary dependencies are retained. Securing/moment slacks still use
        thousandths; OR variables are Boolean rather than free soft slacks.
        """
        import numpy as np
        from ortools.sat.python import cp_model
        c, A, rhs, _, _ = self._built or self.build()
        n, nb, T = self.n, len(self.m.boxes), self.T
        taken = {int(g) for q in fixed.values() for g in T.slot_cells[q] if g >= 0}
        solid = np.zeros(len(c))
        for v, (b, q) in enumerate(self.pair):
            if fixed.get(b) == q:
                solid[v] = 1.
        free = [v for v, (b, q) in enumerate(self.pair) if b not in fixed
                and not ({int(g) for g in T.slot_cells[q] if g >= 0} & taken)]
        cp = cp_model.CpModel()
        variables = {v: cp.NewBoolVar(f'x{v}') for v in free}
        variables.update({v: cp.NewBoolVar(f'port{v}') for v in range(self.port_graph.start, self.port_graph.stop)})
        soft = {}
        rows, used = A.tocsr(), A@solid
        for i in range(rows.shape[0]):
            cols = rows.indices[rows.indptr[i]:rows.indptr[i+1]]
            vals = rows.data[rows.indptr[i]:rows.indptr[i+1]]
            terms = [(int(j), float(a)) for j, a in zip(cols, vals) if int(j) in variables]
            slacks = [(int(j), float(a)) for j, a in zip(cols, vals) if n+nb <= j < self.base_columns]
            room = float(rhs[i]-used[i])
            if not terms and not slacks:
                if room < -1e-8:
                    if log is not None:
                        log.append(dict(tail='fixed row infeasible', row=i))
                    return None
                continue
            magnitude = max(abs(a) for _, a in terms+slacks)
            scale = 20000./max(1e-12, magnitude)
            expression = sum(int(np.ceil(a*scale-1e-9))*variables[j] for j, a in terms)
            for j, a in slacks:
                if j not in soft:
                    soft[j] = cp.NewIntVar(0, 10000000, f'soft{j}')
                expression += int(np.ceil(a*scale*.001-1e-9))*soft[j]
            cp.Add(expression <= int(np.floor(room*scale+1e-9)))
        by_box = defaultdict(list)
        for v in free:
            by_box[self.pair[v][0]].append(variables[v])
        for b in self.m.boxes:
            if b not in fixed:
                if not by_box[b]:
                    return None
                cp.AddExactlyOne(by_box[b])
        cp.Minimize(sum(int(round(1000*c[v]))*x for v, x in variables.items())
                    +sum(int(round(c[j]))*z for j, z in soft.items()))
        if hint is not None:
            for v in free:
                cp.AddHint(variables[v], int(hint[v] >= .5))
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.max_time_in_seconds = max(.01, seconds)
        solver.parameters.random_seed = seed
        status = solver.Solve(cp)
        if log is not None:
            log.append(dict(tail=solver.StatusName(status), left=nb-len(fixed), vars=len(variables),
                            port_aux=self.port_graph.stop-self.port_graph.start,
                            s=round(solver.WallTime(), 2)))
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        out = dict(fixed)
        out.update({self.pair[v][0]: self.pair[v][1] for v in free if solver.Value(variables[v])})
        return out

    def exact_port_account(self, placed):
        """Independent graph count on internal {box index: slot index}."""
        result = exact_closure(self.port_calls, placed, self.port_support, self.port_tier,
                               self.T.stack_cover, self.T.stack_deck)
        w = self.m.p.instance.objective
        result['weighted_top_hatch'] = (w.hatch_cover*result['hatch_now']
                                       +w.future_hatch_cover*result['hatch_later']
                                       +w.future_restow*w.future_restow_lifts_per_box*result['restow_boxes'])
        result['exact_for_active_projection'] = not self.active_rule_freeze
        result['rule_freeze_v1_enabled'] = self.active_rule_freeze
        return result

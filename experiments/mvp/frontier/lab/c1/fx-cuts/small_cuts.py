"""Capacity-preserving binary swap neighborhoods; exact surrogate cuts, real replay acceptance.

A variable exchanges two equal-length boxes between their occupied slots. Disjoint
pairs preserve occupied cells and size mix. Pair costs approximate yard digging,
future overstows and hatch mixing; they are NOT the real objective. The exact
binary solver checks submodularity (including consistent label reversals), uses a
s-t cut where possible and otherwise enumerates at most 12 variables. Neither
that exact neighborhood result nor a local replay optimum proves global optimality.
The caller must still finish every returned assignment and run the full checker.
"""
from __future__ import annotations

from collections import deque
from itertools import combinations, product
import random
from time import monotonic


LAST_STATS = {}


def binary_energy(labels, unary, pairwise):
    return sum(unary[i][x] for i, x in enumerate(labels)) + sum(
        table[labels[i]][labels[j]] for (i, j), table in pairwise.items())


def _cut(unary, pairwise):
    """Exact cut for submodular 2x2 tables, using an ordinary Dinic flow."""
    n = len(unary)
    # E = constant + a*x + b*y + c*|x-y|, with c >= 0.
    linear = [float(row[1] - row[0]) for row in unary]
    edges = []
    for (i, j), t in pairwise.items():
        e00, e01, e10, e11 = t[0][0], t[0][1], t[1][0], t[1][1]
        c = (e01 + e10 - e00 - e11) / 2
        if c < -1e-9:
            raise ValueError('non-submodular pair passed to cut')
        linear[i] += e10 - e00 - c
        linear[j] += e01 - e00 - c
        if c > 1e-9:
            edges.append((i, j, c))
    source, sink = n, n + 1
    graph = [[] for _ in range(n + 2)]

    def arc(a, b, cap):
        graph[a].append([b, len(graph[b]), float(cap)])
        graph[b].append([a, len(graph[a]) - 1, 0.0])

    # Label 0 is the source shore, label 1 the sink shore.
    for i, value in enumerate(linear):
        arc(source, i, max(0.0, value))
        arc(i, sink, max(0.0, -value))
    for i, j, cap in edges:
        arc(i, j, cap)
        arc(j, i, cap)
    while True:
        level = [-1] * len(graph)
        level[source] = 0
        queue = deque([source])
        while queue:
            u = queue.popleft()
            for v, _, cap in graph[u]:
                if cap > 1e-9 and level[v] < 0:
                    level[v] = level[u] + 1
                    queue.append(v)
        if level[sink] < 0:
            break
        cursor = [0] * len(graph)

        def send(u, amount):
            if u == sink:
                return amount
            while cursor[u] < len(graph[u]):
                e = graph[u][cursor[u]]
                v, rev, cap = e
                if cap > 1e-9 and level[v] == level[u] + 1:
                    pushed = send(v, min(amount, cap))
                    if pushed > 1e-9:
                        e[2] -= pushed
                        graph[v][rev][2] += pushed
                        return pushed
                cursor[u] += 1
            return 0.0
        while send(source, float('inf')) > 1e-9:
            pass
    seen = {source}
    queue = deque([source])
    while queue:
        for v, _, cap in graph[queue.popleft()]:
            if cap > 1e-9 and v not in seen:
                seen.add(v)
                queue.append(v)
    return tuple(0 if i in seen else 1 for i in range(n))


def solve_binary(unary, pairwise, *, enumeration_limit=12):
    """Return (labels, algorithm). Exact for supplied pairwise surrogate only.

    A consistent XOR reversal of labels can make some non-submodular energies
    submodular. An inconsistent signed cycle is rejected; small cases enumerate.
    Larger frustrated problems raise instead of silently changing their energy.
    """
    n = len(unary)
    adjacency = [[] for _ in range(n)]
    for (i, j), t in pairwise.items():
        slack = t[0][1] + t[1][0] - t[0][0] - t[1][1]
        if abs(slack) <= 1e-9:
            continue
        parity = int(slack < 0)
        adjacency[i].append((j, parity))
        adjacency[j].append((i, parity))
    flips = [None] * n
    consistent = True
    for root in range(n):
        if flips[root] is not None:
            continue
        flips[root] = 0
        todo = [root]
        while todo:
            i = todo.pop()
            for j, parity in adjacency[i]:
                wanted = flips[i] ^ parity
                if flips[j] is None:
                    flips[j] = wanted
                    todo.append(j)
                elif flips[j] != wanted:
                    consistent = False
    if consistent:
        u = [tuple(row[x ^ flips[i]] for x in (0, 1)) for i, row in enumerate(unary)]
        p = {(i, j): tuple(tuple(t[x ^ flips[i]][y ^ flips[j]] for y in (0, 1))
                          for x in (0, 1)) for (i, j), t in pairwise.items()}
        labels = _cut(u, p)
        return tuple(x ^ flips[i] for i, x in enumerate(labels)), 'min-cut'
    if n > enumeration_limit:
        raise ValueError('frustrated non-submodular neighborhood exceeds enumeration limit')
    labels = min(product((0, 1), repeat=n), key=lambda x: (binary_energy(x, unary, pairwise), x))
    return labels, 'enumeration'


class Proxy:
    """Pairwise graph on cargo, with all geometry precomputed per occupied slot."""

    def __init__(self, problem, assignment):
        from stowage.conventions import cells_of_slot, parse_slot
        self.p = problem
        self.geometry = {}
        for slot in assignment.values():
            stacks = {problem.cells[c].stack_id for c in cells_of_slot(slot)}
            covers = {problem.stacks[s].hatch_cover_id for s in stacks} - {None}
            loc = parse_slot(slot)
            self.geometry[slot] = (stacks, covers, loc.tier, loc.on_deck)

    def cost(self, a, sa, b, sb):
        p = self.p
        sta, ca, ta, da = self.geometry[sa]
        stb, cb, tb, db = self.geometry[sb]
        cost = 0.0
        a_before_b = bool((sta & stb and ta < tb) or (ca & cb and not da and db))
        b_before_a = bool((sta & stb and tb < ta) or (ca & cb and not db and da))
        # A later-port box over earlier cargo creates future restows. Pair counts
        # overestimate when several lower boxes trigger one opening: deliberate proxy.
        if a_before_b and p.pod(b) > p.pod(a):
            cost += 2
        if b_before_a and p.pod(a) > p.pod(b):
            cost += 2
        # A mixed hold opens its cover at more ports; unique openings are hyperedges,
        # so this pairwise encouragement is explicitly not a hatch-lift identity.
        if ca & cb and not da and not db and p.pod(a) != p.pod(b):
            cost += 1
        if p.yard_stack.get(a) == p.yard_stack.get(b) and p.yard_stack.get(a) is not None:
            if a_before_b and p.yard_depth.get(a, 0) > p.yard_depth.get(b, 0):
                cost += 1
            if b_before_a and p.yard_depth.get(b, 0) > p.yard_depth.get(a, 0):
                cost += 1
        return cost


def _energy(proxy, assignment, pairs):
    p, T = proxy.p, proxy.p.engine.T
    paired = {b for pair in pairs for b in pair}
    fixed = [b for b in assignment if b not in paired]
    options = []
    for a, b in pairs:
        options.append((({a: assignment[a], b: assignment[b]}),
                        ({a: assignment[b], b: assignment[a]})))
    unary = []
    for choice in options:
        row = []
        for placements in choice:
            value = 0.0
            for box, slot in placements.items():
                if not T.static[T.box_index[box], T.slot_index[slot]]:
                    value += 1e6
                value += sum(proxy.cost(box, slot, b, assignment[b]) for b in fixed)
            (a, sa), (b, sb) = placements.items()
            value += proxy.cost(a, sa, b, sb)
            row.append(value)
        unary.append(row)
    pairwise = {}
    for i, j in combinations(range(len(pairs)), 2):
        table = tuple(tuple(sum(proxy.cost(a, sa, b, sb)
                                for a, sa in options[i][x].items()
                                for b, sb in options[j][y].items())
                            for y in (0, 1)) for x in (0, 1))
        if any(any(value for value in row) for row in table):
            pairwise[i, j] = table
    return unary, pairwise


def improve(problem, assignment, *, seed=0, budget_s=30.0):
    """Replay-ranked assignments, baseline included; full finisher/checker is external.

    At most 12 disjoint equal-length swaps are solved together. Cut candidates can
    cross a single-swap local barrier; a deterministic randomized single-swap scan
    supplies an independent true-objective refinement. All accepted candidates go
    through the actual placement mask and full move replay before being returned.
    """
    from alns import Sequencer, _mask_ok
    started = monotonic()
    deadline = started + max(0.0, budget_s)
    rng = random.Random(seed)
    sequencer = Sequencer(problem)
    stats = dict(cut=0, enumeration=0, evaluated=0, rejected=0, improvements=0)
    seen, pool = set(), []

    def evaluate(candidate):
        key = tuple(sorted(candidate.items()))
        if key in seen or monotonic() >= deadline:
            return None
        seen.add(key)
        stats['evaluated'] += 1
        actions = sequencer.order(candidate)
        if actions is None or not _mask_ok(problem, actions):
            stats['rejected'] += 1
            return None
        scored = problem.score(actions)
        if scored is None:
            stats['rejected'] += 1
            return None
        pool.append((scored.objective, key, dict(candidate)))
        return scored.objective

    current = dict(assignment)
    best = evaluate(current)
    if best is None:
        best = float('inf')
    proxy = Proxy(problem, current)
    rounds = 0
    while monotonic() < deadline and rounds < 12:
        rounds += 1
        # Random disjoint pairs give a capacity-preserving binary neighborhood.
        lengths = {}
        for b in current:
            lengths.setdefault(problem.boxes[b].length_ft, []).append(b)
        pairs = []
        for boxes in lengths.values():
            rng.shuffle(boxes)
            pairs.extend(zip(boxes[::2], boxes[1::2]))
        pairs = pairs[:12]
        if pairs:
            unary, pairwise = _energy(proxy, current, pairs)
            labels, algorithm = solve_binary(unary, pairwise)
            stats['cut' if algorithm == 'min-cut' else 'enumeration'] += 1
            candidate = dict(current)
            for (a, b), flip in zip(pairs, labels):
                if flip:
                    candidate[a], candidate[b] = current[b], current[a]
            cost = evaluate(candidate)
            if cost is not None and cost < best:
                current, best = candidate, cost
                stats['improvements'] += 1
        moves = [(a, b) for a, b in combinations(current, 2)
                 if problem.boxes[a].length_ft == problem.boxes[b].length_ft]
        rng.shuffle(moves)
        changed = False
        for a, b in moves:
            if monotonic() >= deadline:
                break
            candidate = dict(current)
            candidate[a], candidate[b] = current[b], current[a]
            cost = evaluate(candidate)
            if cost is not None and cost < best:
                current, best = candidate, cost
                stats['improvements'] += 1
                changed = True
                break
        if not changed and rounds >= 4:
            break
    # Keep a small diverse pool, including the supplied assignment as the fallback.
    pool.sort(key=lambda row: (row[0], row[1]))
    out = [row[2] for row in pool[:8]]
    if not any(candidate == assignment for candidate in out):
        out.append(dict(assignment))
    stats.update(wall_s=monotonic() - started, best=None if best == float('inf') else best)
    LAST_STATS.clear()
    LAST_STATS.update(stats)
    return out

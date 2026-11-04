"""Copied from c1/fx-vlsn/search.py; changes are mask injection and optional fixed work.

The source remains read-only. Fixed work is used to compare identical decisions,
independently of hardware load and wall-time stopping.
"""
from __future__ import annotations

from collections import defaultdict
import math
import random
from time import monotonic

from alns import Moves, Sequencer, _mask_ok
from stowage.actions.space import Place
from stowage.conventions import cells_of_slot
from stowage.solver.gold_warmstart_search import precedence_sets


class CachedSequencer:
    """Same yard-first order as ALNS; cache the slot dependency graph."""

    def __init__(self, problem):
        self.p = problem
        self.cache = {}
        self.above = {}
        for b in problem.boxes:
            stack = problem.instance_yard.get(problem.yard_stack.get(b), ())
            self.above[b] = set(stack[stack.index(b)+1:]) if b in stack else set()

    def order(self, assignment):
        slots = tuple(sorted(assignment.values()))
        if len(set(slots)) != len(slots):
            return None
        if slots not in self.cache:
            dummy = [Place(str(i), s) for i, s in enumerate(slots)]
            self.cache[slots] = precedence_sets(self.p.instance, dummy)
            if len(self.cache) > 512:
                self.cache.pop(next(iter(self.cache)))
        predecessors = self.cache[slots]
        owner = {s: b for b, s in assignment.items()}
        pending, done, taken, out = set(range(len(slots))), set(), set(), []
        while pending:
            ready = [i for i in pending if predecessors[i] <= done]
            if not ready:
                return None
            chosen = min(ready, key=lambda i: (len(self.above[owner[slots[i]]] - taken), owner[slots[i]]))
            box = owner[slots[chosen]]
            out.append(Place(box, slots[chosen]))
            pending.remove(chosen)
            done.add(chosen)
            taken.add(box)
        return tuple(out)


def cyclic_move(problem, assignment, rng, max_depth=8):
    """Rotate 2..max_depth same-length boxes, usually with matching POD.

    A cycle simultaneously moves all boxes; no feasible intermediate swaps
    are required. The whole cycle, rather than its individual arcs, is scored.
    """
    groups = defaultdict(list)
    same_pod = rng.random() < .70
    for box in assignment:
        b = problem.boxes[box]
        groups[(b.length_ft, problem.pod(box) if same_pod else None)].append(box)
    groups = [g for g in groups.values() if len(g) >= 2]
    if not groups:
        return None
    pool = rng.choice(groups)
    limit = min(max_depth, len(pool))
    # Bias toward short cycles without excluding deep exchanges.
    depth = rng.choices(range(2, limit+1), weights=[1/math.sqrt(k-1) for k in range(2, limit+1)])[0]
    boxes = rng.sample(pool, depth)
    out = dict(assignment)
    for i, box in enumerate(boxes):
        out[box] = assignment[boxes[(i+1) % depth]]
    return out


def run(problem, starts, *, budget_s, seed=0, neighborhood='cycle', scorer='fast', keep=6, mask=None, max_iterations=None):
    """Copied C1 kernel; optional injected mask and fixed-work timing protocol.

    max_iterations makes both loop termination and annealing deterministic.
    Production calls omit it and retain the original wall-budget behavior.
    """
    from fast_score import FastScorer
    rng = random.Random(seed)
    sequencer = CachedSequencer(problem)
    moves = Moves(problem, rng)
    fast = FastScorer(problem) if scorer == 'fast' else None
    began = monotonic()
    deadline = began + max(0., budget_s)
    stats = dict(evaluations=0, replay=0, fast=0, fast_fallback=0, masks=0,
                 mask_rejected=0, accepted=0, improvements=0, depths={}, trace=[])
    stats.update(score_s=0., sequence_s=0., mask_s=0., archive_replay_s=0.)
    archive, seen = {}, set()

    def evaluate(actions):
        stats['evaluations'] += 1
        score_started = monotonic()
        if fast is not None:
            value = fast.score(actions)
            if value is not None:
                stats['fast'] += 1
                stats['score_s'] += monotonic()-score_started
                return value
            stats['fast_fallback'] += 1
        stats['replay'] += 1
        scored = problem.score(actions)
        stats['score_s'] += monotonic()-score_started
        return scored.objective if scored is not None else None

    parents = []
    for raw in starts:
        raw = getattr(raw, 'actions', raw)
        actions = tuple(Place(b, s) for b, s in raw.items()) if isinstance(raw, dict) else tuple(raw)
        scored = problem.score(actions)
        if scored is not None:
            key = tuple(sorted((a.box, a.slot) for a in actions))
            if key not in archive or scored.objective < archive[key][0]:
                archive[key] = (scored.objective, actions)
            parents.append((scored.objective, dict(key), actions))
    if not parents:
        stats['wall_s'] = monotonic()-began
        return [], stats
    parents.sort(key=lambda x: x[0])
    value, assignment, actions = parents[0]
    best = value
    stats['start'] = best
    stats['trace'].append((0., best))
    iteration = 0
    while monotonic() < deadline and (max_iterations is None or iteration < max_iterations):
        iteration += 1
        if iteration % 251 == 0:
            value, assignment, actions = rng.choice(parents[:6])
        if neighborhood == 'swap':
            proposal = moves.swap(assignment)
        elif neighborhood == 'baseline_moves':
            proposal = getattr(moves, rng.choice(Moves.OPERATORS))(assignment)
        elif rng.random() < .15:
            proposal = moves.relocate_top(assignment)
        else:
            proposal = cyclic_move(problem, assignment, rng)
        if proposal is None:
            continue
        key = tuple(sorted(proposal.items()))
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > 100000:
            seen.clear()
        if problem.mixes_hold_stack(proposal):
            continue
        sequence_started = monotonic()
        ordered = sequencer.order(proposal)
        stats['sequence_s'] += monotonic()-sequence_started
        if ordered is None:
            continue
        candidate = evaluate(ordered)
        if candidate is None:
            continue
        progress = (iteration/max_iterations if max_iterations is not None
                    else (monotonic()-began)/max(budget_s, 1.e-9))
        temperature = max(.05, 1.1*(1-progress))
        delta = candidate-value
        if delta > 0 and rng.random() >= math.exp(-delta/temperature):
            continue
        # Keep traversal mask-legal too: relaxed walks spend almost all their
        # time in dead regions on weight-constrained slots.
        stats['masks'] += 1
        mask_started = monotonic()
        mask_ok = _mask_ok(problem, ordered) if mask is None else mask.check(ordered)
        stats['mask_s'] += monotonic()-mask_started
        if not mask_ok:
            stats['mask_rejected'] += 1
            continue
        value, assignment, actions = candidate, proposal, ordered
        stats['accepted'] += 1
        if candidate <= best and key not in archive:
            # Always independently replay a candidate before returning it.
            replay_started = monotonic()
            scored = problem.score(ordered)
            stats['archive_replay_s'] += monotonic()-replay_started
            if scored is None or scored.objective != candidate:
                raise AssertionError('fast score disagrees with real replay')
            archive[key] = (candidate, ordered)
            parents.append((candidate, proposal, ordered))
            parents.sort(key=lambda x: x[0])
            parents = parents[:12]
            if candidate < best:
                best = candidate
                stats['improvements'] += 1
                stats['trace'].append((round(monotonic()-began, 4), best))
            archive = dict(sorted(archive.items(), key=lambda kv: kv[1][0])[:keep])
    stats.update(best=best, wall_s=monotonic()-began, iterations=iteration,
                 unique=len(seen), topology_cache=len(sequencer.cache))
    return [v[1] for v in sorted(archive.values(), key=lambda v: v[0])], stats

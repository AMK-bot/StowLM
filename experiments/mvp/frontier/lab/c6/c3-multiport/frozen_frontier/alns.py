"""Adaptive large neighbourhood search over complete stow plans (frontier benchmark, ALNS layer).

Method: destroy part of a plan, rebuild it, accept by simulated annealing, and learn which operators pay off
(Ropke & Pisinger 2006; applied to stowage by Larsen & Pacino 2021). What is ours: the search works on the project's
real plan representation (ordered ``Place(box, slot)`` actions), every rebuild goes through the shared feasibility
engine, and every candidate is scored by the real move replay, so the score is the checker's own objective
(yard relocations + restows and hatch lifts now and at later ports), not a surrogate.

A destroyed box takes with it every box that physically depends on it: boxes above it, and deck cargo over its hatch.
APPROXIMATION: the mask has no ballast or full physics; ``planner.py`` finishes and fully checks the best plans.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math
import random
from time import monotonic

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.conventions import cells_of_slot, format_slot, parse_slot
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.moves import apply_plan
from stowage.solver.gold_warmstart_search import precedence_sets


@dataclass
class Scored:
    actions: tuple
    objective: float
    plan: object


@dataclass
class Stats:
    iterations: int = 0
    accepted: int = 0
    improved: int = 0
    infeasible: int = 0
    best_trace: list = field(default_factory=list)
    operator_calls: dict = field(default_factory=dict)
    operator_gains: dict = field(default_factory=dict)


class Problem:
    """Shared read-only views of one instance, plus the real objective."""

    def __init__(self, instance):
        self.instance = instance
        self.space = get_action_space('boxslot')
        self.boxes = {b.id: b for b in instance.containers}
        self.cells = {c.id: c for c in instance.vessel.cells}
        self.stacks = {s.id: s for s in instance.vessel.stacks}
        self.port_order = {c.locode: c.index for c in instance.rotation.calls}
        self.yard_stack = {cid: stack.key for stack in instance.yard.stacks for cid in stack.boxes}
        self.yard_depth = {cid: len(stack.boxes)-1-i for stack in instance.yard.stacks
                           for i, cid in enumerate(stack.boxes)}
        self.instance_yard = {stack.key: tuple(stack.boxes) for stack in instance.yard.stacks}
        self.engine = make_feasibility_engine(instance, self.space, config=FeasibilityConfig(extra={'global_screen': 0}))
        self.slots_by_length = defaultdict(list)
        for cell in instance.vessel.cells:
            if cell.accepts_20:
                self.slots_by_length[20].append(cell.id)
            where = parse_slot(cell.id)
            partner = format_slot(where.bay+2, where.row, where.tier)
            if cell.accepts_40 and partner in self.cells:
                self.slots_by_length[40].append(format_slot(where.bay+1, where.row, where.tier))
        self.objective = instance.objective

    def pod(self, box):
        return self.port_order[self.boxes[box].pod]

    def mixes_hold_stack(self, assignment):
        """True if a hold stack would hold both 20- and 40-foot boxes, which the lashing check refuses."""
        lengths = defaultdict(set)
        for box, slot in assignment.items():
            for cell in cells_of_slot(slot):
                stack = self.cells[cell].stack_id
                if not self.stacks[stack].on_deck:
                    lengths[stack].add(self.boxes[box].length_ft)
        return any(len(found) > 1 for found in lengths.values())

    def cells_tcg(self, slot):
        return self.cells[cells_of_slot(slot)[0]].tcg_m

    def score(self, actions) -> Scored | None:
        try:
            plan = self.space.expand(self.instance, list(actions))
        except (ValueError, KeyError, TypeError):
            return None
        replay = apply_plan(self.instance, plan)
        if not replay.legal:
            return None
        return Scored(tuple(actions), float(replay.count.weighted_nonproductive), plan)


class Rebuilder:
    """Greedy randomised construction through the feasibility engine, sharing one engine via push/pop."""

    def __init__(self, problem: Problem, rng: random.Random):
        self.p, self.rng = problem, rng

    def _estimate(self, ex, placed, box, slot, noise):
        """Marginal cost estimate of placing ``box`` in ``slot`` now. Same units as the objective (lifts)."""
        p, obj = self.p, self.p.objective
        cost = 0.0
        location = ex.location(box)
        if location is not None and location[0] == 'yard':
            stack = ex.yard[location[1]]
            cost += obj.relocate*(len(stack)-stack.index(box)-1)
        footprint = cells_of_slot(slot)
        stacks = {p.cells[c].stack_id for c in footprint}
        pod = p.pod(box)
        tier = parse_slot(slot).tier
        deck = parse_slot(slot).on_deck
        covers = {p.stacks[s].hatch_cover_id for s in stacks} - {None}
        for other, other_slot in placed:
            other_pod = p.pod(other)
            if pod <= other_pod:
                continue
            other_stacks = {p.cells[c].stack_id for c in cells_of_slot(other_slot)}
            # This box stays aboard past the one below it, so that box's discharge restows this one.
            if other_stacks & stacks and parse_slot(other_slot).tier < tier:
                cost += obj.future_restow*obj.future_restow_lifts_per_box
            elif deck and not parse_slot(other_slot).on_deck:
                if {p.stacks[s].hatch_cover_id for s in other_stacks} & covers:
                    cost += obj.future_hatch_cover
        if noise:
            cost += self.rng.uniform(0., noise)
        return cost

    def rebuild(self, kept, remaining, *, noise, deadline):
        """Kept actions first, in order, then the remaining boxes greedily. None on dead end or timeout."""
        p = self.p
        engine = p.engine
        pushed = 0
        try:
            ex = Expander(p.instance)
            placed = []
            for action in kept:
                if not engine.is_legal(action):
                    return None
                engine.push(action)
                pushed += 1
                ex.step(action)
                placed.append((action.box, action.slot))
            out = list(kept)
            pending = set(remaining)
            while pending:
                if monotonic() >= deadline:
                    return None
                best = None
                occupied = {c for _, placed_slot in placed for c in cells_of_slot(placed_slot)}
                for box in engine.legal_box_ids():
                    if box not in pending:
                        continue
                    for slot in engine.legal_slots_for_box(box):
                        # The mask allows a slot that is already taken: the expander would evict its box, which is
                        # a same-call restow and leaves that box unplaced.
                        if occupied.intersection(cells_of_slot(slot)):
                            continue
                        value = self._estimate(ex, placed, box, slot, noise)
                        if best is None or value < best[0]:
                            best = (value, box, slot)
                if best is None:
                    return None
                action = Place(best[1], best[2])
                engine.push(action)
                pushed += 1
                ex.step(action)
                placed.append((action.box, action.slot))
                out.append(action)
                pending.discard(action.box)
            return tuple(out)
        finally:
            for _ in range(pushed):
                engine.pop()


def _closure(chosen, successors):
    """Add every action that depends on a chosen one."""
    out, todo = set(chosen), list(chosen)
    while todo:
        for next_ in successors[todo.pop()]:
            if next_ not in out:
                out.add(next_)
                todo.append(next_)
    return out


class Destroyers:
    """Each picks seed actions to remove; dependents are added by ``_closure``."""

    def __init__(self, problem: Problem, rng: random.Random):
        self.p, self.rng = problem, rng

    def random(self, actions, k):
        return self.rng.sample(range(len(actions)), min(k, len(actions)))

    def same_pod(self, actions, k):
        pod = self.p.pod(self.rng.choice(actions).box)
        pool = [i for i, a in enumerate(actions) if self.p.pod(a.box) == pod]
        return self.rng.sample(pool, min(k, len(pool)))

    def same_hatch(self, actions, k):
        hatch = lambda a: self.p.stacks[self.p.cells[cells_of_slot(a.slot)[0]].stack_id].hatch_cover_id
        target = hatch(self.rng.choice(actions))
        pool = [i for i, a in enumerate(actions) if hatch(a) == target]
        return self.rng.sample(pool, min(k, len(pool)))

    def same_yard_stack(self, actions, k):
        key = self.p.yard_stack.get(self.rng.choice(actions).box)
        pool = [i for i, a in enumerate(actions) if self.p.yard_stack.get(a.box) == key]
        return self.rng.sample(pool, min(k, len(pool)))

    def deep_in_yard(self, actions, k):
        ranked = sorted(range(len(actions)), key=lambda i: (-self.p.yard_depth.get(actions[i].box, 0), self.rng.random()))
        return ranked[:k]

    def overstowing(self, actions, k):
        """Boxes sitting over a box that leaves earlier: the sources of later restows."""
        p = self.p
        below = defaultdict(list)
        for i, a in enumerate(actions):
            for c in cells_of_slot(a.slot):
                below[p.cells[c].stack_id].append((parse_slot(a.slot).tier, i))
        bad = set()
        for column in below.values():
            column.sort()
            for (_, low), (_, high) in zip(column, column[1:]):
                if p.pod(actions[high].box) > p.pod(actions[low].box):
                    bad.add(high)
        pool = sorted(bad) or list(range(len(actions)))
        return self.rng.sample(pool, min(k, len(pool)))

    OPERATORS = ('random', 'same_pod', 'same_hatch', 'same_yard_stack', 'deep_in_yard', 'overstowing')


class Sequencer:
    """Cheap load order for a fixed box-to-slot assignment: dependency order, shallowest yard box first."""

    def __init__(self, problem: Problem):
        self.p = problem

    def order(self, assignment):
        p = self.p
        actions = [Place(box, slot) for box, slot in assignment.items()]
        predecessors = precedence_sets(p.instance, actions)
        pending, done, out = set(range(len(actions))), set(), []
        taken = set()
        while pending:
            ready = [i for i in pending if predecessors[i] <= done]
            if not ready:
                return None
            def key(i):
                box = actions[i].box
                stack = p.instance_yard.get(p.yard_stack.get(box), ())
                above = stack[stack.index(box)+1:] if box in stack else ()
                return (sum(1 for b in above if b not in taken), box)
            chosen = min(ready, key=key)
            pending.discard(chosen)
            done.add(chosen)
            taken.add(actions[chosen].box)
            out.append(actions[chosen])
        return tuple(out)


class Moves:
    """Neighbourhoods on a complete assignment. Each returns a new assignment or None."""

    def __init__(self, problem: Problem, rng: random.Random):
        self.p, self.rng = problem, rng

    def _column(self, slot):
        return self.p.cells[cells_of_slot(slot)[0]].stack_id

    def swap(self, a):
        p = self.p
        first = self.rng.choice(list(a))
        same = [b for b in a if b != first and p.boxes[b].length_ft == p.boxes[first].length_ft]
        if not same:
            return None
        other = self.rng.choice(same)
        out = dict(a)
        out[first], out[other] = a[other], a[first]
        return out

    def swap_across_pods(self, a):
        """Swap two boxes with different discharge ports, the swap that can remove an overstow."""
        p = self.p
        first = self.rng.choice(list(a))
        pool = [b for b in a if p.boxes[b].length_ft == p.boxes[first].length_ft and p.pod(b) != p.pod(first)]
        if not pool:
            return None
        other = self.rng.choice(pool)
        out = dict(a)
        out[first], out[other] = a[other], a[first]
        return out

    def swap_yard_mates(self, a):
        """Swap a box with one from the same yard stack, which changes who is dug out first."""
        p = self.p
        first = self.rng.choice(list(a))
        key = p.yard_stack.get(first)
        pool = [b for b in a if b != first and p.yard_stack.get(b) == key and p.boxes[b].length_ft == p.boxes[first].length_ft]
        if not pool:
            return None
        other = self.rng.choice(pool)
        out = dict(a)
        out[first], out[other] = a[other], a[first]
        return out

    def swap_columns(self, a):
        """Exchange the contents of two ship columns tier for tier (same tiers, same lengths)."""
        p = self.p
        columns = defaultdict(dict)
        for box, slot in a.items():
            columns[self._column(slot)][parse_slot(slot).tier] = box
        if len(columns) < 2:
            return None
        one, two = self.rng.sample(list(columns), 2)
        if set(columns[one]) != set(columns[two]):
            return None
        out = dict(a)
        for tier in columns[one]:
            x, y = columns[one][tier], columns[two][tier]
            if p.boxes[x].length_ft != p.boxes[y].length_ft:
                return None
            out[x], out[y] = a[y], a[x]
        return out

    def relocate_top(self, a):
        """Move a top-of-column box to any free slot of its length that sits on something or on the floor."""
        p = self.p
        used = {c for slot in a.values() for c in cells_of_slot(slot)}
        tops = {}
        for box, slot in a.items():
            column = self._column(slot)
            if column not in tops or parse_slot(slot).tier > parse_slot(a[tops[column]]).tier:
                tops[column] = box
        box = self.rng.choice(list(tops.values()))
        free = [s for s in p.slots_by_length.get(p.boxes[box].length_ft, ()) if not (set(cells_of_slot(s)) & used)]
        if not free:
            return None
        out = dict(a)
        out[box] = self.rng.choice(free)
        return out

    OPERATORS = ('swap', 'swap_across_pods', 'swap_yard_mates', 'swap_columns', 'relocate_top')


def _mask_ok(problem, actions):
    engine, pushed = problem.engine, 0
    try:
        for action in actions:
            if not engine.is_legal(action):
                return False
            engine.push(action)
            pushed += 1
        return engine.is_complete()
    finally:
        for _ in range(pushed):
            engine.pop()


def search(instance, start_actions, *, time_limit_s, seed=0, start_temperature=1.2, reaction=0.15,
           problem=None, keep=5, gate=None):
    """Simulated annealing over assignments. Returns the best ``keep`` distinct mask-legal plans, cheapest first.

    ``gate(scored)`` is an optional costly check run only on a plan that would become the new best.
    """
    p = problem or Problem(instance)
    rng = random.Random(seed)
    moves, sequencer = Moves(p, rng), Sequencer(p)
    deadline = monotonic()+time_limit_s
    current = p.score(start_actions)
    if current is None:
        raise ValueError('start plan does not replay')
    assignment = {a.box: a.slot for a in current.actions}
    best = [current]
    seen = {tuple(sorted(assignment.items()))}
    weights = {name: 1.0 for name in Moves.OPERATORS}
    stats = Stats(operator_calls={n: 0 for n in weights}, operator_gains={n: 0. for n in weights})
    stats.best_trace.append((0.0, current.objective))
    started = monotonic()
    while monotonic() < deadline:
        progress = (monotonic()-started)/max(time_limit_s, 1e-9)
        temperature = max(0.02, start_temperature*(1-progress))
        name = rng.choices(list(weights), weights=list(weights.values()))[0]
        stats.iterations += 1
        stats.operator_calls[name] += 1
        proposal = getattr(moves, name)(assignment)
        reward = 0.0
        if (proposal is not None and tuple(sorted(proposal.items())) not in seen
                and not p.mixes_hold_stack(proposal)):
            ordered = sequencer.order(proposal)
            candidate = p.score(ordered) if ordered is not None else None
            if candidate is None:
                stats.infeasible += 1
            else:
                delta = candidate.objective-current.objective
                if (delta <= 0 or rng.random() < math.exp(-delta/temperature)):
                    if _mask_ok(p, candidate.actions):
                        current, assignment = candidate, proposal
                        seen.add(tuple(sorted(proposal.items())))
                        stats.accepted += 1
                        reward = 0.5
                        new_best = candidate.objective < best[0].objective-1e-9
                        if gate is None or (new_best and gate(candidate)):
                            if new_best:
                                stats.improved += 1
                                stats.best_trace.append((round(monotonic()-started, 2), candidate.objective))
                                reward = 2.0
                            best.append(candidate)
                            best.sort(key=lambda s: s.objective)
                            del best[keep:]
                    else:
                        stats.infeasible += 1
        stats.operator_gains[name] += reward
        weights[name] = (1-reaction)*weights[name]+reaction*max(reward, 0.05)
    return best, stats

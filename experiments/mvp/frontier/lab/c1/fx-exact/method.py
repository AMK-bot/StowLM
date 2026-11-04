"""Exact, time-bounded repair inside an explicitly restricted neighborhood.

One hatch/yard group's boxes may change identity at the group's original load
positions. Every load position retains its original slot, and every other box
retains both its slot and load position. Exhausting the tree proves optimality
only within the original same-length_ft classes in that neighborhood, with the default expander's yard relocation rule and
feasibility mask; it does not prove a globally optimal stow or load order.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import math
from pathlib import Path
from time import monotonic

from alns import Problem, Sequencer
from sequence import order_legal
from stowage.actions.space import Place
from stowage.conventions import cells_of_slot
from stowage.model import MovePlan


# Captured at module import, rather than read after another worker edits source.
_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_MAX_EXACT_INTEGER = 2 ** 53


class _Stopped(Exception):
    pass


def _current_cost(moves, objective, *, integral=False):
    """Already emitted lifts; future projection and closing covers add >= 0."""
    convert = int if integral else float
    return sum(convert(objective.relocate) if m.kind == 'relocate' else
               convert(objective.restow) if m.kind in ('restow_off', 'restow_on', 'restow_shift') else
               convert(objective.hatch_cover) if m.kind in ('hatch_cover_off', 'hatch_cover_on') else 0
               for m in moves)


def exact_neighborhood(problem, actions, free_boxes, *, deadline, keep=4):
    """Return (best scored actions, diagnostics), preserving the incumbent.

    Proof covers all mask-legal complete permutations within each original
    length_ft class at the chosen positions (40 and 45 feet remain separate).
    Any interruption sets exhaustive=False. A single engine/score call is atomic,
    so a deadline can overrun by at most that call's wall time.
    """
    started = monotonic()
    p, actions = problem, tuple(actions)
    free = set(free_boxes) & {a.box for a in actions}
    positions = [i for i, a in enumerate(actions) if a.box in free]
    stats = dict(free_boxes=sorted(free), positions=positions, nodes=0, leaves=0,
                 pruned=0, illegal=0, exhaustive=False, elapsed_s=0., lower_bound=None,
                 neighborhood='fixed slot-access schedule; permute selected boxes within original length_ft classes at their original positions',
                 proof_scope='mask-legal Place-only permutations within original same-length_ft classes plus supplied replay-legal incumbent; fixed occupied slots and slot-access schedule; default yard relocation')
    if any(not isinstance(a, Place) for a in actions) or len({a.box for a in actions}) != len(actions):
        stats['unsupported'] = True
        return [], stats
    if monotonic() >= deadline:
        return [], stats
    original = p.score(actions)
    if original is None:
        stats['unsupported'] = True
        return [], stats
    best = [original]
    seen = {tuple((a.box, a.slot) for a in original.actions)}
    engine = p.engine
    # Count*weight in apply_plan can round differently from repeated float sums.
    # Prefix certificates therefore use exact Python integers only, and only
    # inside binary64's exact integer range. Fractional nonnegative weights use
    # zero as the bound and retain full enumeration without prefix pruning.
    weights = p.objective
    values = [getattr(weights, name) for name in
              ('relocate', 'restow', 'hatch_cover', 'future_restow', 'future_hatch_cover')]
    nonnegative = all(math.isfinite(value) and value >= 0 for value in values)
    can_bound = nonnegative and all(float(value).is_integer() and value <= _MAX_EXACT_INTEGER
                                    for value in values)
    ex = p.space.expander(p.instance)
    prefix = []
    # Pending arcs form a cut through the decision tree. Their prefix costs
    # lower-bound every as-yet-unvisited completion, including on timeout.
    frontier = Counter({0.: 1})

    def prefix_bound(cost):
        return cost if can_bound and cost <= _MAX_EXACT_INTEGER else 0

    def remove_bound(bound):
        frontier[bound] -= 1
        if not frontier[bound]:
            del frontier[bound]

    def visit(index, remaining, state, cost):
        if monotonic() >= deadline:
            raise _Stopped
        remove_bound(prefix_bound(cost))
        stats['nodes'] += 1
        if can_bound and cost <= _MAX_EXACT_INTEGER and cost >= best[0].objective:
            # Equal leaves cannot improve; incumbent is already retained.
            stats['pruned'] += 1
            return
        if index == len(actions):
            stats['leaves'] += 1
            if not engine.is_complete():
                stats['illegal'] += 1
                return
            identity = tuple((a.box, a.slot) for a in prefix)
            if identity in seen:
                return
            seen.add(identity)
            scored = p.score(tuple(prefix))
            if scored is not None and scored.objective <= best[0].objective:
                best.append(scored)
                best.sort(key=lambda item: item.objective)
                del best[max(1, keep):]
            return
        old = actions[index]
        choices = sorted(remaining, key=lambda box: (box != old.box, p.yard_depth.get(box, 0), box)) \
            if old.box in free else [old.box]
        choices = [box for box in choices if p.boxes[box].length_ft == p.boxes[old.box].length_ft]
        parent_bound = prefix_bound(cost)
        if choices:
            frontier[parent_bound] += len(choices)
        for box in choices:
            if monotonic() >= deadline:
                raise _Stopped
            action = Place(box, old.slot)
            if not engine.is_legal(action):
                remove_bound(parent_bound)
                stats['illegal'] += 1
                continue
            trial = state.copy()
            try:
                emitted = trial.step(action)
            except (ValueError, KeyError, TypeError):
                remove_bound(parent_bound)
                stats['illegal'] += 1
                continue
            child_cost = cost + _current_cost(emitted, weights, integral=can_bound)
            remove_bound(parent_bound)
            frontier[prefix_bound(child_cost)] += 1
            engine.push(action)
            prefix.append(action)
            try:
                visit(index + 1, remaining - {box} if old.box in free else remaining,
                      trial, child_cost)
            finally:
                prefix.pop()
                engine.pop()
    try:
        visit(0, free, ex, 0)
        stats['exhaustive'] = True
    except _Stopped:
        pass
    finally:
        stats['elapsed_s'] = monotonic() - started
        stats['best_objective'] = best[0].objective
        stats['incumbent_objective'] = original.objective
        stats['optimal_in_neighborhood'] = stats['exhaustive']
        stats['prefix_bound_arithmetic'] = 'exact integers through 2**53' if can_bound else 'disabled'
        stats['lower_bound'] = best[0].objective if stats['exhaustive'] else (min(
            best[0].objective, min(frontier, default=0.)) if nonnegative else None)
    return best, stats


solve_neighborhood = exact_neighborhood


def _groups(problem, actions):
    groups = defaultdict(set)
    for action in actions:
        yard = problem.yard_stack.get(action.box)
        if yard is not None:
            groups['yard:' + str(yard)].add(action.box)
        hatches = {problem.stacks[problem.cells[cell].stack_id].hatch_cover_id
                   for cell in cells_of_slot(action.slot)} - {None}
        for hatch in hatches:
            groups['hatch:' + str(hatch)].add(action.box)
    # First prove small neighborhoods, then spend the remainder on larger ones.
    return sorted(((key, boxes) for key, boxes in groups.items() if len(boxes) > 1),
                  key=lambda item: (len(item[1]), item[0]))


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    """Lab plug-in: bounded exact hatch/yard assignment repairs, best first."""
    del seed  # deterministic search, preserving incumbent first at each branch
    deadline = monotonic() + max(0., budget_s)
    p = context.get('problem') or Problem(instance)
    stored = context.get('stored')
    stats = context.setdefault('exact_repair_stats', [])
    metadata = context.setdefault('method_metadata', {})
    metadata['exact_repair_stats'] = stats
    metadata['actual_method_sha256'] = _SOURCE_SHA256
    stored_plan = context.get('stored_plan') or context.get('baseline', {}).get('plan')
    if stored_plan is not None:
        stored_plan = MovePlan.model_validate(stored_plan) if isinstance(stored_plan, dict) else stored_plan
        stored = tuple(p.space.from_plan(instance, stored_plan))
    if not stored:
        stats.append(dict(unsupported=True, reason='no stored incumbent'))
        return []
    if isinstance(stored, dict):
        actions, stuck = order_legal(p, stored)
        if stuck:
            actions = Sequencer(p).order(stored)
    else:
        actions = tuple(stored)
    if not actions:
        stats.append(dict(unsupported=True, reason='stored assignment cannot be sequenced'))
        return [stored]
    if monotonic() >= deadline:
        return [tuple(actions)]
    incumbent = p.score(actions)
    if incumbent is None:
        return [stored]
    if any(not isinstance(action, Place) for action in actions):
        stats.append(dict(unsupported=True, reason='baseline contains non-Place actions'))
        return [tuple(actions)]
    global_bound = context.get('exact_lower_bound')
    if isinstance(global_bound, (int, float)) and incumbent.objective <= global_bound:
        stats.append(dict(global_lower_bound=global_bound, best_objective=incumbent.objective,
                          globally_optimal=True, reason='incumbent meets supplied certified global bound'))
        return [incumbent.actions]
    candidates = [incumbent]
    groups = _groups(p, actions)
    for index, (name, free) in enumerate(groups):
        now = monotonic()
        if now >= deadline:
            break
        # Fair budgets keep a large hatch from starving later yard groups.
        group_deadline = min(deadline, now + (deadline - now) / (len(groups) - index))
        repaired, info = exact_neighborhood(p, candidates[0].actions, free,
                                            deadline=group_deadline, keep=4)
        info['group'] = name
        stats.append(info)
        candidates.extend(repaired)
        candidates.sort(key=lambda item: item.objective)
        unique = {}
        for candidate in candidates:
            unique.setdefault(tuple((a.box, a.slot) for a in candidate.actions), candidate)
        candidates = list(unique.values())[:4]
    output = [candidate.actions for candidate in candidates]
    # An equal/lower replay score does not imply the same ballast/physics pass.
    # Keep the actual stored incumbent even when four strict improvements exist.
    if incumbent.actions not in output:
        output.append(incumbent.actions)
    return output

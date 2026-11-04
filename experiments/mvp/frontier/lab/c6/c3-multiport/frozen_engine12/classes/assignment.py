# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-fvs/assignment.py.
"""Yard-aware assignment proposals inside interchangeable cargo classes.

For a fixed topological ship order, a yard pair is inverted when the lower
box is requested before the upper box. A Hungarian step assigns one class
to its occupied slots, pricing every inversion against boxes outside that
class. This is an exact linear assignment subproblem; the full objective
also has within-class pairs and is NOT a linear assignment problem. We
therefore retain only improving steps and finish with exact pair exchanges.

No candidate is a certificate of physical feasibility. ``exact=True`` keeps
all Container facts other than its identity and untrusted category tag.
The broader pass allows different masses within a weight class, which can
alter weight order, stability and strength: its proposals MUST go through
the normal full checker after ordering and finishing.
"""
from __future__ import annotations

from collections import defaultdict
import json
import random
from time import monotonic

from scipy.optimize import linear_sum_assignment

from frozen_frontier.alns import Sequencer
from stowage.actions.space import Place
from stowage.solver.gold_warmstart_search import precedence_sets


def interchangeable_signature(box, *, exact=True):
    """Conservative physical/booking signature, never trusting ``category``.

    Keeping every schema field automatically includes reefer, DG entries,
    OOG, height, direct delivery and any future schema additions. Broader
    groups remove mass facts only, retaining the generator's weight class.
    Missing weight classes do not permit a broad mass exchange.
    """
    facts = box.model_dump(mode='json')
    facts.pop('id', None)
    facts.pop('category', None)
    if not exact and facts.get('weight_class') is not None:
        for key in ('gross_mass_kg', 'tare_kg', 'max_gross_mass_kg'):
            facts.pop(key, None)
    return json.dumps(facts, sort_keys=True, separators=(',', ':'))


def _groups(problem, assignment, *, exact):
    groups = defaultdict(list)
    for box in sorted(assignment):
        groups[interchangeable_signature(problem.boxes[box], exact=exact)].append(box)
    return [group for group in groups.values() if len(group) > 1]


def _yard_pairs(problem, assignment):
    """(upper, lower) pairs; yard model stores bottom first."""
    pairs = []
    for stack in problem.instance_yard.values():
        boxes = [box for box in stack if box in assignment]
        pairs.extend((upper, lower) for i, lower in enumerate(boxes)
                     for upper in boxes[i+1:])
    return pairs


def _inversions(assignment, rank, pairs):
    return sum(rank[assignment[upper]] > rank[assignment[lower]]
               for upper, lower in pairs)


def _ship_rank(problem, assignment, rng, policy):
    """Sample a legal order of occupied ship positions, not of yard moves."""
    actions = [Place(box, slot) for box, slot in sorted(assignment.items())]
    predecessors = precedence_sets(problem.instance, actions)
    pending, done, rank = set(range(len(actions))), set(), {}
    while pending:
        ready = [i for i in pending if predecessors[i] <= done]
        if not ready:
            return None
        if policy == 0:
            key = lambda i: (problem.yard_depth.get(actions[i].box, 0), i)
        elif policy == 1:
            key = lambda i: (-problem.yard_depth.get(actions[i].box, 0), i)
        elif policy == 2:
            key = lambda i: (actions[i].slot, i)
        else:
            ties = {i: rng.random() for i in ready}
            key = lambda i: (ties[i], i)
        selected = min(ready, key=key)
        rank[actions[selected].slot] = len(rank)
        pending.remove(selected)
        done.add(selected)
    return rank


def _improve(assignment, groups, rank, pairs, rng, deadline):
    """Coordinate descent; exact costs, approximate global optimization."""
    current = dict(assignment)
    value = _inversions(current, rank, pairs)
    for _ in range(3):
        changed = False
        shuffled = list(groups)
        rng.shuffle(shuffled)
        for group in shuffled:
            if monotonic() >= deadline:
                return current
            members = set(group)
            slots = sorted((current[box] for box in group), key=rank.get)
            external = [(upper, lower) for upper, lower in pairs
                        if (upper in members) != (lower in members)]
            internal = [(upper, lower) for upper, lower in pairs
                        if upper in members and lower in members]
            # Hungarian solves the exact cost against other classes. A small
            # desired-rank term breaks ties toward exposing this class top first.
            degree = {box: sum(upper == box for upper, _ in internal)
                      - sum(lower == box for _, lower in internal) for box in group}
            desired = {box: i for i, box in enumerate(sorted(group,
                       key=lambda box: (-degree[box], rank[current[box]], box)))}
            costs = []
            scale = max(1, len(group)**3)
            for box in group:
                row = []
                for j, slot in enumerate(slots):
                    r = rank[slot]
                    cost = sum((r > rank[current[lower]]) if upper == box
                               else (rank[current[upper]] > r)
                               for upper, lower in external if box in (upper, lower))
                    row.append(scale*cost + abs(j-desired[box]))
                costs.append(row)
            rows, columns = linear_sum_assignment(costs)
            proposal = dict(current)
            for i, j in zip(rows, columns):
                proposal[group[i]] = slots[j]
            proposed_value = _inversions(proposal, rank, pairs)
            if proposed_value < value:
                current, value, changed = proposal, proposed_value, True
            # Unlike the matching step, this includes both internal and external
            # pairs exactly; exchanges can repair Hungarian's internal surrogate.
            for i, first in enumerate(group):
                for second in group[i+1:]:
                    if monotonic() >= deadline:
                        return current
                    proposal = dict(current)
                    proposal[first], proposal[second] = current[second], current[first]
                    proposed_value = _inversions(proposal, rank, pairs)
                    if proposed_value < value:
                        current, value, changed = proposal, proposed_value, True
        if not changed:
            break
    return current


def assignment_candidates(problem, stored, *, seed=0, limit=12, budget_s=2.0,
                          allow_mass_exchange=False):
    """Return distinct replay-legal complete stows, scored best first.

    ``stored`` is a box-to-slot dict. The original assignment is included as
    a fallback if its greedy ship order replays. This routine does no ballast
    finishing and makes no full-check claim. Caller should optimize each
    returned stow's order, then finish and fully check strict improvements.
    """
    if not stored or limit <= 0:
        return []
    baseline = dict(stored)
    if set(baseline) != set(problem.instance.load_list):
        return []
    # Exchanging an incoming box with inherited cargo is outside this model.
    if problem.instance.onboard.placements or problem.instance.discharge_list:
        return []
    deadline = monotonic()+max(0., budget_s)
    rng = random.Random(seed)
    pairs = _yard_pairs(problem, baseline)
    exact_groups = _groups(problem, baseline, exact=True)
    broad_groups = _groups(problem, baseline, exact=False) if allow_mass_exchange else []
    group_sets = [exact_groups]
    if broad_groups != exact_groups:
        group_sets.append(broad_groups)
    candidates, seen = [], set()

    def retain(assignment):
        identity = tuple(sorted(assignment.items()))
        if identity in seen:
            return
        seen.add(identity)
        ordered = Sequencer(problem).order(assignment)
        scored = problem.score(ordered) if ordered is not None else None
        if scored is not None:
            candidates.append((scored.objective, identity, dict(assignment)))

    retain(baseline)
    for policy in range(max(4, limit)):
        for groups in group_sets:
            if monotonic() >= deadline:
                break
            rank = _ship_rank(problem, baseline, rng, policy)
            if rank is None:
                continue
            retain(_improve(baseline, groups, rank, pairs, rng, deadline))
        if monotonic() >= deadline or len(candidates) >= limit:
            break
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [assignment for _, _, assignment in candidates[:limit]]


def joint_candidates(problem, stored, *, time_limit_s=2.0, seed=0, exact=True):
    """Jointly assign interchangeable boxes and break yard/ship cycles.

    The CP-SAT objective is the number of upper yard boxes whose original
    yard precedence is released. For every unreleased upper box, its ship
    rank must precede every lower incoming box in the same yard stack.
    Occupied-slot ranks obey ship support/hatch precedences independently
    of the chosen cargo identities. This is a cycle relaxation: released
    boxes are assumed never to block again, and external yard blockers are
    omitted. Even an OPTIMAL result proves this model's optimum, not the
    real relocation optimum. Replay and full checking remain mandatory.

    ``exact=True`` means exact Container-attribute exchange signatures, not
    an assertion that the relaxation matches replay. A feasible solution
    returns one complete assignment; no solver incumbent returns no stow.
    """
    from ortools.sat.python import cp_model

    started = monotonic()
    metadata = dict(model='joint assignment and directed-cycle relaxation',
                    exact_signature=bool(exact), status='UNSUPPORTED',
                    elapsed_s=0., workers=1, relocation_lower_bound=None,
                    relaxed_relocations=None, released_box_ids=[],
                    caveat='Released boxes may block again in replay; external yard blockers omitted.')
    if (not stored or set(stored) != set(problem.instance.load_list)
            or problem.instance.onboard.placements or problem.instance.discharge_list):
        metadata['elapsed_s'] = monotonic()-started
        return [], metadata
    if time_limit_s <= 0:
        metadata.update(status='NO_TIME', elapsed_s=monotonic()-started)
        return [], metadata

    assignment = dict(stored)
    boxes = sorted(assignment)
    slots = [assignment[box] for box in boxes]
    if len(set(slots)) != len(slots):
        metadata.update(status='DUPLICATE_SLOT', elapsed_s=monotonic()-started)
        return [], metadata
    n = len(boxes)
    index = {box: i for i, box in enumerate(boxes)}
    predecessors = precedence_sets(problem.instance,
                                   [Place(box, assignment[box]) for box in boxes])
    groups = defaultdict(list)
    for box in boxes:
        groups[interchangeable_signature(problem.boxes[box], exact=exact)].append(box)

    model = cp_model.CpModel()
    slot_ranks = [model.NewIntVar(0, n-1, f'slot_rank_{i}') for i in range(n)]
    model.AddAllDifferent(slot_ranks)
    for upper, below in enumerate(predecessors):
        for lower in below:
            model.Add(slot_ranks[lower] < slot_ranks[upper])

    box_slots, box_ranks = {}, {}
    for group in groups.values():
        allowed = [index[box] for box in group]
        for box in group:
            i = index[box]
            slot = model.NewIntVarFromDomain(cp_model.Domain.FromValues(allowed),
                                            f'box_slot_{i}')
            box_slots[box] = slot
            if len(group) == 1:
                box_ranks[box] = slot_ranks[i]
            else:
                rank = model.NewIntVar(0, n-1, f'box_rank_{i}')
                model.AddElement(slot, slot_ranks, rank)
                box_ranks[box] = rank
            model.AddHint(slot, i)
        model.AddAllDifferent([box_slots[box] for box in group])

    pairs = _yard_pairs(problem, assignment)
    released = {upper: model.NewBoolVar(f'released_{index[upper]}')
                for upper in sorted({upper for upper, _ in pairs})}
    for upper, lower in pairs:
        model.Add(box_ranks[upper] < box_ranks[lower]).OnlyEnforceIf(released[upper].Not())
    model.Minimize(sum(released.values()))

    # A legal ship-rank permutation and the identity assignment provide a
    # readily available incumbent, even when every yard precedence conflicts.
    rank_hint = _ship_rank(problem, assignment, random.Random(seed), 0)
    if rank_hint is not None:
        for i, slot in enumerate(slots):
            model.AddHint(slot_ranks[i], rank_hint[slot])
        for upper, variable in released.items():
            conflicts = any(rank_hint[assignment[upper]] > rank_hint[assignment[lower]]
                            for candidate, lower in pairs if candidate == upper)
            model.AddHint(variable, int(conflicts))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = int(seed) % 2147483647
    solver.parameters.max_time_in_seconds = max(0.001, time_limit_s-(monotonic()-started))
    status = solver.Solve(model)
    feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    metadata.update(status=solver.StatusName(status), elapsed_s=monotonic()-started,
                    boxes=n, exchange_classes=sum(len(group) > 1 for group in groups.values()),
                    yard_pairs=len(pairs), branches=solver.NumBranches(),
                    conflicts=solver.NumConflicts())
    if feasible:
        # This bound is for the explicitly documented relaxation and allowed
        # assignment classes; it is not a bound over unrestricted ship stows.
        metadata.update(relocation_lower_bound=float(solver.BestObjectiveBound()),
                        relaxed_relocations=int(round(solver.ObjectiveValue())),
                        released_box_ids=[box for box, var in released.items()
                                          if solver.BooleanValue(var)],
                        slot_rank={slot: int(solver.Value(slot_ranks[i]))
                                   for i, slot in enumerate(slots)})
        candidate = {box: slots[solver.Value(box_slots[box])] for box in boxes}
        return [candidate], metadata
    return [], metadata

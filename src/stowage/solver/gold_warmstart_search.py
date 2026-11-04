"""Bounded fixed-stow yard LNS; proposals require independent admission.

Large neighbourhood search releases related decisions and rebuilds them
(Shaw 1998, doi:10.1007/3-540-49481-2_30). Here a yard/hatch-related suffix
loses its retrieval order, while every box keeps its final ship slot. A ready
set contains loads whose ship supports and hatch predecessors are finished.
Choosing accessible ready boxes can avoid relocating boxes needed later;
this is the restricted blocks-relocation intuition (Caserta et al., EJOR
219(1), 2011). Actual complete move replay, including future moves, ranks
proposals. This module neither changes rules nor certifies a proposal.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import random
from time import monotonic

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.conventions import cells_of_slot
from stowage.moves import apply_plan


class SearchStopped(Exception):
    pass


@dataclass
class Proposal:
    plan: object
    objective: float
    neighborhood: dict


def precedence_sets(instance, actions):
    """Support edges, then all hold loads before deck loads on that hatch.

    Long boxes participate in both physical 20-foot columns. This is a
    necessary ordering restriction for the fixed assignment, never a mask.
    """
    owner = {cell: i for i, action in enumerate(actions) for cell in cells_of_slot(action.slot)}
    predecessors = [set() for _ in actions]
    holds, decks = defaultdict(set), defaultdict(set)
    for stack in instance.vessel.stacks:
        sequence = list(dict.fromkeys(owner[cell] for cell in stack.cell_ids if cell in owner))
        for lower, upper in zip(sequence, sequence[1:]):
            predecessors[upper].add(lower)
        for index in sequence:
            if stack.hatch_cover_id is not None:
                (decks if stack.on_deck else holds)[stack.hatch_cover_id].add(index)
    for hatch in holds.keys() & decks.keys():
        for upper in decks[hatch]:
            predecessors[upper].update(holds[hatch]-{upper})
    return predecessors


def _yard_key(ex, action):
    location = ex.location(action.box)
    if location is None or location[0] != 'yard':
        return 0, 0, ''
    stack = ex.yard[location[1]]
    position = stack.index(action.box)
    # Loading this box also uncovers pending lower retrievals in its stack.
    unlocked = sum(cid in ex.load_set for cid in stack[:position])
    return len(stack)-position-1, -unlocked, location[1]


def _repair(instance, actions, predecessors, cut, policy, rng, deadline):
    ex = Expander(instance)
    order = list(range(cut))
    for index in order:
        if monotonic() >= deadline:
            raise SearchStopped
        ex.step(actions[index])
    pending = set(range(cut, len(actions)))
    done = set(order)
    while pending:
        if monotonic() >= deadline:
            raise SearchStopped
        ready = [i for i in pending if predecessors[i] <= done]
        if not ready:
            raise ValueError('fixed-stow precedence cycle')
        ready.sort(key=lambda i: (*_yard_key(ex, actions[i]), i))
        # APPROXIMATION: bounded greedy repair explores three exact immediate
        # expansions, not the full retrieval permutation tree. Related restarts
        # diversify choices; neither score supplies a feasibility certificate.
        choices = ready[:3]
        if policy >= 2 and len(ready) > 3:
            choices.append(rng.choice(ready[3:]))
        ranked = []
        for index in choices:
            if monotonic() >= deadline:
                raise SearchStopped
            trial = ex.copy()
            try:
                moves = trial.step(actions[index])
            except (ValueError, KeyError, TypeError):
                continue
            if any(move.kind.startswith('restow') for move in moves):
                continue
            cost = sum(instance.objective.relocate for move in moves if move.kind == 'relocate')
            cost += sum(instance.objective.hatch_cover for move in moves
                        if move.kind in ('hatch_cover_off', 'hatch_cover_on'))
            blockers, unlock, stack = _yard_key(ex, actions[index])
            if policy == 0:
                score = (cost, blockers, unlock, index)
            elif policy == 1:
                score = (cost, unlock, blockers, index)
            else:
                # A bounded random tie/near-tie exploration is search only.
                score = (cost+rng.uniform(0., 1.25)*instance.objective.relocate,
                         blockers, unlock, rng.random())
            ranked.append((score, index, trial))
        if not ranked:
            raise ValueError('no expandable fixed-stow ready action')
        _, selected, ex = min(ranked, key=lambda item:item[0])
        order.append(selected)
        pending.remove(selected)
        done.add(selected)
    return tuple(actions[i] for i in order)


def search(instance, baseline, *, deadline, seed=0, max_neighborhoods=64,
           keep=3, baseline_replay=None):
    """Return raw strict improvements plus measured construction diagnostics.

    Stage A with unique Place actions is deliberately the supported scope.
    Other plans return no proposals, preserving the caller's baseline.
    """
    started = monotonic()
    metrics = dict(neighborhoods=0, complete_candidates=0, strict_candidates=0,
                   duplicate_orders=0, repair_failures=0, unsupported=False,
                   objective_definition='apply_plan.count.weighted_nonproductive',
                   neighborhood='related dependency-closed suffix retrieval order; fixed box-to-slot assignment')
    proposals = []
    try:
        space = get_action_space('boxslot')
        actions = tuple(space.from_plan(instance, baseline))
        if (instance.onboard.placements or instance.discharge_list
                or not actions or any(not isinstance(action, Place) for action in actions)
                or len({action.box for action in actions}) != len(actions)
                or {action.box for action in actions} != set(instance.load_list)):
            metrics['unsupported'] = True
            return [], metrics
        reference = baseline_replay or apply_plan(instance, baseline)
        if not reference.legal:
            raise ValueError('baseline move replay rejected')
        original = {p.container_id:p.slot for p in reference.final_state.ship.placements}
        value = reference.count.weighted_nonproductive
        predecessors = precedence_sets(instance, actions)
        groups = defaultdict(list)
        yard = {cid:stack.key for stack in instance.yard.stacks for cid in stack.boxes}
        for index, action in enumerate(actions):
            groups[yard.get(action.box, action.box)].append(index)
        related_cuts = sorted({indices[0] for indices in groups.values()})
        cuts = [0] + [cut for cut in related_cuts if cut]
        rng, seen = random.Random(seed), {tuple((a.box,a.slot) for a in actions)}
        for turn in range(max_neighborhoods):
            if monotonic() >= deadline:
                break
            # APPROXIMATION: alternate whole-order repairs and yard-related
            # suffixes, bounded by max_neighborhoods and the caller deadline.
            cut = 0 if turn < 3 or turn % 3 == 0 else cuts[(turn//3) % len(cuts)]
            policy = turn % 4
            metrics['neighborhoods'] += 1
            try:
                ordered = _repair(instance, actions, predecessors, cut, policy, rng, deadline)
                identity = tuple((a.box,a.slot) for a in ordered)
                if identity in seen:
                    metrics['duplicate_orders'] += 1
                    continue
                seen.add(identity)
                candidate = space.expand(instance, ordered).model_copy(update={
                    'ballast':baseline.ballast,
                    'provenance':baseline.provenance})
                if monotonic() >= deadline:
                    break
                replay = apply_plan(instance, candidate)
                metrics['complete_candidates'] += 1
                if (not replay.legal or replay.count.restow_lifts != reference.count.restow_lifts
                        or {p.container_id:p.slot for p in replay.final_state.ship.placements} != original):
                    metrics['repair_failures'] += 1
                    continue
                objective = replay.count.weighted_nonproductive
                if objective < value-1e-9:
                    metrics['strict_candidates'] += 1
                    proposals.append(Proposal(candidate, objective,
                                              dict(turn=turn, cut=cut, policy=policy)))
                    proposals.sort(key=lambda proposal:proposal.objective)
                    proposals = proposals[:keep]
            except SearchStopped:
                break
            except (ValueError, KeyError, TypeError) as error:
                metrics['repair_failures'] += 1
                metrics['last_repair_error'] = f'{type(error).__name__}: {error}'
        return proposals, metrics
    finally:
        metrics['elapsed_s'] = monotonic()-started

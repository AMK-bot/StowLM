# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-fvs/method.py.
"""Feedback-yard lower bounds, real replay orders, and class assignment proposals."""
from __future__ import annotations

from dataclasses import asdict
import random
import hashlib
import json
from time import monotonic

from alns import Sequencer
from .assignment import assignment_candidates, joint_candidates
from .exact import solve_relaxation, witness_actions, search_replay
from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.model import MovePlan
from stowage.solver.gold_warmstart_search import precedence_sets


def _stored_actions(instance, context):
    plan = context.get('stored_plan')
    if plan is None:
        return None
    if isinstance(plan, dict):
        plan = MovePlan.model_validate(plan)
    actions = tuple(get_action_space('boxslot').from_plan(instance, plan))
    return actions if all(isinstance(a, Place) for a in actions) else None


def _repair(problem, assignment, rng, policy, bound, deadline):
    """Sample a ship-topological order, evaluating actual yard states at each step."""
    actions = [Place(box, slot) for box, slot in sorted(assignment.items())]
    pred = precedence_sets(problem.instance, actions)
    if policy % 3 == 0 and bound.order:
        index = {a.box: i for i, a in enumerate(actions)}
        for upper, lower in bound.yard_edges:
            if upper not in bound.relocated:
                pred[index[lower]].add(index[upper])
    ex = Expander(problem.instance)
    pending, done, out = set(range(len(actions))), set(), []
    while pending:
        if monotonic() >= deadline:
            return None
        ready = [i for i in pending if pred[i] <= done]
        ranked = []
        for i in ready:
            trial = ex.copy()
            try:
                moves = trial.step(actions[i])
            except (ValueError, KeyError, TypeError):
                continue
            if any(m.kind.startswith('restow') for m in moves):
                continue
            reloc = sum(m.kind == 'relocate' for m in moves)
            loc = ex.location(actions[i].box)
            unlock = 0
            if loc and loc[0] == 'yard':
                stack = ex.yard[loc[1]]
                unlock = sum(box in ex.load_set for box in stack[:stack.index(actions[i].box)])
            noise = 0. if policy < 2 else rng.uniform(0., 1.75)
            key = (reloc + noise, -unlock, rng.random() if policy else i)
            ranked.append((key, i, trial))
        if not ranked:
            return None
        _, i, ex = min(ranked, key=lambda item: item[0])
        out.append(actions[i])
        pending.remove(i)
        done.add(i)
    return tuple(out)


def optimize_order(problem, assignment, *, seed, budget_s, initial=None):
    started = monotonic()
    deadline = started + max(.01, budget_s)
    bound = solve_relaxation(problem, assignment, time_limit_s=min(2., max(.01, budget_s*.2)), seed=seed)
    best, best_count = None, None
    seen, complete = set(), 0

    def retain(actions):
        nonlocal best, best_count, complete
        if actions is None:
            return
        key = tuple(a.box for a in actions)
        if key in seen:
            return
        seen.add(key)
        scored = problem.score(actions)
        if scored is None:
            return
        from stowage.moves import apply_plan
        count = apply_plan(problem.instance, scored.plan).count
        complete += 1
        if best is None or scored.objective < best.objective:
            best, best_count = scored, count

    if initial and all(isinstance(a, Place) for a in initial) and {a.box:a.slot for a in initial} == assignment:
        retain(initial)
    retain(Sequencer(problem).order(assignment))
    if bound.order:
        retain(witness_actions(assignment, bound))
    rng, rounds = random.Random(seed), 0
    while monotonic() < deadline and rounds < 6:
        if best_count is not None and bound.certifies(best_count.relocations, restow_lifts=best_count.restow_lifts):
            break
        retain(_repair(problem, assignment, rng, rounds, bound, deadline))
        rounds += 1
    replay_search = None
    if (monotonic() < deadline and (best_count is None or not bound.certifies(
            best_count.relocations, restow_lifts=best_count.restow_lifts))):
        replay_search = search_replay(problem, assignment,
                                     incumbent_actions=best.actions if best else None,
                                     time_limit_s=deadline-monotonic(), lower_bound=bound.lower_bound)
        retain(replay_search.actions or None)
    metadata = asdict(bound)
    if replay_search is not None:
        metadata['replay_search'] = {key:value for key,value in asdict(replay_search).items() if key != 'actions'}
    metadata['relocated'] = sorted(bound.relocated)
    metadata['mandatory_nonloads'] = sorted(bound.mandatory_nonloads)
    metadata.update(rounds=rounds, complete_orders=complete,
                    best_relocations=best_count.relocations if best_count else None,
                    certified=bool(best_count and bound.certifies(best_count.relocations, restow_lifts=best_count.restow_lifts)),
                    scope='fixed stow, each box loaded once, no ship restows; equality certifies only when certified=True',
                    assignment_sha256=hashlib.sha256(json.dumps(assignment,sort_keys=True).encode()).hexdigest(),
                    elapsed_s=monotonic()-started)
    return (best.actions if best else None), metadata


def order(instance, assignment, *, seed, budget_s, context):
    actions, metadata = optimize_order(context['problem'], assignment, seed=seed,
                                      budget_s=budget_s, initial=_stored_actions(instance, context))
    context['method_metadata'].setdefault('order_bounds', []).append(metadata)
    return actions


def propose_order(instance, *, seed, budget_s, context):
    """Ablation: change only the order of the stored stow."""
    if context.get('stored') is None:
        context['method_metadata']['unsupported_missing_stored_stow'] = True
        return []
    initial = _stored_actions(instance, context)
    actions = order(instance, context['stored'], seed=seed, budget_s=budget_s, context=context)
    return [a for a in (actions, initial) if a]


def _propose(instance, *, seed, budget_s, context, mass_exchange):
    started = monotonic()
    deadline = started + budget_s
    stored = context.get('stored')
    if stored is None:
        context['method_metadata']['unsupported_missing_stored_stow'] = True
        return []
    p = context['problem']
    initial = _stored_actions(instance, context)
    candidates = assignment_candidates(p, stored, seed=seed, limit=10,
                                      budget_s=min(2., budget_s*.15),
                                      allow_mass_exchange=mass_exchange)
    joint, joint_meta = joint_candidates(p, stored, time_limit_s=min(3., budget_s*.2),
                                          seed=seed, exact=not mass_exchange)
    context['method_metadata']['joint'] = joint_meta
    combined = [stored] + joint + candidates
    candidates, identities = [], set()
    for candidate in combined:
        identity = tuple(sorted(candidate.items()))
        if identity not in identities:
            candidates.append(candidate)
            identities.add(identity)
    context['method_metadata']['mass_exchange'] = mass_exchange
    context['method_metadata']['assignments_tried'] = len(candidates)
    ranked = []
    bounds = []
    for i, assignment in enumerate(candidates):
        remaining = deadline-monotonic()
        if remaining <= 0:
            break
        allocation = max(.05, remaining / max(1, len(candidates)-i))
        candidate_initial = initial if i == 0 else None
        if assignment in joint and joint_meta.get('slot_rank'):
            candidate_initial = tuple(Place(box, slot) for box,slot in sorted(
                assignment.items(), key=lambda item: joint_meta['slot_rank'][item[1]]))
        actions, metadata = optimize_order(p, assignment, seed=seed+i, budget_s=allocation,
                                           initial=candidate_initial)
        metadata['stored_assignment'] = assignment == stored
        metadata['from_joint'] = assignment in joint
        metadata['from_matching'] = any(assignment == candidate for candidate in combined[1+len(joint):])
        bounds.append(metadata)
        if actions:
            scored = p.score(actions)
            if scored:
                ranked.append(scored)
    context['method_metadata']['order_bounds'] = bounds
    # Leave a guaranteed admission attempt for the stored plan in --tries 3.
    ranked.sort(key=lambda c: c.objective)
    strict = [c.actions for c in ranked if c.objective < context['stored_lifts']]
    out, seen = [], set()
    for actions in strict[:2] + ([initial] if initial else []):
        key = tuple((a.box,a.slot) for a in actions)
        if key not in seen:
            out.append(actions)
            seen.add(key)
    context['method_metadata']['elapsed_s'] = monotonic()-started
    return out


def propose(instance, *, seed, budget_s, context):
    """Engine alias: conservative identical-attribute exchange only."""
    return _propose(instance, seed=seed, budget_s=budget_s, context=context, mass_exchange=False)


def propose_exact_classes(instance, *, seed, budget_s, context):
    return _propose(instance, seed=seed, budget_s=budget_s, context=context, mass_exchange=False)

"""Frontier 0.9.0 start-pool extension; unchanged search and finishing policy.

Only supplied starts alter decisions. No decoding, extra search allocation, or
pre-finishing is added. ``extra_starts`` is an iterable of ``(origin, Scored)``
or ``(origin, MovePlan)`` pairs; every supplied candidate is replay-scored.
The origin travels with the actual candidate when duplicate stows replace one
another in frontier's pool. ``search_changed`` compares ordered actions, rather
than objective values or a finisher's ``search-N`` label.

Search ancestry is not retained contribution. Every admitted seed persists its
box-slot choices; final_contribution replays the checked final moves and divides
retained load-list choices by the whole load-list size (logic review P0.1).
The fresh none arm supplies the separate unique-choice and moves-saved tests.
All search runs scoring the final stow are recorded, including shared stows and
plans a run visited but did not retain in its returned pool.
"""
from __future__ import annotations

from pathlib import Path
import sys
from time import perf_counter

# Frozen frontier modules use local absolute imports. Import that implementation,
# rather than copying or editing any of its solver helpers.
_FRONTIER = Path(__file__).resolve().parents[1] / 'frontier'
if str(_FRONTIER) not in sys.path:
    sys.path.insert(0, str(_FRONTIER))
from experiments.mvp.frontier.planner import (
    Problem, TwoStepPlanner, _finish, _key, _yard_first, blocked_plan, compacted,
    get_action_space, order_legal, search, sight_clear, solve_master,
)
from experiments.mvp.hybrid.contribution import for_plan

NAME = 'hybrid.frontier'
VERSION = '0.2.0'


def _actions(scored):
    """Exact action content, including order; unlike frontier's stow key."""
    return tuple((a.box, a.slot) for a in scored.actions)


def _seed_decisions(scored):
    """Persist the producing seed's box-to-slot choices, independent of order."""
    return dict(sorted(dict(_actions(scored)).items()))


def plan_and_check(instance, *, search_s=60., finalize_s=600., seed=0, tries=3, feasibility_config=None, extra_starts=()):
    """Portfolio search, then full checker, with optional replayed extra starts.

    Returns (result or None, record of what happened).

    The two-step plan and the port-blocked plan are finished first. The cheapest that finishes is the fallback, and its tank
    fill is the stand-in trim for the SOLAS sight gate (the ship's default tanks if neither finished). Both stay search starts
    either way. Other starts are yard-first constructions and the master plan, which tries taller deck stacks first and drops a
    tier until the sight gate passes. Search spends 2/3 of its time on the best start and 1/3 on the runner-up. Plans go through
    the finisher and checker cheapest first; the first that passes is the answer. A stow whose finish already failed isn't retried.
    """
    from stowage.feasibility import FeasibilityConfig
    config = feasibility_config or FeasibilityConfig(extra={'global_screen': 0})
    out = dict(tries=[], search_s=search_s, final_origin=None, final_search_changed=None,
               final_search_origin=None, final_seed_decisions=None, final_contribution=None,
               final_load_list=sorted(instance.load_list), final_reaching_runs=[], final_shared_stow=False)
    problem = Problem(instance)
    space = get_action_space('boxslot')
    started = perf_counter()
    two = TwoStepPlanner(feasibility_config=config).plan(instance, seed=seed, time_limit_s=120)
    out.update(twostep_status=two.status, twostep_plan_s=round(perf_counter()-started, 2))
    fallback, fallback_row, reference, starts, unfinished = None, None, None, [], set()

    def record_final(result, row):
        """Ancestry is bookkeeping; retention comes from the checked result."""
        contribution = for_plan(instance, result.plan, row['origin'], row['seed_decisions'])
        final_key = tuple(sorted(contribution['final_decisions'].items()))
        reaching_runs = reached.get(final_key, ())
        out.update(final_origin=row['origin'], final_search_origin=row['origin'],
                   final_search_changed=row['search_changed'], final_seed_decisions=row['seed_decisions'],
                   final_reaching_runs=list(reaching_runs), final_shared_stow=len(reaching_runs) > 1,
                   final_contribution=contribution)

    def finish_start(label, plan):
        nonlocal fallback, fallback_row, reference
        scored = problem.score(tuple(space.from_plan(instance, plan)))
        if scored is None:
            return
        out[f'{label}_objective'] = scored.objective
        starts.append((label, scored))
        result, row = _finish(instance, scored.plan, seed=seed, finalize_s=finalize_s, label=label)
        row.update(search_objective=scored.objective, origin=label, search_changed=False,
                   seed_decisions=_seed_decisions(scored))
        out[f'{label}_finish'] = row
        if result is None:
            unfinished.add(_key(scored))
        elif fallback_row is None or scored.objective < fallback_row['search_objective']:
            fallback, fallback_row = result, row
            reference = dict(result.plan.ballast.tank_masses_kg)

    if two.plan is not None:
        finish_start('twostep', two.plan)
    began = perf_counter()
    blocked = blocked_plan(instance, seed=seed, config=config, time_limit_s=120)
    out.update(blocked_status=blocked.status, blocked_plan_s=round(perf_counter()-began, 2))
    if blocked.plan is not None:
        finish_start('blocked', compacted(instance, blocked.plan))

    def clear(scored):
        return sight_clear(instance, scored.plan, reference or {})[0]

    built, attempts = _yard_first(problem, seed=seed, time_limit_s=max(5., search_s*0.15), rounds=40)
    usable = [s for s in built[:4] if clear(s)]
    out.update(yard_first_attempts=attempts, yard_first_built=len(built), yard_first_clear=len(usable),
               yard_first_best=usable[0].objective if usable else None)
    starts += [('yard_first', s) for s in usable[:2]]
    masters = []
    for tiers in (4, 3, 2, 1):
        master = solve_master(problem, time_limit_s=max(8., search_s*0.12), workers=2, seed=seed, deck_tiers=tiers)
        row = dict(deck_tiers=tiers, status=master.status, surrogate=master.objective, bound=master.bound, cuts=master.cuts,
                   seconds=round(master.wall_s, 1))
        masters.append(row)
        if master.assignment is None:
            continue
        ordered, stuck = order_legal(problem, master.assignment)
        row['stuck'] = stuck
        scored = problem.score(ordered) if stuck is None else None
        if scored is None:
            continue
        row['objective'] = scored.objective
        row['sight_clear'] = clear(scored)
        if row['sight_clear']:
            starts.append(('master', scored))
            break
    out['masters'] = masters
    # Supplied candidates get the same replay scoring and sight gate as constructed starts.
    # Appending preserves every baseline tie-break when no extra start is supplied.
    extra_rows = []
    for label, candidate in extra_starts:
        # MovePlan.actions contains encoded dictionaries (or None), whereas
        # Scored.actions contains Place objects. Decode both through the space.
        plan = candidate.plan if hasattr(candidate, 'plan') else candidate
        actions = tuple(space.from_plan(instance, plan))
        scored = problem.score(tuple(actions))
        row = dict(origin=label, replayed=scored is not None, sight_clear=False)
        if scored is not None:
            row.update(objective=scored.objective, sight_clear=clear(scored),
                       seed_decisions=_seed_decisions(scored))
            if row['sight_clear']:
                starts.append((label, scored))
        extra_rows.append(row)
    out['extra_starts'] = extra_rows
    starts.sort(key=lambda item: item[1].objective)
    out['starts'] = [dict(origin=label, objective=scored.objective, seed_decisions=_seed_decisions(scored))
                     for label, scored in starts]
    if not starts:
        return fallback, out
    pool, lineage, reached, runs = {}, {}, {}, []
    shares = [2/3, 1/3] if len(starts) > 1 else [1.]
    for (label, start), share in zip(starts, shares):
        run_index = len(runs)
        witness = dict(run=run_index, origin=label, seed_decisions=_seed_decisions(start))

        def remember(scored):
            witnesses = reached.setdefault(_key(scored), [])
            if not any(item['run'] == run_index for item in witnesses):
                witnesses.append(witness)

        # Frozen search exposes only its kept plans. Observe its existing score
        # calls so a stow visited by an earlier run cannot become an exclusive
        # later-run contribution merely because the earlier run dropped it.
        original_score = problem.score

        def scored_with_witness(actions):
            scored = original_score(actions)
            if scored is not None:
                remember(scored)
            return scored

        remember(start)
        problem.score = scored_with_witness
        begin = perf_counter()
        try:
            best, stats = search(instance, start.actions, time_limit_s=search_s*share, seed=seed, problem=problem, keep=tries,
                                 gate=clear)
        finally:
            problem.score = original_score
        candidates = []
        for scored in best:
            key = _key(scored)
            pool[key] = scored
            # Pool replacement and provenance replacement are atomic in this order.
            # _key ignores load order; change detection must compare ordered actions.
            lineage[key] = dict(origin=label, search_changed=_actions(scored) != _actions(start),
                                seed_decisions=_seed_decisions(start))
            remember(scored)
            candidates.append(dict(objective=scored.objective, **lineage[key]))
        runs.append(dict(start=label, origin=label, candidates=candidates, start_objective=start.objective, best=best[0].objective, iterations=stats.iterations,
                         accepted=stats.accepted, infeasible=stats.infeasible, wall_s=round(perf_counter()-begin, 1),
                         trace=stats.best_trace[-5:], gains={k: round(v, 1) for k, v in stats.operator_gains.items()}))
    out['runs'] = runs
    ranked = sorted((s for s in pool.values() if _key(s) not in unfinished), key=lambda s: s.objective)
    for rank, scored in enumerate(ranked[:tries]):
        if fallback is not None and scored.objective >= fallback_row['search_objective']:
            break
        result, row = _finish(instance, scored.plan, seed=seed, finalize_s=finalize_s, label=f'search-{rank}')
        row.update(search_objective=scored.objective, **lineage[_key(scored)])
        out['tries'].append(row)
        if row['passed']:
            record_final(result, row)
            return result, out
        # Kept so a plan that only ran out of finisher time can be finished later without repeating the search.
        row['actions'] = [dict(box=a.box, slot=a.slot) for a in scored.actions]
    if fallback is not None:
        record_final(fallback, fallback_row)
        out['tries'].append(dict(fallback_row))
    return fallback, out

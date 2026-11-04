# Provenance: copied from experiments/mvp/frontier/planner.py (frozen Frontier 0.9.0).
"""Frontier benchmark planner: an incumbent plan, then search on the real objective, then finisher and full checker.

Same gate as every other method in this project: a plan counts only if the V4 finisher completes it and the full checker
passes it. ``plan_and_check`` tries the best search plans in order and returns the first one that passes, falling back
to the cheapest start that finished in the same run, so it can't lose to that start. If no start finishes, there is no such floor.
"""
from __future__ import annotations

from dataclasses import dataclass

import os
import random
from time import monotonic, perf_counter

from stowage.actions import get_action_space
from stowage.checker import check
from stowage.model import PlannerResult
from stowage.planner import finalize
from stowage.solver.twostep import TwoStepPlanner

from alns import Problem, Rebuilder, Sequencer, _mask_ok, search
from blocked import blocked_plan, compacted
from master import solve_master
from sight import sight_clear
from sequence import order_legal

NAME = 'frontier.alns'
VERSION = '0.9.0'


def _key(scored):
    """The stow a scored plan describes, ignoring load order."""
    return tuple(sorted((a.box, a.slot) for a in scored.actions))


def baseline_finish(instance, plan, *, seed, finalize_s, label):
    """Finalize with the V4 finisher, scoped exactly as the baseline runs do, then run the full checker."""
    result = PlannerResult(status='ok', plan=plan, planner=NAME, planner_version=VERSION, seed=seed)
    os.environ['STOWAGE_BALLAST_V4'] = '1'
    started = perf_counter()
    try:
        result = finalize(instance, result, time_limit_s=finalize_s)
    finally:
        os.environ.pop('STOWAGE_BALLAST_V4', None)
    row = dict(label=label, finalize_status=result.status, finalize_s=round(perf_counter()-started, 2), passed=False,
               message=(result.message or '')[:160])
    if result.status == 'ok' and result.plan is not None:
        report = check(instance, result.plan)
        row['passed'] = bool(report.passed)
        row['weighted'] = float(report.count.weighted_nonproductive) if report.passed else None
        if not report.passed:
            row['violations'] = sorted({v.rule_id for v in report.violations})[:6]
            return None, row
        return result, row
    return None, row


def _yard_first(problem, *, seed, time_limit_s, rounds):
    """Randomised yard-first constructions (Junqueira HR2 idea): cheapest marginal lift first, then a clean load order."""
    rng = random.Random(seed)
    rebuilder, sequencer = Rebuilder(problem, rng), Sequencer(problem)
    deadline = monotonic()+time_limit_s
    found, attempts = {}, 0
    while attempts < rounds and monotonic() < deadline:
        rebuilt = rebuilder.rebuild([], list(problem.instance.load_list), noise=0. if attempts == 0 else 1.5, deadline=deadline)
        attempts += 1
        if rebuilt is None:
            continue
        assignment = {a.box: a.slot for a in rebuilt}
        if problem.mixes_hold_stack(assignment):
            continue
        ordered = sequencer.order(assignment)
        if ordered is None or not _mask_ok(problem, ordered):
            continue
        scored = problem.score(ordered)
        if scored is not None:
            found[_key(scored)] = scored
    return sorted(found.values(), key=lambda s: s.objective), attempts


def baseline_plan_and_check(instance, *, search_s=60., finalize_s=600., seed=0, tries=3, feasibility_config=None):
    """Portfolio search, then finisher and full checker. Returns (result or None, record of what happened).

    The two-step plan and the port-blocked plan are finished first. The cheapest that finishes is the fallback, and its tank
    fill is the stand-in trim for the SOLAS sight gate (the ship's default tanks if neither finished). Both stay search starts
    either way. Other starts are yard-first constructions and the master plan, which tries taller deck stacks first and drops a
    tier until the sight gate passes. Search spends 2/3 of its time on the best start and 1/3 on the runner-up. Plans go through
    the finisher and checker cheapest first; the first that passes is the answer. A stow whose finish already failed isn't retried.
    """
    from stowage.feasibility import FeasibilityConfig
    config = feasibility_config or FeasibilityConfig(extra={'global_screen': 0})
    out = dict(tries=[], search_s=search_s)
    problem = Problem(instance)
    space = get_action_space('boxslot')
    started = perf_counter()
    two = TwoStepPlanner(feasibility_config=config).plan(instance, seed=seed, time_limit_s=120)
    out.update(twostep_status=two.status, twostep_plan_s=round(perf_counter()-started, 2))
    fallback, fallback_row, reference, starts, unfinished = None, None, None, [], set()

    def finish_start(label, plan):
        nonlocal fallback, fallback_row, reference
        scored = problem.score(tuple(space.from_plan(instance, plan)))
        if scored is None:
            return
        out[f'{label}_objective'] = scored.objective
        starts.append((label, scored))
        result, row = baseline_finish(instance, scored.plan, seed=seed, finalize_s=finalize_s, label=label)
        row['search_objective'] = scored.objective
        out[f'{label}_finish'] = row
        if result is None:
            unfinished.add(_key(scored))
        elif fallback_row is None or scored.objective < fallback_row['search_objective']:
            fallback, fallback_row = result, row
            reference = dict(result.plan.ballast.tank_masses_kg) if result.plan.ballast is not None else {}

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
        master = solve_master(problem, time_limit_s=max(8., search_s*0.12), workers=1, seed=seed, deck_tiers=tiers)
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
    starts.sort(key=lambda item: item[1].objective)
    if not starts:
        return fallback, out
    pool, runs = {}, []
    shares = [2/3, 1/3] if len(starts) > 1 else [1.]
    for (label, start), share in zip(starts, shares):
        begin = perf_counter()
        best, stats = search(instance, start.actions, time_limit_s=search_s*share, seed=seed, problem=problem, keep=tries,
                             gate=clear)
        for scored in best:
            pool[_key(scored)] = scored
        runs.append(dict(start=label, start_objective=start.objective, best=best[0].objective, iterations=stats.iterations,
                         accepted=stats.accepted, infeasible=stats.infeasible, wall_s=round(perf_counter()-begin, 1),
                         trace=stats.best_trace[-5:], gains={k: round(v, 1) for k, v in stats.operator_gains.items()}))
    out['runs'] = runs
    ranked = sorted((s for s in pool.values() if _key(s) not in unfinished), key=lambda s: s.objective)
    for rank, scored in enumerate(ranked[:tries]):
        if fallback is not None and scored.objective >= fallback_row['search_objective']:
            break
        result, row = baseline_finish(instance, scored.plan, seed=seed, finalize_s=finalize_s, label=f'search-{rank}')
        row['search_objective'] = scored.objective
        out['tries'].append(row)
        if row['passed']:
            return result, out
        # Kept so a plan that only ran out of finisher time can be finished later without repeating the search.
        row['actions'] = [dict(box=a.box, slot=a.slot) for a in scored.actions]
    if fallback is not None:
        out['tries'].append(dict(fallback_row))
    return fallback, out



@dataclass
class StartBank:
    problem: object
    starts: list
    fallback: object
    fallback_row: dict | None
    reference: dict | None
    unfinished: set
    record: dict

    def clear(self, scored):
        return sight_clear(self.problem.instance, scored.plan, self.reference or {})[0]


def baseline_search_s(instance):
    """run_maps.py's val-0.9 settings: six seconds per load, capped at 120."""
    return min(120., max(20., 6.*len(instance.load_list)))


def collect_starts(instance, *, problem=None, search_s=120., finalize_s=600., seed=0,
                   feasibility_config=None, finish=None, stop_if=None, record=None):
    """Copy of Frontier 0.9's construction/finished-fallback body.

    ``finish`` has the frozen _finish signature and returns (result, row).
    The one-worker master respects the engine's CPU cap. Budgets otherwise
    exactly match the frozen body. A true stop_if raises StopIteration(result).
    """
    finish = finish or baseline_finish
    from stowage.feasibility import FeasibilityConfig
    config = feasibility_config or FeasibilityConfig(extra={'global_screen': 0})
    out = record if record is not None else {}
    out.update(tries=[], search_s=search_s)
    problem = problem or Problem(instance)
    space = get_action_space('boxslot')
    started = perf_counter()
    two = TwoStepPlanner(feasibility_config=config).plan(instance, seed=seed, time_limit_s=120)
    out.update(twostep_status=two.status, twostep_plan_s=round(perf_counter()-started, 2))
    fallback, fallback_row, reference, starts, unfinished = None, None, None, [], set()

    def finish_start(label, plan):
        nonlocal fallback, fallback_row, reference
        scored = problem.score(tuple(space.from_plan(instance, plan)))
        if scored is None:
            return
        out[f'{label}_objective'] = scored.objective
        starts.append((label, scored))
        result, row = finish(instance, scored.plan, seed=seed, finalize_s=finalize_s, label=label)
        row['search_objective'] = scored.objective
        out[f'{label}_finish'] = row
        if result is None:
            unfinished.add(_key(scored))
        elif fallback_row is None or scored.objective < fallback_row['search_objective']:
            fallback, fallback_row = result, row
            reference = dict(result.plan.ballast.tank_masses_kg) if result.plan.ballast is not None else {}
        if result is not None and stop_if is not None and stop_if(result):
            raise StopIteration(result)

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
        master = solve_master(problem, time_limit_s=max(8., search_s*0.12), workers=1, seed=seed, deck_tiers=tiers)
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
    starts.sort(key=lambda item: item[1].objective)
    return StartBank(problem, starts, fallback, fallback_row, reference, unfinished, out)


def baseline_search(instance, bank, *, search_s, seed=0, keep=3):
    """Unchanged ALNS, original two-thirds/one-third allocation, with archive."""
    pool, runs = {}, []
    starts = bank.starts
    shares = [2/3, 1/3] if len(starts) > 1 else [1.]
    for (label, start), share in zip(starts, shares):
        begin = perf_counter()
        best, stats = search(instance, start.actions, time_limit_s=search_s*share,
                             seed=seed, problem=bank.problem, keep=keep, gate=bank.clear)
        for scored in best:
            pool[_key(scored)] = scored
        runs.append(dict(start=label, start_objective=start.objective,
                         best=best[0].objective if best else None,
                         iterations=stats.iterations, accepted=stats.accepted,
                         infeasible=stats.infeasible, wall_s=round(perf_counter()-begin, 1),
                         trace=stats.best_trace[-5:],
                         gains={k: round(v, 1) for k, v in stats.operator_gains.items()}))
    return sorted(pool.values(), key=lambda scored: scored.objective), dict(runs=runs)

"""Build Frontier's four actual start families without finishing any plan.

Use a single returned bank unchanged across paired search ablations. This is
not a byte-for-byte reconstruction of wall-bounded Frontier 0.9.0 starts.
"""
from __future__ import annotations

import math
from time import monotonic

from alns import Scored
from blocked import blocked_plan, compacted
from master import solve_master
from planner import _yard_first
from sequence import order_legal
from stowage.actions.space import Place
from stowage.conventions import cells_of_slot
from stowage.feasibility import FeasibilityConfig
from stowage.solver.twostep import TwoStepPlanner


def _complete(problem, actions):
    """The supported empty-ship Stage A bank has one distinct Place per load."""
    loads = set(problem.instance.load_list)
    if (len(actions) != len(loads) or any(not isinstance(a, Place) for a in actions)
            or {a.box for a in actions} != loads):
        return False
    occupied = set()
    for action in actions:
        footprint = cells_of_slot(action.slot)
        if occupied.intersection(footprint):
            return False
        occupied.update(footprint)
    return True


def build(problem, *, seed, budget_s):
    """Return ``([(label, alns.Scored), ...], stats)`` for an empty-ship call.

    The bank preserves distinct construction/order variants, including a raw
    blocked plan and its cheaper compaction. It is sorted by replay objective,
    with no finisher, full-checker or sight-gate claim. Reuse exactly this bank
    for all methods in a paired experiment; do not regenerate it per method.

    Soft cumulative slices are 30% two-step, 25% blocked, 15% yard-first and
    30% master. Unused time carries forward. Calls already in progress cannot
    be interrupted; actual overruns are reported. Completion/replay admission
    of an in-flight result is allowed to finish after its slice/deadline.
    """
    budget_s = float(budget_s)
    if not math.isfinite(budget_s) or budget_s < 0:
        raise ValueError('budget_s must be finite and nonnegative')
    started = monotonic()
    deadline = started+budget_s
    stats = dict(requested_budget_s=budget_s, seed=seed, stages=[], starts=[],
                 source='unchanged Frontier constructor families; no finisher',
                 global_screen=0, master_workers=1, unsupported=False)
    found = []
    if problem.instance.onboard.placements or problem.instance.discharge_list:
        stats.update(unsupported=True, reason='requires empty-ship loading call',
                     wall_s=monotonic()-started, budget_overrun_s=0.)
        return found, stats
    config = FeasibilityConfig(extra={'global_screen': 0})

    def remaining(end):
        return max(0., min(end, deadline)-monotonic())

    def admit(label, *, plan=None, scored=None, stage):
        """Replay all newly produced plans; yard-first already did this."""
        if scored is None:
            if plan is None:
                stage.setdefault('rejected', []).append(dict(label=label, reason='replay'))
                return
            actions = tuple(problem.space.from_plan(problem.instance, plan))
            if not _complete(problem, actions):
                stage.setdefault('rejected', []).append(dict(label=label, reason='incomplete_or_nonunique'))
                return
            scored = problem.score(actions)
        if scored is None:
            stage.setdefault('rejected', []).append(dict(label=label, reason='replay'))
            return
        if not isinstance(scored, Scored) or not _complete(problem, scored.actions):
            stage.setdefault('rejected', []).append(dict(label=label, reason='incomplete_or_nonunique'))
            return
        found.append((label, scored))
        stage.setdefault('retained', []).append(dict(label=label, objective=scored.objective))

    def stage_row(name, end):
        row = dict(family=name, requested_s=remaining(end), status='not_started')
        stats['stages'].append(row)
        return row

    end = started+budget_s*.30
    row = stage_row('twostep', end)
    began = monotonic()
    if row['requested_s'] > 0:
        try:
            result = TwoStepPlanner(feasibility_config=config).plan(
                problem.instance, seed=seed, time_limit_s=row['requested_s'])
            row.update(status=result.status, message=result.message,
                       planner_metrics=result.metrics)
            if result.plan is not None:
                admit('twostep', plan=result.plan, stage=row)
        except Exception as exc:
            row.update(status='error', error=f'{type(exc).__name__}: {exc}')
    else:
        row['status'] = 'budget_exhausted'
    row['wall_s'] = monotonic()-began

    end = started+budget_s*.55
    row = stage_row('blocked', end)
    began = monotonic()
    if row['requested_s'] > 0:
        try:
            result = blocked_plan(problem.instance, seed=seed, config=config,
                                  time_limit_s=row['requested_s'])
            row.update(status=result.status, message=result.message,
                       planner_metrics=result.metrics)
            if result.plan is not None:
                admit('blocked_raw', plan=result.plan, stage=row)
                if remaining(end) > 0:
                    compact = compacted(problem.instance, result.plan)
                    # compacted() returns its input object if no cheaper plan.
                    if compact is not result.plan:
                        admit('blocked_compacted', plan=compact, stage=row)
                    else:
                        row['compaction'] = 'no_cheaper_plan'
                else:
                    row['compaction'] = 'skipped_deadline'
        except Exception as exc:
            row.update(status='error', error=f'{type(exc).__name__}: {exc}')
    else:
        row['status'] = 'budget_exhausted'
    row['wall_s'] = monotonic()-began

    end = started+budget_s*.70
    row = stage_row('yard_first', end)
    began = monotonic()
    if row['requested_s'] > 0:
        try:
            built, attempts = _yard_first(problem, seed=seed,
                                          time_limit_s=row['requested_s'], rounds=40)
            row.update(status='completed', attempts=attempts, built=len(built))
            for i, scored in enumerate(built):
                admit(f'yard_first_{i}', scored=scored, stage=row)
        except Exception as exc:
            row.update(status='error', error=f'{type(exc).__name__}: {exc}')
    else:
        row['status'] = 'budget_exhausted'
    row['wall_s'] = monotonic()-began

    # Allocate remaining master time evenly across the tiers still untried.
    # Unlike Frontier's sight-gated early exit, retain every tier that yields
    # a complete replay-legal candidate so all search methods see one bank.
    tiers = (4, 3, 2, 1)
    for index, deck_tiers in enumerate(tiers):
        allowance = remaining(deadline)/(len(tiers)-index)
        row = dict(family='master', deck_tiers=deck_tiers, workers=1,
                   requested_s=allowance, status='not_started')
        stats['stages'].append(row)
        began = monotonic()
        if allowance > 0:
            try:
                result = solve_master(problem, time_limit_s=allowance,
                                      workers=1, seed=seed, deck_tiers=deck_tiers)
                row.update(status=result.status, surrogate=result.objective,
                           bound=result.bound, cuts=result.cuts,
                           solver_wall_s=result.wall_s)
                if result.assignment is not None:
                    ordered, stuck = order_legal(problem, result.assignment)
                    row['stuck'] = stuck
                    if stuck is None and _complete(problem, ordered):
                        admit(f'master_tiers_{deck_tiers}',
                              scored=problem.score(ordered), stage=row)
                    elif stuck is None:
                        row['status'] = 'incomplete_assignment'
            except Exception as exc:
                row.update(status='error', error=f'{type(exc).__name__}: {exc}')
        else:
            row['status'] = 'budget_exhausted'
        row['wall_s'] = monotonic()-began

    found.sort(key=lambda item:item[1].objective)
    stats['starts'] = [dict(label=label, objective=scored.objective,
                            actions=[dict(box=a.box, slot=a.slot) for a in scored.actions])
                       for label, scored in found]
    stats['count'] = len(found)
    stats['distinct_assignments'] = len({tuple(sorted((a.box, a.slot) for a in scored.actions))
                                         for _, scored in found})
    stats['wall_s'] = monotonic()-started
    stats['budget_overrun_s'] = max(0., stats['wall_s']-budget_s)
    return found, stats

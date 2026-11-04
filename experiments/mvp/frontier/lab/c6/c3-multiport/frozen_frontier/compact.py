"""D174 "plan on paper": treat a StowLM plan as a draft. Keep each box's final slot, drop the intermediate placements, and let
software pick a clean load order; then the move engine, the V4 finisher and the full checker, as for any plan.

The order is a topological sort of two_step's necessary precedences (support below first, hold before deck under a hatch),
tried with three tie-breaks: yard-top first, finish a hatch before the next, and the model's own order. The expanded plan
with the fewest weighted moves is the compacted plan. It replaces the original only if it passes and costs less (or the
original failed): no repair, no slot changes, same stow. A plan with discharges keeps them first, in the model's order; a
plan that moves a box other than a load-list box is left alone.
Library: compact_plan(), finish_and_check(). Usage: compact.py train [workers=5] | compact.py retry [workers=4] | compact.py folder <run_dir> <out> [workers]
The finisher budget is COMPACT_FINISHER_S (default 120).
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ.setdefault(name, '1')

HERE = Path(__file__).resolve().parent
from frozen_env import ROOT
DEPLOYMENT = ROOT/'experiments/mvp/exit/deployment-d137'
BUDGET_S = float(os.environ.get('COMPACT_FINISHER_S', 120))


def parts(count):
    get = lambda f, k: getattr(f, k) if not isinstance(f, dict) else f[k]
    return dict(relocations=count.relocations, restow_now=count.restow_lifts, hatch_now=count.hatch_cover_lifts,
                restow_later=sum(get(f, 'restow_lifts') for f in count.future),
                hatch_later=sum(get(f, 'hatch_cover_lifts') for f in count.future), total=count.weighted_nonproductive)


def topological(n, edges, key):
    """Kahn's algorithm, always taking the ready item with the smallest key(item, chosen so far)."""
    needs = {i: set() for i in range(n)}
    for before, after in edges:
        needs[after].add(before)
    chosen, ready = [], {i for i in range(n) if not needs[i]}
    while ready:
        pick = min(ready, key=lambda i: key(i, chosen))
        chosen.append(pick)
        ready.remove(pick)
        for i, required in needs.items():
            if i not in chosen and i not in ready and required <= set(chosen):
                ready.add(i)
    if len(chosen) != n:
        raise ValueError('precedences have a cycle')
    return chosen


def compact_plan(instance, plan):
    """(compacted MovePlan or None, info). info has the original count, the order used and why nothing was produced."""
    from stowage.actions import get_action_space
    from stowage.actions.space import Discharge, Place
    from stowage.conventions import cells_of_slot
    from stowage.moves import apply_plan, count_moves
    from stowage.solver.twostep_terminal import _precedences

    applied = apply_plan(instance, plan)
    info = dict(before=parts(applied.count), n_actions=len(plan.actions or []), n_loads=len(instance.load_list))
    if not applied.legal:
        return None, dict(info, reason='original plan is illegal')
    space = get_action_space('boxslot')
    actions = list(space.from_plan(instance, plan))
    loads = set(instance.load_list)
    if any(isinstance(a, Place) and a.box not in loads for a in actions):
        return None, dict(info, reason='plan moves a box that is not on the load list')
    discharges = [a for a in actions if isinstance(a, Discharge)]
    final = {p.container_id: p.slot for p in applied.final_state.ship.placements if p.container_id in loads}
    if set(final) != loads:
        return None, dict(info, reason='a load-list box did not end aboard')
    boxes = sorted(final)
    slots = [SimpleNamespace(slot=final[box]) for box in boxes]
    edges = _precedences(instance, slots)
    last = {a.box: k for k, a in enumerate(actions) if isinstance(a, Place)}
    position = {box: (stack.id if hasattr(stack, 'id') else index, at, len(stack.boxes))
                for index, stack in enumerate(instance.yard.stacks) for at, box in enumerate(stack.boxes)}
    stacks = {s.id: s for s in instance.vessel.stacks}
    cells = {c.id: c for c in instance.vessel.cells}

    def above(i, chosen):
        """Load-list and other boxes still above this one in its yard stack."""
        gone = {boxes[k] for k in chosen}
        where = position.get(boxes[i])
        if where is None:
            return 0
        stack = next(s for k, s in enumerate(instance.yard.stacks) if (s.id if hasattr(s, 'id') else k) == where[0])
        return sum(b not in gone for b in stack.boxes[where[1]+1:])

    def hatch(i):
        cell = cells_of_slot(slots[i].slot)[0]
        return stacks[cells[cell].stack_id].hatch_cover_id

    def by_hatch(i, chosen):
        same = not chosen or hatch(chosen[-1]) == hatch(i)
        return (not same, above(i, chosen), last[boxes[i]] if boxes[i] in last else i)

    orders = dict(yard_top=lambda i, chosen: (above(i, chosen), last.get(boxes[i], i)),
                  hatch_then_yard=by_hatch,
                  model_order=lambda i, chosen: last.get(boxes[i], i))
    best, best_cost, best_name, errors = None, None, None, {}
    for name, key in orders.items():
        try:
            order = topological(len(boxes), edges, key)
            candidate = space.expand(instance, [*discharges, *(Place(boxes[i], slots[i].slot) for i in order)])
        except Exception as error:
            errors[name] = f'{type(error).__name__}: {error}'
            continue
        cost = count_moves(instance, candidate).weighted_nonproductive
        if best_cost is None or cost < best_cost:
            best, best_cost, best_name = candidate, cost, name
    info.update(order=best_name, order_errors=errors)
    if best is None:
        return None, dict(info, reason='no order expanded')
    return best, dict(info, compact_raw=parts(count_moves(instance, best)))


def finish_and_check(instance, plan, budget_s=BUDGET_S, seed=None):
    """The V4 finisher scoped to finalize, then the full checker; no repair. Returns (row, finished result, report). ``seed`` is
    the finisher's seed, the map's own by default (rev-k1b P2: every arm must pass the same one, and the row records it)."""
    from frozen_checks import check
    from stowage.model import PlannerResult
    from stowage.planner import finalize
    started = time.perf_counter()
    result = PlannerResult(status='ok', plan=plan.model_copy(update={'ballast': None}), planner='strategy.compact', planner_version='1',
                           seed=instance.seed if seed is None else seed, provenance=plan.provenance)
    os.environ['STOWAGE_BALLAST_V4'] = '1'
    try:
        result = finalize(instance, result, time_limit_s=budget_s)
    finally:
        os.environ.pop('STOWAGE_BALLAST_V4', None)
    row = dict(finisher_s=round(time.perf_counter()-started, 2), finalize_status=result.status, message=result.message,
               finisher_seed=instance.seed if seed is None else seed)
    if result.status != 'ok' or result.plan is None:
        return dict(row, passed=False, timed_out=row['finisher_s'] >= .9*budget_s), None, None
    report = check(instance, result.plan)
    row.update(passed=report.passed is True, check_s=round(time.perf_counter()-started-row['finisher_s'], 2),
               rules=[v.rule_id for v in report.violations][:5])
    if report.passed:
        row.update(moves=report.count.weighted_nonproductive, after=parts(report.count))
    return row, result, report


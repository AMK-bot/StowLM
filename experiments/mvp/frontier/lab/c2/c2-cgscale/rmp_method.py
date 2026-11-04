"""Frozen page ladder plus a bounded restricted-master feasibility heuristic.

Sadykov et al. (2019), doi:10.1287/ijoc.2018.0822, motivates solving a
restricted integer master alongside a dive. No objective is needed here: the
pool already contains individually checked patterns, and the immediate goal is
an exact cover of the boxes without sharing a physical stack.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import importlib.util
from pathlib import Path
import sys
from time import monotonic

from ortools.sat.python import cp_model


HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('_cgscale_rmp_base_method', HERE / 'method.py')
base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = base
_spec.loader.exec_module(base)
_OriginalModel = base.Model
_original_one_stow = base.one_stow


def restricted_master(model, root_x, *, deadline, seconds=2.):
    """Find a complete stow in the existing pool; never generate new patterns.

    Returned indices still enter the base dive's legal/lashing/cover checks and
    then its real replay. CP-SAT has one worker and a two-second total allowance.
    """
    started = monotonic()
    stop = min(deadline, started+seconds)
    record = dict(status='DEADLINE', seconds=0., columns=0, chosen=0)

    def done(chosen=None):
        record['seconds'] = round(monotonic()-started, 6)
        record['chosen'] = len(chosen) if chosen is not None else 0
        return chosen, record

    if monotonic() >= stop:
        return done()
    problem = cp_model.CpModel()
    boxes, stacks = set(model.boxes), set(model.stacks)
    cover, stack_use = defaultdict(list), defaultdict(list)
    use = {}
    for k, column in enumerate(model.columns):
        page = model.pages[column.page]
        if (k in model.banned or not set(column.boxes) <= boxes or
                not set(page.stacks) <= stacks):
            continue
        variable = problem.NewBoolVar(f'p{k}')
        use[k] = variable
        for box in column.boxes:
            cover[box].append(variable)
        for stack in page.stacks:
            stack_use[stack].append(variable)
        if root_x.get(k, 0.) > .95:
            problem.AddHint(variable, 1)
        if len(use) % 256 == 0 and monotonic() >= stop:
            return done()
    record['columns'] = len(use)
    if any(not cover[box] for box in boxes):
        record['status'] = 'UNCOVERED_POOL'
        return done()
    for box in sorted(boxes):
        problem.AddExactlyOne(cover[box])
    for variables in stack_use.values():
        problem.AddAtMostOne(variables)
    remaining = stop-monotonic()
    if remaining <= 0.:
        return done()
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.max_time_in_seconds = remaining
    status = solver.Solve(problem)
    record['status'] = solver.StatusName(status)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return done()
    return done([k for k, variable in use.items() if solver.Value(variable)])


class Model(_OriginalModel):
    def generate(self, *, active_boxes, active_stacks, deadline, phase=1, **kwargs):
        root = super().generate(active_boxes=active_boxes, active_stacks=active_stacks,
                                deadline=deadline, phase=phase, **kwargs)
        if (phase == 1 and set(active_boxes) == set(self.boxes) and
                set(active_stacks) == set(self.stacks) and
                root['uncovered'] <= 1e-7 and self.seed_chosen is None):
            chosen, record = restricted_master(self, root['x'], deadline=deadline)
            if not hasattr(self, 'rmp_history'):
                self.rmp_history = []
            self.rmp_history.append(record)
            if chosen is not None:
                self.seed_chosen = chosen
        return root


def one_stow(problem, model, **kwargs):
    scored, row = _original_one_stow(problem, model, **kwargs)
    history = list(getattr(model, 'rmp_history', ()))
    row.update(rmp_calls=len(history), rmp_s=round(sum(item['seconds'] for item in history), 6),
               rmp=history)
    return scored, row


# These assignments affect only the uniquely imported private base module.
base.Model = Model
base.one_stow = one_stow


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    metadata = context.setdefault('method_metadata', {})
    metadata['entry_point'] = 'rmp_method.py:propose'
    metadata['method_sha256'] = hashlib.sha256((HERE / 'method.py').read_bytes()).hexdigest()
    metadata['rmp_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return base.propose(instance, seed=seed, budget_s=budget_s, context=context)


finish = base.finish

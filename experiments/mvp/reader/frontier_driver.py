"""Instruction-aware Frontier 0.9, sharing hybrid's portfolio implementation.

The portfolio is bound to private dependency globals rather than copied. Solver
helpers, ALNS, sequencing, finishing and full checking remain the frozen versions.
One solve per process: the two-step engine factory hook is scoped and restored.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import threading
from time import perf_counter
from types import FunctionType

from experiments.mvp.hybrid import planner as portfolio
from experiments.mvp.reader.constraints import constrain_frontier_problem, wrap_engine
from experiments.mvp.reader.occupancy import strengthen_constraint
from llm.instructions.replan import constraint_rule

_LOCK = threading.Lock()


@contextmanager
def _constructor_rule(constrained):
    """Restrict both carrier and terminal engines, plus existing solver hooks."""
    import stowage.solver.twostep as twostep

    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("reader constructor already active; use separate worker processes")
    original = twostep.make_feasibility_engine
    owner = threading.get_ident()

    def factory(instance, *args, **kwargs):
        engine = original(instance, *args, **kwargs)
        if threading.get_ident() == owner and instance.id == constrained.instance.id:
            return wrap_engine(engine, constrained)
        return engine

    try:
        with constraint_rule(constrained):
            twostep.make_feasibility_engine = factory
            yield
    finally:
        twostep.make_feasibility_engine = original
        _LOCK.release()


def _bind(function, replacements):
    """Reuse a function's exact code with isolated, explicit dependencies."""
    namespace = dict(function.__globals__)
    namespace.update(replacements)
    bound = FunctionType(function.__code__, namespace, function.__name__,
                         function.__defaults__, function.__closure__)
    bound.__kwdefaults__ = dict(function.__kwdefaults__ or {})
    return bound


def plan_and_check(instance, constrained=None, *, search_s=60., finalize_s=600.,
                   seed=0, workers=1, tries=3, feasibility_config=None,
                   constructor_s=120., enable_port_blocked=True):
    """Same frontier portfolio under a hard instruction, with full final gate.

    ``constrained`` supplies ``instance``, ``forbids`` and ``violations``. A future
    reader may produce it through the same compiler; no natural language enters
    search. Every admitted scored start/candidate and finished plan is audited.
    ``None`` runs the identical portfolio without an instruction. No repair.
    """
    if workers not in (1, 2):
        raise ValueError("workers must be 1 or 2")
    if constrained is not None and constrained.instance.id != instance.id:
        raise ValueError("constraint belongs to another instance")
    # Direct callers supplying production constraints receive the same retained
    # preflight and final occupancy checks as the sentence compiler.
    constrained = strengthen_constraint(constrained, reject_retained=True)
    began = perf_counter()
    audit = dict(scored=0, rejected=0, finished=0, finish_rejected=0)

    def problem_factory(inst):
        problem = portfolio.Problem(inst)
        if constrained is None:
            return problem
        constrain_frontier_problem(problem, constrained)
        previous = problem.score

        def score(actions):
            scored = previous(actions)
            audit['scored'] += 1
            if scored is not None and constrained.violations(scored.plan):
                scored = None
            if scored is None:
                audit['rejected'] += 1
            return scored

        problem.score = score
        return problem

    class Constructor:
        def __init__(self, **kwargs):
            self.inner = portfolio.TwoStepPlanner(**kwargs)

        def plan(self, inst, **kwargs):
            kwargs['time_limit_s'] = min(kwargs.get('time_limit_s', constructor_s), constructor_s)
            if constrained is None:
                return self.inner.plan(inst, **kwargs)
            with _constructor_rule(constrained):
                return self.inner.plan(inst, **kwargs)

    def blocked(inst, **kwargs):
        if not enable_port_blocked:
            return SimpleNamespace(status='disabled_by_standing_brief', plan=None)
        kwargs['time_limit_s'] = min(kwargs.get('time_limit_s', constructor_s), constructor_s)
        if constrained is None:
            return portfolio.blocked_plan(inst, **kwargs)
        with _constructor_rule(constrained):
            return portfolio.blocked_plan(inst, **kwargs)

    def master(problem, **kwargs):
        kwargs['workers'] = workers
        return portfolio.solve_master(problem, **kwargs)

    def finish(inst, plan, **kwargs):
        result, row = portfolio._finish(inst, plan, **kwargs)
        audit['finished'] += 1
        if result is not None and constrained is not None:
            violations = constrained.violations(result.plan)
            row['instruction_violations'] = violations
            row['instruction_passed'] = not violations
            if violations:
                row['passed'] = False
                audit['finish_rejected'] += 1
                return None, row
        return result, row

    run = _bind(portfolio.plan_and_check, dict(
        Problem=problem_factory, TwoStepPlanner=Constructor, blocked_plan=blocked,
        solve_master=master, _finish=finish))
    result, record = run(instance, search_s=search_s, finalize_s=finalize_s,
                         seed=seed, tries=tries, feasibility_config=feasibility_config)
    # Defence in depth if the reused portfolio's return paths ever change.
    if result is not None and constrained is not None:
        violations = constrained.violations(result.plan)
        record['final_instruction_violations'] = violations
        if violations:
            result = None
    record.update(instruction_audit=audit, reader_wall_s=round(perf_counter()-began, 3),
                  portfolio_source='experiments.mvp.hybrid.planner.plan_and_check',
                  frontier_version='0.9.0', workers=workers, constructor_s=constructor_s,
                  port_blocked_enabled=enable_port_blocked)
    return result, record

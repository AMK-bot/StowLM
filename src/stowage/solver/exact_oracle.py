"""A checker-constrained certificate from an independent physical brute force.

The public bounds oracle deliberately relaxes ballast and nonlinear departure
physics. Its proved optimum B is therefore a lower bound on the full checker
problem. If a checker-approved witness costs exactly B, the two inequalities
close: no checker-feasible plan can be cheaper, and this one attains the bound.
The witness can come from the oracle or from A*, so the argument does not require
the relaxed oracle's first minimum-cost stow to satisfy stability.

This is a relaxation certificate, not a claim that the bounds oracle itself
checks physics. Failure to find ballast is never an infeasibility proof. This
helper tries arrival tank masses on an oracle witness; an A* witness retains its
own ballast. It does not start expensive cold ballast calibration. A caller can
supply any independently finished witness for another certification attempt.

Only integral objective weights and exactly representable integer costs below
2**53 are certified against the float-valued bounds oracle. Other weights still
produce checker/relaxed diagnostics, but no numerical optimality certificate.
This avoids inventing equality by using a floating-point tolerance.

Certificates concern weighted lifts only. Full physical oracle witnesses may
use overrides and are reference artifacts, never gold plans unless separately
replayed through the public feasibility engine. This is the usual lower-bound
and feasible-upper-bound proof of optimality (Bertsimas & Tsitsiklis, 1997,
Introduction to Linear Optimization, ch. 1).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
import math
from time import monotonic, perf_counter
from typing import Literal

from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.checker import check
from stowage.model import BallastCondition, CheckReport, Instance, MovePlan


@dataclass(frozen=True)
class CheckerOracleCertificate:
    """OPTIMAL certifies the real checker problem; LIMIT never does."""

    status: Literal['OPTIMAL', 'FEASIBLE', 'LIMIT', 'INFEASIBLE', 'CHECK_FAILED']
    objective: float | None
    lower_bound: float
    relaxed_status: str
    relaxed_objective: float | None
    brute_force_agreement: bool
    checker_passed: bool
    certificate_source: str | None
    expanded_nodes: int
    wall_time_s: float
    plan: MovePlan | None = None
    report: CheckReport | None = None
    message: str = ''


def _cost(instance, count):
    w = instance.objective
    return sum((Fraction(str(weight))*number for weight, number in (
        (w.relocate, count.relocations), (w.restow, count.restow_lifts),
        (w.hatch_cover, count.hatch_cover_lifts),
        (w.future_restow, sum(f.restow_lifts for f in count.future)),
        (w.future_hatch_cover, sum(f.hatch_cover_lifts for f in count.future)))), Fraction(0))


def checker_brute_force_certificate(
    instance: Instance, *, incumbent: MovePlan | None = None,
    time_limit_s: float = 5., max_nodes: int = 100_000,
) -> CheckerOracleCertificate:
    """Compare full-checker witnesses to a proved independent physical bound.

    The wall deadline includes process startup, counting/search bounds and
    every full checker operation, including native numerical calls. Only a
    completely checked witness can survive an interrupted worker. Timeout
    returns FEASIBLE with that witness, otherwise LIMIT; it never upgrades an
    unchecked incumbent or an unfinished search to an optimality certificate.
    Missing/failed witnesses retain LIMIT, rather than excluding other ballast
    choices or other stows at the relaxed optimum. Inputs are not mutated.
    """
    if not math.isfinite(time_limit_s) or time_limit_s <= 0 or max_nodes < 1:
        raise ValueError('oracle limits must be positive and finite')
    started = perf_counter()
    deadline = monotonic()+time_limit_s
    weights = (instance.objective.relocate, instance.objective.restow,
               instance.objective.hatch_cover, instance.objective.future_restow,
               instance.objective.future_hatch_cover)
    if any(not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError('oracle weights must be finite and nonnegative')
    if incumbent is not None and (incumbent.instance_id != instance.id
                                  or incumbent.call_index != instance.call_index):
        raise ValueError('oracle witness must match instance and call')
    from stowage.solver.exact_deadline import run_with_deadline
    outcome = run_with_deadline(_checker_brute_force_certificate, (instance,),
        {'incumbent': incumbent, 'time_limit_s': time_limit_s,
         'max_nodes': max_nodes, '_deadline': deadline}, deadline=deadline)
    elapsed = perf_counter()-started
    if not outcome.timed_out:
        return replace(outcome.value, wall_time_s=elapsed)
    checkpoint = outcome.checkpoint
    message = 'Hard wall deadline reached; only completed bounds and full checker witnesses were retained.'
    if isinstance(checkpoint, CheckerOracleCertificate):
        checked = checkpoint.checker_passed and checkpoint.plan is not None and checkpoint.report is not None
        return replace(checkpoint, status='FEASIBLE' if checked else 'LIMIT',
            objective=checkpoint.objective if checked else None,
            plan=checkpoint.plan if checked else None,
            report=checkpoint.report if checked else None,
            checker_passed=bool(checked),
            brute_force_agreement=False, certificate_source=None,
            wall_time_s=elapsed, message=message)
    return CheckerOracleCertificate('LIMIT', None, 0., 'limit', None,
        False, False, None, 0, elapsed, message=message)


def _checker_brute_force_certificate(
    instance: Instance, *, incumbent: MovePlan | None = None,
    time_limit_s: float = 5., max_nodes: int = 100_000,
    _deadline: float | None = None,
) -> CheckerOracleCertificate:
    """Worker calculation; publish only complete evidence to its watchdog."""
    from stowage.solver.exact_deadline import publish_checkpoint
    started = perf_counter()
    weights = (instance.objective.relocate, instance.objective.restow,
               instance.objective.hatch_cover, instance.objective.future_restow,
               instance.objective.future_hatch_cover)
    integral = all(math.isfinite(w) and w >= 0 and float(w).is_integer()
                   and w < 2**53 for w in weights)
    bound = lower_bound(instance)
    counting = sum((Fraction(str(w))*n for w, n in zip(weights, (
        bound.relocations, bound.restow_lifts, bound.hatch_cover_lifts,
        bound.future_restow_lifts, bound.future_hatch_cover_lifts))), Fraction(0))
    publish_checkpoint(CheckerOracleCertificate('LIMIT', None, float(counting),
        'limit', None, False, False, None, 0, perf_counter()-started))
    remaining = time_limit_s if _deadline is None else max(1e-9, _deadline-monotonic())
    oracle = brute_force_optimum(instance, max_nodes=max_nodes,
                                 time_limit_s=remaining, check_static_rules=True)
    physical = (Fraction(int(oracle.objective)) if integral and oracle.status == 'optimal'
                and oracle.objective is not None and oracle.objective < 2**53
                and float(oracle.objective).is_integer() else None)
    lower = max(counting, physical) if physical is not None else counting
    publish_checkpoint(CheckerOracleCertificate('LIMIT', None, float(lower),
        oracle.status, oracle.objective, False, False, None, oracle.expanded_nodes,
        perf_counter()-started))
    candidates = ([incumbent] if incumbent is not None else [])
    if oracle.plan is not None:
        candidates.append(oracle.plan)
    best, best_report, best_cost = None, None, None
    last_report = None
    for candidate in candidates:
        if candidate.instance_id != instance.id or candidate.call_index != instance.call_index:
            raise ValueError('oracle witness must match instance and call')
        if candidate.ballast is None:
            candidate = candidate.model_copy(update={'ballast': BallastCondition(
                source='planner', tank_masses_kg={t.id: instance.onboard.tank_masses_kg.get(t.id, 0.)
                    for t in instance.vessel.tanks if t.adjustable})})
        report = check(instance, candidate)
        last_report = report
        if report.passed and report.count is not None:
            value = _cost(instance, report.count)
            if oracle.status == 'infeasible' or lower > value:
                raise AssertionError('independent lower bound contradicts a full-checker witness')
            if best_cost is None or value < best_cost:
                best, best_report, best_cost = candidate, report, value
                publish_checkpoint(CheckerOracleCertificate('FEASIBLE', float(best_cost),
                    float(lower), oracle.status, oracle.objective, False, True, None,
                    oracle.expanded_nodes, perf_counter()-started, best, best_report))
            # An already certified witness avoids another costly full check.
            if integral and oracle.status == 'optimal' and value < 2**53 and value == oracle.objective:
                break
    agreement = best_cost is not None and physical is not None and best_cost == physical
    if best_cost is not None and (oracle.status == 'infeasible' or lower > best_cost):
        raise AssertionError('independent lower bound contradicts a full-checker witness')
    if agreement:
        status, source = 'OPTIMAL', 'independent_brute_force_and_full_checker'
    elif best_cost is not None and best_cost == counting:
        status, source = 'OPTIMAL', 'counting_bound_and_full_checker'
    elif best_cost is not None:
        status, source = 'FEASIBLE', None
    elif oracle.status == 'infeasible':
        status, source = 'INFEASIBLE', 'infeasible_physical_relaxation'
    else:
        status, source = ('CHECK_FAILED' if candidates else 'LIMIT'), None
    return CheckerOracleCertificate(status, None if best_cost is None else float(best_cost),
        float(lower), oracle.status, oracle.objective, agreement, best is not None,
        source, oracle.expanded_nodes, perf_counter()-started, best,
        best_report or last_report,
        'Integral D11 weights required for brute-force equality.' if not integral else
        'Oracle witness uses arrival ballast unless an own-ballast incumbent is supplied.')

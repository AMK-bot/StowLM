"""Scorecard adapter: securing proof, inherited recipe, then small ballast LP.

Stored recipes are search seeds only. Every retained plan receives the public
full checker here and the scorecard's independent full checker afterward.
"""
from __future__ import annotations

from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
sys.path[:0] = [str(HERE), str(LAB/'c1/fx-finish'), str(LAB/'c1/leader')]


def finish(instance, plan, *, seed=0, finalize_s=20., label='c2-benders', context=None):
    from ballast import solve_ballast
    from early_bound import securing_bound
    from e9fix import install
    from stowage.checker import check
    from stowage.model import BallastCondition
    from stowage.moves import apply_plan
    install()
    started = monotonic()
    replay = apply_plan(instance, plan)
    bound = securing_bound(instance, replay.final_state.ship)
    meta = dict(finisher='c2-benders-lp', securing_bound=bound,
                securing_s=monotonic()-started)
    if bound.get('proof'):
        return None, dict(meta, status='infeasible_proven')
    stored = (context or {}).get('stored_plan') or {}
    if stored.get('ballast'):
        # An initial recipe may be tried on any candidate; it is not cached
        # acceptance and does not exempt any rule or fresh checker call.
        plan = plan.model_copy(update={'ballast': BallastCondition.model_validate(stored['ballast'])})
    before = monotonic()
    report = check(instance, plan)
    meta.update(inherited_full_checker_s=monotonic()-before,
                inherited_full_checker_passed=report.passed)
    if report.passed:
        return plan, dict(meta, status='full_checker_pass')
    remaining = finalize_s-(monotonic()-started)
    if remaining <= 0:
        return None, dict(meta, status='unknown', reason='budget after inherited full check')
    outcome = solve_ballast(instance, plan, budget_s=remaining,
                           max_iterations=3, exact_recheck=True)
    candidate = outcome.pop('candidate_plan', None)
    meta['ballast_subproblem'] = outcome
    if outcome.get('full_checker_passed') and candidate is not None:
        return candidate, dict(meta, status='full_checker_pass')
    return None, dict(meta, status='unknown')

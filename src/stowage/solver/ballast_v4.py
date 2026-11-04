"""Opt-in exact-first ballast search, with unchanged full-checker acceptance.

The caller runs this inside the existing ballast worker/watchdog. No process or
thread is started here. Equilibrium/GM loading probes at all three D57 samples
screen proposals before expensive curves, strength and securing. A candidate
is accepted only by the ordinary full checker, including static rules and
instructions. Exhausting this bounded search means not found, never infeasible.

APPROXIMATION: deterministic seed recipes and finite-difference local steps
truncate optimization search, not physics or hard limits. Endpoint recipes
explore the real discontinuity in tank free surfaces. Sources: IMO MSC.267(85),
Part B 3.1; Conn, Gould & Toint, Trust Region Methods (2000), chapter 15.
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
from pydantic import Field

from stowage.model import BallastOutcome
from stowage.solver.ballast_deadline import ExactDeadline
from stowage.solver.ballast_search import Deadline, Found


class BallastV4Outcome(BallastOutcome):
    """Explicit search telemetry; unknown worker counters can remain None."""
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    not_found_cause: str | None = None


def finish(instance, plan, *, time_limit_s=30., seed=0, _deadline=None):
    """One-core worker body. Parent owns the independent hard watchdog."""
    from stowage.solver.ballast import _provenance, _seed_masses, _validated_replay
    from stowage.solver.ballast_v4_evaluator import Evaluator
    started = time.monotonic()
    deadline = min(started+time_limit_s, _deadline) if _deadline is not None else started+time_limit_s
    provenance = _provenance(instance, plan, seed)
    diagnostics = dict(version='v4-exact-first-trim-seeds-screened-arrival', num_workers=1,
        evaluations=0, candidate_count=0, candidates_evaluated=0, selective_margin_evaluations=0,
        candidate_definition='unique normalized tank-fill vectors evaluated',
        full_candidate_definition='distinct full proposals, including inherited arrival recipe; excludes selected-margin-only probes',
        timing_definition='completed calls only; independent parent-watchdog timeout counters are unknown',
        cache_hits=0, cheap_probe_calls=0, cheap_probe_s=0.,
        cheap_rejections=0, physics_part_calls=0, physics_part_s=0.,
        physics_rejections=0, full_checker_calls=0, full_checker_s=0., full_checker_passed=None,
        lp_status='not_used: exact nonlinear probes; no linear qualification bypass',
        seed_grid='physics/probe_trimseed.py: inherited then exact 3-by-3 grid',
        seed_outcomes=[])
    evaluator = None

    def outcome(status, *, candidate=None, parts=None, cause=None, proof=None):
        import resource
        diagnostic_copy = dict(diagnostics)
        diagnostic_copy['elapsed_s'] = time.monotonic()-started
        diagnostic_copy['worker_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        diagnostic_copy['termination_cause'] = cause or (
            'accepted:full_checker' if status in ('ok', 'kept') else status)
        return BallastV4Outcome(status=status, plan=candidate, parts=parts or {},
            proof=proof, iterations=diagnostics['evaluations'],
            wall_time_s=time.monotonic()-started, provenance=provenance,
            diagnostics=diagnostic_copy, not_found_cause=cause)

    # Preserve the ordinary illegal-move input contract independently of budget.
    replay = _validated_replay(instance, plan)
    if not math.isfinite(time_limit_s) or time_limit_s <= 0 or time.monotonic() >= deadline:
        return outcome('not_found', cause='timeout:search')
    # The parent watchdog bounds opaque checker/native calls. Avoid a global
    # profiling-hook deadline around search; ExactContext retains its own exact
    # budgets, and every expensive callback has before/after clock checks.
    try:
        if plan.ballast is not None:
            from stowage.checker import check
            diagnostics['stage'] = 'existing_ballast_full_checker'
            before = time.monotonic()
            report = check(instance, plan)
            diagnostics['full_checker_calls'] += 1
            diagnostics['full_checker_s'] += time.monotonic()-before
            diagnostics['full_checker_passed'] = report.passed
            if time.monotonic() >= deadline:
                raise Deadline
            if report.passed:
                return outcome('kept', candidate=plan, parts=report.parts)
            return outcome('not_found', parts=report.parts, cause='checker:existing_ballast_rejected')
        # Arrival fills are the first proposal for ALL sizes. Read the actual
        # arrival declaration, not the replayed cargo end state. Larger cases
        # use the existing exact screens before their full-checker gate, so an
        # already rejected recipe cannot consume a full curve/checker budget.
        from stowage.solver.ballast_v4_seeds import trim_seed_recipes
        recipes = trim_seed_recipes(instance)
        inherited = recipes[0]
        # APPROXIMATION: the 16-box threshold selects search ordering only.
        # The tiny direct lane avoids setup/duplicate parts on a passing fill.
        direct_arrival = len(instance.load_list) <= 16
        if direct_arrival:
            from stowage.checker import check
            from stowage.model import BallastCondition
            initial_candidate = plan.model_copy(update={'ballast': BallastCondition(
                tank_masses_kg=inherited.tank_masses_kg, source='finisher')})
            diagnostics['evaluations'] += 1
            diagnostics['candidate_count'] += 1
            diagnostics['candidates_evaluated'] += 1
            diagnostics['stage'] = 'inherited_arrival_full_checker'
            record = dict(label=inherited.label, method='direct_full_checker', status='checking')
            diagnostics['seed_outcomes'].append(record)
            before = time.monotonic()
            report = check(instance, initial_candidate)
            elapsed = time.monotonic()-before
            diagnostics['full_checker_calls'] += 1
            diagnostics['full_checker_s'] += elapsed
            diagnostics['full_checker_passed'] = report.passed
            record.update(status='accepted' if report.passed else 'rejected',
                          full_checker_passed=report.passed, elapsed_s=elapsed,
                          rule_ids=sorted({v.rule_id for v in report.violations}))
            diagnostics['tiny_initial_full_checker_passed'] = report.passed
            if time.monotonic() >= deadline:
                raise Deadline
            if report.passed:
                diagnostics['accepted_seed'] = inherited.label
                return outcome('ok', candidate=initial_candidate, parts=report.parts)
        from stowage.solver.ballast_proof import prove_infeasible
        diagnostics['stage'] = 'fixed_stow_proof'
        proof = prove_infeasible(instance, replay.final_state.ship)
        if proof is not None:
            return outcome('infeasible_proven', proof=proof)
        diagnostics['stage'] = 'context_setup'
        evaluator = Evaluator(instance, plan, replay.final_state.ship, deadline, diagnostics)
        initial, tanks, capacity = evaluator.initial, evaluator.tanks, evaluator.capacity

        def recipe_fractions(recipe):
            raw = np.array([recipe.tank_masses_kg.get(t.id, 0.) for t in tanks])
            if not np.all(np.isfinite(raw) & (raw >= 0.) & (raw <= capacity)):
                return None  # do not silently clip an exact labelled recipe
            return evaluator.fractions(np.divide(raw, capacity,
                out=np.zeros(len(tanks)), where=capacity > 0))

        inherited_values = recipe_fractions(inherited)
        grid_seen = {}
        if direct_arrival and inherited_values is not None:
            identity = inherited_values.tobytes()
            grid_seen[identity] = inherited.label
            # Count the direct gate once, while allowing the old search to
            # evaluate useful margins for that same rejected fill later.
            evaluator.candidate_identities.add(identity)
            evaluator.recipe_identities.add(identity)
        grid_starts = []
        for recipe in recipes[1:] if direct_arrival else recipes:
            evaluator.checkpoint()
            values = recipe_fractions(recipe)
            record = dict(label=recipe.label, method='exact_screen', status='checking')
            diagnostics['seed_outcomes'].append(record)
            if values is None:
                record.update(status='invalid_masses', full_checker_passed=None)
                continue
            identity = values.tobytes()
            if identity in grid_seen:
                record.update(status='duplicate', duplicate_of=grid_seen[identity],
                              full_checker_passed=None)
                continue
            grid_seen[identity] = recipe.label
            if recipe.label != inherited.label:
                grid_starts.append(values)
            before = time.monotonic()
            full_calls = diagnostics['full_checker_calls']
            try:
                margins = evaluator.evaluate(values)
            except Found:
                record.update(status='accepted', full_checker_passed=True,
                              elapsed_s=time.monotonic()-before)
                diagnostics['accepted_seed'] = recipe.label
                raise
            except (Deadline, ExactDeadline):
                record.update(status='timeout', full_checker_passed=None,
                              elapsed_s=time.monotonic()-before)
                raise
            record.update(status='rejected', elapsed_s=time.monotonic()-before,
                          full_checker_passed=False if diagnostics['full_checker_calls'] > full_calls else None,
                          negative_keys=sorted(key for key, value in margins.items() if value < 0.))
        # The old portfolio remains in its original order after the grid.
        seeds = [initial]
        for recipe in _seed_masses(instance, None):
            values = np.array([recipe.get(t.id, replay.final_state.ship.tank_masses_kg.get(t.id, 0.))
                               for t in tanks])
            seeds.append(np.divide(values, capacity, out=np.zeros(len(tanks)), where=capacity > 0))
        # Real empty/full endpoints precede arbitrary slack conditions.
        seeds.extend([np.zeros(len(tanks)), np.ones(len(tanks))])
        unique, seen = [], set()
        for values in seeds:
            values = evaluator.fractions(values)
            key = values.tobytes()
            if key in seen:
                continue
            seen.add(key)
            unique.append(values)
            evaluator.evaluate(values)
        unique.extend(grid_starts)
        if tanks:
            from stowage.solver.ballast_pressed import search as pressed_search
            # APPROXIMATION: cap endpoint-basin exploration to one fifth
            # of remaining time, leaving local correction a useful budget.
            pressed_deadline = time.monotonic()+.2*max(0., deadline-time.monotonic())
            context = evaluator.contexts[0][1] if evaluator.contexts else None
            try:
                unique.extend(pressed_search(tanks, initial, evaluator.evaluate, context, pressed_deadline))
            except Deadline:
                pass
            ranked = sorted(evaluator.measured.items(), key=lambda item: item[1])[:3]
            starts = [np.frombuffer(raw, dtype=float).copy() for raw, _ in ranked]
            starts.extend(unique)
            from stowage.solver.ballast_successive import search
            fsm = np.array([max(row.fsm_m4 for row in tank.table)*tank.content_density_kg_m3
                            for tank in tanks])
            weights = 1.+fsm/max(float(fsm.max(initial=0.)), 1.)
            search(starts, evaluator.evaluate, deadline, seed, slack_weights=weights)
        return outcome('not_found', parts=evaluator.best_parts, cause='search:bounded_exhaustion')
    except Found:
        if evaluator is not None and evaluator.accepted is not None and evaluator.accepted_report.passed:
            return outcome('ok', candidate=evaluator.accepted, parts=evaluator.accepted_report.parts)
        return outcome('not_found', cause='checker:no_acceptance_witness')
    except (Deadline, ExactDeadline):
        return outcome('not_found', parts=evaluator.best_parts if evaluator is not None else {},
                       cause='timeout:'+diagnostics.get('stage', 'search'))
    except (ValueError, ArithmeticError, KeyError, TypeError, FileNotFoundError, RuntimeError) as error:
        diagnostics['search_error'] = f'{type(error).__name__}: {error}'
        return outcome('not_found', parts=evaluator.best_parts if evaluator is not None else {},
                       cause='unknown:required_calculation')

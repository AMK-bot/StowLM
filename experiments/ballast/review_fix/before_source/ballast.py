"""Ballast repair with exact local slopes and an independent hard watchdog.

First press symmetric tank sets full, trim each with a bounded exact peak-fill
search, and correct list with a heeling pair. This changes the search order,
never the physics or acceptance limits. Next try 3.7's manifest-specific,
narrow-band LP with margins greater than its measured errors. If necessary,
a successive linear program measures slopes of
only the currently violated exact quantities and makes bounded tank changes.
Cheap floating-equilibrium probes cover draught, trim, list and liquid-corrected
GM; GZ areas/weather and strength are recomputed when those quantities are active.
Every accepted condition passes all four exact checker parts. No failed local
model or timed-out search is an infeasibility proof.

Empty/full endpoints are preferred as a secondary objective, since fewer slack
tanks reduce free surface. Actual tank geometry and slack FSM remain in every
exact probe. No pump limits are invented: the frozen schema has none. Sources:
IMO MSC.267(85), Part B 3.1; Conn, Gould & Toint, Trust Region Methods (2000),
chapter 15 (successive linear models verified against the original functions).

The public call runs in a reusable, dedicated process. Clock checks inside
Python physics raise a non-numerical deadline exception; an independent parent
watchdog can also stop a native call or blocked IPC. Only this module's recorded
worker is ever stopped. A passing condition is returned immediately, so no known
feasible result is discarded in pursuit of a better ballast tie-break.
"""
from __future__ import annotations

import math
import time

import numpy as np

from stowage.frozen import current_provenance, frozen
from stowage.model import BallastCondition, BallastOutcome, Instance, MovePlan, PartReport
from stowage.solver.ballast_deadline import (
    BallastDeadline, ExactDeadline, exact_budget, run_with_deadline,
)
from stowage.solver.ballast_search import Deadline, Found
from stowage.solver.ballast_successive import search


def _provenance(instance, plan, seed):
    return current_provenance(created_by='solver.finish_ballast', seed=seed).merge(
        instance.provenance, plan.provenance, parent_ids=[instance.id, plan.id])


def _validated_replay(instance, plan):
    from stowage.moves import apply_plan

    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise ValueError('Ballast requires a legal move sequence: '+
                         '; '.join(v.rule_id for v in replay.violations))
    return replay


@frozen("solver.finish_ballast", owner="4.2", implemented=True)
def finish_ballast(instance: Instance, plan: MovePlan, *, time_limit_s: float = 30.0, seed: int = 0) -> BallastOutcome:
    """Return the plan with departure ballast, or a proof/flag that none exists.

    - ``plan.ballast`` already set → ``status="kept"``, plan unchanged (the planner owns its ballast; the checker
      still judges it).
    - Otherwise validate moves first: illegal sequences raise ValueError even with no search budget.
    - Otherwise ``ok`` (ballast.source = "finisher", physics parts pass), ``infeasible_proven`` (with ``proof``), or
      ``not_found``. Never raises for an unballastable stow.
    Deterministic for a given seed; respects ``time_limit_s`` (wall clock).
    """
    started = time.monotonic()
    provenance = _provenance(instance, plan, seed)
    if plan.ballast is not None:
        return BallastOutcome(status='kept', plan=plan, provenance=provenance,
                              wall_time_s=time.monotonic()-started)
    # Move validity is an input contract, independent of whether there is time
    # to search for ballast. Keep it outside the worker deadline so a tiny
    # budget cannot turn an invalid plan into an inconclusive physics result.
    _validated_replay(instance, plan)
    if not math.isfinite(time_limit_s) or time_limit_s <= 0:
        return BallastOutcome(status='not_found', provenance=provenance,
                              wall_time_s=time.monotonic()-started)
    deadline = started+time_limit_s
    try:
        # Reserve a small allowance to serialize the completed result. The
        # parent's deadline is authoritative even if a native solver hangs.
        result = run_with_deadline(_finish_ballast, (instance, plan),
            {'time_limit_s': time_limit_s, 'seed': seed,
             '_deadline': deadline-min(.05, time_limit_s*.05)}, deadline=deadline)
    except (BallastDeadline, ExactDeadline):
        result = BallastOutcome(status='not_found', provenance=provenance)
    merged = provenance.merge(result.provenance)
    updates = {'provenance': merged, 'wall_time_s': time.monotonic()-started}
    if result.plan is not None:
        updates['plan'] = result.plan.model_copy(update={
            'provenance': merged.merge(result.plan.provenance)})
    return result.model_copy(update=updates)


def _normal_margin(name, key, value, instance, report):
    if 'mrad' in key:
        scale = .05
    elif key.endswith('rad'):
        scale = max(instance.vessel.hull.max_list_rad, .01) if key == 'list_rad' else .1
    elif 'kg' in key:
        scale = max(1., abs(report.metrics.get('displacement_kg', 1.)))
    elif key == 'blind_distance_m':
        scale = max(1., report.metrics.get('limit_m', 1.))
    else:
        scale = 1.
    return float(value)/scale if math.isfinite(value) else -1e6


def _finish_ballast(instance, plan, *, time_limit_s=30., seed=0, _deadline=None):
    """Worker implementation; directly callable by physics-isolated unit tests."""
    from stowage.checker.stability import check_stability
    from stowage.checker.strength import check_strength
    from stowage.checker.visibility import check_visibility
    from stowage.checker.lashing import check_lashing
    from stowage.linear.model import build_linear_model, solve_ballast_lp
    from stowage.linear.bands import band_for_instance
    from stowage.solver.ballast_physics import ExactContext
    from stowage.solver.ballast_proof import prove_infeasible

    started = time.monotonic()
    deadline = started+time_limit_s if _deadline is None else _deadline
    provenance = _provenance(instance, plan, seed)
    parts, best_parts, accepted = {}, {}, None
    iterations, best_merit = 0, math.inf
    def outcome(status, **kwargs):
        return BallastOutcome(status=status, wall_time_s=time.monotonic()-started,
                              provenance=provenance, **kwargs)
    if plan.ballast is not None:
        return outcome('kept', plan=plan)
    replay = _validated_replay(instance, plan)
    if not math.isfinite(time_limit_s) or time_limit_s <= 0:
        return outcome('not_found')
    try:
        with exact_budget(deadline):
            ship = replay.final_state.ship
            proof = prove_infeasible(instance, ship)
            if proof is not None:
                return outcome('infeasible_proven', proof=proof)
            tanks = sorted((t for t in instance.vessel.tanks if t.adjustable), key=lambda t:t.id)
            capacity = np.array([t.capacity_m3*t.content_density_kg_m3 for t in tanks])
            initial = np.array([ship.tank_masses_kg.get(t.id, 0.) for t in tanks])/capacity
            initial = np.clip(initial, 0., 1.)
            fills = []
            try:
                context = ExactContext(instance, ship, deadline) if tanks and instance.vessel.hull is not None else None
            except (ValueError, ArithmeticError, KeyError, TypeError, FileNotFoundError):
                context = None
            fixed = {t.id: ship.tank_masses_kg.get(t.id,0.) for t in instance.vessel.tanks if not t.adjustable}
            cache = {}
            checkers = {'stability': check_stability, 'strength': check_strength,
                        'visibility': check_visibility, 'lashing': check_lashing}
            loading_keys = {'stability/'+key for key in ('gm_m','trim_m','list_rad',
                'draught_aft_m','draught_fwd_m','draught_fwd_min_m','port_draught_m')}
            weather_keys = {'stability/'+key for key in ('weather_beam_draught',
                'weather_kg_lower','weather_kg_upper','weather_period','weather_r')}

            def evaluate(fractions, active_keys=None):
                nonlocal iterations, parts, best_parts, best_merit, accepted
                if time.monotonic() >= deadline:
                    raise Deadline
                fractions = np.clip(np.asarray(fractions,dtype=float),0.,1.)
                selected = None if active_keys is None else set(active_keys)
                key = (fractions.tobytes(), None if selected is None else tuple(sorted(selected)))
                if key in cache:
                    return cache[key]
                iterations += 1
                margins = {}
                if context is not None and (selected is None or selected & (loading_keys|weather_keys)):
                    try:
                        fast_selected = None if selected is None else selected-{'stability/weather_table_applicability'}
                        margins = context.margins(fractions, active_keys=fast_selected)
                        if (selected is not None and 'stability/weather_table_applicability' in selected
                                and any(v < 0. for k,v in margins.items() if k in weather_keys)):
                            # A failed necessary range condition makes the
                            # Code's conjunction false without another GZ curve.
                            margins['stability/weather_table_applicability'] = -1.
                    except (ValueError, ArithmeticError):
                        # No equilibrium here is not an impossibility proof.
                        margins = {}
                    if (selected is not None and selected <= (loading_keys|weather_keys|{'stability/weather_table_applicability'})
                            and selected <= margins.keys()):
                        cache[key] = margins
                        return margins
                    if selected is None and any(v < -1e-9 for v in margins.values()):
                        # These are exact loading clearances, not a completed
                        # stability report: areas/weather remain unevaluated.
                        merit = sum(min(v,0.)**2 for v in margins.values())
                        if merit < best_merit:
                            best_merit = merit
                            best_parts = {'stability': PartReport(name='stability', ran=False,
                                error='Exact loading probes only; full GZ/weather check not completed',
                                margins={k.split('/',1)[1]: v*instance.vessel.hull.max_list_rad
                                    if k.endswith('/list_rad') else v for k,v in margins.items()})}
                        cache[key] = margins
                        return margins
                masses = {t.id: float(x*c) for t,x,c in zip(tanks,fractions,capacity)}
                candidate = ship.model_copy(update={'tank_masses_kg': {**fixed, **masses}})
                reports = {}
                for name, checker in checkers.items():
                    if time.monotonic() >= deadline:
                        raise Deadline
                    if selected is not None and not any(k.startswith(name+'/') for k in selected):
                        continue
                    report = checker(instance, candidate, call_index=plan.call_index)
                    reports[name] = report
                    for k,v in report.margins.items():
                        label = name+'/'+k
                        if selected is None or label in selected:
                            margins[label] = _normal_margin(name,k,v,instance,report)
                    if name == 'strength' and instance.vessel.hull is not None:
                        for station in instance.vessel.hull.stations:
                            position = format(station.x_m,'.12g')
                            for prefix, lower, upper in (
                                ('sf_n',station.min_shear_n,station.max_shear_n),
                                ('bm_nm',station.min_bending_nm,station.max_bending_nm)):
                                value = report.metrics.get(prefix+'@'+position)
                                if value is None:
                                    continue
                                for side, clearance, scale in (
                                    ('upper',upper-value,abs(upper)),
                                    ('lower',value-lower,abs(lower))):
                                    label = f'strength/{prefix}@{position}/{side}'
                                    if selected is None or label in selected:
                                        margins[label] = clearance/max(scale,1.)
                    if (name == 'stability' and context is not None and
                            report.margins.get('weather_table_applicability',0.) < 0.):
                        margins.update(context.margins(fractions, active_keys=weather_keys))
                    # Discrete applicability and unavailable calculations have
                    # no invented gradient. They remain hard negative flags.
                    failures = [v for v in report.violations if v.severity == 'hard']
                    if not report.ran or (failures and not any(v < 0 for k,v in margins.items() if k.startswith(name+'/'))):
                        margins[name+'/unavailable' if not report.ran else name+'/hard_violation'] = -1.
                if selected is not None:
                    cache[key] = margins
                    return margins
                parts = reports
                merit = sum(min(v,0.)**2 for v in margins.values())
                if merit < best_merit:
                    best_merit, best_parts = merit, reports.copy()
                if len(reports) == 4 and all(p.ran and not any(v.severity=='hard' for v in p.violations)
                                              for p in reports.values()):
                    accepted = plan.model_copy(update={'ballast': BallastCondition(
                        tank_masses_kg=masses,source='finisher'), 'provenance': provenance})
                    raise Found
                cache[key] = margins
                return margins

            # Try low-free-surface basins before paying for a fitted LP. The
            # last fifth remains available for the general violated-quantity
            # loop if these discrete tank sets do not cover this vessel/stow.
            from stowage.solver.ballast_pressed import search as pressed_search
            pressed_deadline = time.monotonic()+.8*max(0., deadline-time.monotonic())
            try:
                fills.extend(pressed_search(tanks, initial, evaluate, context, pressed_deadline))
            except Deadline:
                pass
            # Preserve useful partial work when the first strategy's own
            # sub-budget ends; exact acceptance still belongs to evaluate.
            measured = [(sum(min(v,0.)**2 for v in margins.values()), key[0])
                        for key,margins in cache.items() if key[1] is None]
            fills.extend(np.frombuffer(raw,dtype=float).copy()
                         for _,raw in sorted(measured,key=lambda row:row[0])[:3])
            anchor = fills[0] if fills else initial
            try:
                # Full manifest held fixed; local tank windows and trim narrow
                # the hydrostatic fit. Local LP rejection cannot prove anything
                # about fills outside these explicitly modelled windows.
                lp_deadline = min(deadline, time.monotonic()+min(.75, time_limit_s*.15))
                with exact_budget(lp_deadline):
                    if instance.vessel.hull is None:
                        raise ValueError('Missing hull data')
                    windows = {t.id: (max(0., x-.25)*c, min(1., x+.25)*c)
                               for t,x,c in zip(tanks,anchor,capacity)}
                    band = band_for_instance(instance, ballast_windows_kg=windows,
                        max_abs_trim_m=min(instance.vessel.hull.max_trim_m, .5))
                    model = build_linear_model(instance, band=band)
                    lp = solve_ballast_lp(model, ship, instance, margin_scale=1.001)
                    if lp.status == 'optimal':
                        fills.append(np.array([lp.tank_masses_kg.get(t.id,0.) for t in tanks])/capacity)
            except (ExactDeadline, ValueError, ArithmeticError, KeyError, TypeError, FileNotFoundError):
                pass
            fills.append(initial)

            fsm = np.array([max(point.fsm_m4 for point in t.table)*t.content_density_kg_m3 for t in tanks])
            slack_weights = 1.+fsm/max(float(fsm.max(initial=0.)),1.)
            search(fills, evaluate, deadline, seed, slack_weights=slack_weights)
            proof = prove_infeasible(instance, ship, exact_parts=best_parts)
            if proof is not None:
                return outcome('infeasible_proven', proof=proof, parts=best_parts, iterations=iterations)
    except Found:
        return outcome('ok', plan=accepted, parts=parts, iterations=iterations)
    except (Deadline, ExactDeadline):
        pass
    return outcome('not_found', parts=best_parts or parts, iterations=iterations)

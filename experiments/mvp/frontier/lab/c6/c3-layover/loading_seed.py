"""Cold work-bounded affine loading tank seed, with native loading calibration.

The assembler is a frozen C4 copy. Two local affine LPs may be solved; neither
area nor securing is evaluated here. Only the unchanged full checker may pass.
"""
from __future__ import annotations
import math
from time import monotonic
import warnings
import numpy as np
from scipy.optimize import linprog, OptimizeWarning


def _probe(instance, ship, *, search_gm_floor=None):
    """Native finite-angle liquid equilibrium at the three ordinary D57 states."""
    from stowage.checker import stability
    from stowage.checker.visibility import _loading_condition
    from stowage.solver.ballast_v4_evaluator import conditions
    rows, quality = [], []
    for phase, inst, state in conditions(instance, ship):
        c = _loading_condition(inst, state)
        lo_trim, hi_trim, lo_mass, hi_mass = stability._loading_policy(inst)
        hull = inst.vessel.hull
        maximum = min(hull.max_draught_m, inst.port.max_draught_m or hull.max_draught_m)
        minimum_gm = max(stability._minimum_loading_gm(inst),
                         float(inst.rule_overrides.get('iscode.a2_2.initial_gm', {}).get('minimum', .15)),
                         float(search_gm_floor) if search_gm_floor is not None else -math.inf)
        trim = float(c.t_aft_m-c.t_fwd_m)
        # SI residuals with mass normalized to the candidate displacement.
        residuals = dict(draft_aft=max(0., c.t_aft_m-maximum),
                         draft_fwd=max(0., c.t_fwd_m-maximum),
                         draft_fwd_min=max(0., hull.min_draught_fwd_m-c.t_fwd_m),
                         trim_upper=max(0., trim-hi_trim), trim_lower=max(0., lo_trim-trim),
                         mass_upper=max(0., c.mass_kg-hi_mass)/c.mass_kg,
                         mass_lower=max(0., lo_mass-c.mass_kg)/c.mass_kg,
                         gm=max(0., minimum_gm-c.gm_m),
                         list=max(0., abs(c.heel_rad)-hull.max_list_rad))
        quality.extend(residuals.values())
        rows.append(dict(phase=phase, draft_m=[float(c.t_aft_m), float(c.t_fwd_m)],
                         trim_m=trim, gm_m=float(c.gm_m), heel_rad=float(c.heel_rad),
                         lcg_m=float(c.lcg_m), tcg_m=float(c.tcg_m), kg_m=float(c.kg_m),
                         mass_kg=float(c.mass_kg), residuals=residuals))
    return rows, (sum(v > 1e-7 for v in quality), max(quality, default=0.), sum(quality))


def _corrections(probes, predicted):
    """Difference between actual and affine draft/trim, for policy RHS correction."""
    out = {}
    for row, pair in zip(probes, predicted):
        phase = row['phase']
        out[phase+'/draft_0'] = float(row['draft_m'][0]-pair[0])
        out[phase+'/draft_1'] = float(row['draft_m'][1]-pair[1])
        out[phase+'/trim'] = float(row['trim_m']-(pair[0]-pair[1]))
    return out


def loading_seed(instance, plan, *, max_lp_iterations=2000, search_gm_floor=None,
                 max_calibrations=2):
    """Return the best native-loading candidate from at most two calibrated LPs.

    No wall stopping criterion, area oracle or stored recipe is used. Half-fill
    tank arms/FSM remain approximations. Unknown local LP status returns the
    best prior candidate, never a physical infeasibility claim.
    """
    from ballast_loading import _assemble, affine_gm
    from stowage.solver.ballast import _validated_replay
    from stowage.checker.full import _ballast
    from stowage.solver.ballast_physics import ExactContext
    from stowage.solver.ballast_v4_evaluator import conditions
    from stowage.model import BallastCondition
    started = monotonic()
    replayed = _validated_replay(instance, plan).final_state.ship
    ship, invalid = _ballast(instance, plan, replayed)
    if invalid:
        return None, dict(status='invalid ballast declaration')
    contexts = [(phase, ExactContext(inst, state, float('inf')))
                for phase, inst, state in conditions(instance, ship)]
    if any(ctx.variable_consumables for _, ctx in contexts):
        return None, dict(status='unknown: adjustable consumable restriction')
    initial, initial_quality = _probe(instance, ship, search_gm_floor=search_gm_floor)
    references = [tuple(row['draft_m']) for row in initial]
    initial_fractions = np.array([ship.tank_masses_kg.get(t.id, 0.)/capacity
        for t, capacity in zip(contexts[0][1].tanks, contexts[0][1].capacity)])
    def gm_calibration(probes, refs, fractions):
        metadata, values = [], {}
        for row, reference, (phase, ctx) in zip(probes, refs, contexts):
            estimate = affine_gm(ctx, reference, fractions)
            bias = row['gm_m']-estimate
            values[phase+'/gm'] = bias
            metadata.append(dict(phase=phase, affine_gm_m=estimate,
                                 native_gm_m=row['gm_m'], native_minus_affine_m=bias))
        return values, metadata
    corrections, gm_metadata = gm_calibration(initial, references, initial_fractions)
    trace, best, best_quality = [], None, None
    active_floor = search_gm_floor
    n = len(contexts[0][1].tanks)
    last_status, error = None, None
    for turn in range(max(0, min(2, int(max_calibrations)))):
        try:
            a, b, names, calibration = _assemble(contexts, references=references,
                corrections=corrections, search_gm_floor=active_floor)
            c = np.r_[np.full(n, .001), np.zeros(a.shape[1]-n)]
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', OptimizeWarning)
                solved = linprog(c, A_ub=a, b_ub=b, bounds=(None, None), method='highs',
                    options={'threads': 1, 'maxiter': int(max_lp_iterations)})
            last_status = int(solved.status)
            row = dict(iteration=turn, status=last_status, iterations=int(solved.nit),
                       references_m=[list(pair) for pair in references], corrections=dict(corrections),
                       gm_calibration=gm_metadata, active_gm_floor=active_floor, assembly=calibration)
            trace.append(row)
            if solved.status != 0:
                if active_floor is not None and turn+1 < min(2, int(max_calibrations)):
                    row['fallback'] = 'requested-floor local LP unknown; next call uses native statutory/manual floor'
                    active_floor = None
                    continue
                break
            masses = {tank.id: float(fraction*capacity) for tank, fraction, capacity in
                      zip(contexts[0][1].tanks, np.clip(solved.x[:n], 0., 1.), contexts[0][1].capacity)}
            candidate = plan.model_copy(update={'ballast': BallastCondition(tank_masses_kg=masses, source='finisher')})
            selected_ship, invalid = _ballast(instance, candidate, replayed)
            if invalid:
                row['error'] = 'invalid affine recipe'; break
            probes, quality = _probe(instance, selected_ship, search_gm_floor=search_gm_floor)
            predicted = np.asarray(solved.x[n:]).reshape(len(contexts), 2)
            row.update(predicted_drafts_m=predicted.tolist(), native=probes,
                       native_quality=list(quality), masses_kg=masses)
            if best_quality is None or quality < best_quality:
                best, best_quality = candidate, quality
            if quality[0] == 0:
                break
            # Keep the latest true hull reference; correct systematic draft and
            # trim residuals caused by approximate half-fill tank centroids.
            references = [tuple(value['draft_m']) for value in probes]
            corrections = _corrections(probes, predicted)
            gm_values, gm_metadata = gm_calibration(probes, references, np.clip(solved.x[:n], 0., 1.))
            corrections.update(gm_values)
        except (ValueError, KeyError, TypeError, ArithmeticError, RuntimeError) as exc:
            error = f'{type(exc).__name__}: {exc}'
            break
    return best, dict(status=last_status, max_lp_iterations=int(max_lp_iterations),
        max_calibrations=max(0, min(2, int(max_calibrations))), lp_calls=len(trace),
        search_gm_floor=search_gm_floor, trace=trace, initial_loading=initial,
        initial_quality=list(initial_quality), best_native_quality=list(best_quality) if best_quality is not None else None,
        error=error, wall_s=monotonic()-started, outward=False, physical_infeasibility_proven=False,
        full_checker_required=True)

# Copied from experiments/mvp/frontier/lab/c2/c2-benders/ballast.py; local LP remains guidance only.
"""Small shared-ballast LP, with explicitly qualified infeasibility.

The hull and tank integrations come from the checker's own providers. Hull
buoyancy and longitudinal buoyancy moment are linearized in the two drafts;
half-fill tank arms, GM and list are approximations. Their approximate Farkas
diagnostic is NEVER a physical certificate or a rigorous local-LP proof.
Only exact mass rows form an outward relaxation. No checker hooks are changed.
"""
from __future__ import annotations

import math
import time
import warnings

import numpy as np
from scipy.optimize import linprog, OptimizeWarning


def _lp(c, a, b, *, equal=None, rhs=None, seconds=1.):
    # scipy forwards this native HiGHS option; it has no public scipy spelling.
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', OptimizeWarning)
        return linprog(c, A_ub=a, b_ub=b, A_eq=equal, b_eq=rhs,
                       bounds=(None, None), method='highs',
                       options={'threads': 1, 'time_limit': max(.001, seconds)})


def farkas(a, b, *, seconds=.5):
    """Return an approximate floating Farkas diagnostic, never a proof.

    Variable bounds must already appear in A*x<=b. Row normalization limits
    mixed physical units. The residual is checked but is allowed to be nonzero;
    it is not corrected over a variable box or by interval arithmetic. Thus
    even the local-LP interpretation is numerical evidence, not rigorous proof.
    """
    a, b = np.asarray(a, float), np.asarray(b, float)
    scale = np.maximum(np.maximum(np.abs(a).max(axis=1), np.abs(b)), 1.)
    aa, bb = a/scale[:, None], b/scale
    m = len(b)
    # y>=0 as explicit inequalities, because _lp uses free variables.
    solved = _lp(bb, -np.eye(m), np.zeros(m),
                 equal=np.vstack([aa.T, np.ones(m)]),
                 rhs=np.r_[np.zeros(a.shape[1]), 1.], seconds=seconds)
    if solved.status != 0:
        return None
    y = np.maximum(solved.x, 0.)
    residual = float(np.max(np.abs(y@aa)))
    contradiction = float(y@bb)
    if residual > 1e-8 or contradiction >= -1e-8:
        return None
    return {'multipliers': (y/scale).tolist(),
            'normalized_residual_inf': residual,
            'normalized_contradiction': contradiction,
            'numeric_qualification': 'approximate floating Farkas diagnostic; nonzero residual allowed; not a proof'}


def mass_relaxation(contexts):
    """Exact mass/capacity inequalities, a genuine outward relaxation."""
    from stowage.checker import stability
    n = len(contexts[0][1].tanks)
    a, b, names = [], [], []
    for phase, ctx in contexts:
        _, _, lo, hi = stability._loading_policy(ctx.instance)
        capacity, base = ctx.capacity, float(ctx.base.mass_kg)
        # Checker rejects only below -1e-10 normalized mass margin. Widen
        # the outer bounds by that tolerance plus floating arithmetic guard.
        guard = max(1e-5, (base+float(capacity.sum()))*2e-10)
        if math.isfinite(hi):
            a.append(capacity); b.append(hi-base+guard); names.append(phase+'/mass_upper')
        if math.isfinite(lo):
            a.append(-capacity); b.append(base-lo+guard); names.append(phase+'/mass_lower')
    for j in range(n):
        row = np.zeros(n); row[j] = 1.
        a.extend([row, -row]); b.extend([1., 0.])
        names.extend([f'tank_{j}/upper', f'tank_{j}/lower'])
    return np.asarray(a), np.asarray(b), names


def _assemble(contexts, references=None, corrections=None, search_gm_floor=None):
    """Return affine LP rows; references are aft/forward draft pairs."""
    from stowage.checker import stability
    from stowage.checker.stability_sounding import sounding
    n = len(contexts[0][1].tanks)
    variables = n+2*len(contexts)
    rows, rhs, names, calibration = [], [], [], []

    def upper(row, bound, name):
        if math.isfinite(bound):
            scale = max(float(np.max(np.abs(row))), abs(float(bound)), 1.)
            rows.append(row/scale); rhs.append(float(bound)/scale); names.append(name)

    def equality(row, value, name):
        upper(row, value, name+'/positive')
        upper(-row, -value, name+'/negative')

    for j in range(n):
        row = np.zeros(variables); row[j] = 1.
        upper(row, 1., f'tank_{j}/upper'); upper(-row, 0., f'tank_{j}/lower')
    for k, (phase, ctx) in enumerate(contexts):
        hull, inst, geometry = ctx.instance.vessel.hull, ctx.instance, ctx.direct_geometry
        ta, tf = references[k] if references else (hull.design_draught_m, hull.design_draught_m)
        reference = geometry.full_submerged(ta, tf, 0., ctx.rho)
        perturbation = .002
        shifted = [geometry.full_submerged(ta+perturbation, tf, 0., ctx.rho),
                   geometry.full_submerged(ta, tf+perturbation, 0., ctx.rho)]
        buoyancy = reference.displacement_kg
        moment = buoyancy*reference.lcb_m
        db = np.array([(s.displacement_kg-buoyancy)/perturbation for s in shifted])
        dm = np.array([(s.displacement_kg*s.lcb_m-moment)/perturbation for s in shifted])
        arms, fsms = [], []
        for tank in ctx.tanks:
            point = sounding(ctx.solids[tank.id], .5*tank.capacity_m3)
            arms.append([point.lcg_m, point.tcg_m, point.vcg_m])
            fsm = point.fsm_m4*tank.content_density_kg_m3
            fsms.append(fsm if fsm/hull.lightship_mass_kg >= .01 else 0.)
        arms = np.asarray(arms)
        # Keep the half-fill FSM as a constant reserve. It is neither a
        # global maximum nor an outward necessary row. Full/empty exact
        # candidates recover the true discontinuous correction on recheck.
        fsm = float(ctx.base.fsm_kgm)+sum(fsms)
        km = reference.vcb_m+reference.bm_t_m
        c = ctx.capacity
        base = ctx.base.mass_kg
        mx, my, mz = ctx.base_moments
        mass_row = np.zeros(variables); mass_row[:n] = c
        xrow = np.zeros(variables); xrow[:n] = c*arms[:, 0]
        yrow = np.zeros(variables); yrow[:n] = c*arms[:, 1]
        zrow = np.zeros(variables); zrow[:n] = c*arms[:, 2]
        draft = slice(n+2*k, n+2*k+2)
        r = mass_row.copy(); r[draft] -= db
        equality(r, buoyancy-db@[ta, tf]-base, phase+'/buoyancy')
        r = xrow.copy(); r[draft] -= dm
        equality(r, moment-dm@[ta, tf]-mx, phase+'/longitudinal_equilibrium')
        lo_trim, hi_trim, lo_mass, hi_mass = stability._loading_policy(inst)
        upper(mass_row, hi_mass-base, phase+'/mass_upper')
        upper(-mass_row, base-lo_mass, phase+'/mass_lower')
        trim = np.zeros(variables); trim[n+2*k] = 1.; trim[n+2*k+1] = -1.
        offset = (corrections or {}).get(phase+'/trim', 0.)
        upper(trim, hi_trim-offset-.01, phase+'/trim_upper')
        upper(-trim, -lo_trim+offset-.01, phase+'/trim_lower')
        maximum = min(hull.max_draught_m, inst.port.max_draught_m or hull.max_draught_m)
        for end in (0, 1):
            r = np.zeros(variables); r[n+2*k+end] = 1.
            offset = (corrections or {}).get(phase+f'/draft_{end}', 0.)
            upper(r, maximum-offset-.01, phase+f'/draft_{end}_upper')
            upper(-r, -(hull.min_draught_fwd_m if end else 0.)+offset-.01,
                  phase+f'/draft_{end}_lower')
        minimum_gm = max(stability._minimum_loading_gm(inst),
                         float(inst.rule_overrides.get('iscode.a2_2.initial_gm', {}).get('minimum', .15)))
        if search_gm_floor is not None:
            # Optional construction restriction. It is deliberately inward
            # and cannot enter mass_relaxation or justify any physical proof.
            minimum_gm = max(minimum_gm, search_gm_floor)
        stiffness = km*mass_row-zrow
        fixed_stiffness = km*base-mz-fsm
        upper(minimum_gm*mass_row-stiffness,
              fixed_stiffness-minimum_gm*base, phase+'/gm')
        tangent = math.tan(hull.max_list_rad)
        upper(yrow-tangent*stiffness, tangent*fixed_stiffness-my, phase+'/heel_positive')
        upper(-yrow-tangent*stiffness, tangent*fixed_stiffness+my, phase+'/heel_negative')
        calibration.append(dict(phase=phase, reference_drafts_m=[ta, tf],
            buoyancy_kg=buoyancy, buoyancy_derivative_kg_m=db.tolist(),
            longitudinal_derivative_kg=dm.tolist(), km_m=km, fsm_reserve_kgm=fsm))
    return np.asarray(rows), np.asarray(rhs), names, calibration


def _probe(contexts, fractions):
    """Require the complete loading-margin key set at every voyage phase."""
    from stowage.solver.ballast_v4_evaluator import LOADING_KEYS
    margins = {}
    complete = True
    for phase, ctx in contexts:
        local = ctx.margins(fractions)
        required = set(LOADING_KEYS)
        if ctx.instance.port.max_draught_m is None:
            required.discard('stability/port_draught_m')
        complete &= required <= local.keys()
        margins.update({phase+'/'+key: float(value) for key, value in local.items()})
    feasible = complete and all(value >= 1e-6 for key, value in margins.items()
                                if 'displacement_' not in key) and all(value >= 0. for value in margins.values())
    return margins, bool(feasible), bool(complete)


def solve_ballast(instance, plan, *, budget_s=3., exact_recheck=False,
                  max_iterations=2, search_gm_floor=None):
    """Return diagnostics and candidate_plan; only `fixed` is a full pass.

    `loading_feasible` means ordinary nonlinear loading probes succeeded at
    departure/midpoint/arrival. GZ/weather, containership second check, sight,
    securing, strength, operations and static rules still need full checking.
    `unknown` includes an infeasible local LP and an expired time budget.
    `search_gm_floor` adds an inward construction restriction to the affine
    GM row only; ordinary nonlinear probes still use the native GM policy.
    """
    from stowage.checker import stability
    from stowage.model import BallastCondition
    from stowage.solver.ballast import _validated_replay
    from stowage.solver.ballast_physics import ExactContext
    from stowage.solver.ballast_v4_evaluator import conditions
    from stowage.solver.ballast_deadline import ExactDeadline
    started = time.monotonic(); deadline = started+max(0., budget_s)
    out = dict(status='unknown', candidate_plan=None, proof=None, outward=False,
               full_checker_passed=None, loading_feasible=False, lp_s=0.,
               setup_s=0., exact_probe_s=0., iterations=[],
               search_gm_floor_m=search_gm_floor,
               approximation='local hull Jacobian; half-fill tank arms and FSM; not outward')
    contexts = []
    try:
        if search_gm_floor is not None and (not math.isfinite(search_gm_floor) or search_gm_floor < 0.):
            raise ValueError('search_gm_floor must be finite and nonnegative')
        from stowage.frozen import active_stubs
        if active_stubs():
            raise ValueError('active frozen-interface override; ordinary physics/proof required')
        ship = _validated_replay(instance, plan).final_state.ship
        for phase, inst, state in conditions(instance, ship):
            ctx = ExactContext(inst, state, deadline)
            if ctx.variable_consumables:
                raise ValueError('adjustable consumables require candidate-dependent voyage derivation')
            contexts.append((phase, ctx))
        out['setup_s'] = time.monotonic()-started
        n = len(contexts[0][1].tanks)
        identifiers = [tank.id for tank in contexts[0][1].tanks]
        capacities = contexts[0][1].capacity
        if any([tank.id for tank in ctx.tanks] != identifiers or
               not np.array_equal(ctx.capacity, capacities) for _, ctx in contexts[1:]):
            raise ValueError('voyage states do not share identical adjustable tanks and capacities')
        aa, bb, nn = mass_relaxation(contexts)
        before = time.monotonic()
        outer = _lp(np.zeros(n), aa, bb, seconds=deadline-before)
        out['lp_s'] += time.monotonic()-before
        if outer.status == 2:
            # Exact mass bounds, directly checkable by interval intersection,
            # avoid promoting a merely numerical Farkas residual to proof.
            from stowage.checker import stability
            capacity_total = float(contexts[0][1].capacity.sum())
            low, high, evidence = 0., capacity_total, []
            for phase, ctx in contexts:
                _, _, lo, hi = stability._loading_policy(ctx.instance)
                guard = max(1e-5, (ctx.base.mass_kg+capacity_total)*2e-10)
                low = max(low, lo-ctx.base.mass_kg-guard)
                high = min(high, hi-ctx.base.mass_kg+guard)
                evidence.append(dict(phase=phase, fixed_mass_kg=ctx.base.mass_kg,
                                     required_minimum_kg=lo, required_maximum_kg=hi))
            if low > high+1e-4:
                out.update(status='infeasible_proven', outward=True,
                    proof=dict(kind='mass_interval', minimum_shared_ballast_kg=low,
                               maximum_shared_ballast_kg=high, phases=evidence))
                return out
        capacity = contexts[0][1].capacity
        references, corrections = None, {}
        for iteration in range(max_iterations):
            if time.monotonic() >= deadline:
                raise ExactDeadline
            aa, bb, nn, calibration = _assemble(contexts, references, corrections, search_gm_floor)
            cost = np.r_[capacity/max(float(capacity.sum()), 1.), np.zeros(6)]
            before = time.monotonic()
            result = _lp(cost, aa, bb, seconds=deadline-before)
            elapsed = time.monotonic()-before; out['lp_s'] += elapsed
            record = dict(iteration=iteration, lp_status=int(result.status), lp_s=elapsed,
                          calibration=calibration, message=result.message)
            out['iterations'].append(record)
            if result.status != 0:
                out['reason'] = 'local LP infeasible' if result.status == 2 else 'LP did not complete'
                if result.status == 2 and time.monotonic() < deadline:
                    ray = farkas(aa, bb, seconds=min(.5, deadline-time.monotonic()))
                    if ray:
                        out['local_certificate'] = dict(kind='local_farkas', physical_proof=False,
                            A_ub=aa.tolist(), b_ub=bb.tolist(), row_names=nn, **ray)
                break
            fractions = np.clip(result.x[:n], 0., 1.)
            masses = {tank.id: float(fraction*cap) for tank, fraction, cap in
                      zip(contexts[0][1].tanks, fractions, capacity)}
            candidate = plan.model_copy(update={'ballast': BallastCondition(
                tank_masses_kg=masses, source='finisher')})
            out.update(candidate_plan=candidate, tank_masses_kg=masses)
            before = time.monotonic()
            margins, feasible, complete = _probe(contexts, fractions)
            out['exact_probe_s'] += time.monotonic()-before
            record.update(exact_margins=margins, probe_complete=complete, loading_feasible=feasible)
            out.update(exact_margins=margins, loading_feasible=feasible)
            if feasible:
                out.update(status='loading_feasible', reason='three nonlinear loading probes passed')
                break
            references = [(float(result.x[n+2*k]), float(result.x[n+2*k+1])) for k in range(3)]
            for k, (phase, ctx) in enumerate(contexts):
                pfx = phase+'/stability/'
                hull = ctx.instance.vessel.hull
                ta, tf = references[k]
                _, hi, _, _ = stability._loading_policy(ctx.instance)
                if pfx+'trim_m' in margins:
                    corrections[phase+'/trim'] = hi-margins[pfx+'trim_m']-(ta-tf)
                for end, name, predicted in ((0, 'draught_aft_m', ta), (1, 'draught_fwd_m', tf)):
                    if pfx+name in margins:
                        corrections[phase+f'/draft_{end}'] = hull.max_draught_m-margins[pfx+name]-predicted
        if out['status'] == 'unknown' and 'reason' not in out:
            out['reason'] = 'local candidates failed nonlinear loading recheck'
        if exact_recheck and out['candidate_plan'] is not None:
            from collections import Counter
            from frozen_checks import check
            before = time.monotonic()
            report = check(instance, out['candidate_plan'])
            out.update(full_checker_s=time.monotonic()-before, full_checker_passed=report.passed,
                       full_checker_report=report.model_dump(mode='json'),
                       full_checker_rules=sorted({v.rule_id for v in report.violations if v.severity == 'hard'}),
                       full_checker_rule_counts=dict(Counter(v.rule_id for v in report.violations
                                                             if v.severity == 'hard')),
                       full_checker_violations=[v.model_dump(mode='json') for v in report.violations
                                                if v.severity == 'hard'],
                       full_checker_physics={name: dict(metrics=dict(part.metrics),
                                                       margins=dict(part.margins),
                                                       ran=part.ran, error=part.error)
                                             for name, part in report.parts.items()
                                             if 'stability' in name or 'lashing' in name})
            if report.passed:
                out.update(status='fixed', reason='unchanged full checker passed')
    except ExactDeadline:
        out['reason'] = 'time budget exhausted; no physical infeasibility claim'
    except (ValueError, KeyError, TypeError, ArithmeticError, OSError) as error:
        out['reason'] = f'{type(error).__name__}: {error}'
    finally:
        out['wall_s'] = time.monotonic()-started
    return out

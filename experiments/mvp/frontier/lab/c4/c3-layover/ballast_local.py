"""Work-bounded ballast guidance and real-area local successive LP.

The loading seed uses the copied C2/engine local hull LP. Actual search
margins come from adapted C3 native physics separation, including the real
containership righting area, securing and visibility. These inward approximations are
guidance only: no failure proves infeasibility and only check() accepts.
"""
from __future__ import annotations

import os
from time import monotonic
import warnings

for _key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
             'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS'):
    os.environ[_key] = '1'

import numpy as np
from scipy.optimize import OptimizeWarning, linprog

from ballast_physics import PhysicsEvaluator


def _table_arm(tank, mass, axis):
    volume = mass/tank.content_density_kg_m3
    return float(np.interp(volume, [r.volume_m3 for r in tank.table],
                           [getattr(r, axis) for r in tank.table]))


def ballast_targets(instance):
    """Cheap moment targets: table interpolation, zero exact-oracle calls.

    The longitudinal reference is native even-keel hydrostatic LCB at the
    estimated departure displacement. Cargo cancels lightship and inherited
    tank/fixed-load offsets from this reference and inherited total transverse
    moment. Missing hydrostatic rows fall back to lightship LCG. This is a
    construction preference, never a physical certificate or substitution of
    GM for the real area criterion.
    """
    hull = instance.vessel.hull
    boxes = {b.id: b for b in instance.containers}
    cargo = (set(instance.load_list) |
             {p.container_id for p in instance.onboard.placements}) - set(instance.discharge_list)
    mass = sum(boxes[b].gross_mass_kg for b in cargo)
    water = sum(float(instance.onboard.tank_masses_kg.get(tank.id, 0.))
                for tank in instance.vessel.tanks)
    displacement = float(hull.lightship_mass_kg+water+mass+
                         sum(f.mass_kg for f in instance.vessel.fixed_loads))
    xref = float(hull.lightship_lcg_m)
    reference_source = 'lightship_lcg_fallback_no_native_hydrostatic_rows'
    table_density = float(getattr(hull, 'water_density_kg_m3', 1025.))
    density = float(getattr(getattr(instance, 'port', None),
                            'water_density_kg_m3', table_density))
    table_displacement = displacement*table_density/density
    rows = sorted(getattr(hull, 'hydrostatics', ()) or (),
                  key=lambda row: row.displacement_kg)
    if rows:
        masses = np.array([r.displacement_kg for r in rows], float)
        centres = np.array([r.lcb_m for r in rows], float)
        if np.isfinite(masses).all() and np.isfinite(centres).all():
            xref = float(np.interp(table_displacement, masses, centres))
            reference_source = 'native_even_keel_hydrostatic_lcb_interpolated_at_departure_displacement'
    mx_offset = float(hull.lightship_mass_kg*(hull.lightship_lcg_m-xref))
    my = float(hull.lightship_mass_kg*hull.lightship_tcg_m)
    for tank in instance.vessel.tanks:
        m = float(instance.onboard.tank_masses_kg.get(tank.id, 0.))
        mx_offset += m*(_table_arm(tank, m, 'lcg_m')-xref)
        my += m*_table_arm(tank, m, 'tcg_m')
    for fixed in instance.vessel.fixed_loads:
        mx_offset += fixed.mass_kg*(fixed.x_m-xref)
        my += fixed.mass_kg*fixed.y_m
    return dict(target_cargo_lcg_m=xref-mx_offset/max(1., mass),
                target_cargo_tcg_m=-my/max(1., mass), kg_weight=.12,
                cargo_mass_kg=float(mass), inherited_liquid_mass_kg=water,
                estimated_departure_displacement_kg=displacement,
                hydrostatic_table_displacement_kg=table_displacement,
                reference_source=reference_source,
                reference_lcg_m=xref, exact_physics_evaluations=0,
                provenance='native even-keel hydrostatics, lightship and tank capacity tables; inherited tanks; heuristic targets',
                area_securing_gate='real native area/securing/visibility margins in finish; no scalar-GM acceptance')


def cargo_cost(instance, box, slot, targets=None):
    from stowage.conventions import cells_of_slot
    targets = targets or ballast_targets(instance)
    cargo = next(b for b in instance.containers if b.id == box) if isinstance(box, str) else box
    cells = {c.id: c for c in instance.vessel.cells+instance.vessel.fixed_load_cells}
    supports = [cells[c] for c in cells_of_slot(slot)]
    x = sum(c.lcg_m for c in supports)/len(supports)
    y = sum(c.tcg_m for c in supports)/len(supports)
    z = max(c.vcg_base_m for c in supports)+cargo.height_m/2.
    return cargo.gross_mass_kg/1000.*(.002*(x-targets['target_cargo_lcg_m'])**2+
        .02*(y-targets['target_cargo_tcg_m'])**2+targets['kg_weight']*z)


def merit(values):
    negative = np.maximum(-np.asarray(values), 0.)
    return float(negative.sum()+.5*negative.max(initial=0.))


def useful(name):
    return not any(s in name for s in ('weather_table_applicability', 'unavailable', 'unrepresented'))


def difference_step(x, direction, radius=.03):
    """A one-sided difference staying inside its current 98% regime."""
    active = np.flatnonzero(direction)
    if not len(active):
        return 0.
    low = np.where(x[active] >= .98, .9800001, 0.)
    high = np.where(x[active] < .98, .9799999, 1.)
    quotients = np.array([(low-x[active])/direction[active],
                          (high-x[active])/direction[active]])
    lower = float(np.max(np.min(quotients, axis=0)))
    upper = float(np.min(np.max(quotients, axis=0)))
    up, down = max(0., upper), max(0., -lower)
    sign, available = (1., up) if up >= down else (-1., down)
    return sign*min(.01, radius*.25, available*.7) if available > 1e-5 else 0.


def loading_seed(instance, plan, *, max_lp_iterations=2000):
    """One affine loading LP; deterministic simplex-iteration work limit.

    No wall budget is used. Half-fill arms/FSM and one hull tangent are
    search restrictions. Its candidate still needs actual native physics.
    """
    from ballast_loading import _assemble
    from stowage.solver.ballast import _validated_replay
    from stowage.checker.full import _ballast
    from stowage.solver.ballast_physics import ExactContext
    from stowage.solver.ballast_v4_evaluator import conditions
    from stowage.model import BallastCondition
    replayed = _validated_replay(instance, plan).final_state.ship
    ship, invalid = _ballast(instance, plan, replayed)
    if invalid:
        return None, dict(status='invalid ballast declaration')
    contexts = [(phase, ExactContext(inst, state, float('inf')))
                for phase, inst, state in conditions(instance, ship)]
    if any(ctx.variable_consumables for _, ctx in contexts):
        return None, dict(status='unknown: adjustable consumable restriction')
    a, b, names, calibration = _assemble(contexts)
    n = len(contexts[0][1].tanks)
    c = np.r_[np.full(n, .001), np.zeros(a.shape[1]-n)]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', OptimizeWarning)
        solved = linprog(c, A_ub=a, b_ub=b, bounds=(None, None), method='highs',
                         options={'threads': 1, 'maxiter': int(max_lp_iterations)})
    metadata = dict(status=int(solved.status), iterations=int(solved.nit),
                    max_lp_iterations=max_lp_iterations, outward=False,
                    physical_infeasibility_proven=False)
    if solved.status != 0:
        return None, metadata
    masses = {tank.id: float(fraction*capacity) for tank, fraction, capacity in
              zip(contexts[0][1].tanks, np.clip(solved.x[:n], 0., 1.), contexts[0][1].capacity)}
    return plan.model_copy(update={'ballast': BallastCondition(tank_masses_kg=masses, source='finisher')}), metadata


def optimize(instance, plan, *, max_evaluations=28, max_iterations=2,
             max_lp_iterations=2000, trust_radius=.15, use_loading_seed=True):
    """Return best recipe, telemetry; acceptance is left to full checker.

    All exact-oracle calls are counted. The cache does not buy extra calls.
    LP limits are iteration counts; no deadline affects search decisions.
    """
    started = monotonic()
    trust_radius = float(trust_radius)
    if not np.isfinite(trust_radius) or trust_radius <= 0.:
        raise ValueError('trust_radius must be finite and positive')
    evaluator = PhysicsEvaluator(instance, plan)
    x = np.clip(evaluator.initial_fractions, 0., 1.)
    best_x, best_merit, best_result = x.copy(), float('inf'), None
    trace, calls, lp_calls = [], 0, 0
    names = None
    groups = {}
    for i, name in enumerate(evaluator.tank_ids):
        groups.setdefault(name.rsplit('-', 1)[0], []).append(i)
    G = np.zeros((len(x), len(groups)))
    for j, indices in enumerate(groups.values()):
        G[indices, j] = 1.
    if 'HEEL-P' in evaluator.tank_ids and 'HEEL-S' in evaluator.tank_ids:
        split = np.zeros(len(x))
        split[evaluator.tank_ids.index('HEEL-P')] = 1.
        split[evaluator.tank_ids.index('HEEL-S')] = -1.
        G = np.column_stack([G, split])

    def evaluate(values, label):
        nonlocal calls, names, best_x, best_merit, best_result
        if calls >= max_evaluations:
            raise StopIteration('exact oracle work cap')
        calls += 1
        result = evaluator.evaluate_fractions(values)
        if not result['completed']:
            raise ValueError('incomplete native area/securing/visibility margins')
        if names is None:
            names = sorted(k for k in result['margins'] if useful(k))
        if not names or not set(names) <= result['margins'].keys():
            raise ValueError('native exact margin set changed')
        vector = np.array([result['margins'][key] for key in names])
        score = merit(vector)
        trace.append(dict(label=label, merit=score, physics_feasible=result['physics_feasible'],
                          fractions=values.tolist(),
                          area={k:v for k,v in result['raw_margins'].items() if '0_flood' in k}))
        if score < best_merit or result['physics_feasible']:
            best_x, best_merit, best_result = values.copy(), score, result
        return vector, result

    seed_metadata, error, status = None, None, 'unknown'
    try:
        values, result = evaluate(x, 'inherited')
        if not result['physics_feasible'] and use_loading_seed and calls < max_evaluations:
            seed_plan, seed_metadata = loading_seed(instance, plan, max_lp_iterations=max_lp_iterations)
            if seed_plan is not None:
                candidate = np.array([seed_plan.ballast.tank_masses_kg[t]/cap for t,cap in
                                      zip(evaluator.tank_ids, evaluator.capacity_kg)])
                y, trial = evaluate(candidate, 'affine_loading_seed')
                if merit(y) < merit(values) or trial['physics_feasible']:
                    x, values, result = candidate, y, trial
        radius = trust_radius
        for iteration in range(max_iterations):
            if result['physics_feasible']:
                status = 'physics_feasible'; break
            # Reserve one real trial after derivatives; incomplete dimensions
            # are fixed by zero bounds, rather than assumed to have zero slope.
            J = np.zeros((len(values), G.shape[1])); active = []
            raw_tcg = float(result.get('raw_loading', {}).get('arrival', {}).get('tcg_m', 0.))
            R = np.zeros(G.shape[1])
            for j in range(G.shape[1]):
                if calls >= max_evaluations-1:
                    break
                step = difference_step(x, G[:, j], radius)
                if np.any(G[:, j] < 0.):
                    step = float(np.clip(step, -.001, .001))
                if not step:
                    continue
                trial_x = np.clip(x+step*G[:, j], 0., 1.)
                y, trial = evaluate(trial_x, f'derivative-{iteration}-{j}')
                J[:, j] = (y-values)/step; active.append(j)
                R[j] = (float(trial.get('raw_loading', {}).get('arrival', {}).get('tcg_m', 0.))-raw_tcg)/step
                if trial['physics_feasible']:
                    status = 'physics_feasible'; break
            if best_result and best_result['physics_feasible']:
                break
            if not active or calls >= max_evaluations:
                break
            n, m = G.shape[1], len(values)
            A = np.vstack([np.c_[-J, -np.eye(m), np.zeros((m,n))],
                           np.c_[G, np.zeros((len(x),m+n))],
                           np.c_[-G, np.zeros((len(x),m+n))],
                           np.c_[np.eye(n), np.zeros((n,m)), -np.eye(n)],
                           np.c_[-np.eye(n), np.zeros((n,m)), -np.eye(n)]])
            low = np.where(x >= .98, .98000001, 0.)
            high = np.where(x < .98, .97999999, 1.)
            b = np.r_[values-1e-4, high-x, x-low, np.zeros(2*n)]
            if abs(raw_tcg) > 1e-5:
                A = np.vstack([A, np.r_[-np.sign(raw_tcg)*R, np.zeros(m+n)]])
                b = np.r_[b, abs(raw_tcg)]
            else:
                A = np.vstack([A, np.r_[R, np.zeros(m+n)], np.r_[-R, np.zeros(m+n)]])
                b = np.r_[b, 1e-5-raw_tcg, 1e-5+raw_tcg]
            bounds = [(-radius, radius) if j in active else (0.,0.) for j in range(n)]
            bounds += [(0.,None)]*(m+n)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', OptimizeWarning)
                solved = linprog(np.r_[np.zeros(n), np.ones(m), np.full(n,1e-3)],
                    A_ub=A, b_ub=b, bounds=bounds, method='highs',
                    options={'threads':1, 'maxiter':int(max_lp_iterations)})
            lp_calls += 1
            if solved.status != 0:
                status = 'local_lp_unknown'; break
            accepted = False
            for scale in (1., .5, .25):
                candidate = np.clip(x+scale*(G@solved.x[:n]), 0., 1.)
                y, trial = evaluate(candidate, f'trial-{iteration}-{scale}')
                if merit(y) < merit(values)-1e-7 or trial['physics_feasible']:
                    x, values, result = candidate, y, trial
                    accepted = True; break
            if not accepted:
                radius *= .5
    except (StopIteration, ValueError, ArithmeticError, KeyError) as exc:
        error = f'{type(exc).__name__}: {exc}'
    if best_result and best_result['physics_feasible']:
        status = 'physics_feasible'
    candidate = evaluator.candidate_plan(best_x*evaluator.capacity_kg)
    return candidate, dict(status=status, oracle_calls=calls, max_evaluations=max_evaluations,
        max_iterations=max_iterations, max_lp_iterations=max_lp_iterations, lp_calls=lp_calls,
        initial_trust_radius=trust_radius,
        direction_count=int(G.shape[1]),
        work_cap_reached=bool(calls >= max_evaluations),
        best_merit=best_merit if np.isfinite(best_merit) else None, error=error,
        trace=trace, loading_seed=seed_metadata, wall_s=monotonic()-started,
        physics_feasible=bool(best_result and best_result['physics_feasible']),
        physical_infeasibility_proven=False,
        real_area_margins={k:v for k,v in (best_result or {}).get('raw_margins', {}).items() if '0_flood' in k},
        search_only=True)

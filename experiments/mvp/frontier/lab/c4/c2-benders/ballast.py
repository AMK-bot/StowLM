"""Cold, geometry-derived ballast seeds and native area/securing search.

No runtime read of stored plans, prior experiment results or tank recipes.
C2's affine seed ALGORITHM and C3's SLP ALGORITHM are reused; all actual
tank masses and cargo moments come from this invocation's instance/replay.
Local restrictions/failed searches never prove physical infeasibility.
"""
from __future__ import annotations

from collections import Counter
from functools import wraps
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
import warnings

import numpy as np
from scipy.optimize import brentq, linprog, OptimizeWarning

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]


def _load(path, name):
    # Private module names prevent score plug-ins importing C3/C4 namesakes.
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


PhysicsEvaluator = _load(HERE/'physics.py', '_c4_benders_physics').PhysicsEvaluator


def _scoped_cache(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        previous = os.environ.get('STOWAGE_VESSEL_CACHE_DIR')
        os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
        try:
            return function(*args, **kwargs)
        finally:
            if previous is None:
                os.environ.pop('STOWAGE_VESSEL_CACHE_DIR', None)
            else:
                os.environ['STOWAGE_VESSEL_CACHE_DIR'] = previous
    return wrapped


def _write(directory, name, data):
    if directory is None:
        return
    temporary = directory/(name+'.tmp')
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False))
    temporary.replace(directory/name)


def _lp(c, a=None, b=None, *, equal=None, rhs=None, bounds=(0., 1.), seconds=1.):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', OptimizeWarning)
        return linprog(c, A_ub=a, b_ub=b, A_eq=equal, b_eq=rhs, bounds=bounds,
                       method='highs', options={'threads': 1, 'time_limit': max(.001, seconds)})


def _merit(result):
    if not result.get('completed'):
        return 1e6
    negative = [max(-value, 0.) for name, value in result['margins'].items()
                if not any(word in name for word in ('unavailable', 'unrepresented'))]
    return sum(negative)+.5*max(negative, default=0.)


_LOADING_MARGINS = {'displacement_minimum_kg', 'displacement_maximum_kg', 'gm_m',
                    'trim_m', 'trim_min_m', 'list_rad', 'draught_aft_m',
                    'draught_fwd_m', 'draught_fwd_min_m', 'draught_aft_min_m'}


def _protected_family_names(result):
    return [name for name in result.get('margins', {}) if '/lashing/' in name or
            ('/stability/' in name and name.rsplit('/', 1)[-1] in _LOADING_MARGINS)]


def _protected_valid(result):
    names = _protected_family_names(result)
    return bool(result.get('completed') and names and any('/lashing/' in n for n in names) and
                all(result['margins'][name] >= 0. for name in names))


def _area_merit(result):
    area = [max(-value, 0.) for name, value in result.get('margins', {}).items()
            if '/stability/containership_area_' in name]
    return sum(area)+.5*max(area, default=0.) if area else _merit(result)


def _protection_rows(result, near_securing=.01):
    """Search restrictions only: secure near seeds, protect valid loading."""
    if not result.get('completed'):
        return []
    securing = [n for n in result['margins'] if '/lashing/' in n]
    if not securing or min(result['margins'][n] for n in securing) < -near_securing:
        return []  # infeasible bootstrap retains the elastic model
    loading = [n for n in _protected_family_names(result) if '/stability/' in n and
               result['margins'][n] >= 0.]
    return securing+loading


def _preserves_protection(result, thresholds):
    return bool(result.get('completed') and all(
        name in result.get('margins', {}) and result['margins'][name] >= value-1e-9
        for name, value in thresholds.items()))


def _slack_bounds(names, protected):
    return [(0., 0.) if name in protected else (0., None) for name in names]


def _regime(x):
    x = np.asarray(x)
    return (x <= 1e-8).astype(int)+2*(x >= .98).astype(int)+4*(x >= 1.-1e-8).astype(int)


def _fd_step(x, direction, maximum=.006):
    active = np.flatnonzero(direction)
    if not len(active):
        return 0.
    low = np.where(x[active] >= .98, .98000001, 0.)
    high = np.where(x[active] < .98, .97999999, 1.)
    limits = np.array([(low-x[active])/direction[active], (high-x[active])/direction[active]])
    negative = max(0., -float(np.max(limits.min(axis=0))))
    positive = max(0., float(np.min(limits.max(axis=0))))
    sign = 1. if positive >= negative else -1.
    room = positive if sign > 0 else negative
    return sign*min(maximum, room*.6) if room > 1e-6 else 0.


def _fd_bounds(valid, radius):
    return [(-radius, radius) if usable else (0., 0.) for usable in valid]


_VOYAGE_PHASES = ('departure', 'mid_voyage', 'arrival')
_TCG_CENTER_TOL_M = 1e-8
_TCG_LP_BAND_M = 1e-5
_TCG_FD_FLOOR = 1e-5  # fraction step; below this, pin rather than amplify GZ noise


def _tcg_same_branch(before, after):
    for phase, value in before.items():
        other = after.get(phase, float('nan'))
        if not math.isfinite(value) or not math.isfinite(other):
            return False
        if abs(value) <= _TCG_CENTER_TOL_M:
            if abs(other) > _TCG_CENTER_TOL_M:
                return False
        elif value*other <= 0. or abs(other) <= _TCG_CENTER_TOL_M:
            return False
    return True


def _tcg_fd_stencil(x, direction, proposed, before, raw_at, deadline=float('inf')):
    """Choose a bounded, all-phase same-sign stencil before expensive GZ.

    Normal-size away-side stencils preserve the loading/securing signal.
    A one-sided capacity endpoint may instead require a native raw-TCG root
    cap. Undefined center cusps and steps below the documented floor pin the
    whole direction. This is numerical search guidance, never a certificate.
    """
    x, direction = np.asarray(x, float), np.asarray(direction, float)
    info = dict(proposed_step=float(proposed), native_loading_points=0,
                raw_before={p: float(v) for p, v in before.items()})
    if not proposed:
        return 0., dict(info, reason='no capacity/regime-valid finite difference')
    if not before or not all(math.isfinite(v) for v in before.values()):
        return 0., dict(info, reason='native phase TCG unavailable')
    active = np.flatnonzero(direction)
    low = np.where(x >= .98, .98000001, 0.)
    high = np.where(x < .98, .97999999, 1.)
    def step_for(sign):
        limits = [(high[j]-x[j])/(sign*direction[j]) if sign*direction[j] > 0. else
                  (x[j]-low[j])/(-sign*direction[j]) for j in active]
        room = max(0., min(limits, default=0.))
        return sign*min(abs(proposed), .6*room)
    cache = {}
    def probe(step):
        if time.monotonic() >= deadline:
            raise TimeoutError('TCG loading stencil budget exhausted')
        step = float(step)
        if step not in cache:
            values = raw_at(x+step*direction)
            if not all(p in values and math.isfinite(values[p]) for p in before):
                raise ValueError('native phase TCG unavailable')
            cache[step] = {p: float(values[p]) for p in before}
            info['native_loading_points'] += 1
        return cache[step]
    signs = (1. if proposed > 0. else -1., -1. if proposed > 0. else 1.)
    candidates = [step_for(sign) for sign in signs]
    try:
        for step in candidates:
            if abs(step) < _TCG_FD_FLOOR:
                continue
            after = probe(step)
            if _tcg_same_branch(before, after):
                return step, dict(info, selected_step=step, raw_after=after,
                                  reversed=step*proposed < 0., root_capped=False)
        # At a center cusp, neither directional derivative represents both
        # sides. Keep an undefined objective column out of the common LP.
        if any(abs(value) <= _TCG_CENTER_TOL_M and any(
                step in cache and abs(cache[step][phase]) > _TCG_CENTER_TOL_M
                for step in candidates) for phase, value in before.items()):
            return 0., dict(info, reason='undefined phase TCG center cusp; direction pinned')
        for step in candidates:
            if abs(step) < _TCG_FD_FLOOR:
                continue
            after = probe(step)
            crossing = [p for p, value in before.items() if value*after[p] <= 0.]
            if not crossing:
                continue
            cap = abs(step)
            for phase in crossing:
                # Parameterize by positive distance so either stencil sign
                # uses an ordered root bracket and the same mass direction.
                sign = 1. if step > 0. else -1.
                root = brentq(lambda distance: probe(sign*distance)[phase], 0., abs(step),
                              xtol=_TCG_FD_FLOOR/32., maxiter=24)
                cap = min(cap, .25*root)
            if cap < _TCG_FD_FLOOR:
                continue
            chosen = math.copysign(cap, step)
            endpoint = probe(chosen)
            if _tcg_same_branch(before, endpoint):
                return chosen, dict(info, selected_step=chosen, raw_after=endpoint,
                                    reversed=chosen*proposed < 0., root_capped=True)
    except (ValueError, ArithmeticError, RuntimeError) as error:
        return 0., dict(info, reason=f'native TCG stencil unavailable: {error}')
    return 0., dict(info, reason='no same-branch stencil above finite-difference floor')


def _tcg_linear_rows(raw, derivatives):
    """All-phase no-away/no-crossing rows, scaled above LP tolerances."""
    rows, bounds = [], []
    for phase, value in raw.items():
        column = np.asarray(derivatives[phase], float)
        scale = max(abs(value), _TCG_LP_BAND_M)
        if abs(value) > _TCG_CENTER_TOL_M:
            signed = np.sign(value)*column/scale
            rows.extend([-signed, signed]); bounds.extend([abs(value)/scale, 0.])
        else:
            rows.extend([column/scale, -column/scale])
            bounds.extend([(_TCG_LP_BAND_M-value)/scale, (_TCG_LP_BAND_M+value)/scale])
    return np.asarray(rows), np.asarray(bounds)


def _geometry_census(evaluator):
    from stowage.checker import stability
    from stowage.checker.stability_sounding import sounding
    from stowage.vessel.tanks import TankGeometry
    geometry, params = stability._context(evaluator.instance.vessel)
    declared = {tank['id']: tank for tank in params.get('tanks', [])}
    rows = []
    for j, tank in enumerate(evaluator.tanks):
        solid = TankGeometry.from_params(declared[tank.id], ship_params=params)
        row = sounding(solid, tank.capacity_m3*.5)
        rows.append(dict(index=j, id=tank.id, x=float(row.lcg_m), y=float(row.tcg_m),
                         z=float(row.vcg_m), fsm=float(row.fsm_m4*tank.content_density_kg_m3),
                         capacity=float(evaluator.capacity_kg[j])))
    # Mirror groups are derived from exact vessel geometry, not tank names.
    groups, used = [], set()
    for i, row in enumerate(rows):
        if i in used:
            continue
        partner = next((j for j, other in enumerate(rows) if j not in used and j != i and
                        math.isclose(row['x'], other['x'], abs_tol=1e-6) and
                        math.isclose(row['z'], other['z'], abs_tol=1e-6) and
                        math.isclose(row['y'], -other['y'], abs_tol=1e-6) and
                        math.isclose(row['capacity'], other['capacity'], rel_tol=1e-9)), None)
        indices = [i] if partner is None else [i, partner]
        used.update(indices)
        groups.append(indices)
    return rows, groups, geometry, params


def _raw_loading(evaluator, masses, geometry, params, phase='arrival'):
    from stowage.checker.stability_loading import loading_condition
    from stowage.solver.ballast_v4_evaluator import conditions
    ship = evaluator.ship.model_copy(update={'tank_masses_kg': {
        **evaluator.fixed_masses_kg, **dict(zip(evaluator.tank_ids, map(float, masses)))}})
    inst, state = next((inst, state) for name, inst, state in conditions(evaluator.instance, ship) if name == phase)
    condition = loading_condition(inst, state, geometry=geometry, ship_params=params,
        free_surface_method=int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1')),
        precomputed_soundings=True, small_tank_exemption=True)
    if condition.errors:
        raise ValueError('; '.join(condition.errors))
    return condition


def _raw_tcg_phases(evaluator, masses, geometry, params):
    """Cheap ordinary upright loading for the three D57 states, no GZ."""
    from stowage.checker.stability_loading import loading_condition
    from stowage.solver.ballast_v4_evaluator import conditions
    ship = evaluator.ship.model_copy(update={'tank_masses_kg': {
        **evaluator.fixed_masses_kg, **dict(zip(evaluator.tank_ids, map(float, masses)))}})
    result = {}
    for phase, inst, state in conditions(evaluator.instance, ship):
        condition = loading_condition(inst, state, geometry=geometry, ship_params=params,
            free_surface_method=int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1')),
            precomputed_soundings=True, small_tank_exemption=True)
        if condition.errors:
            raise ValueError('; '.join(condition.errors))
        result[phase] = float(condition.tcg_m)
    if set(result) != set(_VOYAGE_PHASES):
        raise ValueError('D57 phase set changed in native TCG probe')
    return result


def _project_stage_point(evaluator, fractions, rows, groups, geometry, params):
    """Native arrival-TCG projection; return the actual bounded mass vector."""
    fractions = np.asarray(fractions, float)
    if (not np.all(np.isfinite(fractions)) or np.any(fractions < -1e-8) or
            np.any(fractions > 1.+1e-8)):
        raise ValueError('TCG projection input outside tank capacities')
    fractions = np.clip(fractions, 0., 1.)
    details = []
    try:
        masses = _balance(evaluator, fractions*evaluator.capacity_kg,
                          rows, groups, geometry, params, diagnostics=details)
    except RuntimeError as error:
        raise ValueError(f'native arrival-TCG projection unavailable: {error}') from error
    actual = np.divide(masses, evaluator.capacity_kg, out=np.zeros_like(masses),
                       where=evaluator.capacity_kg > 0.)
    if (not np.all(np.isfinite(actual)) or np.any(actual < -1e-9) or np.any(actual > 1.+1e-9)):
        raise ValueError('TCG projection exceeded tank capacities')
    actual = np.clip(actual, 0., 1.)
    raw = _raw_tcg_phases(evaluator, actual*evaluator.capacity_kg, geometry, params)
    if abs(raw['arrival']) > _TCG_CENTER_TOL_M:
        raise ValueError('native arrival-TCG projection did not bracket/center')
    return actual, dict(balance=details, raw_after=raw,
                        maximum_fraction_change=float(np.max(np.abs(actual-fractions), initial=0.)))


def _effective_fd_direction(base, endpoint, step):
    """A projected-null direction supplies neither a gradient nor an LP move."""
    delta = np.asarray(endpoint, float)-np.asarray(base, float)
    return delta/step if step and np.max(np.abs(delta), initial=0.) > 1e-8 else np.zeros_like(delta)


def _balance(evaluator, masses, rows, groups, geometry, params, diagnostics=None):
    masses = np.asarray(masses, float).copy()
    # A nearly empty preferred pair need not have enough transferable moment.
    # Try occupied alternatives; every transfer preserves that pair's mass.
    paired = [group for group in groups if len(group) == 2 and masses[group].sum() > 1e-5]
    if not paired:
        return masses
    narrow = sorted(paired, key=lambda g: rows[g[0]]['fsm']/max(rows[g[0]]['capacity'], 1.))
    preferred = sorted(narrow[:max(1, len(narrow)//2)], key=lambda g: -abs(rows[g[0]]['y']))
    ordered = preferred+[g for g in narrow if g not in preferred]
    before = float(_raw_loading(evaluator, masses, geometry, params).tcg_m)
    if abs(before) <= 1e-8:
        return masses
    for group in ordered:
        a, b = group
        low = max(-masses[a], masses[b]-evaluator.capacity_kg[b])
        high = min(evaluator.capacity_kg[a]-masses[a], masses[b])
        if high-low <= 1e-5:
            continue
        def residual(delta):
            trial = masses.copy(); trial[a] += delta; trial[b] -= delta
            return float(_raw_loading(evaluator, trial, geometry, params).tcg_m)
        left, right = residual(low), residual(high)
        if left*right > 0.:
            continue
        delta = brentq(residual, low, high, xtol=1e-4)
        trial = masses.copy(); trial[a] += delta; trial[b] -= delta
        after = float(_raw_loading(evaluator, trial, geometry, params).tcg_m)
        if abs(after) > 1e-7:
            continue
        if diagnostics is not None:
            diagnostics.append(dict(status='balanced', pair=[rows[j].get('id', j) for j in group],
                                    transfer_kg=float(delta), arrival_tcg_before_m=before,
                                    arrival_tcg_after_m=after))
        return trial
    if diagnostics is not None:
        diagnostics.append(dict(status='no_bracket', arrival_tcg_before_m=before,
                                occupied_pairs_tried=len(ordered)))
    return masses


def _narrow_redistribute(evaluator, masses, rows, groups, selected=None, *,
                         whole_pair=False, diagnostics=None):
    """Replace wide contents at the same approximate mass/x/y moments.

    Whole-pair mode includes pressed/full partners. LP failure rejects only
    this search seed, never the physical stow or the exact ballast problem.
    """
    masses = np.asarray(masses, float).copy()
    ratios = np.array([r['fsm']/max(r['capacity'], 1.) for r in rows])
    threshold = float(np.quantile(ratios, .65))
    fraction = np.divide(masses, evaluator.capacity_kg, out=np.zeros_like(masses), where=evaluator.capacity_kg > 0.)
    remove = [j for j, row in enumerate(rows) if ratios[j] > threshold and
              (whole_pair or .001 < fraction[j] < .98) and (selected is None or j in selected)]
    use = [j for j, row in enumerate(rows) if ratios[j] < threshold and
           j not in remove and evaluator.capacity_kg[j]-masses[j] > 1.]
    info = dict(whole_pair=bool(whole_pair), removed_tanks=[rows[j].get('id', j) for j in remove],
                status='no_replacement', outward=False)
    if not remove or len(use) < 2:
        if diagnostics is not None:
            diagnostics.append(info)
        return masses
    removed = masses[remove].copy()
    total = float(removed.sum())
    target_x = float(sum(masses[j]*rows[j]['x'] for j in remove))
    target_y = float(sum(masses[j]*rows[j]['y'] for j in remove))
    a = np.array([[1.]*len(use), [rows[j]['x'] for j in use], [rows[j]['y'] for j in use]])
    solution = _lp(np.array([rows[j]['z']+ratios[j] for j in use]), equal=a,
                   rhs=np.array([total, target_x, target_y]),
                   bounds=[(0., evaluator.capacity_kg[j]-masses[j]) for j in use])
    info.update(lp_status=int(solution.status))
    if solution.status == 0:
        trial = masses.copy(); trial[remove] = 0.; trial[use] += solution.x
        residual = a@solution.x-np.array([total, target_x, target_y])
        info['replacement_residual_mass_x_y'] = residual.tolist()
        tolerance = np.maximum(1e-4, np.abs([total, target_x, target_y])*1e-8)
        if (np.all(np.abs(residual) <= tolerance) and np.all(trial >= -1e-6) and
                np.all(trial <= evaluator.capacity_kg+1e-6)):
            masses = np.clip(trial, 0., evaluator.capacity_kg)
            info['status'] = 'candidate'
        else:
            info['status'] = 'replacement_residual_guard_rejected'
    if diagnostics is not None:
        diagnostics.append(info)
    return masses


def _wide_pairs(rows, groups, masses):
    ratios = [row['fsm']/max(row['capacity'], 1.) for row in rows]
    cutoff = float(np.quantile(ratios, .65))
    choices = [g for g in groups if len(g) == 2 and ratios[g[0]] > cutoff and
               sum(masses[j] for j in g) > 1.]
    return sorted(choices, key=lambda g: (-rows[g[0]]['x'], -ratios[g[0]]))


def _nearfull_seed(masses, capacities, rows, groups, depth, fraction):
    """Adjust retained pressed pairs, preserving empty or asymmetric regimes."""
    trial = np.asarray(masses, float).copy()
    for group in groups:
        if (rows[group[0]]['z'] < depth/4 and
                all(trial[j] >= .98*capacities[j] for j in group)):
            trial[group] = fraction*capacities[group]
    return trial


def _unique_seed(seeds, point, maximum=9):
    point = np.asarray(point, float)
    if len(seeds) >= maximum or any(np.allclose(point, prior, rtol=0., atol=1e-10) for prior in seeds):
        return False
    seeds.append(point.copy())
    return True


def _directions(rows, groups, maximum_groups=5):
    if not groups:
        return np.zeros((len(rows), 0))
    score = lambda group: sum(rows[j]['fsm'] for j in group)/max(sum(rows[j]['capacity'] for j in group), 1.)
    selected = []
    narrow = sorted(groups, key=score)[:max(1, (len(groups)+1)//2)]
    center = .5*(min(r['x'] for r in rows)+max(r['x'] for r in rows))
    span = max(max(r['x'] for r in rows)-min(r['x'] for r in rows), 1.)
    choices = [min(narrow, key=lambda g: rows[g[0]]['x']),
               max(narrow, key=lambda g: rows[g[0]]['x']),
               max(groups, key=lambda g: sum(rows[j]['capacity'] for j in g)/(1.+rows[g[0]]['z'])),
               max(narrow, key=lambda g: abs(rows[g[0]]['y'])/
                   ((1.+score(g))*(1.+abs(rows[g[0]]['x']-center)/span))),
               max(narrow, key=lambda g: sum(rows[j]['capacity'] for j in g)*
                   max(0., rows[g[0]]['x']-center)/(1.+score(g)))]
    for group in choices:
        if group not in selected:
            selected.append(group)
    for group in sorted(groups, key=score):
        if len(selected) >= min(maximum_groups, len(groups)):
            break
        if group not in selected:
            selected.append(group)
    directions = []
    for group in selected[:maximum_groups]:
        d = np.zeros(len(rows)); d[group] = 1.; directions.append(d)
    paired = [g for g in groups if len(g) == 2]
    if paired:
        narrow = sorted(paired, key=score)[:max(1, len(paired)//2)]
        group = max(narrow, key=lambda g: abs(rows[g[0]]['y']))
        d = np.zeros(len(rows)); d[group[0]] = 1.; d[group[1]] = -1.
        directions.append(d)
    return np.column_stack(directions)


def _loading_drafts(evaluator, masses, geometry, params):
    """Ordinary direct finite-liquid departure equilibrium, without GZ curves."""
    from stowage.checker.stability_equilibrium import floating_equilibrium
    from stowage.checker.stability_loading import liquid_cg
    condition = _raw_loading(evaluator, masses, geometry, params, phase='departure')
    inst = evaluator.instance
    method = int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1'))
    if method == 1:
        floating = floating_equilibrium(geometry, condition, inst.port.water_density_kg_m3,
                                       inst.vessel.hull.lpp_m, liquid_cg)
    elif method == 2:
        floating = geometry.equilibrium(condition.mass_kg, condition.lcg_m,
            condition.tcg_m, condition.kg_m+condition.fsc_m, inst.port.water_density_kg_m3)
    else:
        raise ValueError('unsupported free-surface method')
    return float(floating.t_fwd_m), float(floating.t_aft_m)


def _forward_draft(evaluator, masses, geometry, params):
    return _loading_drafts(evaluator, masses, geometry, params)[0]


def _draft_limit(evaluator):
    inst = evaluator.instance
    return min(inst.vessel.hull.max_draught_m,
               inst.port.max_draught_m or inst.vessel.hull.max_draught_m)-.0005


def _fill_ray_weights(rows, groups, masses, capacities, lcg):
    """Geometry-only aft/fore kg shares conserving the linear LCG estimate."""
    score = lambda g: sum(rows[j]['fsm'] for j in g)/max(sum(rows[j]['capacity'] for j in g), 1.)
    pairs = [g for g in groups if len(g) == 2]
    narrow = sorted(pairs, key=score)[:max(1, (len(pairs)+1)//2)]
    room = np.where(masses >= .98*capacities, capacities-masses, .97999999*capacities-masses)
    available = [g for g in narrow if min(room[g]) > 1.]
    aft = [g for g in available if rows[g[0]]['x'] < lcg-1e-5]
    fore = [g for g in available if rows[g[0]]['x'] > lcg+1e-5]
    if not aft or not fore:
        return None, dict(reason='no available narrow mirror pairs bracketing current departure LCG')
    # Moment capacity promotes a stocked-size forward deep pair over a
    # farther small peak pair; no identifier selects either pair.
    potential = lambda g: 2.*min(room[g])*abs(rows[g[0]]['x']-lcg)/(1.+score(g))
    a, f = max(aft, key=potential), max(fore, key=potential)
    xa, xf = rows[a[0]]['x'], rows[f[0]]['x']
    weights = np.zeros(len(rows))
    weights[a] = (xf-lcg)/(xf-xa)/2.
    weights[f] = (lcg-xa)/(xf-xa)/2.
    return weights, dict(aft_pair=[rows[j].get('id', j) for j in a],
                         fore_pair=[rows[j].get('id', j) for j in f],
                         departure_lcg_target_m=float(lcg), kg_weights=weights.tolist())


def _large_fill_ray(evaluator, masses, rows, groups, geometry, params, deadline):
    """Two large search seeds bounded by native departure drafts/capacity.

    A bounded scan/root finds the first sampled draft crossing. It is search
    guidance, never a monotonicity or physical infeasibility certificate.
    Each probe balances arrival TCG before measuring BOTH native drafts.
    """
    masses = np.asarray(masses, float).copy()
    initial = _raw_loading(evaluator, masses, geometry, params, phase='departure')
    weights, info = _fill_ray_weights(rows, groups, masses, evaluator.capacity_kg, initial.lcg_m)
    info.update(kind='large_narrow_fill_ray', outward=False, direct_equilibrium_calls=0)
    if weights is None:
        return [], info
    active = weights > 0.
    room = np.where(masses >= .98*evaluator.capacity_kg, evaluator.capacity_kg-masses,
                    .97999999*evaluator.capacity_kg-masses)
    capacity_end = float(np.min(room[active]/weights[active]))
    target = _draft_limit(evaluator)
    cache = {}
    def probe(added):
        if time.monotonic() >= deadline:
            raise TimeoutError('large-fill loading-only ray budget exhausted')
        added = float(added)
        if added not in cache:
            trial = _balance(evaluator, masses+added*weights, rows, groups, geometry, params)
            if np.any(trial < -1e-6) or np.any(trial > evaluator.capacity_kg+1e-6):
                raise ValueError('large-fill balancing exceeded tank capacity')
            drafts = _loading_drafts(evaluator, trial, geometry, params)
            info['direct_equilibrium_calls'] += 1
            cache[added] = (trial, drafts)
        return max(cache[added][1])-target
    info.update(capacity_endpoint_added_kg=capacity_end, reserved_draft_limit_m=target)
    if capacity_end <= 1. or probe(0.) > 0.:
        info['reason'] = 'no positive capacity or native departure draft headroom'
        return [], info
    end, previous = capacity_end, 0.
    for added in np.linspace(0., capacity_end, 5)[1:]:
        if probe(added) > 0.:
            end = float(brentq(probe, previous, added, xtol=.05, maxiter=40))
            info['endpoint_limited_by'] = 'native departure draft'
            break
        previous = float(added)
    else:
        info['endpoint_limited_by'] = 'capacity or retained fill regime'
    info['endpoint_added_kg'] = end
    points = []
    for fraction in (.5, .95):
        added = fraction*end
        residual = probe(added)
        trial, drafts = cache[float(added)]
        arrival = _raw_loading(evaluator, trial, geometry, params)
        departure = _raw_loading(evaluator, trial, geometry, params, phase='departure')
        row = dict(ray_fraction=fraction, added_kg=added, departure_drafts_fwd_aft_m=list(drafts),
                   draft_excess_m=float(residual), arrival_tcg_m=float(arrival.tcg_m),
                   actual_departure_lcg_m=float(departure.lcg_m))
        if residual <= 1e-7:
            points.append((trial.copy(), row))
    info['points'] = [row for _, row in points]
    return points, info


def _forward_control(available, rows, masses, primary):
    """Rank actual removable fore-aft moment, not just the furthest arm."""
    primary_x = rows[primary[0]]['x']
    return max(available, key=lambda group:
               (rows[group[0]]['x']-primary_x)*float(min(masses[group])))


def _compensated_trial(evaluator, x, rows, groups, geometry, params, step, deadline):
    """Geometry-derived pair increase, with only necessary forward drawdown.

    This is a search line, not a constraint relaxation or a feasibility claim.
    Exact complete physics still checks the result. Tank identifiers do not
    select either pair; the current geometry and available contents do.
    """
    pairs = [g for g in groups if len(g) == 2]
    if len(pairs) < 2:
        return None, {'reason': 'no usable mirror pairs'}
    ratio = lambda g: rows[g[0]]['fsm']/max(rows[g[0]]['capacity'], 1.)
    narrow = sorted(pairs, key=ratio)[:max(1, (len(pairs)+1)//2)]
    middle = .5*(min(r['x'] for r in rows)+max(r['x'] for r in rows))
    span = max(max(r['x'] for r in rows)-min(r['x'] for r in rows), 1.)
    primary = max(narrow, key=lambda g: abs(rows[g[0]]['y'])/
                  ((1.+ratio(g))*(1.+abs(rows[g[0]]['x']-middle)/span)))
    masses = np.asarray(x, float)*evaluator.capacity_kg
    trial = masses.copy()
    room = min(1.-x[j] if x[j] >= .98 else .97999999-x[j] for j in primary)
    step = min(float(step), max(0., float(room)))
    if step <= 1e-6:
        return None, {'reason': 'no primary capacity in current correction regime'}
    added = step*float(np.mean(evaluator.capacity_kg[primary]))
    trial[primary] += added
    target = _draft_limit(evaluator)
    calls = 0
    removed_total = 0.
    def draft_residual(point):
        nonlocal calls
        if time.monotonic() >= deadline:
            raise TimeoutError('forward-draft line budget exhausted')
        calls += 1
        return max(_loading_drafts(evaluator, point, geometry, params))-target
    trial = _balance(evaluator, trial, rows, groups, geometry, params)
    info = dict(primary_tanks=[evaluator.tank_ids[j] for j in primary],
                primary_fraction_increase=step, added_each_kg=added,
                target_forward_draft_m=target, target_aft_draft_m=target)
    # Freeboard may permit a pure increase even with all forward controls
    # empty. Only ask for drawdown after the native draft actually requires it.
    if draft_residual(trial) <= 0.:
        info.update(uncompensated=True, control_tanks=[], control_removed_each_kg=0.,
                    direct_equilibrium_calls=calls)
        return trial/evaluator.capacity_kg, info
    available = [g for g in pairs if g != primary and
                 rows[g[0]]['x'] > rows[primary[0]]['x']+.05*span and
                 min(trial[g]) > 1.]
    if not available:
        return None, dict(info, reason='native draft needs drawdown but no forward pair has removable contents',
                          direct_equilibrium_calls=calls)
    control = _forward_control(available, rows, trial, primary)
    for repeat in range(2):
        if time.monotonic() >= deadline:
            raise TimeoutError('forward-draft line budget exhausted')
        trial = _balance(evaluator, trial, rows, groups, geometry, params)
        baseline = trial.copy()
        def residual(removed):
            point = baseline.copy(); point[control] -= removed
            return draft_residual(point)
        zero = residual(0.)
        if zero <= 0.:
            break
        maximum = max(0., float(min(baseline[control]))-1e-6)
        if maximum <= 0. or residual(maximum) > 0.:
            return None, {'reason': 'available forward drawdown cannot meet draft target',
                          'direct_equilibrium_calls': calls}
        removed = brentq(residual, 0., maximum, xtol=1e-4)
        trial[control] -= removed; removed_total += removed
    trial = _balance(evaluator, trial, rows, groups, geometry, params)
    if draft_residual(trial) > 1e-7:
        return None, dict(info, reason='coupled balance/drawdown did not reach both draft limits',
                          direct_equilibrium_calls=calls)
    info.update(uncompensated=False, control_tanks=[evaluator.tank_ids[j] for j in control],
                control_removed_each_kg=removed_total, direct_equilibrium_calls=calls)
    return trial/evaluator.capacity_kg, info


def _native_manifest():
    manifest = {}
    for name, module in tuple(sys.modules.items()):
        source = getattr(module, '__file__', None)
        if name.startswith('stowage.') and source and Path(source).suffix == '.py':
            path = Path(source).resolve()
            if path.is_relative_to(ROOT/'src'):
                manifest[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


@_scoped_cache
def finish_candidate(instance, plan, seed=0, budget_s=900., out=None):
    """Return best current-run candidate and honest JSON-safe failure evidence.

    Always ignore a supplied departure ballast recipe. Initialization is the
    native replay of THIS instance's initial tanks. No archive path is opened.
    One process/thread; no watchdog/worker spawned. Caller owns CPU admission.
    """
    started = time.monotonic(); deadline = started+max(0., float(budget_s))
    directory = Path(out).resolve() if out is not None else None
    if directory is not None:
        if not directory.is_relative_to(HERE):
            raise ValueError('finisher output outside C4 experiment')
        directory.mkdir(parents=True, exist_ok=True)
    clean = plan.model_copy(update={'ballast': None})
    meta = dict(status='unknown', full_checker_passed=None, full_checker_report=None,
                initialization='this instance native initial/reached tanks; supplied plan ballast ignored',
                seed=int(seed), evaluations=0, full_checker_calls=0, trace=[],
                local_infeasibility_is_physical_proof=False, complete_stow=False)
    evaluator = best_evaluator = None; best_x = best_result = None; best_value = float('inf')
    protected_evaluator = protected_x = protected_result = None
    protected_rank = (float('inf'), float('inf'))
    evaluators = []; stage_label = 'cold-seeds'
    # Reserve one internal unchanged full check; the shared score adapter
    # separately reserves its own independent acceptance call.
    search_deadline = max(started, deadline-45.)
    max_evaluations = max(1, min(160, int(max(0., budget_s-45.)/3.)))
    meta['work_caps'] = dict(max_evaluations=max_evaluations, slp_iterations=16,
        maximum_fd_directions=6, maximum_seed_count=9, maximum_repaired_stows=2,
        repair_budget_s=60., forward_compensated_probes=6, full_check_reserve_s=45.)
    meta['slp_iterations_used'] = meta['forward_compensated_probes'] = 0

    def evaluate(x, label, require_complete=True, until=None):
        nonlocal best_x, best_result, best_value, best_evaluator
        nonlocal protected_evaluator, protected_x, protected_result, protected_rank
        until = min(search_deadline, until if until is not None else search_deadline)
        if time.monotonic() >= until or sum(ev.evaluations for ev in evaluators) >= max_evaluations:
            raise TimeoutError('bounded native physics search exhausted')
        x = np.clip(np.asarray(x, float), 0., 1.)
        result = evaluator.evaluate_fractions(x, deadline=until)
        value = _merit(result)
        record = dict(label=label, stage=stage_label, cargo_context=evaluator.context_identity,
                      completed=result['completed'], physics_feasible=result['physics_feasible'],
                      merit=value, wall_s=result['wall_s'], fractions=x.tolist(),
                      native_part_calls=sum(key.endswith(('/stability', '/lashing'))
                                            for key in result['part_times_s']),
                      negative_margins={k: v for k, v in result['raw_margins'].items() if v < 0.})
        meta['trace'].append(record)
        if _protected_valid(result):
            rank = (_area_merit(result), value)
            if rank < protected_rank:
                protected_evaluator, protected_x, protected_result, protected_rank = (
                    evaluator, x.copy(), result, rank)
                _write(directory, 'protected-best-physics.json', result)
        if best_x is None or result['physics_feasible'] or value < best_value:
            # One atomic assignment binds this tank vector to this exact
            # cargo replay. Repair must never combine old x with new cargo.
            best_evaluator, best_x, best_result, best_value = evaluator, x.copy(), result, value
            _write(directory, 'best-physics.json', result)
        _write(directory, 'trace.json', meta['trace'])
        if require_complete and not result['completed']:
            raise ValueError('native physics unavailable')
        return result

    def run_stage(active, start_x, label, until, iteration_cap):
        nonlocal evaluator, stage_label
        evaluator, stage_label = active, label
        stage = dict(name=label, cargo_context=active.context_identity, iterations=0,
                     started_s=time.monotonic()-started)
        meta.setdefault('stages', []).append(stage)
        x = np.asarray(start_x, float).copy(); result = None
        local_x = x.copy(); local_result = None; local_merit = float('inf')
        protected_thresholds = {}
        protected_area_mode = False
        projection_cache = {}

        def project(point):
            if time.monotonic() >= until:
                raise TimeoutError('projected stage budget exhausted')
            point = np.asarray(point, float)
            key = point.tobytes()
            if key not in projection_cache:
                projection_cache[key] = _project_stage_point(active, point, rows, groups, geometry, params)
                stage['projection_calls'] = len(projection_cache)
            actual, info = projection_cache[key]
            return actual.copy(), info

        def objective(answer):
            return _area_merit(answer) if protected_area_mode else _merit(answer)

        def sample(point, note, require_complete=True):
            nonlocal local_x, local_result, local_merit
            actual, projection = project(point)
            meta.setdefault('tcg_projections', []).append(dict(stage=label, sample=note, **projection))
            answer = evaluate(actual, label+'/'+note, require_complete=require_complete, until=until)
            merit = objective(answer)
            if ((local_result is None or answer['physics_feasible'] or merit < local_merit) and
                    _preserves_protection(answer, protected_thresholds)):
                local_x, local_result, local_merit = actual.copy(), answer, merit
            return answer, actual

        try:
            rows, groups, geometry, params = _geometry_census(active)
            G = _directions(rows, groups); radius = .03
            result, x = sample(x, 'initial', require_complete=False)
            if not result['completed']:
                raise ValueError('stage initial native physics unavailable')
            protected_thresholds = {name: min(0., result['margins'][name])
                                    for name in _protection_rows(result)}
            protected_area_mode = _protected_valid(result)
            local_merit = objective(local_result)
            stage['protection'] = dict(rows=sorted(protected_thresholds),
                exact_thresholds=protected_thresholds.copy(), near_securing_ratio_excess=.01,
                objective='containership area under native securing/loading protection' if protected_area_mode else 'elastic margins')
            # A small exact line portfolio first releases a nearly binding
            # forward draft without spending a full GZ Jacobian per root step.
            if not result['physics_feasible']:
                for step in (.5*radius, radius):
                    if meta['forward_compensated_probes'] >= 6 or time.monotonic() >= until:
                        break
                    point, info = _compensated_trial(active, x, rows, groups, geometry, params, step, until)
                    meta.setdefault('forward_lines', []).append(dict(stage=label, **info))
                    if point is not None:
                        meta['forward_compensated_probes'] += 1
                        answer, _ = sample(point, 'forward-line', require_complete=False)
                        if answer['physics_feasible']:
                            break
                x, result = local_x.copy(), local_result
            for iteration in range(iteration_cap):
                if (result['physics_feasible'] or time.monotonic() >= until or
                        meta['slp_iterations_used'] >= 16):
                    break
                meta['slp_iterations_used'] += 1; stage['iterations'] += 1
                names = sorted(k for k in result['margins'] if not any(word in k for word in
                               ('weather_table_applicability', 'unavailable', 'unrepresented')))
                values = np.array([result['margins'][key] for key in names])
                # Once a family becomes valid, subsequent exact proposals
                # must preserve it. A near-securing seed may improve its
                # small existing deficit but may not worsen it.
                for name in _protection_rows(result):
                    protected_thresholds[name] = max(protected_thresholds.get(name, -float('inf')),
                                                      min(0., result['margins'][name]))
                if _protected_valid(result):
                    protected_area_mode = True
                    stage['protection']['objective'] = 'containership area under native securing/loading protection'
                local_merit = objective(local_result)
                J = np.zeros((len(values), G.shape[1]))
                effective_G = np.zeros_like(G)
                R = {phase: np.zeros(G.shape[1]) for phase in _VOYAGE_PHASES}
                valid_fd = np.zeros(G.shape[1], dtype=bool)
                raw = {phase: float(result['raw_loading'][phase]['tcg_m']) for phase in _VOYAGE_PHASES}
                def invalid(direction, reason):
                    meta.setdefault('invalid_derivatives', []).append(dict(
                        stage=label, iteration=iteration, direction=direction, reason=reason))
                for j in range(G.shape[1]):
                    step = _fd_step(x, G[:, j], min(.006, radius/4))
                    if np.any(G[:, j] < 0.):
                        step = float(np.clip(step, -.0005, .0005))
                    before_probe = time.monotonic()
                    step, stencil = _tcg_fd_stencil(x, G[:, j], step, raw,
                        lambda point: project(point)[1]['raw_after'],
                        deadline=until)
                    meta.setdefault('tcg_stencils', []).append(dict(stage=label, iteration=iteration,
                        direction=j, wall_s=time.monotonic()-before_probe, **stencil))
                    if not step:
                        invalid(j, stencil.get('reason', 'no valid TCG finite difference'))
                        continue
                    fd_point, _ = project(x+step*G[:, j])
                    effective = _effective_fd_direction(x, fd_point, step)
                    if not np.any(effective):
                        invalid(j, 'projected finite difference is a null direction')
                        continue
                    if np.any((fd_point >= .98) != (x >= .98)):
                        invalid(j, 'TCG projection crossed a pressed/slack fill branch')
                        continue
                    trial, fd_point = sample(x+step*G[:, j], f'fd-{iteration}-{j}', require_complete=False)
                    if trial['physics_feasible']:
                        break
                    if not trial['completed'] or not set(names) <= trial['margins'].keys():
                        invalid(j, 'native physics or required margin keys unavailable')
                        continue
                    trial_raw = {phase: float(trial['raw_loading'][phase]['tcg_m']) for phase in _VOYAGE_PHASES}
                    if not _tcg_same_branch(raw, trial_raw):
                        invalid(j, 'full native telemetry left probed phase TCG sign branch')
                        continue
                    changed = any(result['raw_loading'].get(phase, {}).get('correction_tanks') !=
                                  trial['raw_loading'].get(phase, {}).get('correction_tanks')
                                  for phase in ('departure', 'mid_voyage', 'arrival'))
                    if changed:
                        meta.setdefault('skipped_branch_derivatives', []).append(
                            dict(stage=label, iteration=iteration, direction=j,
                                 reason='free-surface correction membership changed'))
                        invalid(j, 'free-surface correction membership changed')
                        continue
                    column = (np.array([trial['margins'][key] for key in names])-values)/step
                    raw_column = {phase: (trial_raw[phase]-raw[phase])/step for phase in _VOYAGE_PHASES}
                    if not np.all(np.isfinite(column)) or not all(math.isfinite(v) for v in raw_column.values()):
                        invalid(j, 'nonfinite derivative')
                        continue
                    J[:, j], effective_G[:, j], valid_fd[j] = column, effective, True
                    for phase in _VOYAGE_PHASES:
                        R[phase][j] = raw_column[phase]
                if local_result['physics_feasible']:
                    break
                if not valid_fd.any():
                    if local_merit < objective(result)-1e-6:
                        x, result = local_x.copy(), local_result
                        continue
                    stage['status'] = 'no valid derivative directions'; break
                n, m = G.shape[1], len(values)
                low = np.where(x >= .98, .98000001, 0.)
                high = np.where(x < .98, .97999999, 1.)
                a = np.vstack([np.c_[-J, -np.eye(m)], np.c_[effective_G, np.zeros((len(x), m))],
                               np.c_[-effective_G, np.zeros((len(x), m))]])
                b = np.r_[values-1e-5, high-x, x-low]
                tcg_rows, tcg_bounds = _tcg_linear_rows(raw, R)
                a = np.vstack([a, np.c_[tcg_rows, np.zeros((len(tcg_rows), m))]])
                b = np.r_[b, tcg_bounds]
                protected = set(protected_thresholds)&set(names)
                bounds = _fd_bounds(valid_fd, radius)+_slack_bounds(names, protected)
                proposal = _lp(np.r_[np.zeros(n), np.ones(m)], a, b, bounds=bounds)
                if protected:
                    meta.setdefault('protected_lp_solves', []).append(dict(
                        stage=label, iteration=iteration, status=int(proposal.status),
                        protected_rows=sorted(protected)))
                if protected and proposal.status == 2:
                    meta.setdefault('elastic_fallbacks', []).append(dict(
                        stage=label, iteration=iteration,
                        reason='local protected LP infeasible; exact native protection still required',
                        physical_proof=False))
                    proposal = _lp(np.r_[np.zeros(n), np.ones(m)], a, b,
                                   bounds=_fd_bounds(valid_fd, radius)+[(0., None)]*m)
                if proposal.status != 0:
                    stage['status'] = 'local guidance unavailable'; break
                delta = effective_G@proposal.x[:n]; accepted = False; protection_rejected = False
                for scale in (1., .5):
                    trial, trial_point = sample(x+scale*delta, f'step-{iteration}-{scale}')
                    if not _preserves_protection(trial, protected_thresholds):
                        protection_rejected = True
                        meta.setdefault('native_protection_rejections', []).append(dict(
                            stage=label, iteration=iteration, scale=scale,
                            violated={name: trial.get('margins', {}).get(name) for name, value in
                                      protected_thresholds.items()
                                      if trial.get('margins', {}).get(name, -float('inf')) < value-1e-9}))
                        continue
                    if trial['physics_feasible'] or objective(trial) < objective(result)-1e-6:
                        x, result = trial_point.copy(), trial
                        accepted = True; radius = min(.08, radius*1.2); break
                # Keep improvements discovered by exact finite differences,
                # even if the linear model's combined step did not improve.
                if local_merit < objective(result):
                    x, result = local_x.copy(), local_result
                    accepted = True
                if protection_rejected:
                    radius *= .5
                if not accepted:
                    if not protection_rejected:
                        radius *= .5
                    if radius < .002:
                        break
        except (TimeoutError, ValueError, KeyError, ArithmeticError, OSError) as error:
            stage['error'] = f'{type(error).__name__}: {error}'
        stage.update(ended_s=time.monotonic()-started, best_merit=local_merit if math.isfinite(local_merit) else None)
        if local_result is not None:
            _write(directory, label+'-plan.json', active.candidate_plan(local_x*active.capacity_kg).model_dump(mode='json'))
        return dict(evaluator=active, x=local_x, result=local_result, merit=local_merit)

    try:
        evaluator = PhysicsEvaluator(instance, clean, cache_size=64)
        evaluators.append(evaluator)
        meta['native_source_before_sha256'] = _native_manifest()
        requested = set(instance.load_list)
        meta['complete_stow'] = requested <= {p.container_id for p in evaluator.ship.placements}
        if not meta['complete_stow']:
            raise ValueError('ballast requires all requested cargo loaded')
        # Exact source-bound securing lower bound, before expensive curves.
        # It rules out this fixed cargo stow only; another arrangement remains
        # undecided. No sampled GM or local LP rejection enters this gate.
        bound_path = ROOT/'experiments/mvp/frontier/lab/c1/fx-finish/early_bound.py'
        bound_module = _load(bound_path, '_c4_benders_securing_bound')
        before = time.monotonic()
        bound = bound_module.securing_bound(instance, evaluator.ship)
        meta.update(securing_bound=bound, securing_bound_s=time.monotonic()-before,
                    securing_bound_source_sha256=hashlib.sha256(bound_path.read_bytes()).hexdigest())
        if bound.get('proof'):
            meta.update(status='infeasible_fixed_stow', proof_scope='this exact cargo stow, all ballast fills',
                        binding_rule='iacs.member_irs.container_securing',
                        wall_s=time.monotonic()-started, native_source_sha256=_native_manifest())
            _write(directory, 'rejected-plan.json', clean.model_dump(mode='json'))
            _write(directory, 'finish-metadata.json', meta)
            return None, meta
        rows, groups, geometry, params = _geometry_census(evaluator)
        meta.update(tank_geometry=rows, directions_geometry_derived=True,
                    initial_tank_masses_kg=dict(zip(evaluator.tank_ids, map(float, evaluator.initial_masses_kg))),
                    input_identity=evaluator.context_identity)
        initial = evaluator.initial_fractions.copy()
        evaluate(initial, 'native-initial-tanks', require_complete=False)
        seeds = [initial]
        # Stable C2 CODE import only; its numerical LP starts from this plan
        # and its checker-owned hull, not from any retained ballast solution.
        if not best_result['physics_feasible'] and time.monotonic() < search_deadline:
            path = ROOT/'experiments/mvp/frontier/lab/c2/c2-benders/ballast.py'
            affine = _load(path, '_c4_cold_affine_seed')
            meta['affine_source_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            linear = affine.solve_ballast(instance, clean,
                budget_s=min(15., max(0., search_deadline-time.monotonic())),
                max_iterations=3, search_gm_floor=.6, exact_recheck=False)
            linear.pop('candidate_plan', None)
            meta['affine_seed'] = linear
            if linear.get('tank_masses_kg'):
                masses = np.array([linear['tank_masses_kg'][name] for name in evaluator.tank_ids])
                _unique_seed(seeds, masses/evaluator.capacity_kg)
                proposals = meta.setdefault('seed_proposals', [])
                # Explore the missing whole-wide-pair regime even when one
                # or both partners are full. Pair selection uses geometry.
                for ordinal, group in enumerate(_wide_pairs(rows, groups, masses)[:2]):
                    details = []
                    one = _narrow_redistribute(evaluator, masses, rows, groups, selected=group,
                                             whole_pair=True, diagnostics=details)
                    row = dict(kind='whole_wide_pair', pair=[rows[j]['id'] for j in group],
                               details=details)
                    row['added'] = _unique_seed(seeds, one/evaluator.capacity_kg)
                    proposals.append(row)
                    if ordinal == 0 and details and details[-1]['status'] == 'candidate':
                        descendant_parent = one
                        try:
                            points, ray = _large_fill_ray(evaluator, one, rows, groups, geometry, params,
                                min(search_deadline, time.monotonic()+30.))
                            meta['large_fill_ray'] = ray
                            for trial, point_info in points:
                                proposals.append(dict(kind='large_narrow_fill_ray', **point_info,
                                    added=_unique_seed(seeds, trial/evaluator.capacity_kg)))
                            if points:
                                descendant_parent = points[-1][0]
                        except (TimeoutError, ValueError, ArithmeticError) as error:
                            meta['large_fill_ray'] = dict(status='unavailable', error=f'{type(error).__name__}: {error}')
                        # Spend remaining slots on retained pressed variants
                        # of the substantial-fill family, not costly variants
                        # of the second (often low-GM) emptied wide pair.
                        for fraction in (.99, .985):
                            trial = _nearfull_seed(descendant_parent, evaluator.capacity_kg, rows, groups,
                                                  instance.vessel.hull.depth_m, fraction)
                            proposals.append(dict(kind='retained_pressed_descendant',
                                parent_pair=row['pair'], fraction=fraction,
                                added=_unique_seed(seeds, trial/evaluator.capacity_kg)))
                redistributed = _narrow_redistribute(evaluator, masses, rows, groups)
                proposals.append(dict(kind='aggregate_slack',
                    added=_unique_seed(seeds, redistributed/evaluator.capacity_kg)))
                # Preserve empty/asymmetric pair status; `any` here would
                # silently refill an emptied partner beside a full tank.
                for fraction in (.99, .985):
                    trial = _nearfull_seed(redistributed, evaluator.capacity_kg, rows, groups,
                                          instance.vessel.hull.depth_m, fraction)
                    proposals.append(dict(kind='aggregate_retained_pressed_descendant', fraction=fraction,
                        added=_unique_seed(seeds, trial/evaluator.capacity_kg)))
        meta['seed_count'] = len(seeds)
        evaluated_seeds = [initial.copy()]
        for k, x in enumerate(seeds[1:]):
            if best_result['physics_feasible']:
                break
            masses = _balance(evaluator, x*evaluator.capacity_kg, rows, groups, geometry, params,
                              diagnostics=meta.setdefault('seed_balancing', []))
            balanced = masses/evaluator.capacity_kg
            if not _unique_seed(evaluated_seeds, balanced):
                continue
            evaluate(balanced, f'geometry-seed-{k}', require_complete=False)
        meta['seed_evaluations'] = len(evaluated_seeds)
        if not best_result['completed']:
            raise ValueError('all cold seed physics reports unavailable')
        # First optimize only the original current-run cargo. Leave a real
        # budget for constructive repair and a new ballast stage afterwards.
        remaining = max(0., search_deadline-time.monotonic())
        first_until = min(search_deadline, time.monotonic()+min(240., .45*remaining))
        if not best_result['physics_feasible']:
            run_stage(best_evaluator, best_x, 'original-cargo', first_until, 3)
        if (not best_result['physics_feasible'] and best_result['completed'] and
                search_deadline-time.monotonic() > 120.):
            repair_path = HERE/'repair.py'
            if repair_path.exists():
                repair = _load(repair_path, '_c4_benders_repair_current')
                current_plan = best_evaluator.candidate_plan(best_x*best_evaluator.capacity_kg)
                candidates, repair_meta = repair.refine_current(instance, current_plan, best_result,
                    seed=seed+17, budget_s=min(60., max(0., search_deadline-time.monotonic()-100.)))
                meta['repair'] = repair_meta
                repaired_stages = []
                for k, repaired in enumerate(candidates[:2]):
                    if time.monotonic() >= search_deadline:
                        break
                    newer = PhysicsEvaluator(instance, repaired, cache_size=16)
                    repaired_rows, repaired_groups, repaired_geometry, repaired_params = _geometry_census(newer)
                    balanced = _balance(newer, newer.initial_masses_kg, repaired_rows, repaired_groups,
                        repaired_geometry, repaired_params, diagnostics=meta.setdefault('repair_balancing', []))
                    # Attach the exact balanced current-run vector to this
                    # new cargo before its first oracle evaluation/cache key.
                    repaired = newer.candidate_plan(balanced)
                    newer = PhysicsEvaluator(instance, repaired, cache_size=16)
                    evaluators.append(newer)
                    evaluator, stage_label = newer, f'repaired-cargo-{k}-initial'
                    # The repair returns the current run's ballast condition;
                    # no cold reinitialization, archive seed, or old-cargo cache.
                    answer = evaluate(newer.initial_fractions, stage_label, require_complete=False)
                    meta.setdefault('repair_physics', []).append(answer)
                    repaired_stages.append(dict(evaluator=newer, x=newer.initial_fractions.copy(),
                                                result=answer, merit=_merit(answer), ordinal=k))
                    if answer['physics_feasible']:
                        break
                # Even a temporarily worse repaired cargo gets a fresh local
                # ballast solve: a cargo change can move the best tank recipe.
                ordered = sorted(repaired_stages, key=lambda item: item['merit'])
                for k, stage in enumerate(ordered):
                    if best_result['physics_feasible'] or time.monotonic() >= search_deadline:
                        break
                    remaining = max(0., search_deadline-time.monotonic())
                    until = (search_deadline if k == len(ordered)-1 else
                             time.monotonic()+.6*remaining)
                    run_stage(stage['evaluator'], stage['x'], f'repaired-cargo-{stage["ordinal"]}',
                              until, min(5, 16-meta['slp_iterations_used']))
        # Use remaining work on the best ATOMIC cargo/tank record. This avoids
        # ending after one repair check with most of the map budget unused.
        if (not best_result['physics_feasible'] and best_result['completed'] and
                search_deadline-time.monotonic() > 15. and meta['slp_iterations_used'] < 16):
            # Refine the best native-secure/loading-valid point by area,
            # rather than starting from an elastic Pareto point that lost
            # securing reserve for a slightly smaller area deficit.
            final_evaluator = protected_evaluator if protected_result is not None else best_evaluator
            final_x = protected_x if protected_result is not None else best_x
            run_stage(final_evaluator, final_x, 'final-current-best', search_deadline,
                      16-meta['slp_iterations_used'])
        meta['status'] = 'physics_feasible' if best_result['physics_feasible'] else 'search_unknown'
    except (TimeoutError, ValueError, KeyError, ArithmeticError, OSError) as error:
        meta['error'] = f'{type(error).__name__}: {error}'
    if protected_result is not None and (best_result is None or not best_result['physics_feasible']):
        best_evaluator, best_x, best_result = protected_evaluator, protected_x.copy(), protected_result
        best_value = _merit(best_result)
        _write(directory, 'best-physics.json', best_result)
    candidate = (best_evaluator.candidate_plan(best_x*best_evaluator.capacity_kg)
                 if best_x is not None else clean)
    if protected_result is not None:
        meta['protected_incumbent'] = dict(cargo_context=protected_evaluator.context_identity,
            area_merit=float(protected_rank[0]), elastic_merit=float(protected_rank[1]),
            fractions=protected_x.tolist(), families='all native lashing and loading margins',
            full_checker_passed=None)
        _write(directory, 'protected-candidate-plan.json',
               protected_evaluator.candidate_plan(protected_x*protected_evaluator.capacity_kg).model_dump(mode='json'))
    if evaluator is not None:
        meta.update(evaluations=sum(ev.evaluations for ev in evaluators),
                    native_part_calls=sum(row['native_part_calls'] for row in meta['trace']),
                    cache_hits=sum(ev.cache_hits for ev in evaluators),
                    best_merit=best_value if math.isfinite(best_value) else None,
                    best_physics=best_result)
    # Always retain the best failure and its exact rule evidence. A completed
    # public check may take longer than the cooperative reserve on a cold hull;
    # caller's per-map watchdog remains authoritative.
    if time.monotonic() < deadline and best_result is not None:
        from stowage.checker import check
        before = time.monotonic(); report = check(instance, candidate)
        meta.update(full_checker_calls=1, full_checker_s=time.monotonic()-before,
                    full_checker_passed=report.passed, full_checker_report=report.model_dump(mode='json'),
                    binding_rule_counts=dict(Counter(v.rule_id for v in report.violations if v.severity == 'hard')))
        if report.passed:
            meta['status'] = 'full_checker_pass'
    if best_result:
        meta['binding_margins'] = dict(sorted(best_result['raw_margins'].items(), key=lambda kv: kv[1])[:12])
    after_sources = _native_manifest()
    meta.update(wall_s=time.monotonic()-started, native_source_sha256=after_sources,
                native_sources_unchanged=all(after_sources.get(path) == digest for path, digest in
                                            meta.get('native_source_before_sha256', {}).items()))
    _write(directory, 'candidate-plan.json', candidate.model_dump(mode='json'))
    _write(directory, 'finish-metadata.json', meta)
    return candidate, meta

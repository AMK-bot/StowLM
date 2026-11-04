"""Current-run trim/draught ballast repair with full-pass incumbent fallback.

Adapted from c4/c2-layout/ballast_opt.py's finite-difference trust-region LP.
No archived stow or recipe is read. All output/cache paths are owned here.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
from time import monotonic
import warnings

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
TRIM_DRAUGHT_RULES = frozenset(('iscode.loading.trim', 'iscode.loading.draft', 'port.call.draft'))


def trim_draught_only(report):
    """Fail closed on unknown IDs and umbrella rules, even if messages say draft."""
    rules = {v.rule_id for v in report.violations if getattr(v, 'severity', 'hard') == 'hard'}
    return bool(rules) and rules <= TRIM_DRAUGHT_RULES


def _target(name):
    metric = name.rsplit('/', 1)[-1]
    return metric in {'trim_m', 'trim_min_m', 'draught_aft_m', 'draught_fwd_m',
                      'draught_fwd_min_m', 'port_draught_m'}


def _check(instance, plan):
    from stowage.checker import check
    return check(instance, plan)


def _complete(instance, plan):
    from stowage.moves import apply_plan
    replay = apply_plan(instance, plan)
    return bool(replay.legal and set(instance.load_list) <=
                {p.container_id for p in replay.final_state.ship.placements})


def _physics_class():
    # Import the exact c4 evaluator privately; avoid a same-name physics module
    # already loaded by another experiment. This source reads no stored plan.
    name = '_c6_cover_ballast_physics'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, LAB/'c4/c2-layout/physics.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].PhysicsEvaluator


def _directions(evaluator, max_groups=4):
    """Pair fills at both longitudinal extremes plus large central/heel tanks."""
    import numpy as np
    groups = defaultdict(list)
    for i, name in enumerate(evaluator.tank_ids):
        groups[name.rsplit('-', 1)[0]].append(i)
    if not groups:
        return np.zeros((len(evaluator.tank_ids), 0)), []
    position = {name: sum((evaluator.tanks[i].x_aft_m+evaluator.tanks[i].x_fore_m)/2
                         for i in members)/len(members) for name, members in groups.items()}
    order = sorted(groups, key=lambda name: (position[name], name))
    chosen = list(dict.fromkeys([order[0], order[-1]]))
    by_capacity = sorted(groups, key=lambda name: (-sum(evaluator.capacity_kg[i] for i in groups[name]), name))
    chosen = (chosen + [name for name in by_capacity if name not in chosen])[:max_groups]
    matrix = np.zeros((len(evaluator.tank_ids), len(chosen)))
    for j, name in enumerate(chosen):
        matrix[groups[name], j] = 1.
    return matrix, chosen


def _difference_step(x, direction):
    """C4's one-sided probe, staying on the same 98%-pressed liquid regime."""
    import numpy as np
    active = np.flatnonzero(direction)
    if not len(active):
        return 0.
    lower = np.where(x[active] >= .98, .9800001, 0.)
    upper = np.where(x[active] < .98, .9799999, 1.)
    quotients = np.array([(lower-x[active])/direction[active], (upper-x[active])/direction[active]])
    low, high = float(np.max(np.min(quotients, axis=0))), float(np.min(np.max(quotients, axis=0)))
    up, down = max(0., high), max(0., -low)
    available = max(up, down)
    return (1. if up >= down else -1.)*min(.01, available*.7) if available > 1e-5 else 0.


def _optimize(instance, plan, deadline, *, out, max_evaluations, max_iterations, diagnostics):
    """Search guidance preserves all non-target exact margins as hard LP rows.

    Nonlinearity means a linear row is not a physical certificate. Trial
    evaluations and final full checking are mandatory, with no peeled rule.
    """
    import numpy as np
    from scipy.optimize import linprog, OptimizeWarning
    evaluator = _physics_class()(instance, plan)
    x = np.clip(evaluator.initial_fractions, 0., 1.)
    groups, group_names = _directions(evaluator, max_groups=max(1, min(4, max_evaluations-4)))
    diagnostics.update(tank_groups=group_names, all_voyage_states=True,
                       optimizer='c4 finite-difference trust LP, trim/draught target, hard other margins')
    if not groups.shape[1]:
        diagnostics['search_status'] = 'no_adjustable_tanks'
        return None
    evaluations, trace = 0, []
    names = None

    def evaluate(y, label):
        nonlocal evaluations, names
        if evaluations >= max_evaluations or monotonic() >= deadline:
            raise TimeoutError('ballast evaluation budget exhausted')
        result = evaluator.evaluate_fractions(y, parts=('stability', 'lashing'), deadline=deadline)
        evaluations += 1
        if not result['completed']:
            raise ValueError('incomplete production physics evaluation')
        if names is None:
            names = sorted(k for k in result['margins'] if not any(
                word in k for word in ('weather_table_applicability', 'unavailable', 'unrepresented')))
        if not set(names) <= result['margins'].keys():
            raise ValueError('production margin shape changed')
        values = np.asarray([result['margins'][k] for k in names], float)
        if not np.all(np.isfinite(values)):
            raise ValueError('nonfinite production margin')
        trace.append(dict(label=label, evaluation=evaluations,
                          physics_feasible=bool(result['physics_feasible']),
                          target_breach=float(np.maximum(-values[[ _target(k) for k in names]], 0.).sum()),
                          minimum_margin=float(values.min(initial=0.))))
        diagnostics['evaluations'] = evaluations
        (out/'trace.json').write_text(json.dumps(trace, indent=2, allow_nan=False)+'\n')
        return values, result

    values, result = evaluate(x, 'initial')
    target = np.asarray([_target(name) for name in names], bool)
    if not target.any():
        diagnostics['search_status'] = 'no_loading_target_margins'
        return None
    if np.any(values[~target] < -1e-8):
        diagnostics['search_status'] = 'unrelated_physics_breach'
        return None
    if result['physics_feasible']:
        return evaluator.candidate_plan(x*evaluator.capacity_kg)
    best, best_result = x.copy(), result
    best_merit = float(np.maximum(-values[target], 0.).sum())
    radius = .06
    try:
        for turn in range(max_iterations):
            derivative = np.zeros((len(values), groups.shape[1]))
            for j in range(groups.shape[1]):
                step = _difference_step(x, groups[:, j])
                if not step:
                    continue
                y = np.clip(x+step*groups[:, j], 0., 1.)
                probe, assessed = evaluate(y, f'difference-{turn}-{j}')
                derivative[:, j] = (probe-values)/step
                merit = float(np.maximum(-probe[target], 0.).sum())
                if np.all(probe[~target] >= -1e-8) and (assessed['physics_feasible'] or merit < best_merit):
                    best, best_result, best_merit = y.copy(), assessed, merit
                if assessed['physics_feasible']:
                    diagnostics['search_status'] = 'physics_feasible_probe'
                    return evaluator.candidate_plan(y*evaluator.capacity_kg)
            # Only existing trim/draught breaches receive slacks. All other
            # stability/loading/securing margins are hard on the linear model.
            count, targets = groups.shape[1], int(target.sum())
            a = np.c_[-derivative, np.zeros((len(values), targets))]
            a[np.flatnonzero(target), count+np.arange(targets)] = -1.
            a = np.vstack([a, np.c_[groups, np.zeros((len(x), targets))],
                           np.c_[-groups, np.zeros((len(x), targets))]])
            low = np.where(x >= .98, .98000001, 0.)
            high = np.where(x < .98, .97999999, 1.)
            b = np.r_[values-target.astype(float)*1e-4, high-x, x-low]
            cost = np.r_[np.zeros(count), np.ones(targets)]
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', OptimizeWarning)
                lp = linprog(cost, A_ub=a, b_ub=b,
                             bounds=[(-radius, radius)]*count+[(0., None)]*targets,
                             method='highs', options={'threads':1, 'time_limit':min(1., max(.01, deadline-monotonic()))})
            if lp.status != 0:
                diagnostics['search_status'] = 'local_hard_guard_lp_no_step'
                break
            step = groups @ lp.x[:count]
            accepted = False
            for scale in (1., .5):
                trial = np.clip(x+scale*step, 0., 1.)
                trial_values, assessed = evaluate(trial, f'trust-{turn}-{scale}')
                merit = float(np.maximum(-trial_values[target], 0.).sum())
                if np.all(trial_values[~target] >= -1e-8) and (assessed['physics_feasible'] or merit < best_merit-1e-8):
                    x, values, result = trial, trial_values, assessed
                    best, best_result, best_merit = trial.copy(), assessed, merit
                    accepted = True
                    break
            if result['physics_feasible']:
                diagnostics['search_status'] = 'physics_feasible_trust_step'
                return evaluator.candidate_plan(x*evaluator.capacity_kg)
            if not accepted:
                radius *= .5
    except TimeoutError as exc:
        diagnostics['search_status'] = str(exc)
    diagnostics.setdefault('search_status', 'bounded_search_no_physics_pass')
    diagnostics['best_target_breach'] = best_merit
    # A final full check is still useful if all named target margins reached
    # zero but the subreport retained a discrete flag. Never call this a pass.
    return evaluator.candidate_plan(best*evaluator.capacity_kg) if best_merit < 1e-8 else None


def repair(instance, plan, deadline, *, incumbent=None, incumbent_lifts=None,
           out=None, max_evaluations=12, max_iterations=2, metadata=None):
    """Return (strictly improving full-pass plan, diagnostics), else incumbent.

    ``deadline`` is absolute monotonic time. If no incumbent object is supplied,
    an incumbent lift threshold is required and the fallback is None. Paths and
    serialized plans cannot be inputs: the caller supplies current-run objects.
    """
    started = monotonic()
    info = dict(kind='cover_ballast_trim_draught', cold_current_run_only=True,
                accepted=False, status='incumbent_retained', evaluations=0)
    if metadata is not None:
        metadata['cover_ballast'] = info
    fallback = incumbent
    previous_cache = os.environ.get('STOWAGE_VESSEL_CACHE_DIR')
    try:
        os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'vessel-cache')
        if not hasattr(plan, 'model_copy') or isinstance(plan, (str, Path, dict)):
            raise TypeError('repair requires a current-run concrete MovePlan')
        if incumbent is not None:
            if isinstance(incumbent, (str, Path, dict)):
                raise TypeError('incumbent must be a current-run concrete MovePlan')
            reference = _check(instance, incumbent)
            if not reference.passed or not _complete(instance, incumbent):
                info['status'] = 'invalid_incumbent'
                return fallback, info
            incumbent_lifts = float(reference.count.weighted_nonproductive)
        if incumbent_lifts is None or not math.isfinite(float(incumbent_lifts)):
            info['status'] = 'missing_incumbent_lift_threshold'
            return fallback, info
        report = _check(instance, plan)
        info['initial_rules'] = sorted({v.rule_id for v in report.violations})
        info['incumbent_lifts'] = float(incumbent_lifts)
        if not _complete(instance, plan):
            info['status'] = 'incomplete_or_illegal_candidate'
            return fallback, info
        if report.passed:
            candidate = plan
        else:
            if not trim_draught_only(report):
                info['status'] = 'not_only_trim_draught'
                return fallback, info
            # Ballast cannot improve cargo lift count; do not spend search on a
            # replay already no cheaper than the known incumbent.
            if float(report.count.weighted_nonproductive) >= float(incumbent_lifts)-1e-9:
                info['status'] = 'no_lift_improvement_available'
                return fallback, info
            remaining = deadline-monotonic()
            if remaining < 10.:
                info['status'] = 'insufficient_ballast_budget'
                return fallback, info
            digest = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()[:16]
            safe_id = re.sub(r'[^A-Za-z0-9_.-]', '_', str(instance.id))
            destination = (Path(out) if out is not None else HERE/'cover-ballast'/f'{safe_id}-{digest}').resolve()
            if not destination.is_relative_to(HERE.resolve()):
                raise ValueError('ballast output must stay in c6/c6-lifts')
            destination.mkdir(parents=True, exist_ok=True)
            os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'vessel-cache')
            candidate = _optimize(instance, plan, deadline-6., out=destination,
                                  max_evaluations=max_evaluations, max_iterations=max_iterations,
                                  diagnostics=info)
            info['output'] = str(destination)
            if candidate is None:
                info['status'] = 'no_ballast_candidate'
                return fallback, info
        final = _check(instance, candidate)
        info.update(final_passed=bool(final.passed),
                    final_rules=sorted({v.rule_id for v in final.violations}),
                    candidate_lifts=float(final.count.weighted_nonproductive))
        if final.passed and _complete(instance, candidate) and float(final.count.weighted_nonproductive) < float(incumbent_lifts)-1e-9:
            info.update(accepted=True, status='full_pass_improved')
            return candidate, info
        info['status'] = 'full_check_or_improvement_rejected'
        return fallback, info
    except Exception as exc:
        info.update(status='incumbent_retained_exception', error=f'{type(exc).__name__}: {exc}')
        return fallback, info
    finally:
        if previous_cache is None:
            os.environ.pop('STOWAGE_VESSEL_CACHE_DIR', None)
        else:
            os.environ['STOWAGE_VESSEL_CACHE_DIR'] = previous_cache
        info['wall_s'] = monotonic()-started

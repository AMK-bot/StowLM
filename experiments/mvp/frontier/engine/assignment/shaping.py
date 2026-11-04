# Copied from experiments/mvp/frontier/lab/c6/c5-securing/method.py (level, shape); engine-local imports.
"""Cold double-bottom shaping is guidance; only the full checker accepts."""
import numpy as np
from .physics import PhysicsEvaluator
from .stow import exact_ratio
from stowage.checker import visibility
from stowage.checker.full import departure_condition


def level(instance, evaluator, fractions):
    """Bring the list near zero with the heel pair: two secant steps on real loading conditions."""
    ids = list(evaluator.tank_ids)
    if 'HEEL-P' not in ids or 'HEEL-S' not in ids:
        return fractions
    p_tank, s_tank = ids.index('HEEL-P'), ids.index('HEEL-S')

    def heel(x):
        ship = departure_condition(instance, evaluator.candidate_plan(np.clip(x, 0., 1.)*evaluator.capacity_kg))
        return float(visibility._loading_condition(instance, ship).heel_rad)
    x = fractions.copy()
    x[p_tank], x[s_tank] = max(x[p_tank], .25), max(x[s_tank], .25)
    now = heel(x)
    for _ in range(2):
        if abs(now) < .002:
            break
        step = .02
        y = x.copy()
        y[p_tank] += step; y[s_tank] -= step
        slope = (heel(y)-now)/(2*step)
        if slope == 0.:
            break
        change = float(np.clip(-now/slope/2, -.24, .24))
        x[p_tank] += change; x[s_tank] -= change
        now = heel(x)
    return x


def shape(instance, plan, floor):
    """Most double-bottom ballast that meets securing, drained in steps, kept level and under the draught limit.

    Full tanks are the stiffest ship. Draining lowers GM and every securing ratio with it, down to the GM floor the
    stability rules need. Tank masses come from this plan's own evaluator. The checker decides; this only proposes.
    """
    evaluator = PhysicsEvaluator(instance, plan)
    start = evaluator.initial_fractions.copy()
    bottoms = np.array([name.startswith('DB-') for name in evaluator.tank_ids])
    hull = instance.vessel.hull
    limit = min(hull.max_draught_m, instance.port.max_draught_m or hull.max_draught_m)
    best = None
    for scale in (1., .875, .75, .625, .5, .375, .25, .125, 0.):
        fractions = start.copy()
        fractions[bottoms] *= scale
        fractions = level(instance, evaluator, fractions)
        trial = evaluator.candidate_plan(np.clip(fractions, 0., 1.)*evaluator.capacity_kg)
        gm, ratio, over = exact_ratio(instance, trial)
        c = visibility._loading_condition(instance, departure_condition(instance, trial))
        draught = max(c.t_aft_m, c.t_fwd_m)
        miss = max(0., ratio-.995)+.3*max(0., floor-gm)+max(0., draught-limit+.02)
        row = (miss, ratio, scale, gm, over, trial)
        if best is None or miss < best[0]:
            best = row
        if miss == 0.:
            break
    return best[1:]

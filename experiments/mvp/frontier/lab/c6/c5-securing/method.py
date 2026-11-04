"""Cycle 5: the box-to-slot assignment LP with securing rows, wrapped in an outer loop on GM. See NOTES.md.
# Copied from lab/c5/c5-securing/method.py, then changed for cycle 6.

Stacks are machines and boxes are jobs. At fixed motion every securing load is linear in tier masses, so legality of a
deck stack is a polytope, not a fit. Each round: LP over all box-slot pairs, dive, exact integer tail, replay, then the
actual GM and failed rules set the next round. Cold: reads no stored plan, assignment, recipe or ballast.
"""
from collections import Counter
import json
import os
import random
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stow import LAB, rolling_dive, Settings, Assignment, block_layout, exact_ratio, reference, repair_oog, to_assignment
from e9fix import install
from alns import Scored, Sequencer
from pages_sight import Model
from pages_fast_dense import Model as FastPages
from capacity import Capacity, intervals
from physics import PhysicsEvaluator
import numpy as np
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker import check, visibility
from stowage.checker.full import departure_condition
from stowage.model import MovePlan

ROUNDS = 6
PAIRS_PER_BOX = 80


def assignment_key(actions):
    return tuple(sorted((a.box, a.slot) for a in actions))


def first_guess_gm(hull):
    """A dense feeder with drained double bottoms settles near 7.5 % of its beam. The replayed ship corrects this."""
    return .075*hull.beam_m


def gm_ceiling(instance, problem, deadline):
    """Top of the highest GM band the aggregate capacity bound leaves open, or None if it never closes. See capacity.py."""
    cap = Capacity(instance, problem)
    for low, high in intervals(instance.vessel.hull):
        if monotonic() > deadline:
            return None
        solved = cap.solve(low, high)
        if solved['uncovered'] is not None and solved['uncovered'] > 1e-6:
            return low
    return None


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


def cover_first(problem, model, solver, seed):
    """A complete stow by first fit, for exchange to bring within the securing rows. Page tools from c4/c2-layout."""
    fast = FastPages(problem)
    for retry in range(8):
        chosen = fast.first_fit(set(fast.boxes), set(fast.stacks), random.Random(seed+retry))
        if chosen is not None:
            break
    if chosen is None:
        # Count-bounded column generation and dive, the same fallback as c4/c2-layout/method_dense.py.
        fast.generate(active_boxes=set(fast.boxes), active_stacks=set(fast.stacks), phase=1,
                      deadline=monotonic()+25., max_rounds=32)
        chosen, _ = fast.dive(deadline=monotonic()+35., rng=random.Random(seed), retreats=5)
    if chosen is None:
        return None
    T = model.T
    return {T.box_index[b]: int(T.slot_index[s]) for b, s in fast.assignment(chosen).items()}


def learn(state, report, gm, ratio):
    """What the failed full check says the next round must change. Search preferences, never rule changes."""
    rules = {v.rule_id for v in report.violations}
    weak = any(q in r for r in rules for q in ('second_check', 'weather', 'area', 'a2_3'))
    if weak:
        # Too tender: the ship needs more GM, so the next stow must carry securing at that stiffer motion.
        state['gm_floor'] = max(state['gm_floor'], gm)+.25
        state['gm'] = state['gm_floor']+.1
    elif ratio > state['limit']+.03 or abs(gm-state['gm']) > .15:
        state['gm'] = max(gm+.05, state['gm_floor']+.1)
    for v in report.violations:
        if 'trim' in v.rule_id and v.measured is not None:
            state['shift'] += -1. if v.measured < 0 else 1.
            break
    if any('draft' in r for r in rules) and not any('trim' in r for r in rules):
        state['shift'] += -1. if any('fwd' in v.message for v in report.violations if 'draft' in v.rule_id) else 1.
    if any('visibility' in r for r in rules):
        state['sight_margin'] += .3
    if weak:
        state['vertical'] *= .97


def propose(instance, *, seed, budget_s, context):
    install()
    started = monotonic()
    deadline = started+budget_s
    p = context['problem']
    hull = instance.vessel.hull
    meta = context.setdefault('method_metadata', {})
    meta.update(replay_legal=False, trials=[], loadavg_start=list(os.getloadavg()), work_limits=dict(rounds=ROUNDS),
                scope='development TRAIN; D15 fixed stow and no later loads')
    context['_candidate_plans'] = {}
    total = sum(p.boxes[b].gross_mass_kg for b in instance.load_list)/1000.
    state = dict(gm=first_guess_gm(hull), limit=.95, shift=0., sight_margin=.15, vertical=total*(hull.depth_m+.5), gm_floor=.5)
    meta['gm_ceiling'] = gm_ceiling(instance, p, monotonic()+.1*budget_s)
    if meta['gm_ceiling'] is not None:
        state['gm'] = min(state['gm'], meta['gm_ceiling'])
    draft = .9*min(hull.max_draught_m, instance.port.max_draught_m or hull.max_draught_m)
    passing, failed, seen = [], [], set()
    model, margin = None, None
    for attempt in range(ROUNDS):
        left = deadline-monotonic()
        if left < 40:
            break
        if model is None or margin != state['sight_margin']:
            margin = state['sight_margin']
            model = Model(p, oracle=None, deck_tiers=None, sight_draft_m=draft, sight_roof_margin_m=margin)
        x = hull.lpp_m*.48+state['shift']
        settings = Settings(limit=state['limit'], lcg=(x-3., x+1.), tcg=.08, vertical_max_tm=state['vertical'],
                            pairs_per_box=PAIRS_PER_BOX if len(model.boxes) > 300 else 0,
                            high_tier_rank=((2, 2), (1, 3)) if len(model.boxes) > 300 else (), seed=seed+attempt)
        row = dict(attempt=attempt, gm_reference=state['gm'], limit=state['limit'], shift=state['shift'],
                   sight_margin=margin, vertical_tm=state['vertical'], log=[])
        meta['trials'].append(row)
        solver = Assignment(model, reference(hull, state['gm']), settings, layout=block_layout(model))
        log = []
        placed = state.pop('resume', None)
        if placed is not None:
            # A stow that only needs more GM keeps its layout: re-solve its securing at the stiffer motion.
            solver.build()
            row['resumed'] = True
        elif len(model.boxes) > 300:
            solver, placed = rolling_dive(model, reference(hull, state['gm']), settings, solver.layout, deadline=monotonic()+.7*left, seed=seed+attempt, log=log)
        else:
            placed = solver.dive(deadline=monotonic()+min(.5*left, 70.), seed=seed+attempt, log=log)
        row['dive'] = log[-2:]
        row['dive_s'] = round(monotonic()-started, 1)
        if placed is None:
            placed = cover_first(p, model, solver, seed+attempt)
            row['fallback'] = placed is not None
        if placed is None:
            row['outcome'] = 'no stow'
            continue
        ratios = solver.overages(placed)
        row['ratio_dive'] = max(ratios.values(), default=0.)
        placed, stuck = solver.fill_hold(placed, instance)
        row['e6_left'] = stuck
        if row['ratio_dive'] > state['limit']+.03:
            log = []
            placed, excess = solver.exchange(placed, deadline=monotonic()+min(.3*(deadline-monotonic()), 60.), log=log)
            row.update(exchange_excess=excess, exchange=log[-1:])
            if excess > 1e-9:
                placed, excess = solver.repair(placed, deadline=monotonic()+min(.3*(deadline-monotonic()), 50.), seed=seed+attempt, log=log)
                placed, excess = solver.exchange(placed, deadline=monotonic()+10., log=log)
                row.update(repair_excess=excess, repair_rounds=len(log))
        assignment, illegal = to_assignment(model, placed, solver)
        row['illegal_columns'] = len(illegal)
        assignment = repair_oog(p, model, model.clear_oog(assignment))
        actions = Sequencer(p).order(assignment) if not p.mixes_hold_stack(assignment) else None
        scored = p.score(actions) if actions is not None else None
        if scored is None:
            row['outcome'] = 'no replay'
            continue
        key = assignment_key(scored.actions)
        if key in seen:
            row['outcome'] = 'repeat'
            continue
        seen.add(key)
        meta['replay_legal'] = True
        ratio, scale, gm, over, shaped = shape(instance, scored.plan, state['gm_floor'])
        scored = Scored(scored.actions, scored.objective, shaped)
        row.update(gm_actual=gm, exact_ratio=ratio, chains_over=over, lifts=scored.objective, db_scale=scale)
        context['_candidate_plans'][key] = scored.plan
        if ratio > 1. and attempt+1 < ROUNDS and abs(gm-state['gm']) > .1 and deadline-monotonic() > 40:
            row['outcome'] = 'gm miss'
            failed.append((1, ratio, scored))
            state['gm'] = max(gm+.05, state['gm_floor']+.1)
            continue
        report = check(instance, scored.plan)
        row.update(outcome='passed' if report.passed else 'failed', lifts=float(report.count.weighted_nonproductive),
                   rules=sorted({v.rule_id for v in report.violations}),
                   worst={r: max((v.measured or 0.) for v in report.violations if v.rule_id == r) for r in {v.rule_id for v in report.violations}})
        if report.passed:
            passing.append(scored)
            break
        failed.append((sum(not (q.startswith('iscode') or 'securing' in q) for q in row['rules']), ratio, scored))
        learn(state, report, gm, ratio)
        if any(q in r for r in row['rules'] for q in ('second_check', 'weather', 'area', 'a2_3')) and not row.get('resumed') and not row.get('fallback'):
            state['resume'] = placed
    meta['loadavg_end'] = list(os.getloadavg())
    meta['propose_wall_s'] = monotonic()-started
    failed.sort(key=lambda r: r[:2])
    return [s.actions for s in passing[:1]+[r[2] for r in failed[:1 if passing else 2]]]


def finish(instance, plan, *, seed, finalize_s, label, context):
    """Checked ballast search, copied from c4/c2-layout/method_dense.py with this folder's output path."""
    started = monotonic()
    actions = [a for a in get_action_space('boxslot').from_plan(instance, plan) if isinstance(a, Place)]
    plan = context.get('_candidate_plans', {}).get(assignment_key(actions), plan)
    report = check(instance, plan)
    if report.passed:
        return plan, dict(finisher='checked concrete ballast', passed=True)
    rules = {v.rule_id for v in report.violations}
    if any(not (q.startswith('iscode') or 'securing' in q) for q in rules):
        return None, dict(finisher='geometry rejected before ballast', rules=sorted(rules))
    ratios = [v.measured/v.limit for v in report.violations if 'securing' in v.rule_id
              and v.measured is not None and v.limit is not None and v.limit > 0]
    if max(ratios, default=0.) > 1.3:
        return None, dict(finisher='bounded repair deferred: distant securing violation', rules=sorted(rules),
                          max_securing_ratio=max(ratios))
    from ballast_opt import optimize
    out = HERE/'ballast'/f'{instance.id}-{label}'
    candidate, meta = optimize(instance, plan, out=out, budget_s=max(1., finalize_s-(monotonic()-started)),
                               max_evaluations=30, max_iterations=4)
    if meta['full_checker_passed']:
        return candidate, meta
    return None, meta

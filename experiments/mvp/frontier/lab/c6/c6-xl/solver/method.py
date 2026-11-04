"""Cycle 5: the box-to-slot assignment LP with securing rows, wrapped in an outer loop on GM. See NOTES.md.

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
from fast_engine import install as install_projection
install_projection()  # Plug-in import precedes Problem.init in the cold runner.
from stow import LAB, Settings, Assignment, block_layout, exact_ratio, reference, repair_oog, to_assignment
from e9fix import install
from alns import Sequencer
from xl_sight import Model
from pages_fast_dense import Model as FastPages
from e8_static import install as install_e8
install_e8()
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker import check
from stowage.model import MovePlan

ROUNDS = 5
PAIRS_PER_BOX = 150
XL_THRESHOLD = 500
XL_PAIRS_PER_BOX = 64


def assignment_key(actions):
    return tuple(sorted((a.box, a.slot) for a in actions))


def first_guess_gm(hull):
    """A dense feeder settles near one tenth of its beam. The first round corrects it from the replayed ship."""
    return .1*hull.beam_m


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


def learn(state, instance, report, gm, ratio):
    """What the failed full check says the next round must change. Search preferences, never rule changes."""
    rules = {v.rule_id for v in report.violations}
    if ratio > state['limit'] or abs(gm-state['gm']) > .15:
        state['gm'] = gm+.05
    if any('securing' in r for r in rules):
        state['limit'] = max(.85, state['limit']-.02)
    for v in report.violations:
        if 'trim' in v.rule_id and v.measured is not None:
            state['shift'] += -1. if v.measured < 0 else 1.
            break
    if any('draft' in r for r in rules) and not any('trim' in r for r in rules):
        state['shift'] += -1. if any('fwd' in v.message for v in report.violations if 'draft' in v.rule_id) else 1.
    if any('visibility' in r for r in rules):
        state['sight_margin'] += .3
    if any('second_check' in r for r in rules):
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
    meta['engine_initialization'] = dict(p.engine.diagnostics)
    tables = p.engine.T
    meta['dimensions'] = dict(load_boxes=len(instance.load_list), inventory_boxes=len(instance.containers),
                              table_boxes=len(tables.box_ids), table_slots=len(tables.slot_ids),
                              table_cells=len(tables.cell_ids), table_stacks=len(tables.stack_ids),
                              static_shape=list(tables.static.shape),
                              fixed_load_cells=len(instance.vessel.fixed_load_cells))
    context['_candidate_plans'] = {}
    total = sum(p.boxes[b].gross_mass_kg for b in instance.load_list)/1000.
    state = dict(gm=first_guess_gm(hull), limit=.95, shift=0., sight_margin=.15, vertical=total*(hull.depth_m+.5))
    draft = .9*min(hull.max_draught_m, instance.port.max_draught_m or hull.max_draught_m)
    passing, failed, seen = [], [], set()
    model, margin = None, None
    for attempt in range(ROUNDS):
        left = deadline-monotonic()
        if left < 20:
            break
        if model is None or margin != state['sight_margin']:
            margin = state['sight_margin']
            stage_started = monotonic()
            model = Model(p, oracle=None, deck_tiers=None, sight_draft_m=draft, sight_roof_margin_m=margin)
            meta.setdefault('model_build_s', []).append(monotonic()-stage_started)
            meta['e8_static'] = getattr(p, '_xl_e8_static', {})
            meta.setdefault('sight_static_s', []).append(getattr(model, 'sight_static_s', None))
        x = hull.lpp_m*.48+state['shift']
        settings = Settings(limit=state['limit'], lcg=(x-3., x+1.), tcg=.08, vertical_max_tm=state['vertical'],
                            pairs_per_box=(XL_PAIRS_PER_BOX if len(model.boxes) > XL_THRESHOLD else PAIRS_PER_BOX) if len(model.boxes) > 300 else 0, seed=seed+attempt)
        row = dict(attempt=attempt, gm_reference=state['gm'], limit=state['limit'], shift=state['shift'],
                   sight_margin=margin, vertical_tm=state['vertical'], log=[])
        meta['trials'].append(row)
        stage_started = monotonic()
        solver = Assignment(model, reference(hull, state['gm']), settings, layout=block_layout(model))
        row['assignment_init_s'] = monotonic()-stage_started
        row['solver'] = solver.metrics
        log = []
        stage_started = monotonic()
        placed = solver.dive(deadline=monotonic()+min(.4*left, 60.), seed=seed+attempt, log=log)
        row['dive'] = log
        row['dive_stage_s'] = monotonic()-stage_started
        row['dive_s'] = round(monotonic()-started, 1)
        if placed is None:
            placed = cover_first(p, model, solver, seed+attempt)
            row['fallback'] = placed is not None
        if placed is None:
            row['outcome'] = 'no stow'
            state['limit'] = min(1., state['limit']+.02)
            continue
        stage_started = monotonic()
        ratios = solver.overages(placed)
        row['ratio_dive'] = max(ratios.values(), default=0.)
        if row['ratio_dive'] > state['limit']+.03:
            log = []
            placed, excess = solver.exchange(placed, deadline=monotonic()+min(.3*(deadline-monotonic()), 60.), log=log)
            row.update(exchange_excess=excess, exchange=log[-1:])
            if excess > 1e-9 and attempt > 0 and (len(model.boxes) <= XL_THRESHOLD or not solver.zero_pair_boxes):
                placed, excess = solver.repair(placed, deadline=monotonic()+min(.3*(deadline-monotonic()), 50.), seed=seed+attempt, log=log)
                placed, excess = solver.exchange(placed, deadline=monotonic()+10., log=log)
                row.update(repair_excess=excess, repair_rounds=len(log))
        row['repair_exchange_s'] = monotonic()-stage_started
        stage_started = monotonic()
        assignment, illegal = to_assignment(model, placed, solver)
        row['illegal_columns'] = len(illegal)
        assignment = repair_oog(p, model, model.clear_oog(assignment))
        actions = Sequencer(p).order(assignment) if not p.mixes_hold_stack(assignment) else None
        scored = p.score(actions) if actions is not None else None
        if scored is None:
            row['outcome'] = 'no replay'
            continue
        row['order_score_s'] = monotonic()-stage_started
        key = assignment_key(scored.actions)
        if key in seen:
            row['outcome'] = 'repeat'
            state['limit'] = max(.85, state['limit']-.02)
            continue
        seen.add(key)
        meta['replay_legal'] = True
        gm, ratio, over = exact_ratio(instance, scored.plan)
        row.update(gm_actual=gm, exact_ratio=ratio, chains_over=over, lifts=scored.objective)
        context['_candidate_plans'][key] = scored.plan
        if ratio > 1. and attempt+1 < ROUNDS and abs(gm-state['gm']) > .1 and deadline-monotonic() > 40:
            # A GM miss needs no full check to see: move the reference and go round again.
            row['outcome'] = 'gm miss'
            failed.append((1, ratio, scored))
            state['gm'] = gm+.05
            continue
        stage_started = monotonic()
        report = check(instance, scored.plan)
        row['full_check_s'] = monotonic()-stage_started
        row.update(outcome='passed' if report.passed else 'failed', lifts=float(report.count.weighted_nonproductive),
                   rules=sorted({v.rule_id for v in report.violations}),
                   worst={r: max((v.measured or 0.) for v in report.violations if v.rule_id == r) for r in {v.rule_id for v in report.violations}})
        if report.passed:
            passing.append(scored)
            break
        failed.append((sum(not (q.startswith('iscode') or 'securing' in q) for q in row['rules']), ratio, scored))
        learn(state, instance, report, gm, ratio)
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
    if max(ratios, default=0.) > 1.25:
        return None, dict(finisher='bounded repair deferred: distant securing violation', rules=sorted(rules),
                          max_securing_ratio=max(ratios))
    from ballast_opt import optimize
    out = HERE/'ballast'/f'{instance.id}-{label}'
    candidate, meta = optimize(instance, plan, out=out, budget_s=max(1., finalize_s-(monotonic()-started)),
                               max_evaluations=12, max_iterations=2)
    if meta['full_checker_passed']:
        return candidate, meta
    return None, meta

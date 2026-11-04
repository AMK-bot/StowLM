# Copied from experiments/mvp/frontier/lab/c6/c5-securing/method.py; guarded streaming engine integration, capacity bound excluded.
"""Cold medium assignment LP, double-bottom shaping and local ballast finish.

Only unchanged full-checker passes are held. Every complete replay is
published before optional physics. Caller control exceptions propagate.
"""
import math
import os
import random
from time import monotonic
from types import SimpleNamespace

from .routing import assignment_active

PROPOSAL_S = 300.
FINALIZE_S = 180.
TRIES = 2
ROUNDS = 6
PAIRS_PER_BOX = 80


def _runtime():
    from .stow import (Settings, Assignment, block_layout, exact_ratio, reference,
                       repair_oog, to_assignment, rolling_dive)
    from .pages_sight import Model
    from .pages_fast_dense import Model as FastPages
    from .shaping import shape
    from ..e9fix import install
    from alns import Scored, Sequencer
    from stowage.checker import check
    from stowage.moves import apply_plan
    return SimpleNamespace(**locals())


def limits(context, budget_s=None):
    config = context.get('engine_config')
    proposal = getattr(config, 'assignment_budget_s', PROPOSAL_S) if budget_s is None else budget_s
    finalize = getattr(config, 'assignment_finalize_s', FINALIZE_S)
    tries = getattr(config, 'assignment_tries', TRIES)
    if not all(math.isfinite(value) and value >= 0. for value in (proposal, finalize)):
        raise ValueError('assignment budgets must be finite and nonnegative')
    if not isinstance(tries, int) or isinstance(tries, bool) or tries < 1:
        raise ValueError('assignment tries must be a positive integer')
    return float(proposal), float(finalize), tries


def assignment_key(actions):
    # Preserve alternative complete load orders, including the same final stow.
    return tuple((action.box, action.slot) for action in actions)


def cover_first(problem, model, solver, seed, *, runtime, deadline):
    """Cold pure-page fallback; it does not consume an external stow or recipe."""
    if monotonic() >= deadline:
        return None
    fast = runtime.FastPages(problem)
    chosen = None
    for retry in range(8):
        if monotonic() >= deadline:
            break
        chosen = fast.first_fit(set(fast.boxes), set(fast.stacks), random.Random(seed+retry))
        if chosen is not None:
            break
    if chosen is None and monotonic() < deadline:
        try:
            fast.generate(active_boxes=set(fast.boxes), active_stacks=set(fast.stacks), phase=1,
                          deadline=min(deadline, monotonic()+25.), max_rounds=32)
            if monotonic() < deadline:
                chosen, _ = fast.dive(deadline=min(deadline, monotonic()+35.),
                                      rng=random.Random(seed), retreats=5)
        except TimeoutError:
            # An unfinished master supplies no fallback candidate or proof.
            return None
    if chosen is None:
        return None
    T = model.T
    return {T.box_index[b]: int(T.slot_index[s]) for b, s in fast.assignment(chosen).items()}


def _load_proxy():
    try:
        return list(os.getloadavg())
    except (OSError, AttributeError):
        return None


def _complete(instance, scored, runtime):
    wanted = set(instance.load_list)
    if {action.box for action in scored.actions} != wanted:
        return False
    replay = runtime.apply_plan(instance, scored.plan)
    return replay.legal and wanted <= {q.container_id for q in replay.final_state.ship.placements}


def _retain_partial(instance, model, solver, attempt, context, meta):
    """Expose cold raw partial work; only a future native replay may certify it."""
    fixed = getattr(solver, 'partial_fixed', {})
    if not fixed:
        return
    assignment = {model.T.box_ids[b]: model.T.slot_ids[q] for b, q in fixed.items()}
    if not 0 < len(assignment) < len(instance.load_list) or not set(assignment) <= set(instance.load_list):
        return
    key = tuple(sorted(assignment.items()))
    seen = context.setdefault('_assignment_partial_keys', set())
    if key in seen:
        return
    seen.add(key)
    row = dict(attempt=attempt, assignment=assignment, boxes_assigned=len(assignment),
               complete=False, replay_legal=None, full_checker_passed=None,
               scope='cold raw assignment only; requires native replay and independent tier inspection')
    meta.setdefault('partial_assignments', []).append(row)
    context['assignment_partial_assignments'] = meta['partial_assignments']


def first_guess_gm(hull):
    """A dense feeder with drained double bottoms settles near 7.5 % of its beam. The replayed ship corrects this."""
    return .075*hull.beam_m


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


def plan_key(scored):
    ballast = getattr(scored.plan, 'ballast', None)
    masses = getattr(ballast, 'tank_masses_kg', {}) if ballast is not None else {}
    return assignment_key(scored.actions), ballast is not None, tuple(sorted(masses.items()))


def _publish(context, scored, meta, *, checked=False, report=None):
    key, exact = assignment_key(scored.actions), plan_key(scored)
    seen = context.setdefault('_assignment_checked_keys' if checked else '_assignment_published_keys', set())
    if exact in seen:
        return False
    seen.add(exact)
    if checked:
        context.setdefault('checked_passes', []).append(scored)
        context.setdefault('assignment_reports', {})[key] = report
        meta['full_checker_passes'] += 1
    else:
        context.setdefault('assignment_candidates', []).append(scored)
        meta['complete_replay_candidates'] += 1
    context.setdefault('_candidate_plans', {})[key] = scored.plan
    callback = context.get('publish_candidate')
    if callback is not None:
        try:
            callback(scored, checked=checked, report=report)
        except Exception as error:
            meta.setdefault('publication_errors', []).append(f'{type(error).__name__}: {error}')
            context['_engine_assignment_callback_error'] = error
            raise
    return True


def _finish_runtime():
    from .ballast_opt import optimize
    from alns import Scored
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    from stowage.checker import check
    return SimpleNamespace(**locals())


def _finish_candidate(instance, scored, *, seed, finalize_s, label, context,
                      on_checked=None, on_check=None):
    runtime = _finish_runtime()
    started = monotonic()
    def checked(plan):
        if on_check is not None:
            on_check()
        return runtime.check(instance, plan)
    report = checked(scored.plan)
    if report.passed:
        if on_checked is not None:
            on_checked(scored.plan, report)
        return scored.plan, dict(finisher='checked concrete C6 ballast', passed=True)
    rules = {v.rule_id for v in report.violations}
    if any(not (q.startswith('iscode') or 'securing' in q) for q in rules):
        return None, dict(finisher='geometry rejected before C6 ballast', passed=False, rules=sorted(rules))
    ratios = [v.measured/v.limit for v in report.violations if 'securing' in v.rule_id
              and v.measured is not None and v.limit is not None and v.limit > 0]
    if max(ratios, default=0.) > 1.3:
        return None, dict(finisher='distant securing violation', passed=False, rules=sorted(rules),
                          max_securing_ratio=max(ratios))
    remaining = finalize_s-(monotonic()-started)
    if remaining <= 0.:
        return None, dict(finisher='C6 assignment finishing budget exhausted', passed=False)
    candidate, metadata = runtime.optimize(instance, scored.plan, budget_s=remaining,
        max_evaluations=30, max_iterations=4, on_checked=on_checked, on_check=on_check)
    if metadata.get('full_checker_passed'):
        fresh = checked(candidate)
        if fresh.passed:
            if on_checked is not None:
                on_checked(candidate, fresh)
            return candidate, dict(metadata, passed=True)
    return None, dict(metadata, passed=False)


def iter_candidates(instance, *, seed=0, budget_s=None, context=None):
    """Stream complete cold candidates; retain exact checked passes before callbacks.

    Proposal, local finish and tries use assignment-specific configuration.
    No aggregate-capacity bound or GM-ceiling filter is imported or applied.
    """
    context = context if context is not None else {}
    config = context.get('engine_config')
    active = assignment_active(instance, enabled=getattr(config, 'assignment_lp', True))
    meta = context.setdefault('assignment_metadata', {})
    context.setdefault('method_metadata', {})['assignment'] = meta
    meta.update(active=active, cold=True, source_cycle=6, trials=[], finishing=[],
        complete_replay_candidates=0, full_checker_calls=0, full_checker_passes=0,
        partial_assignments=[], aggregate_capacity_used=False,
        scope='strict unchanged checker; current rule set; D15 fixed stow and no later loads')
    if not active:
        meta['skipped'] = 'assignment LP inactive for tiny/small or disabled'
        return
    if getattr(getattr(instance, 'onboard', None), 'placements', ()):
        meta['skipped'] = 'empty-arrival assignment model; use arrival route'
        return
    proposal_s, finalize_s, tries = limits(context, budget_s)
    meta.update(proposal_budget_s=proposal_s, finalize_budget_s=finalize_s, finish_tries_limit=tries,
                work_limits=dict(rounds=ROUNDS, finish_tries=tries))
    if proposal_s == 0.:
        meta['skipped'] = 'zero assignment budget'
        return
    started = monotonic(); deadline = started+proposal_s
    for key, default in (('assignment_candidates', []), ('checked_passes', []),
                         ('assignment_reports', {}), ('_candidate_plans', {})):
        context.setdefault(key, default)
    runtime = _runtime()
    if getattr(config, 'e9_fix', True):
        runtime.install()
    p = context.get('problem')
    if p is None:
        from alns import Problem
        p = Problem(instance); context['problem'] = p
    hull = instance.vessel.hull
    if hull is None or not instance.load_list:
        meta['skipped'] = 'requires hull and nonempty load list'
        return
    total = sum(p.boxes[b].gross_mass_kg for b in instance.load_list)/1000.
    state = dict(gm=first_guess_gm(hull), gm_floor=.5, limit=.95, shift=0., sight_margin=.15,
                 vertical=total*(hull.depth_m+.5))
    draft = .9*min(hull.max_draught_m, instance.port.max_draught_m or hull.max_draught_m)
    model, margin = None, None
    failed, seen_checks = [], set()
    meta['loadavg_start'] = _load_proxy()
    try:
        for attempt in range(ROUNDS):
            left = deadline-monotonic()
            if left < 40.:
                meta['stop_reason'] = 'proposal budget exhausted before another round'
                break
            row = dict(attempt=attempt, gm_reference=state['gm'], gm_floor=state['gm_floor'],
                limit=state['limit'], shift=state['shift'], sight_margin=state['sight_margin'],
                vertical_tm=state['vertical'], log=[])
            meta['trials'].append(row)
            try:
                if model is None or margin != state['sight_margin']:
                    next_margin = state['sight_margin']
                    model = runtime.Model(p, oracle=None, deck_tiers=None,
                        sight_draft_m=draft, sight_roof_margin_m=next_margin)
                    margin = next_margin
                x = hull.lpp_m*.48+state['shift']
                settings = runtime.Settings(limit=state['limit'], lcg=(x-3., x+1.), tcg=.08,
                    vertical_max_tm=state['vertical'], pairs_per_box=PAIRS_PER_BOX if len(model.boxes)>300 else 0,
                    high_tier_rank=((2,2),(1,3)) if len(model.boxes)>300 else (), seed=seed+attempt)
                solver = runtime.Assignment(model, runtime.reference(hull,state['gm']), settings,
                                            layout=runtime.block_layout(model))
                log=[]
                try:
                    placed=state.pop('resume',None)
                    if placed is not None:
                        solver.build(); row['resumed']=True
                    elif len(model.boxes)>300:
                        solver,placed=runtime.rolling_dive(model,runtime.reference(hull,state['gm']),
                            settings,solver.layout,deadline=min(deadline,monotonic()+.7*left),seed=seed+attempt,log=log)
                    else:
                        placed=solver.dive(deadline=min(deadline,monotonic()+min(.5*left,70.)),seed=seed+attempt,log=log)
                finally:
                    try:
                        _retain_partial(instance,model,solver,attempt,context,meta)
                    except Exception as error:
                        row['partial_retention_error']=f'{type(error).__name__}: {error}'
                row.update(dive=log[-2:],dive_s=monotonic()-started)
                if placed is None:
                    placed=cover_first(p,model,solver,seed+attempt,runtime=runtime,deadline=deadline)
                    row['fallback']=placed is not None
                if placed is None:
                    row['outcome']='no stow found';continue
                ratios=solver.overages(placed)
                row['ratio_dive']=max(ratios.values(),default=0.)
                placed,stuck=solver.fill_hold(placed,instance)
                row['e6_left']=stuck
                if row['ratio_dive']>state['limit']+.03 and monotonic()<deadline:
                    log=[]
                    placed,excess=solver.exchange(placed,deadline=min(deadline,monotonic()+min(.3*(deadline-monotonic()),60.)),log=log)
                    row.update(exchange_excess=excess,exchange=log[-1:])
                    if excess>1e-9 and monotonic()<deadline:
                        placed,excess=solver.repair(placed,deadline=min(deadline,monotonic()+min(.3*(deadline-monotonic()),50.)),seed=seed+attempt,log=log)
                        if monotonic()<deadline:
                            placed,excess=solver.exchange(placed,deadline=min(deadline,monotonic()+10.),log=log)
                        row.update(repair_excess=excess,repair_rounds=len(log))
                assignment,illegal=runtime.to_assignment(model,placed,solver)
                row['illegal_columns']=len(illegal)
                assignment=runtime.repair_oog(p,model,model.clear_oog(assignment))
                if set(assignment)!=set(instance.load_list):
                    row['outcome']='incomplete assignment';continue
                actions=runtime.Sequencer(p).order(assignment) if not p.mixes_hold_stack(assignment) else None
                scored=p.score(actions) if actions is not None else None
                if scored is None or not _complete(instance,scored,runtime):
                    row['outcome']='no complete legal replay';continue
                meta['replay_legal']=True
                row.update(complete_replay_legal=True,lifts=scored.objective)
                if _publish(context,scored,meta):
                    yield scored
                raw=scored
                try:
                    ratio,scale,gm,over,shaped=runtime.shape(instance,scored.plan,state['gm_floor'])
                    scored=runtime.Scored(scored.actions,scored.objective,shaped)
                    if not _complete(instance,scored,runtime):
                        raise ValueError('shaping changed complete legal cargo replay')
                    row['db_scale']=scale
                    if _publish(context,scored,meta):
                        yield scored
                except Exception as error:
                    if error is context.get('_engine_assignment_callback_error'):
                        raise
                    scored=raw
                    gm,ratio,over=state['gm'],None,None
                    row['securing_diagnostic_error']=f'{type(error).__name__}: {error}'
                row.update(gm_actual=gm,exact_ratio=ratio,chains_over=over)
                exact=plan_key(scored)
                if exact in seen_checks:
                    row['outcome']='repeat exact cargo and ballast';continue
                seen_checks.add(exact)
                if (ratio is not None and ratio>1. and attempt+1<ROUNDS and abs(gm-state['gm'])>.1 and deadline-monotonic()>40.):
                    row['outcome']='reference GM miss; full check deferred to local finish'
                    failed.append((1,ratio,scored));state['gm']=max(gm+.05,state['gm_floor']+.1);continue
                meta['full_checker_calls']+=1
                report=runtime.check(instance,scored.plan)
                context['assignment_reports'][assignment_key(scored.actions)]=report
                if report.passed:
                    _publish(context,scored,meta,checked=True,report=report)
                    meta.setdefault('first_full_checker_pass_s',monotonic()-started)
                row.update(outcome='passed' if report.passed else 'failed',
                    checked_lifts=float(report.count.weighted_nonproductive),
                    rules=sorted({v.rule_id for v in report.violations}),
                    worst={r:max((v.measured or 0.) for v in report.violations if v.rule_id==r)
                           for r in {v.rule_id for v in report.violations}})
                if report.passed:break
                failed.append((sum(not(q.startswith('iscode') or 'securing' in q) for q in row['rules']),ratio if ratio is not None else float('inf'),scored))
                learn(state,report,gm,ratio if ratio is not None else 0.)
                if any(q in r for r in row['rules'] for q in ('second_check','weather','area','a2_3')) and not row.get('resumed') and not row.get('fallback'):
                    state['resume']=placed
            except Exception as error:
                if error is context.get('_engine_assignment_callback_error'):raise
                row.update(outcome='search error; retained candidates survive',error=f'{type(error).__name__}: {error}')
        meta['propose_wall_s']=monotonic()-started
        finish_started=monotonic()
        best_pass=min((s.objective for s in context['checked_passes']),default=float('inf'))
        choices=sorted(failed,key=lambda item:item[:2])[:max(0,tries-(1 if context['checked_passes'] else 0))]
        seen_finish=set()
        for rank,(_,_,scored) in enumerate(sorted(choices,key=lambda item:item[2].objective)):
            if plan_key(scored) in seen_finish or scored.objective>=best_pass:continue
            seen_finish.add(plan_key(scored))
            details=dict(rank=rank,search_lifts=scored.objective,budget_s=finalize_s)
            meta['finishing'].append(details)
            def on_check():
                meta['full_checker_calls']+=1
            def on_checked(plan,report):
                held=runtime.Scored(scored.actions,float(report.count.weighted_nonproductive),plan)
                _publish(context,held,meta,checked=True,report=report)
                meta.setdefault('first_full_checker_pass_s',monotonic()-started)
            try:
                candidate,finish_meta=_finish_candidate(instance,scored,seed=seed,
                    finalize_s=finalize_s,label=f'assignment-{rank}',context=context,
                    on_checked=on_checked,on_check=on_check)
                details.update(finish_meta)
                if candidate is not None:
                    finished=runtime.Scored(scored.actions,scored.objective,candidate)
                    if _publish(context,finished,meta):yield finished
                    best_pass=min(best_pass,scored.objective)
            except Exception as error:
                if error is context.get('_engine_assignment_callback_error'):raise
                details.update(passed=False,error=f'{type(error).__name__}: {error}')
        meta['finish_wall_s']=monotonic()-finish_started
    finally:
        meta.setdefault('propose_wall_s',monotonic()-started)
        meta.update(loadavg_end=_load_proxy(),total_wall_s=monotonic()-started,
                    held_checked_passes=len(context['checked_passes']))


def propose(instance, *, seed=0, budget_s=None, context=None):
    return [scored.actions for scored in iter_candidates(instance,seed=seed,budget_s=budget_s,context=context)]


def finish(instance,plan,*,seed=0,finalize_s=None,label='assignment',context=None):
    context=context if context is not None else {}
    config=context.get('engine_config')
    if not assignment_active(instance,enabled=getattr(config,'assignment_lp',True)):
        return None,dict(finisher='assignment inactive for tiny/small',passed=False)
    _,budget,_=limits(context)
    if finalize_s is not None:budget=min(budget,finalize_s)
    runtime=_finish_runtime()
    actions=tuple(a for a in runtime.get_action_space('boxslot').from_plan(instance,plan) if isinstance(a,runtime.Place))
    exact=context.get('_candidate_plans',{}).get(assignment_key(actions),plan)
    scored=runtime.Scored(actions,0.,exact)
    meta=context.setdefault('assignment_metadata',{})
    for key in ('full_checker_calls','full_checker_passes','complete_replay_candidates'):
        meta.setdefault(key,0)
    context.setdefault('method_metadata',{})['assignment']=meta
    before=len(context.get('checked_passes',[]))
    def on_check():
        meta['full_checker_calls']+=1
    def on_checked(candidate,report):
        held=runtime.Scored(actions,float(report.count.weighted_nonproductive),candidate)
        _publish(context,held,meta,checked=True,report=report)
    try:
        return _finish_candidate(instance,scored,seed=seed,finalize_s=budget,label=label,
                                 context=context,on_checked=on_checked,on_check=on_check)
    except Exception as error:
        if error is context.get('_engine_assignment_callback_error'):raise
        held=context.get('checked_passes',[])[before:]
        if held:
            best=min(held,key=lambda value:value.objective)
            return best.plan,dict(passed=True,recovered_checked_pass=True,
                                 error=f'{type(error).__name__}: {error}')
        raise

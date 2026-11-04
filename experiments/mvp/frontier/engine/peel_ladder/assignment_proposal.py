# Copied from experiments/mvp/frontier/lab/c5/c5-peel/assignment_proposal.py; frozen body unchanged.
"""Tier-aware GM feedback around the copied assignment LP, never page fallback."""
from time import monotonic
from assignment_runtime import load_assignment
from tiers import analyze_report
from stowage.checker import check

def _remember(ordinary, instance, problem, context, seen, assignment, *, kind, level):
    """Full-check an unmodified candidate before any heuristic can replace it."""
    actions = ordinary.Sequencer(problem).order(assignment) if not problem.mixes_hold_stack(assignment) else None
    scored = problem.score(actions) if actions is not None else None
    if scored is None:
        return None
    key = ordinary.assignment_key(scored.actions)
    if key in seen:
        return seen[key]
    report = check(instance, scored.plan)
    analysis = analyze_report(report, instance=instance)
    context['_candidate_plans'][key] = scored.plan
    context['_candidate_reports'][key] = report
    context['_candidate_kinds'][key] = kind
    try:
        gm, ratio, over = ordinary.exact_ratio(instance, scored.plan)
    except (ValueError, RuntimeError, ArithmeticError, KeyError):
        gm, ratio, over = None, None, None
    completed = analysis['level'] is not None and analysis['level'] <= level and analysis['ashore_count'] == 0
    diagnostic = dict(kind=kind, gm_actual=gm, securing_ratio_actual=ratio,
        securing_chains_over=over, rules=analysis['broken_rule_ids'],
        checker_level=analysis['level'], boxes_ashore=analysis['ashore_count'],
        lifts=float(report.count.weighted_nonproductive) if report.count is not None else None,
        outcome='completed' if completed else 'not completed at permitted level')
    entry = dict(report=report, analysis=analysis, gm=gm, ratio=ratio,
                 completed=completed, diagnostic=diagnostic)
    seen[key] = entry
    return entry

def propose(instance, *, seed, budget_s, context, level, allow_partial, factory, initial_reference=None):
    ordinary = load_assignment()
    p = context['problem']
    hull = instance.vessel.hull
    deadline = monotonic()+budget_s
    meta = context.setdefault('method_metadata', {})
    meta.update(trials=[], work_limits={'rounds': ordinary.ROUNDS, 'pairs_per_box_large': ordinary.PAIRS_PER_BOX,
                'checked_variants_per_round': 2},
                source='tier-aware assignment LP; no page fallback')
    context['_candidate_plans'] = {}
    context['_candidate_reports'] = {}
    context['_candidate_kinds'] = {}
    total = sum(p.boxes[b].gross_mass_kg for b in instance.load_list)/1000.
    state = dict(gm=ordinary.first_guess_gm(hull), limit=.95, shift=0., sight_margin=.15,
                 vertical=total*(hull.depth_m+.5))
    if initial_reference:
        state['gm'] = initial_reference['gm']
    meta['initial_reference'] = initial_reference or {'source': 'ordinary first-guess construction proxy'}
    draft = .9*min(hull.max_draught_m, instance.port.max_draught_m or hull.max_draught_m)
    model, margin, seen = None, None, {}
    dense = len(instance.load_list) > 60
    for attempt in range(ordinary.ROUNDS):
        left = deadline-monotonic()
        if left < 20.:
            break
        if model is None or margin != state['sight_margin']:
            margin = state['sight_margin']
            model = ordinary.Model(p, oracle=None, deck_tiers=None,
                sight_draft_m=draft, sight_roof_margin_m=margin)
        x = hull.lpp_m*.48+state['shift']
        settings = ordinary.Settings(limit=state['limit'], lcg=(x-3., x+1.) if dense else None,
            tcg=.08 if dense else None,
            vertical_max_tm=state['vertical'] if dense else None, seed=seed+attempt,
            pairs_per_box=ordinary.PAIRS_PER_BOX if len(model.boxes) > 300 else 0)
        solver = factory(model, ordinary.reference(hull, state['gm'],
            draught=initial_reference.get('draught') if initial_reference else None,
            cb=initial_reference.get('cb', .65) if initial_reference else .65), settings,
                         layout=ordinary.block_layout(model))
        row = dict(attempt=attempt, gm_reference=state['gm'], limit=state['limit'],
            shift=state['shift'], sight_margin=margin, allow_partial=allow_partial)
        meta['trials'].append(row)
        log = []
        placed = solver.dive(deadline=monotonic()+min(.4*left, 60.), seed=seed+attempt, log=log)
        row['dive'] = log[-2:]
        if placed is None:
            row['outcome'] = 'no assignment incumbent within work'
            proof = getattr(solver, 'restricted_proof', None)
            if proof:
                row['restricted_model_exhaustion'] = proof
            if proof and proof['phase'] == 'lp' and settings.pairs_per_box == 0:
                # No candidate changed the targets or reference, and every
                # eligible edge is present. Another seed gives the same LP.
                meta['stop_reason'] = 'current complete-edge LP infeasible under these construction targets; map remains unknown'
                break
            continue
        raw_assignment = {model.T.box_ids[box]: model.T.slot_ids[slot] for box, slot in placed.items()}
        raw = _remember(ordinary, instance, p, context, seen, raw_assignment, kind='raw-goal-program', level=level)
        row['raw_candidate'] = raw['diagnostic'] if raw else {'outcome': 'no replay-legal candidate'}
        if raw and raw['completed']:
            row['outcome'] = 'completed'
            break
        ratios = solver.overages(placed)
        row['securing_ratio_reference'] = max(ratios.values(), default=0.)
        # Securing remains relevant through the level testing T2, then is
        # peeled. It must not continue steering L5's higher-tier goals.
        if level <= 4 and row['securing_ratio_reference'] > state['limit']+.03:
            log = []
            placed, excess = solver.exchange(placed,
                deadline=monotonic()+min(.3*max(0., deadline-monotonic()), 60.), log=log)
            row.update(exchange_excess=excess, exchange=log[-1:])
            if excess > 1e-9 and attempt > 0 and deadline-monotonic() > 1.:
                placed, excess = solver.repair(placed,
                    deadline=monotonic()+min(.3*(deadline-monotonic()), 50.), seed=seed+attempt, log=log)
                row.update(repair_excess=excess)
        assignment, illegal = ordinary.to_assignment(model, placed, solver)
        row['illegal_columns'] = len(illegal)
        assignment = ordinary.repair_oog(p, model, model.clear_oog(assignment))
        processed = _remember(ordinary, instance, p, context, seen, assignment, kind='postprocessed', level=level)
        row['postprocessed_candidate'] = processed['diagnostic'] if processed else {'outcome': 'no replay-legal candidate'}
        selected = processed or raw
        row['outcome'] = selected['diagnostic']['outcome'] if selected else 'no replay-legal candidate'
        if selected and selected['completed']:
            break
        if level <= 4 and selected and selected['gm'] is not None and selected['ratio'] is not None:
            ordinary.learn(state, instance, selected['report'], selected['gm'], selected['ratio'])
        # At L5, T2 construction goals are dropped. The next seeded integer
        # round may improve T1 goals; do not tighten trim/sight/securing then.
    meta['elapsed_s'] = budget_s-max(0., deadline-monotonic())
    meta['unique_checked_candidates'] = len(context['_candidate_reports'])
    return list(context['_candidate_plans'].values())

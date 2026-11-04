"""Warm page neighbourhoods and explicit closed-hold designs, full checker gated."""
from collections import Counter, defaultdict
from itertools import combinations
import json
from pathlib import Path
import random
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
sys.path[:0] = [str(HERE), str(LAB/'c1/fx-scout'), str(LAB/'c1/leader')]
# The local page wrapper must precede scout on sys.path.
from pages import Model
from lns import seed_assignment, improve
from repair import repair_oog
from lash_oracle import Fast
from e9fix import install
from alns import Sequencer
from stowage.checker import check
from stowage.conventions import cells_of_slot
from stowage.model.plan import MovePlan
from stowage.moves import apply_plan

install()


def reference(instance, context):
    """Approved TRAIN incumbents only; this entry is explicitly a warm search."""
    for run in ('quick-medium-cached', 'extras-cached'):
        path = LAB/'c2/c2-layout/results'/run/f'{instance.id}.json'
        if path.exists():
            row = json.loads(path.read_text())
            if row.get('passed'):
                good = [r for r in row['method_metadata'].get('layouts', [])
                        if r.get('full_check') and r.get('lifts') == row['lifts']]
                return row['assignment'], MovePlan.model_validate(row['plan']), (good[-1]['settings'] if good else {}), str(path)
    input_path = context.get('instance_path')
    for row in json.loads((HERE/'MANIFEST.json').read_text()):
        if row['input'] == input_path and row['v6_passed']:
            path = Path(row['metadata']).parent/'finished.json'
            plan = MovePlan.model_validate(json.loads(path.read_text())['plan'])
            replay = apply_plan(instance, plan)
            return {p.container_id: p.slot for p in replay.final_state.ship.placements}, plan, {}, str(path)
    return None


def layout_for(model, closed=()):
    capacity = defaultdict(int)
    for page in model.pages:
        if page.kind == '20' and page.cover is not None and page.cover not in closed:
            capacity[page.cover] += len(page.slots)
    need = Counter()
    for b in model.boxes:
        need[model.pod[b]] += float(model.T.units[b])
    layout = {}
    for c in sorted(capacity, key=lambda c: (-capacity[c], c)):
        port = max(need, key=need.get)
        layout[c] = port
        need[port] -= capacity[c]
    return layout


def assignment_score(problem, model, assignment, *, fixed=None):
    assignment = repair_oog(problem, model, model.clear_oog(assignment))
    if fixed and any(assignment[b] != s for b,s in fixed.items()):
        return None
    if problem.mixes_hold_stack(assignment):
        return None
    actions = Sequencer(problem).order(assignment)
    return problem.score(actions) if actions is not None else None


def construct(problem, model, *, deadline, seed):
    start = monotonic()
    root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                          phase=1, deadline=min(deadline, start+8), max_rounds=40)
    row = dict(lp_uncovered=round(root['uncovered'], 4), columns=len(model.columns))
    if root['uncovered'] > 3 or monotonic() >= deadline:
        return None, row
    if model.seed_chosen is None:
        model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                       phase=3, deadline=min(deadline, monotonic()+5), max_rounds=8)
    chosen, info = model.dive(deadline=deadline, rng=random.Random(seed), retreats=3)
    row.update(info)
    if chosen is None:
        return None, row
    chosen, heel = model.balance(chosen, random.Random(seed))
    scored = assignment_score(problem, model, model.assignment(chosen))
    row.update(heel=heel, replay_legal=scored is not None,
               lifts=scored.objective if scored else None, seconds=monotonic()-start)
    return scored, row


def propose(instance, *, seed, budget_s, context):
    install()
    p = context['problem']
    deadline = monotonic()+budget_s
    meta = context.setdefault('method_metadata', {})
    meta.update(trials=[], method='closed covers + page LNS', replay_legal=False)
    (HERE/'trace').mkdir(exist_ok=True)

    def record(row):
        meta['trials'].append(row)
        (HERE/'trace'/f'{instance.id}.json').write_text(json.dumps(meta, indent=2, default=str)+'\n')
        print(instance.id, row.get('kind'), row.get('lifts'), row.get('full_check'), row.get('rules', row.get('status', '')), flush=True)

    ref = reference(instance, context)
    passing, hopeful = [], []
    settings = dict(deck_tiers=3 if len(instance.load_list)>120 else 4,
                    lash_limit=.99, lcg_target=None, hold_reward=1.)
    if ref:
        assignment, ref_plan, old_settings, source = ref
        settings.update(old_settings)
        settings['lash_limit'] = .99
        settings['hold_reward'] = 1.
        meta['warm_reference'] = source
        context['_ballast'] = ref_plan.ballast
        first_model = Model(p)
        actions = Sequencer(p).order(assignment)
        first = p.score(actions) if actions else None
        if first is None:
            record(dict(kind='reference', replay_legal=False))
            return []
        # Check the concrete plan with its recorded ballast, never the metadata flag.
        checked_plan = first.plan.model_copy(update={'ballast': ref_plan.ballast})
        report = check(instance, checked_plan)
        record(dict(kind='reference', lifts=first.objective, replay_legal=True,
                    full_check=report.passed, rules=sorted({v.rule_id for v in report.violations})))
        if report.passed:
            passing.append(first)
        ship = apply_plan(instance, checked_plan).final_state.ship
    else:
        first_model = Model(p)
        first, row = construct(p, first_model, deadline=min(deadline, monotonic()+25), seed=seed)
        if first is None:
            record(dict(kind='cold_reference', **row))
            return []
        assignment = {a.box:a.slot for a in first.actions}
        report = check(instance, first.plan)
        record(dict(kind='cold_reference', full_check=report.passed,
                    rules=sorted({v.rule_id for v in report.violations}), **row))
        if report.passed:
            passing.append(first)
        ship = apply_plan(instance, first.plan).final_state.ship
        context['_ballast'] = first.plan.ballast
        if any('trim' in v.rule_id and v.measured is not None and v.measured < 0 for v in report.violations):
            total = sum(p.boxes[b].gross_mass_kg for b in assignment)
            lcg = sum(p.boxes[b].gross_mass_kg*p.cells[cells_of_slot(s)[0]].lcg_m for b,s in assignment.items())/total
            settings['lcg_target'] = lcg-3.
    meta['replay_legal'] = True
    bound_path = HERE/'bound_results.json'
    if passing and bound_path.exists():
        for certificate in json.loads(bound_path.read_text()):
            if certificate['instance_id'] == instance.id and certificate['lower_bound'] >= min(s.objective for s in passing):
                meta['stopped_at_certified_bound'] = certificate['lower_bound']
                return [s.actions for s in passing]
    oracle = Fast(instance, ship)

    def evaluate(scored, row):
        if scored is None:
            record(row)
            return False
        row.update(replay_legal=True, lifts=scored.objective)
        meta['replay_legal'] = True
        if passing and scored.objective >= min(s.objective for s in passing):
            row['status'] = 'not cheaper than checked incumbent'
            record(row)
            return False
        checked = scored.plan.model_copy(update={'ballast': context.get('_ballast')})
        r = check(instance, checked)
        rules = sorted({v.rule_id for v in r.violations})
        row.update(full_check=r.passed, rules=rules)
        record(row)
        if r.passed:
            passing.append(scored)
            return True
        if all(q.startswith('iscode') for q in rules):
            hopeful.append(scored)
        return False

    # LNS keeps outside pages exact, and charges each used cover and POD edge.
    model = Model(p, oracle=oracle, **settings)
    held_oracle = model.oracle
    try:
        model.oracle = None  # independently checked incumbent bypasses fitted securing gate only
        chosen = seed_assignment(model, assignment)
    except ValueError as e:
        chosen = None
        record(dict(kind='lns_seed', status=str(e)))
    finally:
        model.oracle = held_oracle
    covers = sorted({q.cover for q in model.pages if q.cover is not None})
    if chosen is not None:
        counts = Counter()
        for k in chosen:
            col = model.columns[k]
            if not model.pages[col.page].deck:
                counts[model.pages[col.page].cover] += len(col.boxes)
        for attempt, target in enumerate(sorted(counts, key=lambda c:(counts[c],c))):
            if monotonic() >= deadline-20:
                break
            # Three-hatch neighbourhood, followed by whole-ship repair for a difficult target.
            nearby = sorted(covers, key=lambda c:(c!=target,abs(covers.index(c)-covers.index(target)),c))[:4]
            out = improve(model, chosen, nearby, closed_hold=[target],
                          seconds=min(14,deadline-monotonic()-10), pricing_s=5, seed=seed+attempt,
                          heel_slack_tm=50, lcg_slack_tm=150,
                          hold_only=[b for b in model.boxes if model.T.e6_threshold is not None
                                     and model.T.mass[b] >= model.T.e6_threshold])
            row = {k:v for k,v in out.items() if k not in ('chosen','assignment')}
            row['kind'] = 'page_lns'
            fixed = {b:s for b,s in assignment.items()
                     if all(model.T.stack_cover[int(j)] not in nearby
                            for j in model.T.slot_stacks[model.T.slot_index[s]] if j >= 0)}
            scored = assignment_score(p, model, out['assignment'], fixed=fixed) if out.get('chosen') else None
            if evaluate(scored, row):
                assignment = {a.box:a.slot for a in scored.actions}
                try:
                    model.oracle = None
                    chosen = seed_assignment(model, assignment)
                finally:
                    model.oracle = held_oracle

    # Whole-map designs explicitly leave a subset of hold covers unused.
    for count in range(min(3,len(covers)-1),0,-1):
        trials = list(combinations(covers,count))
        trials.sort(key=lambda cc: (sum(counts.get(c,0) for c in cc) if chosen else 0, cc))
        for closed in trials[:8]:
            if monotonic() >= deadline-20:
                break
            design = Model(p, oracle=oracle, closed_hold=closed, **settings)
            layout = layout_for(design, closed)
            # Build again so the type signatures include the new port permissions.
            design = Model(p, oracle=oracle, closed_hold=closed, layout=layout,
                           layout_deck='no_restow', **settings)
            scored, row = construct(p, design, deadline=min(deadline-5,monotonic()+22), seed=seed+count)
            row.update(kind='closed_design', closed_hold=closed)
            evaluate(scored,row)
        if monotonic() >= deadline-20:
            break
    passing.sort(key=lambda s:s.objective)
    hopeful.sort(key=lambda s:s.objective)
    meta['full_passes'] = len(passing)
    return [s.actions for s in passing[:2]]+[s.actions for s in hopeful[:1]]


def finish(instance, plan, *, seed, finalize_s, label, context):
    from planner import _finish
    plan = plan.model_copy(update={'ballast': context.get('_ballast',plan.ballast)})
    report = check(instance, plan)
    if report.passed:
        return plan, dict(finisher='inherited ballast/direct', passed=True)
    return _finish(instance, plan, seed=seed, finalize_s=finalize_s, label=label)

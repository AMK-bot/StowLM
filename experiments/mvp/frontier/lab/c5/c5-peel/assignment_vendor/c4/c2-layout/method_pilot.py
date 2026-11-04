"""C4 full-cohort moment-aware pages and native area/securing finishing."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
sys.path[:0] = [str(HERE), str(LAB/'c1/fx-scout'), str(LAB/'c1/leader')]
from e9fix import install
from alns import Scored, Sequencer
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker import check
from stowage.model import MovePlan
from stowage.moves import apply_plan
from pages import Model
from lns import seed_assignment, improve
from lash_oracle import Fast
from repair import repair_oog
import constructor

install()


def assignment_key(actions):
    return tuple(sorted((a.box, a.slot) for a in actions))


def reference(instance):
    rows = json.loads((LAB/'c3/c2-layout/ladder_comparison.json').read_text())
    for row in rows:
        if row['input'] and row.get('passed') and Path(row['evidence']).stem == instance.id:
            source = Path(row['evidence'])
            result = json.loads(source.read_text())
            return MovePlan.model_validate(result['plan']), str(source)
    if instance.id == 'random.medium.185100000.0.2':
        source = LAB/'c3/c2-benders/results/verified-independent/plan.json'
        return MovePlan.model_validate(json.loads(source.read_text())), str(source)
    return None


def scored_assignment(problem, model, assignment, fixed=None):
    assignment = repair_oog(problem, model, model.clear_oog(assignment))
    if set(assignment) != set(problem.instance.load_list):
        return None
    if fixed and any(assignment[b] != s for b,s in fixed.items()):
        return None
    if problem.mixes_hold_stack(assignment):
        return None
    actions = Sequencer(problem).order(assignment)
    return problem.score(actions) if actions is not None else None


def propose(instance, *, seed, budget_s, context):
    install()
    started = monotonic()
    deadline = started+budget_s
    p = context['problem']
    meta = context.setdefault('method_metadata', {})
    meta.update(replay_legal=False, trials=[], loadavg_start=list(os.getloadavg()),
                work_limits=dict(construction_attempts=4, full_checks=8,
                                 coverage_rounds=32, improvement_rounds=12, local_passes=4),
                scope='development TRAIN; D15 fixed stow and no later loads')
    diagnostic = HERE/'diagnostics'
    diagnostic.mkdir(exist_ok=True)
    passing, failed, seen = [], [], set()
    context['_candidate_plans'] = {}
    last_report = None

    def assess(scored, lane, details=None):
        nonlocal last_report
        if scored is None or len(meta['trials']) >= 8:
            return None
        key = assignment_key(scored.actions)
        if key in seen:
            return None
        seen.add(key)
        if {a.box for a in scored.actions} != set(instance.load_list):
            return None
        plan = scored.plan
        if context.get('_ballast') is not None:
            plan = plan.model_copy(update={'ballast':context['_ballast']})
        replay = apply_plan(instance,plan)
        if not replay.legal:
            return None
        meta['replay_legal'] = True
        report = check(instance,plan)
        last_report = report
        index = len(meta['trials'])
        stem = diagnostic/f'{instance.id}-{index:02d}'
        plan_path, report_path = Path(str(stem)+'.plan.json'), Path(str(stem)+'.report.json')
        plan_path.write_text(plan.model_dump_json(indent=2)+'\n')
        report_path.write_text(report.model_dump_json(indent=2)+'\n')
        rules = sorted({v.rule_id for v in report.violations})
        row = dict(lane=lane, replay_legal=True, full_check=report.passed,
                   lifts=float(report.count.weighted_nonproductive), rules=rules,
                   violations=[v.model_dump(mode='json') for v in report.violations],
                   plan=str(plan_path),report=str(report_path), details=details or {})
        meta['trials'].append(row)
        (HERE/'trace').mkdir(exist_ok=True)
        (HERE/'trace'/f'{instance.id}.json').write_text(json.dumps(meta,indent=2,default=str)+'\n')
        concrete = Scored(scored.actions, float(report.count.weighted_nonproductive), plan)
        context['_candidate_plans'][key] = plan
        if report.passed:
            passing.append(concrete)
        else:
            geometry = sum(not (q.startswith('iscode') or 'securing' in q) for q in rules)
            failed.append((geometry,len(rules),concrete))
        print(instance.id,lane,concrete.objective,report.passed,rules,flush=True)
        return concrete

    ref = reference(instance)
    ref_plan = None
    if ref:
        ref_plan, source = ref
        meta['warm_reference'] = source
        context['_ballast'] = ref_plan.ballast
        actions = tuple(a for a in get_action_space('boxslot').from_plan(instance,ref_plan) if isinstance(a,Place))
        assess(Scored(actions,0.,ref_plan),'inherited_checked_plan')
        bound_rows = json.loads((HERE/'bound_results.json').read_text())
        if passing and any(r['instance_id']==instance.id and r['lower_bound']>=passing[0].objective for r in bound_rows):
            meta['stopped_at_certified_bound'] = True
            return [passing[0].actions]

    # Warm fixed-charge page neighbourhoods retain known passes and exact
    # outside assignments. Moment targets preserve their physical vicinity.
    oracle = None
    if passing and monotonic() < deadline-20:
        incumbent = min(passing,key=lambda s:s.objective)
        ship = apply_plan(instance,incumbent.plan).final_state.ship
        oracle = Fast(instance,ship)
        model = Model(p,oracle=oracle,lash_limit=1.,deck_tiers=None,vertical_weight=.02)
        assignment = dict(assignment_key(incumbent.actions))
        try:
            model.oracle = None
            chosen = seed_assignment(model,assignment)
        except ValueError:
            chosen = None
        finally:
            model.oracle = oracle
        if chosen:
            moments = model.selection_moments(chosen)
            model.cargo_vmoment_max = moments['vertical_tm']+moments['mass_t']*.05
            model.lcg_min,model.lcg_max = moments['lcg_m']-.35,moments['lcg_m']+.35
            model.lcg_target = model.lcg_max
            model.tcg_min,model.tcg_max = moments['tcg_m']-.03,moments['tcg_m']+.03
            counts = Counter(model.pages[model.columns[k].page].cover for k in chosen
                             if not model.pages[model.columns[k].page].deck)
            covers = sorted({q.cover for q in model.pages if q.cover is not None})
            for attempt,target in enumerate(sorted(counts,key=counts.get)[:2]):
                if monotonic() >= deadline-20:
                    break
                nearby = sorted(covers,key=lambda c:(c!=target,abs(covers.index(c)-covers.index(target)),c))[:4]
                fixed = {b:s for b,s in assignment.items() if all(
                    model.T.stack_cover[int(j)] not in nearby for j in model.T.slot_stacks[model.T.slot_index[s]] if j>=0)}
                out = improve(model,chosen,nearby,closed_hold=[target] if attempt==0 else (),
                              seconds=min(12.,deadline-monotonic()-15),pricing_s=4.,seed=seed+attempt,
                              max_rounds=8,heel_slack_tm=30,lcg_slack_tm=100)
                candidate = scored_assignment(p,model,out['assignment'],fixed) if out.get('chosen') else None
                if candidate is not None and candidate.objective < incumbent.objective:
                    assessed = assess(candidate,'moment_fixed_charge_lns',{k:v for k,v in out.items() if k not in ('assignment','chosen')})
                    if assessed is not None and meta['trials'][-1]['full_check']:
                        incumbent = assessed

    # Whole-map reconstruction uses count-bounded pricing and genuine physical
    # moment duals, followed by exact native all-rule checks.
    target_x = instance.vessel.hull.lpp_m*.48
    settings = []
    for attempt in range(4):
        settings.append(dict(deck_tiers=((3,4,5),(4,5,5),None,(3,5,5))[attempt],
            vertical_weight=(.02,.04,.02,.08)[attempt],hold_reward=(2.,1.,3.,1.)[attempt],
            lcg_min=target_x-3.,lcg_max=target_x+1.,tcg_min=-.08,tcg_max=.08,
            lash_limit=(1.,.99,1.,.98)[attempt],attempt_s=18.,rmp_s=3.,polish=True))
    build_context = dict(problem=p,settings=settings,oracle=oracle,seed_plans=[ref_plan] if ref_plan else [],
                         local_s=2.,descent_passes=4,pool_variants=2,emit_seeds=False)
    if monotonic() < deadline-12 and len(meta['trials']) < 8:
        for scored,details in constructor.iter_generate(instance,seed=seed,budget_s=max(0.,deadline-monotonic()-5),context=build_context):
            if monotonic() >= deadline or len(meta['trials']) >= 8:
                break
            assessed = assess(scored,'moment_reconstruction',details)
            if assessed is not None and last_report is not None:
                trim = [v for v in last_report.violations if 'trim' in v.rule_id and v.measured is not None]
                if trim:
                    shift = -1. if trim[0].measured<0 else 1.
                    for option in settings[int(details.get('attempt',0))+1:]:
                        option['lcg_min'] += shift;option['lcg_max'] += shift
            if passing and not ref and len(passing)>=1:
                break
    meta['construction'] = build_context.get('construct_metadata',{})
    meta['loadavg_end'] = list(os.getloadavg())
    meta['propose_wall_s'] = monotonic()-started
    passing.sort(key=lambda s:s.objective)
    failed.sort(key=lambda row:(row[0],row[1],row[2].objective))
    # Return at most one failed physical near-miss beside one retained pass,
    # so score.py's objective ranking cannot hide that pass beyond tries=2.
    candidates = passing[:1]+[r[2] for r in failed[:1 if passing else 2]]
    return [s.actions for s in candidates]


def finish(instance, plan, *, seed, finalize_s, label, context):
    started = monotonic()
    actions = [a for a in get_action_space('boxslot').from_plan(instance,plan) if isinstance(a,Place)]
    plan = context.get('_candidate_plans',{}).get(assignment_key(actions),plan)
    report = check(instance,plan)
    if report.passed:
        return plan,dict(finisher='checked concrete ballast',passed=True)
    rules = {v.rule_id for v in report.violations}
    if any(not (q.startswith('iscode') or 'securing' in q) for q in rules):
        return None,dict(finisher='geometry rejected before ballast',rules=sorted(rules))
    # Native area/securing search, explicitly targeting real area margins.
    # Its local Jacobian and failed search never certify global infeasibility.
    from ballast_opt import optimize
    out = HERE/'ballast'/f'{instance.id}-{label}'
    candidate,meta = optimize(instance,plan,out=out,budget_s=max(1.,finalize_s-(monotonic()-started)),
                            max_evaluations=12,max_iterations=2)
    if meta['full_checker_passed']:
        return candidate,meta
    return None,meta

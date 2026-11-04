"""Cargo-first cold ladder; only unchanged checker passes count as L0 passes."""
from pathlib import Path
import sys
from time import monotonic
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import load_engine
load_engine()
from engine.planner import plan_and_check as normal_engine
from engine.config import Config
from stowage.checker import check
from stowage.actions import get_action_space
from stowage.model import PlannerResult
from tiers import load_table
from finishing import Candidates, retain_failures, finish_relaxed, capture_checked

# D242: dense-sweep-scale work, fixed before the new measurements.
DEFAULT_BUDGETS = {'L0_search_s':120., 'L0_finish_s':45.,
    'relaxed_search_s':120., 'relaxed_finish_s':45.,
    'max_candidates':2, 'max_ballast_recipes':9}

def normal(instance, *, seed=0, budget_s=120., finalize_s=45.):
    return normal_engine(instance,seed=seed,budget_s=budget_s,finalize_s=finalize_s,
        config=Config(area_securing_s=45.,lp_rounds=40,polish_rounds=8,
                      dive_nodes=80,layout_rungs=12,lns_iterations=6))

def _record(instance,row,*,searched_level,trials):
    from stowage.moves import apply_plan
    plan,report,analysis=row['plan'],row['report'],row['analysis']
    aboard={p.container_id for p in apply_plan(instance,plan).final_state.ship.placements}
    wanted=set(instance.load_list)
    loaded=wanted&aboard
    left=sorted(wanted-aboard)
    boxes={b.id:b for b in instance.containers}
    level=analysis['level']
    labels={0:'passes the full checker',1:'relaxed plan',2:'relaxed plan',3:'relaxed plan',
        4:"breaks a class or statutory limit, needs a person's sign-off",
        5:'not safe to sail, shown only to say what would have to change'}
    return dict(level=level,level_name=f'L{level}',searched_level=searched_level,
        tiers_peeled=list(load_table().levels[level]),status=labels[level],passed=bool(report.passed),
        rules_broken=analysis['broken_rules'],boxes_left_ashore=left,
        boxes_aboard=len(loaded),boxes_total=len(wanted),
        boxes_aboard_percent=100.*len(loaded)/len(wanted) if wanted else 100.,
        teu_aboard=sum(boxes[b].length_ft/20 for b in loaded),
        mass_aboard_kg=sum(boxes[b].gross_mass_kg for b in loaded),
        lifts=float(report.count.weighted_nonproductive),checker_report=report.model_dump(mode='json'),
        plan=plan.model_dump(mode='json'),trials=list(trials),source=row['source'])

def _package(instance,bank,*,complete,searched_level,trials,seed,result=None):
    safe=bank.best_safe_partial()
    selected=complete or safe or bank.best.get(5)
    if selected is None:
        return None,dict(level=None,passed=False,status='no plan found at L5 within this work',
            completion_status='not_completed',completed=None,best_safe_partial=None,
            trials=trials,checked_candidates=len(bank.rows))
    record=_record(instance,selected,searched_level=searched_level,trials=trials)
    def nested(row):
        if row is None:return None
        r=_record(instance,row,searched_level=searched_level,trials=[])
        r.pop('trials',None)
        return r
    record.update(completed=nested(complete),best_safe_partial=nested(safe),
                  finishing_diagnostics=bank.finishing_records,
                  completion_status='completed' if complete is not None else 'partial')
    if result is None:
        result=PlannerResult(status='ok',plan=selected['plan'],planner='frontier.peel',
                             planner_version='0.2.0',seed=seed)
    return result,record

def solve(instance,*,seed=0,budgets=None,normal_call=None):
    """Stop on completion; keep the cargo-best safe L3 partial independently."""
    work=dict(DEFAULT_BUDGETS,**(budgets or {}));trials=[];started=monotonic()
    with capture_checked() as captured:
        result,engine_record=(normal_call or normal)(instance,seed=seed,
            budget_s=work['L0_search_s'],finalize_s=work['L0_finish_s'])
    trials.append(dict(level=0,status='full checker pass' if result is not None else
        'no completed plan found at L0 within this work',engine=engine_record,
        elapsed_s=monotonic()-started))
    bank=Candidates(instance)
    if result is not None and result.plan is not None:
        report=check(instance,result.plan)
        if report.passed:
            row=bank.observe(result.plan,report,source='ordinary-L0-unchanged')
            return _package(instance,bank,complete=row,searched_level=0,trials=trials,
                            seed=seed,result=result)
    for plan,report in captured:
        bank.observe(plan,report,source='L0-retained-rejected-finish')
    from masks import peeled_masks
    from search import generate
    for level in range(1,6):
        before=monotonic()
        print('LADDER',getattr(instance,'id','?'),f'L{level}','proposal',flush=True)
        context={'complete_only':level>=4,'complete_seeds':[r['plan'] for r in bank.rows
            if r['analysis']['level'] is not None and r['analysis']['ashore_count']==0]}
        with peeled_masks(level),retain_failures(bank):
            candidates=generate(instance,level=level,seed=seed,
                budget_s=work['relaxed_search_s'],context=context)
        for candidate in candidates:
            bank.observe(getattr(candidate,'plan',candidate),source=f'L{level}-raw-selection')
        # All same-run ordinary complete stows remain candidates even when
        # their present ballast reaches a deeper level. Finish cargo first.
        pool=[row for row in bank.rows if row['analysis']['level'] is not None]
        pool=sorted(pool,key=bank.rank)
        complete_pool=[r for r in pool if r['analysis']['ashore_count']==0]
        if level>=4 and complete_pool:pool=complete_pool
        # At L3 also finish a partial option for the independent safe record.
        if level==3 and complete_pool:
            partials=[r for r in pool if r['analysis']['ashore_count']>0]
            pool=complete_pool[:1]+partials[:1]
        finishing_started=monotonic();finished=0
        for row in pool[:work['max_candidates']]:
            remaining=work['relaxed_finish_s']-(monotonic()-finishing_started)
            if remaining<=0:break
            remaining/=max(1,min(len(pool),work['max_candidates'])-finished)
            finish_relaxed(instance,row['plan'],bank,level=level,seed=seed,
                budget_s=remaining,max_recipes=work['max_ballast_recipes'])
            finished+=1
        complete=bank.best_completed(level)
        safe=bank.best_safe_partial()
        print('LADDER',getattr(instance,'id','?'),f'L{level}',
              'completed' if complete else 'not completed',
              'safe_partial_ashore',safe['analysis']['ashore_count'] if safe else None,flush=True)
        context.pop('complete_seeds',None)
        trials.append(dict(level=level,status='completed' if complete else
            f'no completed plan found at L{level} within this work',
            elapsed_s=monotonic()-before,candidates=len(candidates),finished=finished,
            complete_candidates=len(complete_pool),safe_partial_ashore=
                safe['analysis']['ashore_count'] if safe else None,search=context))
        if complete is not None:
            return _package(instance,bank,complete=complete,searched_level=level,
                            trials=trials,seed=seed)
    return _package(instance,bank,complete=None,searched_level=5,trials=trials,seed=seed)

def _actions(instance,plan):
    return tuple(get_action_space('boxslot').from_plan(instance,plan))

def propose(instance,*,seed,budget_s,context):
    budgets=dict(context.get('peel_budgets',{}))
    budgets['L0_search_s']=budget_s
    budgets['L0_finish_s']=context.get('finalize_s',45.)
    result,record=solve(instance,seed=seed,budgets=budgets)
    context.setdefault('method_metadata',{})['peel']=record
    if result is None:return []
    context['_peel_result']=result
    context['_peel_actions']=_actions(instance,result.plan)
    return [context['_peel_actions']]

def finish(instance,plan,*,seed,finalize_s,label,context):
    result=context.get('_peel_result')
    if result is None or _actions(instance,plan)!=context.get('_peel_actions'):
        raise ValueError('finish requires the unchanged actions from peel.propose')
    return result,dict(label=label,reused_peel_finish=True,
                       peel=context.get('method_metadata',{}).get('peel'))

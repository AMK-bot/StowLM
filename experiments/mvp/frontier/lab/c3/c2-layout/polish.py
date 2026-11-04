"""Unforced page-LP/IP neighbourhoods on newly checked Cycle 3 plans."""
from collections import defaultdict
from pathlib import Path
import json,sys
from time import monotonic

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parents[1]/'c1/fx-scout')]
import method_lp as base
from pages_lp import Model
from lns_lp import seed_assignment,improve
from alns import Scored
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker import check
from stowage.conventions import parse_slot,cells_of_slot
from stowage.model.plan import MovePlan
from stowage.moves import apply_plan
from lash_oracle import Fast


def propose(instance,*,seed,budget_s,context):
    base.install()
    deadline=monotonic()+budget_s
    source=HERE/'results/f70-lp'/f'{instance.id}.json'
    if not source.exists():return []
    row=json.loads(source.read_text())
    if not row.get('passed'):return []
    p=context['problem'];T=p.engine.T
    plan=MovePlan.model_validate(row['plan'])
    replay=apply_plan(instance,plan)
    report=check(instance,plan)
    if not replay.legal or not report.passed:return []
    context['_ballast']=plan.ballast
    actions=tuple(a for a in get_action_space('boxslot').from_plan(instance,plan) if isinstance(a,Place))
    incumbent=Scored(actions,float(report.count.weighted_nonproductive),plan)
    assignment={a.box:a.slot for a in actions}
    bays=sorted({parse_slot(s).bay for i,s in enumerate(T.slot_ids) if T.slot_deck[i] and T.slot_n[i]==1})
    caps=[3,3,3]
    for slot in assignment.values():
        si=T.slot_index[slot]
        if T.slot_deck[si]:
            part=min(2,sum(b<parse_slot(slot).bay for b in bays)*3//max(1,len(bays)))
            caps[part]=max(caps[part],int(max(T.slot_k[si]))+1)
    mass=sum(p.boxes[b].gross_mass_kg for b in assignment)
    lcg=sum(p.boxes[b].gross_mass_kg*p.cells[cells_of_slot(s)[0]].lcg_m for b,s in assignment.items())/mass
    model=Model(p,oracle=Fast(instance,replay.final_state.ship),deck_tiers=tuple(caps),
                lash_limit=1.,lcg_target=lcg,hold_reward=1.)
    oracle=model.oracle
    try:
        model.oracle=None
        chosen=seed_assignment(model,assignment)
    except ValueError:
        return [actions]
    finally:model.oracle=oracle
    meta=context.setdefault('method_metadata',{})
    meta.update(warm_reference=str(source),replay_legal=True,trials=[])
    covers=sorted({page.cover for page in model.pages if page.cover is not None})
    for attempt in range(min(6,len(covers))):
        if monotonic()>=deadline-20:break
        nearby=[covers[(attempt+j)%len(covers)] for j in range(min(4,len(covers)))]
        out=improve(model,chosen,nearby,seconds=min(15,deadline-monotonic()-10),
                    pricing_s=4,seed=seed+attempt,heel_slack_tm=50,lcg_slack_tm=100)
        trace={k:v for k,v in out.items() if k not in ('chosen','assignment')}
        meta['trials'].append(trace)
        if not out.get('chosen'):continue
        fixed={b:s for b,s in assignment.items() if all(T.stack_cover[int(j)] not in nearby
                  for j in T.slot_stacks[T.slot_index[s]] if j>=0)}
        candidate=base.assignment_score(p,model,out['assignment'],fixed=fixed)
        trace['lifts']=candidate.objective if candidate else None
        if candidate is None or candidate.objective>=incumbent.objective:continue
        candidate_plan=candidate.plan.model_copy(update={'ballast':plan.ballast})
        checked=check(instance,candidate_plan)
        trace.update(full_check=checked.passed,rules=sorted({v.rule_id for v in checked.violations}))
        print(instance.id,'unforced_lns',candidate.objective,checked.passed,trace['rules'],flush=True)
        if checked.passed:
            incumbent=candidate;assignment={a.box:a.slot for a in candidate.actions}
            try:
                model.oracle=None
                chosen=seed_assignment(model,assignment)
            finally:model.oracle=oracle
    return [incumbent.actions]


finish=base.finish

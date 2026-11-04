"""Same-root page rounding comparison and score.py plug-in."""
from __future__ import annotations
import os
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
import json
from pathlib import Path
import random
import sys
from time import monotonic
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'c1'/'leader'))
from e9fix import install
install()
from alns import Sequencer
from physics_master import PhysicalModel
from hydro import LinearRow, checker_margins
from stowage.checker import check
import numpy as np


def diagnostic(problem, model, chosen, root_target):
    if chosen is None:
        return None, dict(complete=False, passed=False)
    before = model.assignment(chosen)
    assignment = model.clear_oog(before)
    row = dict(packed=len(assignment), oog_repair=assignment != before)
    if problem.mixes_hold_stack(assignment):
        return None, dict(row,complete=False,passed=False,reason='mixed hold')
    actions = Sequencer(problem).order(assignment)
    scored = problem.score(actions) if actions is not None else None
    if scored is None:
        return None, dict(row,complete=False,passed=False,reason='sequence or replay')
    report = check(problem.instance,scored.plan)
    values = model.hydro.total(chosen)
    # After E9 swaps, get the actual additive values by rebuilding each page.
    if assignment != before:
        from pages import Pattern
        lookup = {model.T.slot_index[s]:model.T.box_index[b] for b,s in assignment.items()}
        values = np.zeros(len(model.feature_names))
        for page_index,page in enumerate(model.pages):
            contents = tuple(lookup[s] for s in page.slots if s in lookup)
            if contents and (page.kind == '20') == (model.T.units[contents[0]] == 1):
                values += model.features(Pattern(page_index,contents,0.))
    row.update(complete=True, lifts=scored.objective, features=dict(zip(model.feature_names,values.tolist())),
               drift=dict(zip(model.feature_names,(values-root_target).tolist())), **checker_margins(report))
    row['plan'] = scored.plan.model_dump(mode='json')
    return scored,row


def compare(problem, *, seed, budget_s):
    from rounding import round_lp, balance_kk
    started = monotonic()
    model = PhysicalModel(problem)
    root_deadline = started+budget_s*.25
    root = model.generate(active_boxes=set(model.boxes),active_stacks=set(model.stacks),
                          phase=1,deadline=root_deadline,max_rounds=140)
    if monotonic() < root_deadline:
        root = model.generate(active_boxes=set(model.boxes),active_stacks=set(model.stacks),phase=3,
                              deadline=root_deadline,max_rounds=25)
    # Enrich the common pool with one feasible integer witness. This keeps a
    # fixed-pool obstruction from obscuring the discrepancy comparison.
    seed_model = model.clone()
    prep_deadline=max(monotonic(),started+budget_s*.60)
    witness, witness_info = seed_model.dive(deadline=prep_deadline,
                                            rng=random.Random(seed))
    # A failed dive still prices useful tail columns; retry that enriched pool.
    if witness is None and monotonic()<prep_deadline:
        witness, retry_info=seed_model.dive(deadline=prep_deadline,rng=random.Random(seed+1))
        witness_info=dict(first=witness_info,retry=retry_info)
    for c in seed_model.columns:
        key=(c.page,c.boxes)
        if key not in model.pool:
            model.pool[key]=len(model.columns)
            model.columns.append(c)
    if witness is not None:
        model.rounding_witness=[model.pool[(seed_model.columns[k].page,seed_model.columns[k].boxes)] for k in witness]
    # Re-solve before deriving preservation targets; every arm sees this pool.
    _,x,_,_,uncovered = model.solve_lp(active_boxes=set(model.boxes),active_stacks=set(model.stacks),hold_full=False,phase=3)
    root = dict(root,x=x,uncovered=uncovered,proved=False)
    # Bay and bending targets are the preceding LP's values, not legal limits.
    target = model.hydro.fractional(root['x'])
    for j,name in enumerate(model.feature_names[4:],4):
        coeff = np.zeros(len(target)); coeff[j]=1.
        model.physical_rows.append(LinearRow(name+'_lp_target',coeff,target[j],target[j],
                                   'LP preservation target, not a legal capacity.'))
    _,x,_,_,uncovered = model.solve_lp(active_boxes=set(model.boxes),active_stacks=set(model.stacks),hold_full=False,phase=3)
    root = dict(root,x=x,uncovered=uncovered,proved=False)
    target = model.hydro.fractional(x)
    meta = dict(seed=seed, root={k:v for k,v in root.items() if k!='x'},
                lp_features=dict(zip(model.feature_names,target.tolist())),
                physical=model.hydro.metadata(), physical_slack=model.last_physical,
                root_s=monotonic()-started, witness=witness_info,
                witness_columns=getattr(model,'rounding_witness',None), variants={})
    # Persist root point/pool to make the shared starting point inspectable.
    meta['root_support'] = {str(k):v for k,v in x.items()}
    meta['root_patterns'] = {str(k):dict(page=model.columns[k].page,boxes=list(model.columns[k].boxes)) for k in x}
    meta['pool_size'] = len(model.columns)
    candidates=[]
    for variant in ('dive','iterated','walk'):
        arm = model.clone()
        arm_started=monotonic()
        deadline=arm_started+budget_s*.13
        if variant == 'dive':
            chosen,info=arm.dive(deadline=deadline,rng=random.Random(seed))
            if chosen is not None:
                chosen,heel=arm.balance(chosen,random.Random(seed))
                info['heel_tm']=heel
        else:
            chosen,info=round_lp(arm,root,deadline=deadline,seed=seed,variant=variant)
            if chosen is not None:
                chosen,heel,kk=balance_kk(arm,chosen,deadline=monotonic()+1.,seed=seed)
                info.update(heel_tm=heel,kk=kk)
        solve_s=monotonic()-arm_started
        scored,row=diagnostic(problem,arm,chosen,target)
        row.update(rounding=info,solve_s=solve_s,wall_s=monotonic()-arm_started)
        meta['variants'][variant]=row
        if scored is not None:
            candidates.append((variant,scored))
    meta['wall_s']=monotonic()-started
    return candidates,meta


def propose(instance, *, seed:int, budget_s:float, context:dict)->list:
    install()
    if instance.onboard.placements:
        context['method_metadata']['skip']='empty arrival only'
        return []
    candidates,meta=compare(context['problem'],seed=seed,budget_s=budget_s)
    context['method_metadata'].update(meta)
    # Portfolio exposes measured alternatives; all go through full score gate.
    return [s.actions for name,s in candidates]


def finish(instance, plan, *, seed, finalize_s, label, context):
    # Controlled default-tank experiment: return the complete plan to score.py's
    # independent full checker; no ballast search obscures the rounding effect.
    return plan,dict(finisher='fixed_default_tanks')


if __name__=='__main__':
    import argparse
    from alns import Problem
    from stowage.io import load_instance
    parser=argparse.ArgumentParser()
    parser.add_argument('--index',type=int,default=0)
    parser.add_argument('--budget-s',type=float,default=150.)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    paths=(HERE.parents[1]/'sets'/'medium12.txt').read_text().splitlines()
    _,meta=compare(Problem(load_instance(paths[args.index])),seed=0,budget_s=args.budget_s)
    Path(args.out).write_text(json.dumps(meta,indent=2)+'\n')
    print({k:{q:v.get(q) for q in ('complete','passed','margins','solve_s')} for k,v in meta['variants'].items()},flush=True)

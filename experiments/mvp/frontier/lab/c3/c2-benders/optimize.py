"""Local trust-region LP using the real area and securing separation oracle.

All derivatives and Broyden updates are search guidance. No local failure
is promoted to a physical impossibility proof. Only full checker acceptance
returns a pass. One process and one numerical thread; no worker is spawned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic
import warnings

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS',
            'MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','HIGHS_THREADS'):
    os.environ[key]='1'
os.environ['STOWAGE_VESSEL_CACHE_DIR']=str(HERE/'results/vessel-cache')
sys.dont_write_bytecode=True
sys.path[:0]=[str(HERE),str(ROOT/'src')]

import numpy as np
from scipy.optimize import linprog, OptimizeWarning
from physics import PhysicsEvaluator
from stowage.checker import check
from stowage.io import load_instance
from stowage.model import MovePlan


def write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False))
    temporary.replace(path)


def merit(values):
    negative=np.maximum(-values,0.)
    return float(negative.sum()+.5*negative.max(initial=0.))


def useful(name):
    # Boolean applicability and exact equality-zero flags have no useful
    # derivative; their real hard violations still reject the final recipe.
    return not any(word in name for word in
                   ('weather_table_applicability','unavailable','unrepresented'))


def difference_step(x,direction):
    """A one-sided difference inside the current98%-correction regime."""
    active=np.flatnonzero(direction)
    if not len(active):return 0.
    lower=np.where(x[active]>=.98,.9800001,0.)
    upper=np.where(x[active]<.98,.9799999,1.)
    quotients=np.array([(lower-x[active])/direction[active],
                        (upper-x[active])/direction[active]])
    low=float(np.max(np.min(quotients,axis=0)))
    high=float(np.min(np.max(quotients,axis=0)))
    up,down=max(0.,high),max(0.,-low)
    sign=1. if up>=down else -1.
    available=up if sign>0 else down
    return sign*min(.01,available*.7) if available>1e-5 else 0.


def optimize(instance,plan,*,out,budget_s=900.,max_evaluations=100,max_iterations=12,
             parts=('stability','lashing')):
    started=monotonic();deadline=started+budget_s
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    evaluator=PhysicsEvaluator(instance,plan)
    capacity=evaluator.capacity_kg
    initial=np.clip(evaluator.initial_fractions,0.,1.)
    groups={}
    for i,name in enumerate(evaluator.tank_ids):
        groups.setdefault(name.rsplit('-',1)[0],[]).append(i)
    # Pair increments preserve the initial transverse mass difference. This
    # restriction reduces search cost; it is never used to prove infeasibility.
    G=np.zeros((len(initial),len(groups)))
    for j,indices in enumerate(groups.values()):G[indices,j]=1.
    # Release the fixed P/S offset without changing the pair's total mass.
    # Raw upright TCG enters the real worst-side area explicitly.
    if 'HEEL-P' in evaluator.tank_ids and 'HEEL-S' in evaluator.tank_ids:
        split=np.zeros(len(initial))
        split[evaluator.tank_ids.index('HEEL-P')]=1.
        split[evaluator.tank_ids.index('HEEL-S')]=-1.
        G=np.column_stack([G,split])
    names=None;trace=[];evaluations=0

    def evaluate(x,label):
        nonlocal names,evaluations
        if evaluator.evaluations>=max_evaluations or monotonic()>=deadline:
            raise TimeoutError('local search budget exhausted')
        result=evaluator.evaluate_fractions(np.clip(x,0.,1.),parts=parts,deadline=deadline)
        evaluations+=1
        if not result['completed']:
            raise ValueError('unknown exact physics part: '+str(result['violation_counts']))
        if names is None:names=sorted(k for k in result['margins'] if useful(k))
        if not set(names)<=result['margins'].keys():
            raise ValueError('exact margin key set changed')
        values=np.array([result['margins'][k] for k in names])
        row=dict(label=label,evaluation=evaluations,wall_s=result['wall_s'],
                 merit=merit(values),minimum_margin=float(values.min()),
                 physics_feasible=result['physics_feasible'],
                 area={k:v for k,v in result['raw_margins'].items() if '0_flood' in k},
                 securing={phase:max((v for k,v in result['metrics'].get(phase+'/lashing',{}).items()
                                      if k.startswith('max_') and k.endswith('_ratio')),default=None)
                           for phase in ('departure','mid_voyage','arrival')},
                 fractions=x.tolist())
        trace.append(row);write(out/'trace.json',trace)
        print(json.dumps({k:v for k,v in row.items() if k!='fractions'}),flush=True)
        return values,result

    x=initial.copy();best_x=x.copy();best_result=None;best_merit=float('inf')
    status='unknown';error=None;radius=.03;J=None;R=None;rebuild=True
    try:
        values,result=evaluate(x,'initial')
        best_result=result;best_merit=merit(values)
        for iteration in range(max_iterations):
            if result['physics_feasible']:
                status='physics_feasible';break
            if rebuild or J is None:
                J=np.zeros((len(values),G.shape[1]))
                R=np.zeros(G.shape[1])
                raw_tcg=float(result['raw_loading']['arrival']['tcg_m'])
                for j in range(G.shape[1]):
                    step=difference_step(x,G[:,j])
                    step=float(np.clip(step,-max(.0001,radius*.25),max(.0001,radius*.25)))
                    if np.any(G[:,j]<0):step=float(np.clip(step,-.001,.001))
                    if not step:continue
                    y,r=evaluate(x+step*G[:,j],f'jacobian-{iteration}-{j}')
                    J[:,j]=(y-values)/step
                    R[j]=(float(r['raw_loading']['arrival']['tcg_m'])-raw_tcg)/step
                    if r['physics_feasible'] or merit(y)<best_merit:
                        best_x=x+step*G[:,j];best_result=r;best_merit=merit(y)
                    if r['physics_feasible']:
                        x,values,result=best_x.copy(),y,r
                        status='physics_feasible'
                        break
                rebuild=False
                if result['physics_feasible']:break
            # Each margin has a nonnegative violation slack; absolute step
            # penalties choose a nearby recipe among similarly good models.
            n=G.shape[1];m=len(values)
            A=np.vstack([np.c_[-J,-np.eye(m),np.zeros((m,n))],
                         np.c_[G,np.zeros((len(x),m+n))],
                         np.c_[-G,np.zeros((len(x),m+n))],
                         np.c_[np.eye(n),np.zeros((n,m)),-np.eye(n)],
                         np.c_[-np.eye(n),np.zeros((n,m)),-np.eye(n)]])
            # Keep this local solve in its current pressed/slack regime;
            # deliberate cross-regime probes are separate search starts.
            local_low=np.where(x>=.98,.98000001,0.)
            local_high=np.where(x<.98,.97999999,1.)
            b=np.r_[values-1e-4,local_high-x,x-local_low,np.zeros(2*n)]
            # The worst-side area contains abs(rawTCG). A single tangent
            # cannot predict benefit beyond zero. Keep the arrival tangent
            # on its own side of that cusp (or in a tiny box around zero).
            raw_tcg=float(result['raw_loading']['arrival']['tcg_m'])
            if abs(raw_tcg)>1e-5:
                A=np.vstack([A,np.r_[-np.sign(raw_tcg)*R,np.zeros(m+n)]])
                b=np.r_[b,abs(raw_tcg)]
            else:
                A=np.vstack([A,np.r_[R,np.zeros(m+n)],np.r_[-R,np.zeros(m+n)]])
                b=np.r_[b,1e-5-raw_tcg,1e-5+raw_tcg]
            c=np.r_[np.zeros(n),np.ones(m),np.full(n,1e-3)]
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',OptimizeWarning)
                lp=linprog(c,A_ub=A,b_ub=b,bounds=[(-radius,radius)]*n+[(0,None)]*(m+n),
                           method='highs',options={'threads':1,'time_limit':1.})
            if lp.status!=0:
                status='local_lp_unknown';break
            direction=lp.x[:n]
            if np.linalg.norm(direction,np.inf)<1e-5:
                if radius>=.3:status='local_stationary_unknown';break
                radius=min(.3,radius*2);rebuild=True;continue
            accepted=False
            for scale in (1.,.5,.25):
                trial=np.clip(x+scale*(G@direction),0.,1.)
                trial_values,trial_result=evaluate(trial,f'step-{iteration}-{scale}')
                candidate_merit=merit(trial_values)
                if trial_result['physics_feasible'] or candidate_merit<best_merit:
                    best_x=trial.copy();best_result=trial_result;best_merit=candidate_merit
                    write(out/'best-physics.json',best_result)
                if trial_result['physics_feasible'] or candidate_merit<merit(values)-1e-5:
                    actual_step=scale*direction
                    residual=trial_values-values-J@actual_step
                    J+=np.outer(residual,actual_step)/max(float(actual_step@actual_step),1e-20)
                    raw_delta=(float(trial_result['raw_loading']['arrival']['tcg_m'])-
                               float(result['raw_loading']['arrival']['tcg_m']))
                    R+=(raw_delta-float(R@actual_step))*actual_step/max(float(actual_step@actual_step),1e-20)
                    # Upright pressed/slack changes are discontinuous. The
                    # exact oracle still tests every step; refresh derivatives.
                    before_regime=((x<=1e-8).astype(int)+2*(x>=.98).astype(int)
                                   +4*(x>=1.-1e-8).astype(int))
                    after_regime=((trial<=1e-8).astype(int)+2*(trial>=.98).astype(int)
                                  +4*(trial>=1.-1e-8).astype(int))
                    cusp=any(np.sign(result.get('raw_loading',{}).get(phase,{}).get('tcg_m',0.))!=
                             np.sign(trial_result.get('raw_loading',{}).get(phase,{}).get('tcg_m',0.))
                             for phase in ('departure','mid_voyage','arrival'))
                    rebuild=not np.array_equal(before_regime,after_regime) or cusp or iteration%3==2
                    x,values,result=trial,trial_values,trial_result
                    radius=min(.06,radius*(1.3 if scale==1. else scale*.9));accepted=True;break
            if not accepted:
                radius*=.5;rebuild=True
                if radius<.003:status='trust_region_unknown';break
    except (TimeoutError,ValueError,ArithmeticError) as exc:
        error=f'{type(exc).__name__}: {exc}'
    candidate=evaluator.candidate_plan(best_x*capacity)
    write(out/'candidate-plan.json',candidate.model_dump(mode='json'))
    if best_result is not None:write(out/'best-physics.json',best_result)
    before=monotonic();report=check(instance,candidate)
    write(out/'full-report.json',report.model_dump(mode='json'))
    complete=bool(set(instance.load_list)<={q.container_id for q in evaluator.ship.placements})
    summary=dict(instance_id=instance.id,status='full_checker_pass' if report.passed else status,
                 complete_stow=complete,full_checker_passed=report.passed,
                 lifts=float(report.count.weighted_nonproductive) if report.passed else None,
                 full_checker_s=monotonic()-before,wall_s=monotonic()-started,
                 evaluations=evaluations,best_merit=best_merit,error=error,
                 unique_physics_evaluations=evaluator.evaluations,cache_hits=evaluator.cache_hits,
                 rule_ids=sorted({v.rule_id for v in report.violations}),
                 group_restriction=list(groups)+(['HEEL-split'] if G.shape[1]>len(groups) else []),
                 physical_infeasibility_proven=False)
    write(out/'summary.json',summary)
    return candidate,summary


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--input',type=Path,default=ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_500/random_yard/seed-185100000.json')
    parser.add_argument('--out',type=Path,default=HERE/'results/optimize')
    parser.add_argument('--budget-s',type=float,default=900.)
    parser.add_argument('--max-evaluations',type=int,default=100)
    args=parser.parse_args()
    if not args.out.resolve().is_relative_to(HERE):parser.error('output outside experiment')
    print(json.dumps({'owned_pid':os.getpid()}),flush=True)
    instance=load_instance(args.input)
    payload=json.loads(args.plan.read_text())
    plan=MovePlan.model_validate(payload.get('candidate_plan',payload.get('plan',payload)))
    _,summary=optimize(instance,plan,out=args.out,budget_s=args.budget_s,max_evaluations=args.max_evaluations)
    print(json.dumps(summary),flush=True)

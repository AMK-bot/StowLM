"""Matched search from a single regenerated Frontier start bank.

Construction is shared, each search receives the same parents and 60 seconds.
Replay-only records need later full checker admission.
"""
import argparse
import json
from pathlib import Path
from time import monotonic, perf_counter

from alns import Problem, search as frontier_search
from catalog import load_baseline, load_set
from original_starts import build
from recombine import recombine
from search import run
from stowage.io import load_instance

HERE = Path(__file__).resolve().parent


def encoded(problem, candidates):
    out = []
    for actions in candidates:
        scored = problem.score(actions)
        if scored:
            out.append(dict(objective=scored.objective,
                            actions=[dict(box=a.box,slot=a.slot) for a in actions]))
    return sorted(out,key=lambda s:s['objective'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--maps', default='0,2')
    parser.add_argument('--budget-s', type=float, default=60.)
    parser.add_argument('--construct-s', type=float, default=30.)
    parser.add_argument('--out', type=Path, default=HERE/'results'/'from_starts60')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    paths = load_set('quick_small')
    baseline = load_baseline()
    for index in map(int,args.maps.split(',')):
        rel=paths[index];ins=load_instance(rel);p=Problem(ins)
        starts, construction = build(p,seed=0,budget_s=args.construct_s)
        starts.sort(key=lambda s:s[1].objective)
        record=dict(id=ins.id,input=rel,stored_lifts=baseline[ins.id]['lifts'],
                    budget_s=args.budget_s,construction=construction,
                    starts=[dict(label=label,objective=s.objective,
                                 actions=[dict(box=a.box,slot=a.slot) for a in s.actions])
                            for label,s in starts],arms={})
        print(ins.id,'starts',[(label,s.objective) for label,s in starts],flush=True)
        if starts:
            began=monotonic();bank=[];stats=[]
            shares=[2/3,1/3] if len(starts)>1 else [1.]
            for (label,start),share in zip(starts,shares):
                plans,st=frontier_search(ins,start.actions,time_limit_s=args.budget_s*share,
                                         seed=0,problem=p,keep=6)
                bank += [s.actions for s in plans]
                stats.append(dict(label=label,iterations=st.iterations,accepted=st.accepted,
                                  improved=st.improved,best=plans[0].objective))
            record['arms']['frontier09']=dict(wall_s=monotonic()-began,stats=stats,
                                               candidates=encoded(p,bank))
            began=monotonic();deadline=began+args.budget_s
            fused=recombine(p,[s.actions for _,s in starts],deadline=began+args.budget_s*.15,seed=0)
            fusion_stats=dict(recombine.last_stats)
            candidates,stats=run(p,fused+[s.actions for _,s in starts],
                                  budget_s=max(0.,deadline-monotonic()),seed=0)
            record['arms']['cycle_fast_fusion']=dict(wall_s=monotonic()-began,stats=stats,
                                                       fusion=fusion_stats,
                                                       candidates=encoded(p,candidates))
            print(ins.id,[(k,v['candidates'][0]['objective'] if v['candidates'] else None,
                           round(v['wall_s'],1)) for k,v in record['arms'].items()],flush=True)
        (args.out/f'{ins.id}.json').write_text(json.dumps(record,indent=2)+'\n')


if __name__=='__main__':
    main()

"""Twenty current-generator slice departures, never empty unloaded plans.

All six actual exports are placed through L1/L2+C1 with C2 disabled solely for
constructing a witness; exact ballast and the full checker remain mandatory.
Generator screening is necessary, not a claim that a ballast solution exists.
Source: architecture.md's frozen feasibility contract; generator module docs.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import time
import resource

for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key] = '1'
ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('STOWAGE_VESSEL_CACHE_DIR',str(ROOT/'experiments/ballast/.cache/vessels'))


def run_one(generator, index, out, generate_only=False):
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine, FeasibilityConfig
    from stowage.generate.random_yard import generate_random_instance, RANDOM_YARD_VERSION
    from stowage.generate.instances import generate_simulated_instance, INSTANCES_VERSION
    from stowage.io import save_instance, save_plan, load_instance, load_plan
    from stowage.moves import apply_plan
    from stowage.checker.rules import check_static_rules
    from stowage.checker.full import check
    from stowage.solver.ballast import finish_ballast
    from stowage.versions import component_versions
    import stowage.linear.cache as lc
    lc._DEFAULT_ROOT=ROOT/'experiments/ballast/.cache/linear'
    seed=42000+index
    vessel=('feeder_500','feeder_800','feeder_1700')[index%3]
    started=time.perf_counter()
    kwargs=dict(seed=seed, vessel_name=vessel, max_attempts=200)
    prefix=f'{generator}-{index:02d}-{vessel}'
    folder=out/prefix
    folder.mkdir(parents=True,exist_ok=True)
    print(json.dumps(dict(phase='generation',id=prefix)),flush=True)
    instance=(load_instance(folder/'instance.json') if (folder/'instance.json').exists() else
              generate_random_instance('tiny',blocking_ratio=.2,**kwargs) if generator=='random'
              else generate_simulated_instance('tiny',noise=.2,**kwargs))
    generation_s=time.perf_counter()-started
    save_instance(instance,folder/'instance.json')
    engine=make_feasibility_engine(instance,get_action_space(),config=FeasibilityConfig(extra={'global_screen':0.}))
    nodes=0
    deadline=time.perf_counter()+30
    def dfs():
        nonlocal nodes
        nodes+=1
        if time.perf_counter()>deadline:
            return None
        if engine.is_complete():
            p=engine.to_plan()
            replay=apply_plan(instance,p)
            if replay.legal:
                static=check_static_rules(instance,replay.final_state.ship,call_index=instance.call_index)
                if static.ran and not static.violations:
                    return p
            return None
        for a in engine.legal_actions():
            engine.push(a)
            p=dfs()
            if p is not None:
                return p
            engine.pop()
        return None
    started=time.perf_counter()
    plan=dfs()
    result=dict(generator=generator,generator_version=RANDOM_YARD_VERSION if generator=='random' else INSTANCES_VERSION,
                components=component_versions(),
                index=index,seed=seed,vessel=vessel,slice='tiny',target_count=len(instance.load_list),
                fixed_load_count=len(instance.vessel.fixed_loads),adjustable_tanks=sum(t.adjustable for t in instance.vessel.tanks),
                generation_s=generation_s,construct_s=time.perf_counter()-started,nodes=nodes,
                construction='L1/L2+C1; C2 off; complete legal move replay and exact static rules required',calls=[])
    if plan is None:
        result['construction_failed']=True
    else:
        save_plan(plan,folder/'input-plan.json')
        result['loaded_count']=apply_plan(instance,plan).count.loads
        for repeat in range(0 if generate_only else 2):
            started=time.perf_counter()
            outcome=finish_ballast(instance,plan,time_limit_s=5.,seed=0)
            call=dict(warm=bool(repeat),status=outcome.status,wall_s=time.perf_counter()-started,
                      reported_wall_s=outcome.wall_time_s,iterations=outcome.iterations,
                      proof=outcome.proof,violations={n:[v.rule_id for v in p.violations] for n,p in outcome.parts.items()})
            call['parts']={n:dict(ran=p.ran,error=p.error,margins=p.margins,metrics=p.metrics)
                           for n,p in outcome.parts.items()}
            if outcome.plan is not None:
                save_plan(outcome.plan,folder/f'finished-{repeat}.json')
                started=time.perf_counter()
                report=check(instance,outcome.plan)
                call.update(check_s=time.perf_counter()-started,full_checker_passed=report.passed,
                            checker_violations=[v.rule_id for v in report.violations])
            result['calls'].append(call)
            print(json.dumps(dict(phase='call',id=prefix,**call)),flush=True)
    (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(phase='result',**result)),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'experiments/ballast/slices_current')
    parser.add_argument('--count',type=int,default=10)
    parser.add_argument('--start-index',type=int,default=0)
    parser.add_argument('--generator',choices=('random','simulated','both'),default='both')
    parser.add_argument('--generate-only',action='store_true')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    results=[]
    started=time.perf_counter()
    for g in (('random','simulated') if args.generator=='both' else (args.generator,)):
        for index in range(args.start_index,args.start_index+args.count):
            try:
                r=run_one(g,index,args.out,args.generate_only)
            except Exception as exc:
                import traceback
                r=dict(generator=g,index=index,error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
                print(json.dumps(r),flush=True)
            results.append(r)
            (args.out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(dict(phase='resources',elapsed_s=time.perf_counter()-started,
                         peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9)),flush=True)


if __name__=='__main__':
    main()

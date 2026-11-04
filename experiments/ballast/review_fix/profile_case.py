"""One owned 35-second watchdog around instrumented 30-second exact search."""
import argparse
import collections
import json
import os
from pathlib import Path
import time

for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
HERE=Path(__file__).resolve().parent
if os.environ.get('BALLAST_REVIEW_BEFORE')=='1':
    import stowage.solver
    stowage.solver.__path__.insert(0,str(HERE/'before_source'))


def profile(instance, plan):
    import stowage.solver.ballast as ballast
    import stowage.solver.ballast_pressed as pressed
    import stowage.solver.ballast_physics as physics
    import stowage.linear.model as linear
    import stowage.checker.stability as stability
    import stowage.checker.strength as strength
    import stowage.checker.visibility as visibility
    import stowage.checker.lashing as lashing
    records=[]
    def instrument(owner,name,label):
        old=getattr(owner,name)
        def wrapped(*args,**kwargs):
            start=time.monotonic()
            record=dict(phase=label)
            try:
                value=old(*args,**kwargs)
                if hasattr(value,'status'):
                    record['status']=value.status
                if hasattr(value,'margins'):
                    record['margins']=value.margins
                return value
            except BaseException as error:
                record['error']=type(error).__name__+': '+str(error)
                raise
            finally:
                record['wall_s']=time.monotonic()-start
                records.append(record)
        setattr(owner,name,wrapped)
    for owner,name,label in (
        (linear,'build_linear_model','LP.build'),(linear,'solve_ballast_lp','LP.solve'),
        (pressed,'search','pressed'),(ballast,'search','successive'),
        (physics.ExactContext,'__init__','context.init'),
        (physics.ExactContext,'margins','context.margins'),
        (physics.ExactContext,'equilibrium','context.equilibrium'),
        (stability,'check_stability','checker.stability'),
        (strength,'check_strength','checker.strength'),
        (visibility,'check_visibility','checker.visibility'),
        (lashing,'check_lashing','checker.lashing')):
        instrument(owner,name,label)
    started=time.monotonic()
    result=ballast._finish_ballast(instance,plan,time_limit_s=30.,seed=0)
    totals=collections.defaultdict(lambda:dict(calls=0,wall_s=0.))
    for record in records:
        row=totals[record['phase']]
        row['calls']+=1
        row['wall_s']+=record['wall_s']
    return dict(wall_s=time.monotonic()-started,outcome=result.model_dump(mode='json'),
                records=records,totals=dict(totals))


if __name__=='__main__':
    from stowage.io import load_instance,load_plan
    from stowage.solver.ballast_deadline import run_with_deadline
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=int,default=0)
    args=parser.parse_args()
    entry=json.loads((HERE/'manifest.json').read_text())[args.index]
    folder=HERE/'inputs'/entry['case']
    instance,plan=load_instance(folder/'instance.json'),load_plan(folder/'input-plan.json')
    result=run_with_deadline(profile,(instance,plan),deadline=time.monotonic()+35.)
    prefix='profile-before-' if os.environ.get('BALLAST_REVIEW_BEFORE')=='1' else 'profile-'
    output=HERE/f'{prefix}{entry["case"]}.json'
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(path=str(output),status=result['outcome']['status'],
        iterations=result['outcome']['iterations'],totals=result['totals'])),flush=True)

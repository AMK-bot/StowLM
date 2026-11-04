"""Read-only timers around original production generation/checker calls.

Observers forward identical inputs and return original results, with no RNG draws.
Witnesses are persisted before checking, so an interrupted check remains replayable.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from functools import wraps
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time
import traceback

for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[key]='1'
sys.dont_write_bytecode=True

class ProfileDeadline(BaseException): pass

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index',type=int,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--cap-s',type=float,default=295)
    p.add_argument('--replay',type=Path)
    args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    events=(args.out/'events.jsonl').open('a',buffering=1)
    context={'gate':0,'check':0,'part':None}
    last_part={}
    def emit(kind,**kw):
        events.write(json.dumps(dict(t_s=time.perf_counter()-started,event=kind,**context,**kw),sort_keys=True,default=str)+'\n')
    def sources():
        root=Path(__file__).resolve().parents[3]
        return {str(f.relative_to(root)):sha256(f.read_bytes()).hexdigest() for f in sorted((root/'src/stowage').rglob('*.py'))}
    before=sources()
    import stowage.checker as checker
    import stowage.checker.full as full
    import stowage.generate.background_acceptance as gate
    from stowage.vessel.hydrostatics import HullGeometry
    from stowage.generate.instances_v3 import generate
    import stowage.generate.instances_v3 as campaign
    selection=json.loads(Path('experiments/solver-diag-1028/pilot-d58/generation/selection.json').read_text())
    spec=selection['specs'][args.index]
    spec.pop('physics_sha256',None)
    originals=[]
    def patch(owner,name,wrapper):
        original=getattr(owner,name)
        originals.append((owner,name,original))
        setattr(owner,name,wrapper(original))
    def timed(label):
        def decorate(original):
            @wraps(original)
            def call(*a,**kw):
                t=time.perf_counter(); emit(label+'_start')
                result=None
                try:
                    result=original(*a,**kw); return result
                finally:
                    emit(label+'_end',elapsed_s=time.perf_counter()-t,status=getattr(result,'status',None))
            return call
        return decorate
    patch(gate,'_content_key',timed('cache_key'))
    import stowage.solver.ballast as ballast
    patch(ballast,'finish_ballast',timed('finisher'))
    patch(campaign,'screen_c2',timed('c2'))
    def gate_wrapper(original):
        @wraps(original)
        def call(instance,audit):
            context['gate']+=1
            emit('candidate_start',instance=instance.id,audit_attempt= audit.attempts)
            result=None;t=time.perf_counter()
            try:
                result=original(instance,audit);return result
            finally:
                emit('candidate_end',elapsed_s=time.perf_counter()-t,accepted=result is not None,audit=asdict(audit))
        return call
    patch(gate,'accept_background_candidate',gate_wrapper)
    from stowage.generate import random_yard, instances
    patch(random_yard,'completion_failures',timed('completion'))
    patch(instances,'completion_failures',timed('completion'))
    for module in (random_yard,instances):
        original=module.accept_background_candidate
        originals.append((module,'accept_background_candidate',original))
        module.accept_background_candidate=gate.accept_background_candidate
    def check_wrapper(original):
        @wraps(original)
        def call(instance,plan,*a,**kw):
            context['check']+=1;n=context['check'];prefix=args.out/f'check-{n:03d}'
            prefix.with_suffix('.instance.json').write_text(instance.model_dump_json(indent=2)+'\n')
            prefix.with_suffix('.plan.json').write_text(plan.model_dump_json(indent=2)+'\n')
            emit('check_start',instance=instance.id,ballast=plan.ballast.model_dump(mode='json') if plan.ballast else None)
            result=None;t=time.perf_counter()
            try:
                result=original(instance,plan,*a,**kw)
                prefix.with_suffix('.report.json').write_text(result.model_dump_json(indent=2)+'\n')
                return result
            finally:
                emit('check_end',elapsed_s=time.perf_counter()-t,passed=getattr(result,'passed',None))
        return call
    patch(checker,'check',check_wrapper)
    def part_wrapper(original):
        @wraps(original)
        def call(instance,ship,name,function):
            previous=context['part'];context['part']=name
            emit('part_start',call_index=instance.call_index,tank_masses=ship.tank_masses_kg)
            result=None;t=time.perf_counter()
            try:
                result=original(instance,ship,name,function);return result
            finally:
                duration=time.perf_counter()-t
                last_part.update(name=name,elapsed_s=duration)
                emit('part_end',elapsed_s=duration,ran=getattr(result,'ran',None),error=getattr(result,'error',None))
                context['part']=previous
        return call
    patch(full,'_run_part',part_wrapper)
    def merge_wrapper(original):
        @wraps(original)
        def call(parts,name,report,identity):
            emit('condition_part',condition_phase=identity.phase,condition_id=identity.id,part_name=name,elapsed_s=last_part.get('elapsed_s') if last_part.get('name')==name else None)
            return original(parts,name,report,identity)
        return call
    patch(full,'_merge_condition',merge_wrapper)
    def solve_wrapper(original):
        @wraps(original)
        def call(self,*a,**kw):
            t=time.perf_counter();emit('equilibrium_start',arguments=a,geometry=type(self).__name__)
            try:return original(self,*a,**kw)
            finally:emit('equilibrium_end',elapsed_s=time.perf_counter()-t,arguments=a,geometry=type(self).__name__)
        return call
    patch(HullGeometry,'_solve_uncached',solve_wrapper)
    def expired(signum,frame):raise ProfileDeadline(f'profile cap {args.cap_s}s')
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,args.cap_s)
    emit('run_start',pid=os.getpid(),spec=spec,source_start=before)
    result={}
    try:
        if args.replay:
            from stowage.model import Instance,MovePlan
            instance=Instance.model_validate_json(args.replay.with_suffix('.instance.json').read_text())
            plan=MovePlan.model_validate_json(args.replay.with_suffix('.plan.json').read_text())
            report=checker.check(instance,plan)
            result=dict(passed=report.passed)
        else:
            instance,audit=generate(spec)
            (args.out/'emitted.instance.json').write_text(instance.model_dump_json(indent=2)+'\n')
            result=dict(accepted=True,audit=audit)
    except BaseException as error:
        result=dict(accepted=False,error=str(error),error_type=type(error).__name__,traceback=traceback.format_exc())
        if hasattr(error,'audit'):result['audit']=error.audit
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        try:
            from stowage.solver.ballast_deadline import shutdown_worker
            shutdown_worker()
        except Exception:pass
        result.update(elapsed_s=time.perf_counter()-started,spec=spec,pid=os.getpid(),source_start=before,source_end=sources(),peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        (args.out/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True,default=str)+'\n')
        emit('run_end',result=result)
        for owner,name,original in reversed(originals):setattr(owner,name,original)
        events.close()
    print(json.dumps({k:v for k,v in result.items() if k not in {'source_start','source_end','traceback'}},sort_keys=True,default=str),flush=True)

if __name__=='__main__':main()

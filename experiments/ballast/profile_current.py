"""Single-worker phase profiling of a saved production slice; no policy overrides.

Each phase has a cooperative 3-second alarm and the supervisor independently
kills only its recorded worker after 20 seconds. Two repeats share caches.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'

class PhaseTimeout(BaseException):
    pass

def worker(folder, no_profile=False, snapshot=None):
    import cProfile
    import io
    import pstats
    import signal
    import numpy as np
    from stowage.io import load_instance, load_plan
    from stowage.moves import apply_plan
    from stowage.solver.ballast_physics import ExactContext
    from stowage.checker.stability import check_stability
    started=time.monotonic()
    def log(**data):
        print(json.dumps(dict(elapsed_s=time.monotonic()-started,**data)),flush=True)
    def expired(*args):
        raise PhaseTimeout()
    signal.signal(signal.SIGALRM,expired)
    def stage(name, fn):
        log(stage=name,event='start')
        profile=cProfile.Profile()
        t=time.monotonic()
        try:
            signal.setitimer(signal.ITIMER_REAL,3.)
            if not no_profile:
                profile.enable()
            result=fn()
            status='ok'
            return result
        except PhaseTimeout:
            status='timeout'
        except BaseException as error:
            status=f'{type(error).__name__}: {error}'
        finally:
            signal.setitimer(signal.ITIMER_REAL,0.)
            profile.disable()
            text=io.StringIO()
            if not no_profile:
                pstats.Stats(profile,stream=text).sort_stats('cumulative').print_stats(20)
            log(stage=name,event='end',status=status,wall_s=time.monotonic()-t,profile=text.getvalue())
    if snapshot:
        from stowage.model import Instance, MovePlan
        instance=stage('load_instance',lambda:Instance.model_validate(json.loads(snapshot.read_text())['instance']))
        plan=MovePlan(id='profile-current',instance_id=instance.id,call_index=instance.call_index)
    else:
        instance=stage('load_instance',lambda:load_instance(folder/'instance.json'))
        plan=stage('load_plan',lambda:load_plan(folder/'input-plan.json'))
    if instance is None or plan is None:
        return
    for repeat in range(2):
        tag=f'{repeat}:'
        replay=stage(tag+'apply_plan',lambda:apply_plan(instance,plan))
        if replay is None:
            continue
        ship=replay.final_state.ship
        context=stage(tag+'ExactContext',lambda:ExactContext(instance,ship,time.monotonic()+60.))
        if context is None:
            continue
        log(stage=tag+'context_info',geometry=type(context.geometry).__name__,
            direct_geometry=type(context.direct_geometry).__name__,method=context.method,
            tanks=len(context.tanks),base_correction_tanks=len(context.base.tanks),
            base_actual_tanks=len(context.base.actual_tanks),
            omitted=list(context.base.omitted_tanks))
        fills=np.zeros(len(context.tanks))
        condition=stage(tag+'condition_zero',lambda:context.condition(fills))
        if condition is not None:
            log(stage=tag+'condition_info',actual_tanks=len(condition.actual_tanks),
                correction_tanks=len(condition.tanks),table_count=len(condition.liquid_tables or {}),
                batch=condition.liquid_cg_groups is not None,omitted=list(condition.omitted_tanks))
        margins=stage(tag+'margins_zero',lambda:context.margins(fills))
        log(stage=tag+'margins_result',margins=margins)
        report=stage(tag+'check_stability',lambda:check_stability(instance,ship,call_index=instance.call_index))
        if report is not None:
            log(stage=tag+'stability_result',ran=report.ran,error=report.error,violations=[v.model_dump() for v in report.violations])

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--folder',type=Path,default=ROOT/'experiments/ballast/slices_current/random-00-feeder_500')
    parser.add_argument('--no-profile',action='store_true')
    parser.add_argument('--snapshot',type=Path)
    args=parser.parse_args()
    if args.worker:
        worker(args.folder,args.no_profile,args.snapshot)
    else:
        process=subprocess.Popen([sys.executable,__file__,'--worker','--folder',str(args.folder)]+(['--no-profile'] if args.no_profile else [])+(['--snapshot',str(args.snapshot)] if args.snapshot else []),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        started=time.monotonic()
        try:
            stdout,stderr=process.communicate(timeout=20.)
            timed_out=False
        except subprocess.TimeoutExpired:
            process.kill()
            stdout,stderr=process.communicate()
            timed_out=True
        result=dict(pid=process.pid,watchdog_timeout=timed_out,exit_code=process.returncode,
            total_s=time.monotonic()-started,folder=str(args.folder),stderr=stderr,
            phases=[json.loads(line) for line in stdout.splitlines() if line.strip()])
        suffix=('_literal' if args.snapshot else '')+('_plain' if args.no_profile else '')
        output=ROOT/f'experiments/ballast/profile_current{suffix}.json'
        output.write_text(json.dumps(result,indent=2)+'\n')
        print(output)

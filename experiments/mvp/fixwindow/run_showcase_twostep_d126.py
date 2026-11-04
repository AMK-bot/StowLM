"""Bounded live two-step attempt on one immutable frozen showcase input.

APPROXIMATION: cargo-only two-step construction with C2 off and no LNS.
D92 design securing is a postconstruction replay gate, not search pruning.
Scoped V4 ballast, unchanged full checker and bare token replay still gate.
Every expensive stage runs in its own recorded, owned watchdog subprocess.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
os.environ.pop('STOWAGE_BALLAST_V4',None)
# Resolve the original read-only utilities explicitly; subprocess __file__ stays this driver.
HERE=Path(__file__).resolve().parents[1]/'solve47/slack/showcase'
sys.path.insert(0,str(HERE))
import utils

ROOT=utils.ROOT
SHIPS=('feeder_500','feeder_800','feeder_1700')
STAGES=('bound','constructor','ballast','checker','mask')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_input(ship,report_path):
    release=json.loads(report_path.read_text())
    relative=f'showcase/{ship}-small.json'
    expected=release.get('hashes',{}).get(relative) or release.get('added_hashes',{}).get(relative)
    if not expected:
        raise ValueError(f'No frozen EVAL-V2-REPORT hash for {relative}')
    path=ROOT/'experiments/mvp/corpus'/relative
    if sha(path)!=expected:
        raise ValueError(f'Frozen showcase hash mismatch: {path}')
    return path,expected,release.get('current_release')


@contextmanager
def v4_finalizer():
    previous=os.environ.get('STOWAGE_BALLAST_V4')
    os.environ['STOWAGE_BALLAST_V4']='1'
    try:
        yield
    finally:
        if previous is None:os.environ.pop('STOWAGE_BALLAST_V4',None)
        else:os.environ['STOWAGE_BALLAST_V4']=previous


def artifact_inputs(output):
    names=['instance.json']
    if (output/'raw-result.json').exists():names.append('raw-result.json')
    if (output/'plan.json').exists():names.append('plan.json')
    return {name:sha(output/name) for name in names}


def isolate_caches(cache_root):
    """Reuse the read-only hero helper; serialize its shared mirror inventory."""
    import fcntl
    cache_root.mkdir(parents=True,exist_ok=True)
    with (cache_root/'.initialization.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        helper=ROOT/'experiments/mvp/showcase_tools/run_hero.py'
        spec=importlib.util.spec_from_file_location('owned_showcase_cache_helper',helper)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.isolate_caches(cache_root)
        fcntl.flock(lock,fcntl.LOCK_UN)


def twostep_complete(instance,args,row):
    """Keep construction and conservative postconstruction screening distinct."""
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.twostep import TwoStepPlanner
    from stowage.solver.gold_warmstart import _mask_replay
    from stowage.solver.securing_screen import DeckSecuringScreen
    config=FeasibilityConfig(extra={'global_screen':0})
    row['config']={'feasibility_extra':{'global_screen':0},'beam_width':4,
                   'lns':False,'securing_screen_mode':'postconstruction replay only'}
    phase=time.perf_counter()
    result=TwoStepPlanner(feasibility_config=config).plan(
        instance,seed=args.seed,time_limit_s=args.solve_s)
    row['construction_s']=time.perf_counter()-phase
    row['constructor_status']=result.status
    row['constructor_complete']=result.status=='ok' and result.plan is not None
    if not row['constructor_complete']:
        return result
    # APPROXIMATION: D92 is a conservative design-envelope gate. Its rejection
    # rejects this candidate; it does not prove physical instance infeasibility.
    phase=time.perf_counter()
    try:
        replay=_mask_replay(instance,result.plan,screen=DeckSecuringScreen(instance))
    finally:
        row['postconstruction_screen_s']=time.perf_counter()-phase
    row['postconstruction_replay']=replay
    metrics={**result.metrics,'constructor_method':'baseline.twostep',
             'lns':False,'postconstruction_d92_replay':replay,
             'postconstruction_d92_replay_s':row['postconstruction_screen_s']}
    if not replay.get('passed'):
        row['candidate_rejected_by_postconstruction_gate']=True
        row['rejection_is_infeasibility_proof']=False
        return result.model_copy(update={'status':'error',
            'message':'Two-step candidate rejected by postconstruction public-mask/D92 replay; not an infeasibility proof',
            'metrics':metrics})
    return result.model_copy(update={'metrics':metrics})


def worker(args):
    output=args.output
    before,source_digest,archive=utils.capture_source_snapshot(output)
    row=dict(stage=args.stage,passed=False,completed=False,started_utc=datetime.now(timezone.utc).isoformat(),
             source_files_start=before,source_sha256_start=source_digest,source_archive=archive,
             input_hashes=artifact_inputs(output),pid=os.getpid(),stages_finisher_environment='legacy except scoped V4 finalize',
             driver_sha256=sha(Path(__file__)),utility_sha256=sha(HERE/'utils.py'))
    started=time.perf_counter()
    try:
        if args.stage!='bound':
            phase=time.perf_counter();isolate_caches(args.cache_root)
            row.update(cache_setup_s=time.perf_counter()-phase,cache_root=str(args.cache_root),
                       cache_helper_sha256=sha(ROOT/'experiments/mvp/showcase_tools/run_hero.py'))
        from stowage.io import load_instance,load_plan,save_plan
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        assert rule_freeze_v1_enabled() is False,'Showcase rule-freeze must remain OFF'
        instance=load_instance(output/'instance.json')
        row['instance_id']=instance.id
        if args.stage=='bound':
            from stowage.bounds import lower_bound
            phase=time.perf_counter();bound=lower_bound(instance,time_limit_s=10.)
            row['call_wall_s']=time.perf_counter()-phase
            utils.save(output/'bound.json',bound.model_dump(mode='json'))
            row.update(passed=True,weighted=bound.weighted)
        elif args.stage=='constructor':
            phase=time.perf_counter()
            result=twostep_complete(instance,args,row)
            row['call_wall_s']=time.perf_counter()-phase
            utils.save(output/'raw-result.json',result.model_dump(mode='json'))
            if result.plan is not None:save_plan(result.plan,output/'raw-plan.json')
            row.update(passed=result.status=='ok' and result.plan is not None,status=result.status,
                       message=result.message,metrics=result.metrics)
        elif args.stage=='ballast':
            from stowage.model import PlannerResult
            from stowage.planner import finalize
            result=PlannerResult.model_validate_json((output/'raw-result.json').read_text())
            phase=time.perf_counter()
            with v4_finalizer():
                result=finalize(instance,result,time_limit_s=args.ballast_s)
            row['call_wall_s']=time.perf_counter()-phase
            assert 'STOWAGE_BALLAST_V4' not in os.environ,'Restore legacy environment before full gates'
            utils.save(output/'final-result.json',result.model_dump(mode='json'))
            if result.plan is not None:save_plan(result.plan,output/'plan.json')
            row.update(passed=result.status=='ok' and result.plan is not None,status=result.status,
                       message=result.message,metrics=result.metrics,ballast_v4_scoped=True)
        elif args.stage=='checker':
            from stowage.checker import check
            phase=time.perf_counter();report=check(instance,load_plan(output/'plan.json'))
            row['call_wall_s']=time.perf_counter()-phase
            utils.save(output/'report.json',report.model_dump(mode='json'))
            counts={}
            for violation in report.violations:
                if violation.severity=='hard':counts[violation.rule_id]=counts.get(violation.rule_id,0)+1
            row.update(passed=report.passed,hard_rule_counts=counts)
            if report.count is not None:
                row['moves']=report.count.model_dump(mode='json')
                row['moves']['total_lifts_now']=report.count.total_lifts_now
        elif args.stage=='mask':
            mask={};phase=time.perf_counter()
            try:
                utils.token_replay(instance,load_plan(output/'plan.json'),False,mask)
            finally:
                utils.summarize_replay(mask)
                utils.save(output/'mask.json',mask)
            row.update(passed=bool(mask.get('passed')),call_wall_s=time.perf_counter()-phase)
        else:raise ValueError(args.stage)
    except BaseException as exc:
        row.update(error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
    finally:
        after=utils.source_files();imported=utils.imported_source_files()
        changed=sorted(path for path in before.keys()|after.keys() if before.get(path)!=after.get(path))
        relevant=utils.pipeline_drift(changed,imported)
        row.update(completed=True,wall_s=time.perf_counter()-started,source_files_end=after,
                   imported_source_files=imported,changed_source_files=changed,
                   changed_pipeline_source_files=relevant,current_tree_evidence_valid=not relevant)
        row['pipeline_passed']=row['passed']
        row['passed']=row['pipeline_passed'] and not relevant
        utils.save(output/'stages'/(args.stage+'.json'),row)


def run_owned_stage(command,timeout,log_path,record):
    """Stop only the exact Popen child created here, never a process pattern."""
    start=time.perf_counter()
    with log_path.open('a') as stream:
        process=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT)
        record.update(pid=process.pid,command=command,watchdog_s=timeout)
        try:
            code=process.wait(timeout=timeout)
            return dict(returncode=code,timed_out=False,wall_s=time.perf_counter()-start)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
            return dict(returncode=process.returncode,timed_out=True,wall_s=time.perf_counter()-start)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ship',choices=SHIPS,required=True)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--solve-s',type=float,default=120)
    parser.add_argument('--ballast-s',type=float,default=120)
    parser.add_argument('--checker-s',type=float,default=180)
    parser.add_argument('--mask-s',type=float,default=240)
    parser.add_argument('--bound-s',type=float,default=30)
    parser.add_argument('--cache-root',type=Path,default=ROOT/'experiments/mvp/runs/showcase-v2-solver-numerical-cache')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--eval-report',type=Path,default=ROOT/'experiments/mvp/corpus_tools/EVAL-V2-REPORT.json')
    parser.add_argument('--stage',choices=STAGES,help=argparse.SUPPRESS)
    parser.add_argument('--preflight',action='store_true',help='Verify frozen hashes/settings; no solve or physics')
    args=parser.parse_args()
    if not 0<args.solve_s<=600 or not 0<args.ballast_s<=600:
        parser.error('solve and ballast budgets must be in (0,600]')
    if min(args.checker_s,args.mask_s,args.bound_s)<=0:parser.error('stage budgets must be positive')
    if args.stage:
        worker(args);return
    input_path,expected,release=freeze_input(args.ship,args.eval_report)
    if args.preflight:
        print(json.dumps(dict(ship=args.ship,input=str(input_path),input_sha256=expected,release=release,
              constructor_method='baseline.twostep',lns=False,securing_screen_mode='postconstruction replay only',c2=False,rule_freeze=False,
              solve_s=args.solve_s,ballast_s=args.ballast_s)));return
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    if (output/'result.json').exists():raise ValueError('Immutable attempt exists; use a fresh output directory')
    (output/'stages').mkdir(exist_ok=True)
    (output/'instance.json').write_bytes(input_path.read_bytes())
    row=dict(ship=args.ship,seed=args.seed,completed=False,passed=False,stages={},
             instance_id=None,input_path=str(input_path),input_sha256=expected,eval_release=release,
             eval_report_sha256=sha(args.eval_report),driver_sha256=sha(Path(__file__)),utility_sha256=sha(HERE/'utils.py'),
             rule_freeze_v1=False,c2=False,securing_screen=True,securing_screen_mode='postconstruction replay only',lns=False,
             constructor_method='baseline.twostep',
             approximation='APPROXIMATION: bounded two-step construction; conservative postconstruction D92 gate; no LNS',
             environment={'generation':'frozen input; unchanged','constructor':None,'finalize':'STOWAGE_BALLAST_V4=1 scoped',
                          'checker':None,'mask':None},command=sys.argv,
             budgets=dict(solve_s=args.solve_s,ballast_s=args.ballast_s,checker_s=args.checker_s,mask_s=args.mask_s),
             started_utc=datetime.now(timezone.utc).isoformat())
    utils.save(output/'result.json',row)
    started=time.perf_counter()
    for stage in STAGES:
        timeout={'bound':args.bound_s,'constructor':args.solve_s+15,'ballast':args.ballast_s+15,
                 'checker':args.checker_s,'mask':args.mask_s}[stage]
        command=[sys.executable,str(Path(__file__).resolve()),'--ship',args.ship,'--seed',str(args.seed),
                 '--solve-s',str(args.solve_s),'--ballast-s',str(args.ballast_s),'--output',str(output),
                 '--checker-s',str(args.checker_s),'--mask-s',str(args.mask_s),'--bound-s',str(args.bound_s),
                 '--cache-root',str(args.cache_root),'--stage',stage]
        recorded=row['stages'][stage]=dict(passed=False)
        execution=run_owned_stage(command,timeout,output/(stage+'.log'),recorded)
        recorded.update(execution)
        stage_path=output/'stages'/(stage+'.json')
        if stage_path.exists():
            evidence=json.loads(stage_path.read_text());recorded.update(evidence_path=str(stage_path),
                passed=bool(evidence.get('passed')) and not execution['timed_out'] and execution['returncode']==0)
            if stage=='checker' and evidence.get('moves'):row['moves']=evidence['moves']
            if evidence.get('instance_id'):row['instance_id']=evidence['instance_id']
        if sha(output/'instance.json')!=expected:
            recorded.update(passed=False,error='Frozen input changed during attempt')
        utils.save(output/'result.json',row)
        if not recorded.get('passed'):
            row['blocked_at']=stage;break
    row.update(completed=True,wall_s=time.perf_counter()-started,finished_utc=datetime.now(timezone.utc).isoformat())
    row['passed']=all(row['stages'].get(stage,{}).get('passed') for stage in STAGES)
    row['artifact_hashes']={name:sha(output/name) for name in
        ('instance.json','raw-result.json','raw-plan.json','final-result.json','plan.json','report.json','mask.json','bound.json')
        if (output/name).exists()}
    utils.save(output/'result.json',row)
    package=dict(instance_path=str(output/'instance.json'),artifact_path=str(output/'final-result.json'),
        report_path=str(output/'report.json'),bound_path=str(output/'bound.json'),mask_path=str(output/'mask.json'),
        method='baseline.twostep',method_detail='live two-step; C2 off; postconstruction D92 replay; scoped V4 ballast; no LNS',
        command=sys.argv,passed=row['passed'])
    utils.save(output/'package_inputs.json',dict(rows=[package]))
    print(json.dumps(dict(ship=args.ship,passed=row['passed'],blocked_at=row.get('blocked_at'),wall_s=row['wall_s'])),flush=True)


if __name__=='__main__':main()

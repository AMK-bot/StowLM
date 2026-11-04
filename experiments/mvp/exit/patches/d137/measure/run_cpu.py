"""D137 real train-only teacher calibration; CPU/native1, checkpoint every map.
No training artifact is published. Re-run requires an identical source epoch.
"""
from __future__ import annotations
import argparse,hashlib,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[6]
sys.path.insert(0,str(ROOT/'src'))
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','STOWAGE_IN_PORT_WORKERS'):
    os.environ[name]='1'
os.environ['TOKENIZERS_PARALLELISM']='false'

def atomic(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temporary.replace(path)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--maps',nargs='+',required=True)
    parser.add_argument('--planning-s',type=float,default=120);parser.add_argument('--finalize-s',type=float,default=120)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    from llm.exit import resources
    from llm.exit.runner import source_pins
    from llm.exit.identity import identity_guard
    from llm.exit.selection import Candidate,verify_candidate
    from llm.stowlm.sft_data import d79_engine_config
    from stowage.io import load_instance
    from stowage.solver.twostep import TwoStepPlanner
    pins=source_pins();manifest=dict(schema='d137-cpu-teacher-calibration-v1',sources=pins,maps=args.maps,
        planning_s=args.planning_s,finalize_s=args.finalize_s,workers=1,native_threads=1,gpu=False,
        approximation='Two or three train maps calibrate throughput; not a performance evaluation or next-epoch guarantee.')
    target=args.out/'manifest.json'
    if target.exists() and json.loads(target.read_text())!=manifest:raise RuntimeError('calibration epoch or inputs changed')
    atomic(target,manifest);resources.OWNER='mvp-exit/d137-calibration'
    (args.out/'compute').mkdir(parents=True,exist_ok=True)
    # CPU-only lease deliberately does not acquire the unrelated resident GPU lock.
    with resources._reservation(args.out/'compute',cores=1,ram_gb=4,gpu=False,estimate_s=1200):
        for index,relative in enumerate(args.maps):
            output=args.out/f'row-{index:03}.json'
            if output.exists():continue
            if source_pins()!=pins:raise RuntimeError('source drift before map')
            path=(ROOT/relative).resolve();instance=load_instance(path)
            identity_guard(ROOT/'experiments/mvp/corpus').require_allowed(instance,role='train',
                raw_sha=hashlib.sha256(path.read_bytes()).hexdigest(),input_path=path)
            started=time.monotonic();row=dict(input=relative,input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                instance_id=instance.id,seed=instance.seed,planning_budget_s=args.planning_s,finalize_budget_s=args.finalize_s)
            atomic(args.out/'phase.json',dict(index=index,phase='twostep',started=time.time()))
            result=TwoStepPlanner(feasibility_config=d79_engine_config()).plan(instance,seed=instance.seed,time_limit_s=args.planning_s)
            row.update(planning_s=time.monotonic()-started,planner_status=result.status,planner_message=result.message,
                planner_metrics=result.metrics,plan_available=result.plan is not None,planner_version=result.planner_version)
            if result.plan is not None:
                atomic(args.out/'phase.json',dict(index=index,phase='finisher_checker_mask',started=time.time()))
                phase=time.monotonic();verified=verify_candidate(instance,Candidate(result.plan,'baseline_twostep','measure'),ballast_s=args.finalize_s)
                row.update(verification_s=time.monotonic()-phase,passed=verified.passed,moves=verified.weighted_moves,
                    verification=verified.details)
            else:row.update(passed=False,outcome='planner_no_complete_plan')
            row['total_s']=time.monotonic()-started;row['source_epoch_unchanged']=source_pins()==pins
            atomic(output,row);print(json.dumps({key:row.get(key) for key in ('input','planning_s','verification_s','total_s','passed','moves')}),flush=True)
            if not row['source_epoch_unchanged']:raise RuntimeError('source drift during measurement; retain row as historical only')
        atomic(args.out/'DONE.json',dict(finished=time.time(),maps=len(args.maps),source_epoch_unchanged=source_pins()==pins))
if __name__=='__main__':main()

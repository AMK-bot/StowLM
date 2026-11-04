"""One-core, resumable direct V4 diagnostic on explicitly allowed train plans.

The wrapper observes Evaluator calls and retains negative margins/tank vectors;
it delegates all decisions and acceptance to the pinned implementation.
"""
from __future__ import annotations
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
RUNTIME = HERE.parent/'search/runtime'
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
             'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','HIGHS_THREADS'):
    os.environ[name] = '1'
os.environ['STOWAGE_IN_PORT_WORKERS'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0, str(RUNTIME/'src'))

def read(path): return json.loads(Path(path).read_text())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2,sort_keys=True,default=str)+'\n')
    temp.replace(path)

def summary(report):
    value=report.model_dump(mode='json') if hasattr(report,'model_dump') else report
    # In this checker aggregate violations may be empty; PartReport owns them.
    violations=[v for p in value['parts'].values() for v in p.get('violations',[])]
    return dict(passed=value['passed'], rules=sorted({v['rule_id'] for v in violations}),
        part_failures={k:dict(rules=sorted({v['rule_id'] for v in p.get('violations',[])}),
            negative_margins={a:b for a,b in p.get('margins',{}).items() if isinstance(b,(int,float)) and b<0},
            metrics={a:b for a,b in p.get('metrics',{}).items() if not a.startswith(('condition:', 'note:'))})
            for k,p in value['parts'].items() if p.get('violations') or p.get('error')})

def run_task(task):
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from stowage.moves import apply_plan
    from stowage.conventions import cells_of_slot
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.solver.ballast_v4 import finish
    from stowage.solver.ballast_v4_evaluator import Evaluator
    from stowage.checker import check
    folder=HERE/'runs'/task['name']; folder.mkdir(parents=True,exist_ok=True)
    if (folder/'complete.json').exists(): return read(folder/'complete.json')
    cohort=read(ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json')
    assert cohort['split']=='train'
    job=next(j for j in cohort['maps'] if j['map_id']==task['map_id'])
    instance_path=ROOT/job['input']; assert sha(instance_path)==job.get('input_sha256',job.get('sha256'))
    instance=load_instance(instance_path)
    result_path=ROOT/task['raw_result']; raw=PlannerResult.model_validate(read(result_path))
    assert raw.plan is not None
    import stowage
    assert Path(stowage.__file__).resolve().is_relative_to(RUNTIME.resolve())
    snapshot=read(RUNTIME/'snapshot.json')
    # Source bytes checked once at task admission, never borrowed from live src.
    for rel,digest in snapshot['files'].items():
        assert sha(RUNTIME/rel)==digest,rel
    plan=raw.plan.model_copy(update={'ballast':None})
    record=dict(task=task,pid=os.getpid(),started_unix=time.time(),
        snapshot_sha256=snapshot['snapshot_sha256'],cache_snapshot=read(RUNTIME/'cache-snapshot.json')['cache_sha256'],
        input_sha256=sha(instance_path),raw_sha256=sha(result_path),
        num_workers=1,rule_freeze=False,observational_wrapper=True)
    save(folder/'admission.json',record)
    replay=apply_plan(instance,plan)
    cells={c.id:c for c in instance.vessel.cells}; boxes={b.id:b for b in instance.containers}
    occupancy={cid:boxes[p.container_id] for p in replay.final_state.ship.placements for cid in cells_of_slot(p.slot)}
    floors={}
    for stack in instance.vessel.stacks:
        z=stack.base_z_m
        for cid in stack.cell_ids:
            floors[cid]=z
            if cid in occupancy:z+=occupancy[cid].height_m
    cargo=[]
    for p in replay.final_state.ship.placements:
        box=boxes[p.container_id]; touched=[cells[cid] for cid in cells_of_slot(p.slot)]
        cargo.append(dict(cid=box.id,pod=str(box.pod),mass_kg=box.gross_mass_kg,slot=p.slot,
            loaded_this_call=box.id in instance.load_list,
            tcg_m=sum(c.tcg_m for c in touched)/len(touched),
            lcg_m=sum(c.lcg_m for c in touched)/len(touched),
            kg_m=sum(floors[c.id] for c in touched)/len(touched)+box.height_m/2))
    mass=sum(c['mass_kg'] for c in cargo)
    geometry=dict(count=len(cargo),mass_kg=mass,
        transverse_moment_kg_m=sum(c['mass_kg']*c['tcg_m'] for c in cargo),
        longitudinal_moment_kg_m=sum(c['mass_kg']*c['lcg_m'] for c in cargo),
        vertical_moment_kg_m=sum(c['mass_kg']*c['kg_m'] for c in cargo),cargo=cargo)
    geometry['tcg_m']=geometry['transverse_moment_kg_m']/mass
    geometry['lcg_m']=geometry['longitudinal_moment_kg_m']/mass
    geometry['kg_m']=geometry['vertical_moment_kg_m']/mass
    save(folder/'cargo.json',geometry)
    evaluate_original=Evaluator.evaluate
    init_original=Evaluator.__init__
    active={}; trace_path=folder/'evaluations.jsonl'
    def observed_init(self,*args,**kwargs):
        init_original(self,*args,**kwargs);active['evaluator']=self
    def observed_evaluate(self,fractions,active_keys=None):
        start=time.monotonic(); error=None; margins=None
        try:
            margins=evaluate_original(self,fractions,active_keys=active_keys)
            return margins
        except BaseException as exc:
            error=type(exc).__name__;raise
        finally:
            row=dict(unix=time.time(),elapsed_s=time.monotonic()-start,
                selected_keys=sorted(active_keys) if active_keys is not None else None,
                fractions=[float(v) for v in fractions],error=error,
                negative_margins={k:float(v) for k,v in (margins or {}).items() if v<0},
                stage=self.diagnostics.get('stage'))
            with trace_path.open('a') as stream:stream.write(json.dumps(row,sort_keys=True)+'\n')
            save(folder/'progress.json',dict(unix=time.time(),diagnostics=self.diagnostics,best_merit=self.best_merit))
    Evaluator.__init__=observed_init;Evaluator.evaluate=observed_evaluate
    started=time.monotonic()
    try:
        with rule_freeze_v1(enabled=False): outcome=finish(instance,plan,time_limit_s=task['budget_s'],seed=0)
        save(folder/'outcome.json',outcome.model_dump(mode='json'))
        record.update(status=outcome.status,cause=outcome.not_found_cause,
            elapsed_s=time.monotonic()-started,diagnostics=outcome.diagnostics)
        evaluator=active.get('evaluator')
        if evaluator is not None:
            save(folder/'best-parts.json',{k:v.model_dump(mode='json') for k,v in evaluator.best_parts.items()})
        if outcome.plan is not None and outcome.status in ('ok','kept'):
            with rule_freeze_v1(enabled=False): report=check(instance,outcome.plan)
            save(folder/'accepted-full-report.json',report.model_dump(mode='json'))
            record['independent_full_checker']=summary(report)
        save(folder/'complete.json',record)
    finally:
        Evaluator.evaluate=evaluate_original;Evaluator.__init__=init_original
    return record

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--detach',action='store_true');args=parser.parse_args()
    if args.detach:
        command=[sys.executable,str(Path(__file__).resolve())]
        with (HERE/'controller.log').open('a') as log:
            child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        save(HERE/'launch.json',dict(pid=child.pid,command=command,unix=time.time()))
        print(child.pid);return
    # Advisory lock covers future launches; the already admitted first long
    # controller predates this guard and is intentionally allowed to finish.
    with (HERE/'controller.lock').open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        save(HERE/'owned.json',dict(pid=os.getpid(),started_unix=time.time(),workers=1))
        for task in read(HERE/'tasks.json'):
            try:
                row=run_task(task);print(json.dumps({k:v for k,v in row.items() if k!='diagnostics'}),flush=True)
            except BaseException:
                save(HERE/'runs'/task['name']/'error.json',dict(traceback=traceback.format_exc(),unix=time.time()))
                raise
        save(HERE/'completion.json',dict(unix=time.time(),pid=os.getpid(),complete=True))

if __name__=='__main__':main()

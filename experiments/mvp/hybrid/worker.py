"""One sealed planner/fresh audit process, including owned finisher children."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
from time import perf_counter

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('HYBRID_RUNTIME_ROOT', HERE.parents[2])).resolve()
ORIGINAL = Path(os.environ.get('HYBRID_ORIGINAL_ROOT', ROOT)).resolve()
WRITE_ROOT = Path(os.environ.get('HYBRID_WRITE_ROOT', ORIGINAL/'experiments/mvp/hybrid')).resolve()
IMMUTABLE = ROOT.parent if os.environ.get('HYBRID_RUNTIME_ROOT') else None
STAT_CACHE = {}


def confine_writes():
    def guard(event,args):
        paths = []
        if event == 'open' and isinstance(args[0],(str,bytes,os.PathLike)):
            mode,flags = args[1:3]
            if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
                paths = [args[0]]
        elif event in ('os.remove','os.rmdir','os.mkdir','os.chmod','os.truncate'):
            paths = [args[0]]
        elif event in ('os.rename','os.link','os.symlink'):
            paths = list(args[:2])
        for value in paths:
            path = Path(os.fsdecode(value)).resolve()
            if (IMMUTABLE is not None and path.is_relative_to(IMMUTABLE)) or (
                    not path.is_relative_to(WRITE_ROOT) and path != Path('/dev/null')):
                raise PermissionError(f'hybrid immutable/write boundary: {path}')
    sys.addaudithook(guard)


# Spawn children execute this module as __mp_main__ too. Install isolation before
# any project imports, including those made when unpickling finisher targets.
confine_writes()
if os.environ.get('HYBRID_RUNTIME_MANIFEST'):
    import runtime as sealed_runtime
    RUNTIME = json.loads(Path(os.environ['HYBRID_RUNTIME_MANIFEST']).read_text())
    sealed_runtime.activate_runtime(RUNTIME)
else:
    RUNTIME = None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_inputs(job):
    if digest(ORIGINAL/job['input']) != job['binding']['input_sha256']:
        raise ValueError('input changed after cohort seal')
    for path in job['candidate_paths']:
        if digest(path) != job['binding']['candidates'][path]:
            raise ValueError(f'candidate changed after cohort seal: {path}')


def plan(job):
    from stowage.io import load_instance
    import planner
    from candidates import load_starts
    from validation import plan_sha256
    instance = load_instance(ORIGINAL/job['input'])
    extras,metadata = load_starts(instance,job['candidate_paths']) if job['arm'] != 'none' else ([],{})
    print(json.dumps(dict(stage='candidates_ready',arm=job['arm'],admitted=len(extras))),flush=True)
    original_finish = planner._finish
    def logged_finish(*args,**kwargs):
        began = perf_counter()
        print(json.dumps(dict(stage='finish_start',label=kwargs['label'])),flush=True)
        result,row = original_finish(*args,**kwargs)
        print(json.dumps(dict(stage='finish_end',label=kwargs['label'],passed=row.get('passed'),
                              seconds=round(perf_counter()-began,2))),flush=True)
        return result,row
    planner._finish = logged_finish
    result,record = planner.plan_and_check(instance,search_s=min(120.,max(20.,6.*len(instance.load_list))),
                                          finalize_s=300.,seed=0,extra_starts=extras)
    row = dict(record,method=planner.NAME,version=planner.VERSION,n_boxes=len(instance.load_list),
               seed=0,finalize_s=300,passed=result is not None,candidates=metadata,repaired=False)
    if result is not None:
        row.update(weighted=next(t for t in reversed(record['tries']) if t['passed'])['weighted'],
                   plan=json.loads(result.plan.model_dump_json()))
        row['plan_sha256'] = plan_sha256(row['plan'])
    return row


def audit(job):
    # Imported checker code is sealed; only the bound original input is external.
    import experiments.mvp.frontier.audit as frozen_audit
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from contribution import for_plan,plan_decisions
    from candidates import _arm,_display
    from validation import plan_sha256
    frozen_audit.ROOT = ORIGINAL
    data = frozen_audit.one(job['target'])
    stored = json.loads(Path(job['target']).read_text())
    instance = load_instance(ORIGINAL/job['input'])
    final_plan = MovePlan.model_validate(stored['plan'])
    evidence = for_plan(instance,final_plan,stored['final_search_origin'],stored['final_seed_decisions'])
    admitted_seeds = {}
    for entry in (stored.get('candidates') or {}).get('admitted',[]):
        path = Path(entry['path'])
        path = path if path.is_absolute() else ORIGINAL/path
        if entry['label'] != f'{_arm(path)}:{_display(path)}' or str(path) not in job['candidate_paths']:
            raise ValueError('admitted label/path differs from the actual pinned source')
        if str(path) not in job['binding']['candidates'] or digest(path) != job['binding']['candidates'][str(path)]:
            raise ValueError('admitted seed is not bound to the cohort candidate bytes')
        raw = json.loads(path.read_text())
        source = MovePlan.model_validate(raw['plan'] if 'plan' in raw else raw)
        decisions = plan_decisions(instance,source)
        seed = {box:decisions[box] for box in sorted(instance.load_list) if box in decisions}
        persisted = next((s.get('seed_decisions') for s in stored.get('extra_starts',[])
                          if s.get('origin') == entry['label']),None)
        if seed != persisted:
            raise ValueError('persisted admitted decisions differ from actual source candidate replay')
        admitted_seeds[entry['label']] = seed
    passed = all(data.get(k) is True for k in ('passed','legal','mask_ok','complete','all_loaded'))
    passed = passed and data.get('weighted') == data.get('stored') and evidence == stored['final_contribution']
    return dict(data,passed=passed,fresh_process=True,repaired=False,pid=os.getpid(),
                plan_sha256=plan_sha256(stored['plan']),final_contribution=evidence,
                admitted_seed_decisions=admitted_seeds)


def main():
    mode,path = sys.argv[1:]
    job = json.loads(Path(path).read_text())
    target = Path(job['target'])
    target = target.with_suffix('.audit.json') if mode == 'audit' else target
    try:
        if RUNTIME is None or job['runtime'] != RUNTIME:
            raise ValueError('worker must execute inside its exact sealed runtime')
        sealed_runtime.verify_runtime(RUNTIME,stat_cache=STAT_CACHE)
        verify_inputs(job)
        row = audit(job) if mode == 'audit' else plan(job)
        verify_inputs(job)
        sealed_runtime.assert_runtime_imports(RUNTIME)
        sealed_runtime.verify_runtime(RUNTIME,stat_cache=STAT_CACHE)
    except Exception as error:
        traceback.print_exc()
        row = dict(passed=False,repaired=False,error=f'{type(error).__name__}: {error}',fresh_process=mode == 'audit')
    row.update(instance_id=job['instance_id'],input=job['input'],arm=job['arm'],
               cohort_id=job['binding']['cohort_id'],runtime_id=job['binding']['runtime_id'],binding=job['binding'])
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary = target.with_name(target.name+f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(row,indent=1,sort_keys=True,allow_nan=False)+'\n')
    temporary.replace(target)


if __name__ == '__main__':
    main()

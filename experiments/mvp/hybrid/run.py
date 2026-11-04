"""D205/D206: declared 40 maps, four workers, explicit release, fresh audits.

UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/mvp/hybrid/run.py
Create <out>/STOP for a cooperative drain. Quarantined cohorts cannot resume.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('HYBRID_ORIGINAL_ROOT', HERE.parents[2])).resolve()
OUTPUT_ROOT = Path(os.environ.get('HYBRID_WRITE_ROOT', ROOT/'experiments/mvp/hybrid')).resolve()
sys.path.insert(0, str(HERE))
from admission import RULE, wait_for_admission, reading
from processes import POLICY, run_owned
from runtime import seal_runtime, verify_runtime, runtime_environment
from validation import (cohort_id, expected_binding, plan_sha256, require_active_directory,
                        require_manifest, validate_row)

THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
           'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS')
LOCK = threading.Lock()
VERIFY_LOCK = threading.Lock()
STAT_CACHE = {}


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def publish(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+f'.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=1, sort_keys=True, allow_nan=False)+'\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def source_binding():
    """Provenance at seal time only; running trials never hash the live tree."""
    paths = list((ROOT/'src').rglob('*.py')) + list((ROOT/'params').rglob('*.toml'))
    paths += list((ROOT/'params').rglob('*.json'))
    for folder, names in (
        ('frontier', ('planner.py','alns.py','blocked.py','blocks.py','master.py','sequence.py','sight.py','audit.py')),
        ('strategy', ('compact.py','blockrule/rule.py','blockrule/capacity_candidate.py','headroom/layout.py','k1b/judge.py'))):
        paths += [ROOT/'experiments/mvp'/folder/name for name in names]
    paths += [p for p in (ROOT/'experiments/mvp/hybrid').glob('*.py') if not p.name.startswith('test_')]
    paths += [ROOT/'experiments/review/v4/logic-hybrid.md', ROOT/'experiments/mvp/gold_tools/driver.py']
    paths += [HERE/'cohort-selection.json', HERE/'review-starts.jsonl']
    paths += [HERE/'PROFILE.md', HERE/'PROFILE.json']
    return {str(p.relative_to(ROOT)):sha(p) for p in sorted(set(paths))}


def validation_inputs():
    paths = [p for name in ('audit_small30.txt','reserve_small25.txt')
             for p in (ROOT/'experiments/mvp/valsplit'/name).read_text().split()]
    if len(paths) != 55 or len(set(paths)) != 55:
        raise ValueError('expected exactly 55 distinct validation maps')
    for path in paths:
        if not (ROOT/path).resolve().is_relative_to(ROOT/'experiments/mvp/corpus/val/small') or path.endswith('.audit.json'):
            raise ValueError(f'non-validation-small input: {path}')
    return paths


def selection():
    """The review forecast fixes membership before accepted measurement rows."""
    chosen = json.loads((HERE/'cohort-selection.json').read_text())
    groups = chosen['groups']
    entering, aa = groups['entering'], groups['aa']
    if (len(entering) != 32 or len(aa) != 8 or len(set(entering+aa)) != 40
            or entering != sorted(entering) or aa != sorted(aa)):
        raise ValueError('D205 requires 32 declared entering and 8 distinct A/A maps')
    table = chosen['table']
    expected_entering = sorted(r['instance_id'] for r in table if r['entering'])
    expected_aa = sorted(r['instance_id'] for r in table if not r['entering'])[:8]
    if entering != expected_entering or aa != expected_aa:
        raise ValueError('declared cohort differs from the fixed review-table selection')
    if sha(HERE/'review-starts.jsonl') != chosen['review_table_sha256']:
        raise ValueError('review forecast changed after cohort declaration')
    forecasts = {r['id']:r for r in (json.loads(line) for line in (HERE/'review-starts.jsonl').read_text().splitlines())}
    if len(forecasts) != 55 or len(table) != 55:
        raise ValueError('selection must describe all 55 reviewed validation maps')
    for row in table:
        iid = row['instance_id']
        path = ROOT/'experiments/mvp/frontier/runs/val-0.9'/f'{iid}.json'
        if chosen['reference_hashes'].get(str(path.relative_to(ROOT))) != sha(path):
            raise ValueError('stored frontier selection evidence changed')
        old = json.loads(path.read_text()).get('runs',[])
        second = old[1]['start_objective'] if len(old)>1 else None
        arms = forecasts[iid]['arms']
        best = lambda tags:min([x[0] for tag in tags for x in arms[tag]['best']],default=None)
        llm, random = best(('k1c','k1b')), best(('krand','k1r'))
        enters = any(cost is not None and (second is None or cost<second) for cost in (llm,random))
        if row != dict(instance_id=iid,second_start=second,llm_best=llm,random_best=random,entering=enters):
            raise ValueError('selection differs from the logic-review start table')
    return chosen


def inputs():
    chosen = selection()
    ids = set(chosen['groups']['entering']+chosen['groups']['aa'])
    paths = [p for p in validation_inputs() if json.loads((ROOT/p).read_text())['id'] in ids]
    if len(paths) != 40:
        raise ValueError('declared 40-map cohort is not contained in validation-small')
    return paths


def contained(path):
    path = Path(path).resolve()
    if not path.is_relative_to(OUTPUT_ROOT):
        raise ValueError(f'output must stay under {OUTPUT_ROOT}: {path}')
    return path


def event(out, kind, **fields):
    row = dict(event=kind, utc=utc(), load=list(os.getloadavg()))
    row.update(fields)
    with LOCK:
        with (out/'events.jsonl').open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True)+'\n')
    print(json.dumps(row, sort_keys=True), flush=True)


def environment(out, runtime):
    tmp = out/'tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    return {**os.environ, **{key:'1' for key in THREADS},
            'UV_CACHE_DIR':str(ROOT/'.uv-cache'), 'PYTHONDONTWRITEBYTECODE':'1',
            'PYTHONUNBUFFERED':'1', 'CUDA_VISIBLE_DEVICES':'', 'TMPDIR':str(tmp),
            'HYBRID_WRITE_ROOT':str(OUTPUT_ROOT), 'HYBRID_ORIGINAL_ROOT':str(ROOT),
            **runtime_environment(runtime)}


def verify_seal(runtime):
    with VERIFY_LOCK:
        return verify_runtime(runtime, stat_cache=STAT_CACHE)


def launch(out, env, mode, jobpath, logfile, job, started=None):
    worker = Path(job['runtime']['root'])/'experiments/mvp/hybrid/worker.py'
    def record(kind, **fields):
        event(out, kind, job=str(jobpath), arm=job['arm'], instance_id=job['instance_id'], **fields)
        if kind == 'owned_pid' and started is not None:
            started.set()
    with logfile.open('a') as stream:
        result = run_owned([sys.executable,str(worker),mode,str(jobpath)], cwd=ROOT, env=env,
                           stream=stream, mode=mode, policy=job['binding']['watchdog_policy'], record=record)
    if result.get('undrained'):
        (out/'STOP').touch()
    return result


def reusable(path, *, manifest, job):
    if not path.exists():
        return False
    require_active_directory(path.parent.parent)
    return validate_row(json.loads(path.read_text()), manifest=manifest,
                        expected_iid=job['instance_id'], expected_input=job['input'],
                        expected_arm=job['arm'], expected_binding=job['binding'])


def trial(job, out, env, manifest, started=None):
    """Only one queued lane may wait for admission; main waits for its launch."""
    started = started or threading.Event()
    if (out/'STOP').exists() or (out/'HOLD').exists():
        started.set()
        return 'stopped'
    target = out/job['arm']/f"{job['instance_id']}.json"
    row, admission, begin = dict(passed=False, repaired=False), None, None
    try:
        if (out/'STOP').exists() or (out/'HOLD').exists():
            return 'stopped'
        require_active_directory(out)
        if reusable(target, manifest=manifest, job=job):
            return 'cached'
        verify_seal(job['runtime'])
        jobpath = out/'jobs'/f"{job['index']:03d}-{job['arm']}.json"
        publish(jobpath, dict(job, target=str(target)))
        log = out/'logs'/f"{job['index']:03d}-{job['arm']}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        admission = wait_for_admission(stopped=lambda:(out/'STOP').exists() or (out/'HOLD').exists(),
                      log=lambda kind,**data:event(out,kind,arm=job['arm'],instance_id=job['instance_id'],**data))
        if admission is None or (out/'STOP').exists() or (out/'HOLD').exists():
            return 'stopped'
        begin = time.perf_counter()
        event(out,'trial_start',arm=job['arm'],instance_id=job['instance_id'],index=job['index'],admission=admission)
        execution = launch(out,env,'plan',jobpath,log,job,started)
        row = json.loads(target.read_text()) if target.exists() else dict(passed=False,repaired=False)
        row['planner_execution'] = execution
        if execution['returncode'] or execution.get('timed_out'):
            row.update(passed=False,error='planner outer watchdog/process failure',timed_out=execution.get('timed_out'))
            (out/'STOP').touch()
        elif row.get('passed'):
            execution = launch(out,env,'audit',jobpath,log,job)
            auditpath = target.with_suffix('.audit.json')
            row['audit'] = json.loads(auditpath.read_text()) if auditpath.exists() else {}
            row['audit_execution'] = execution
            if execution['returncode'] or execution.get('timed_out') or row['audit'].get('passed') is not True:
                row.update(passed=False,error='fresh audit/watchdog failed',timed_out=execution.get('timed_out'))
                (out/'STOP').touch()
        verify_seal(job['runtime'])
        if sha(ROOT/job['input']) != job['binding']['input_sha256']:
            raise ValueError('bound input changed during trial')
        for path in job['candidate_paths']:
            if sha(path) != job['binding']['candidates'][path]:
                raise ValueError(f'bound candidate changed during trial: {path}')
    except Exception as error:
        row.update(passed=False,error=f'{type(error).__name__}: {error}')
        (out/'STOP').touch()
    finally:
        # Release the FIFO startup handshake on stop, cache hit, or failure too.
        started.set()
    if admission is None and not row.get('error'):
        return 'stopped'
    row.update(binding=job['binding'],cohort_id=manifest['cohort_id'],instance_id=job['instance_id'],
               arm=job['arm'],input=job['input'],completed=True,repaired=False,admission=admission,
               start_utc=admission['utc'] if admission else utc(),end_utc=utc(),
               load_start=[admission['load_1m']] if admission else [],load_end=list(os.getloadavg()),
               core_mask_start=admission['core_mask'] if admission else None,
               core_count_start=admission.get('core_count') if admission else None,
               wall_s=round(time.perf_counter()-begin,2) if begin else 0.)
    if row.get('plan') is not None:
        row['plan_sha256'] = plan_sha256(row['plan'])
    if not row.get('error') and not validate_row(row,manifest=manifest,expected_iid=job['instance_id'],expected_arm=job['arm']):
        row.update(passed=False,error='shared row validator rejected completed trial')
    if row.get('error'):
        (out/'STOP').touch()  # No replacement trials after an infrastructure failure.
    publish(target,row)
    event(out,'trial_end',arm=job['arm'],instance_id=job['instance_id'],passed=row.get('passed'),
          weighted=row.get('weighted'),wall_s=row['wall_s'],error=row.get('error'))
    return row.get('passed')


def reference_rows(maps):
    records = {}
    for iid in maps:
        path = ROOT/'experiments/mvp/frontier/runs/val-0.9'/f'{iid}.json'
        if path.exists():
            source = json.loads(path.read_text())
            records[iid] = dict(passed=source.get('passed'),weighted=source.get('weighted'),
                source_sha256=sha(path),runs=[{k:run.get(k) for k in ('start','start_objective','iterations')}
                                             for run in source.get('runs',[])])
    return records


def build_manifest(out,args):
    path = out/'manifest.json'
    if path.exists():
        manifest = json.loads(path.read_text())
        require_manifest(manifest)
        if manifest.get('amendments') != ['D205','D206'] or manifest.get('groups') != selection()['groups']:
            raise ValueError('resume cohort predates the declared D205/D206 selection')
        if manifest['workers'] != args.workers:
            raise ValueError('resume worker count differs')
        for arm in ('llm','random'):
            supplied = getattr(args,f'{arm}_candidates')
            if supplied is not None and [str(Path(p).resolve()) for p in supplied] != manifest['candidate_paths'][arm]:
                raise ValueError('resume candidate paths differ')
        verify_seal(manifest['runtime'])
        return manifest
    from candidates import default_paths,candidate_files
    paths = inputs()
    maps = {json.loads((ROOT/p).read_text())['id']:p for p in paths}
    if len(maps) != 40:
        raise ValueError('duplicate validation instance IDs')
    candidate_paths = {arm:[str(Path(p).resolve()) for p in
                       (getattr(args,f'{arm}_candidates') or default_paths(arm))] for arm in ('llm','random')}
    pinned = {arm:{iid:[str(p) for p in candidate_files(iid,roots)] for iid in maps}
              for arm,roots in candidate_paths.items()}
    candidates = {p:sha(p) for by_map in pinned.values() for files in by_map.values() for p in files}
    sources = source_binding()
    profile = json.loads((HERE/'PROFILE.json').read_text())
    runtime = seal_runtime(sources,destination=OUTPUT_ROOT/'runtime',repo_root=ROOT)
    chosen = selection()
    manifest = dict(decision='D199',amendments=['D205','D206'],cohort_status='active',cohort_nonce=uuid.uuid4().hex,
                    created_utc=utc(),n_maps=40,maps=maps,groups=chosen['groups'],
                    selection=chosen,selection_sha256=sha(HERE/'cohort-selection.json'),
                    profile=profile,profile_report_sha256=sources['experiments/mvp/hybrid/PROFILE.md'],
                    arms=['none','llm','random'],seed=0,search_s_per_box=6,search_cap_s=120,
                    search_floor_s=20,finalize_s=300,workers=4,admission_rule=RULE,
                    watchdog_policy=POLICY,runtime=runtime,sources=sources,
                    logic_review_sha256=sources['experiments/review/v4/logic-hybrid.md'],
                    candidate_paths=candidate_paths,candidates=candidates,pinned_files=pinned,
                    inputs={p:sha(ROOT/p) for p in paths},reference_rows=reference_rows(maps))
    manifest['cohort_id'] = cohort_id(manifest)
    require_manifest(manifest)
    publish(path,manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default=str(OUTPUT_ROOT/'runs/val-d205'))
    parser.add_argument('--workers',type=int,default=4,choices=(4,))
    parser.add_argument('--llm-candidates',action='append')
    parser.add_argument('--random-candidates',action='append')
    parser.add_argument('--start-trials',action='store_true',
                        help='explicit release after leader authorization; default only prepares and holds')
    args = parser.parse_args()
    out = contained(args.out)
    out.mkdir(parents=True,exist_ok=True)
    require_active_directory(out)
    lock = (out/'RUN.LOCK').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    for folder in ('jobs','logs'):
        directory = out/folder
        directory.mkdir(exist_ok=True)
        if directory.stat().st_mode & 0o222 == 0:
            raise ValueError(f'{directory} is read-only; quarantine old run instead of reusing it')
    manifest = build_manifest(out,args)
    if not args.start_trials:
        (out/'HOLD').write_text('Skill-check Part 2 first. Await explicit leader instruction before --start-trials.\n')
        publish(out/'ARMED.json',dict(cohort_id=manifest['cohort_id'],workers=4,trials=120,
                state='held; awaiting explicit leader release after skill-check Part 2',utc=utc()))
        event(out,'run_armed',workers=4,trials=120,cohort_id=manifest['cohort_id'])
        return
    (out/'HOLD').unlink(missing_ok=True)
    env = environment(out,manifest['runtime'])
    jobs = [dict(index=index,arm=arm,input=inp,instance_id=iid,runtime=manifest['runtime'],
                 binding=expected_binding(manifest,inp),
                 candidate_paths=manifest['pinned_files'].get(arm,{}).get(iid,[]))
            for index,(iid,inp) in enumerate(manifest['maps'].items()) for arm in ('none','llm','random')]
    event(out,'run_start',pid=os.getpid(),workers=4,trials=len(jobs),cohort_id=manifest['cohort_id'])
    with ThreadPoolExecutor(4) as executor:
        active = set()
        for job in jobs:
            while len(active) >= 4:
                done,_ = wait(active,timeout=1,return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
                active.difference_update(done)
            if (out/'STOP').exists():
                break
            started = threading.Event()
            future = executor.submit(trial,job,out,env,manifest,started)
            active.add(future)
            # Submit the next FIFO job only after this one actually launches,
            # reuses a validated row, or cooperatively stops. Admission waiting
            # cannot reorder starts or consume the trial's wall-clock budget.
            while not started.wait(.5):
                if future.done():
                    future.result()
                    break
        for future in as_completed(active):
            future.result()
    if (out/'STOP').exists():
        event(out,'run_stopped')
        return
    verify_seal(manifest['runtime'])
    report = Path(manifest['runtime']['root'])/'experiments/mvp/hybrid/report.py'
    with (out/'report.log').open('a') as stream:
        execution = run_owned([sys.executable,str(report),str(out),'--output',str(OUTPUT_ROOT/'HYBRID.md'),'--require-complete'],
            cwd=ROOT,env=env,stream=stream,mode='audit',policy=POLICY,
            record=lambda kind,**data:event(out,kind,stage='report',**data))
    if execution['returncode'] or execution.get('timed_out'):
        raise RuntimeError('frozen final reporting failed; see report.log')
    verify_seal(manifest['runtime'])
    publish(out/'DONE.json',dict(end_utc=utc(),trials=len(jobs),cohort_id=manifest['cohort_id']))
    event(out,'run_end',trials=len(jobs),report=str(OUTPUT_ROOT/'HYBRID.md'))


if __name__ == '__main__':
    main()

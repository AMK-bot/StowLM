"""D199 CPU-only, resumable label streams. No planner source changes."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'src'), str(HERE)]
THREADS = ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
           'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','HIGHS_THREADS',
           'BLIS_NUM_THREADS','RAYON_NUM_THREADS')
for name in THREADS:
    os.environ[name] = '1'
os.environ.update(UV_CACHE_DIR=str(ROOT/'.uv-cache'), CUDA_VISIBLE_DEVICES='',
                  STOWAGE_IN_PORT_WORKERS='1', PYTHONDONTWRITEBYTECODE='1')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=1)+'\n')
    temporary.replace(path)


def stratified(paths):
    groups = defaultdict(deque)
    for entry in sorted(paths, key=lambda e: e['input']):
        groups[entry['ship'], entry['yard']].append(entry)
    result = []
    while any(groups.values()):
        for key in sorted(groups):
            if groups[key]:
                result.append(groups[key].popleft())
    return result


def prepare():
    from llm.exit import outcome_epoch
    if (HERE/'manifest.json').exists():
        manifest = read(HERE/'manifest.json')
        if outcome_epoch.epoch_id(outcome_epoch.process_snapshot()) != manifest['outcome_epoch']:
            raise RuntimeError('resume epoch differs; use a fresh run directory')
        if sources() != manifest['sources']:
            raise RuntimeError('resume source identity differs')
        return manifest
    val = [json.loads(line) for line in (ROOT/'experiments/mvp/sft/stowlm-v2/data/val.jsonl').read_text().splitlines()]
    assert len(val) == 80
    val_ids = {row['instance_id'] for row in val}
    val_hashes = {row['meta']['gold']['input_sha256'] for row in val}
    baseline = ROOT/'experiments/mvp/gold/twostep-teacher/best-d172'
    by_size = defaultdict(list)
    excluded = []
    corpus_count = 0
    for path in sorted((ROOT/'experiments/mvp/corpus/train').rglob('*.json')):
        if path.name.endswith('.audit.json'):
            continue
        raw = read(path)
        if 'load_list' not in raw:
            continue
        corpus_count += 1
        input_sha = sha(path)
        if raw['id'] in val_ids or input_sha in val_hashes:
            excluded.append(str(path.relative_to(ROOT)))
            continue
        parts = path.relative_to(ROOT/'experiments/mvp/corpus').with_suffix('').parts
        key = '__'.join(parts)
        old = baseline/key/'record.json'
        row = dict(key=key, input=str(path.relative_to(ROOT)), input_sha256=input_sha,
                   instance_id=raw['id'], size=raw['size_class'], ship=parts[2], yard=parts[3],
                   baseline_record=str(old.relative_to(ROOT)) if old.exists() else None,
                   baseline_record_sha256=sha(old) if old.exists() else None)
        by_size[row['size']].append(row)
    small, tiny = map(deque, (stratified(by_size['small']), stratified(by_size['tiny'])))
    entries = []
    # Shared fixed order: each 17-map block has 14 small, 3 tiny maps.
    while small or tiny:
        for queue, count in ((small, 14), (tiny, 3)):
            entries.extend(queue.popleft() for _ in range(min(count, len(queue))))
    if Counter(e['size'] for e in entries[:1700]) != {'small':1400, 'tiny':300}:
        raise RuntimeError('cannot satisfy requested rule prefix')
    epoch = outcome_epoch.process_snapshot()
    manifest = dict(schema='stowlm9-manifest-v1', outcome_epoch=outcome_epoch.epoch_id(epoch),
                    sources=sources(), entries=entries, corpus_count=corpus_count,
                    excluded_val=excluded, val_sha256=sha(ROOT/'experiments/mvp/sft/stowlm-v2/data/val.jsonl'),
                    rule_deadline='2026-10-31T19:45:00-04:00',
                    config=dict(rule_targets={'small':1400,'tiny':300}, seed=0,
                                rule_plan_s=120, rule_finisher_s=120,
                                frontier_version='0.9.0', search_s_per_box=6,
                                search_cap_s=120, frontier_finisher_s=300))
    manifest['manifest_id'] = digest(manifest)
    write(HERE/'outcome-epoch.json', epoch)
    write(HERE/'manifest.json', manifest)
    return manifest


def sources():
    paths = list((ROOT/'experiments/mvp/frontier').glob('*.py'))
    paths += [ROOT/'experiments/mvp/strategy/compact.py',
              ROOT/'experiments/mvp/strategy/blockrule/rule.py',
              ROOT/'experiments/mvp/strategy/blockrule/capacity_candidate.py',
              ROOT/'experiments/mvp/strategy/headroom/layout.py']
    paths += [HERE/name for name in ('run.py','labels.py','export.py','prompt_v9.py')]
    return {str(path.relative_to(ROOT)):sha(path) for path in sorted(paths)}


def binding(entry, manifest, stream):
    return dict(manifest_id=manifest['manifest_id'], input_sha256=entry['input_sha256'],
                baseline_record_sha256=entry['baseline_record_sha256'], stream=stream)


def reusable(row, entry, manifest, stream):
    return (row.get('binding') == binding(entry, manifest, stream)
            and row.get('outcome_epoch') == manifest['outcome_epoch']
            and row.get('status') == 'complete'
            and not row.get('needs_rule_timeout_retry',False)
            and sha(ROOT/entry['input']) == entry['input_sha256'])


def single_thread_solver_policy():
    """Apply the job's native-thread limit without editing planner sources.

    Frontier 0.9 explicitly asks CP-SAT for two workers; environment variables
    alone do not constrain that setting. The wrapper changes only this resource
    setting at solve entry. Its source is pinned in the manifest.
    """
    from ortools.sat.python import cp_model
    original = cp_model.CpSolver.solve
    if getattr(original,'_stowlm9_single_thread',False):
        return

    def solve(self,*args,**kwargs):
        self.parameters.num_workers = 1
        return original(self,*args,**kwargs)

    solve._stowlm9_single_thread = True
    cp_model.CpSolver.solve = solve
    cp_model.CpSolver.Solve = solve


def finish_rule(instance):
    from experiments.mvp.frontier.blocked import blocked_plan
    from stowage.feasibility import FeasibilityConfig
    from stowage.planner import finalize
    result = blocked_plan(instance, seed=0, config=FeasibilityConfig(extra={'global_screen':0}), time_limit_s=300)
    if result.plan is None:
        return None, dict(status=result.status, message=result.message)
    previous = os.environ.get('STOWAGE_BALLAST_V4')
    os.environ['STOWAGE_BALLAST_V4'] = '1'
    try:
        finished = finalize(instance, result, time_limit_s=900)
    finally:
        if previous is None:
            os.environ.pop('STOWAGE_BALLAST_V4', None)
        else:
            os.environ['STOWAGE_BALLAST_V4'] = previous
    return finished.plan if finished.status == 'ok' else None, dict(status=finished.status, message=finished.message)


def one(stream, index):
    # Snapshot before outcome modules are imported; each map is a fresh process.
    from llm.exit import outcome_epoch
    expected = outcome_epoch.process_snapshot()
    manifest = read(HERE/'manifest.json')
    if outcome_epoch.epoch_id(expected) != manifest['outcome_epoch'] or sources() != manifest['sources']:
        raise RuntimeError('worker code/epoch mismatch')
    from labels import canonicalize, certify, score_plan
    from llm.exit.identity import identity_guard
    from stowage.io import load_instance
    from stowage.model import MovePlan
    entry = manifest['entries'][index]
    single_thread_solver_policy()
    path = ROOT/entry['input']
    if not path.resolve().is_relative_to(ROOT/'experiments/mvp/corpus/train') or sha(path) != entry['input_sha256']:
        raise ValueError('input outside unchanged train corpus')
    instance = load_instance(path)
    identity_guard(ROOT/'experiments/mvp/corpus').require_allowed(instance, role='train', input_path=str(path), raw_sha=entry['input_sha256'])
    target = HERE/stream/'rows'/f"{entry['key']}.json"
    row = dict(entry, manifest_id=manifest['manifest_id'], binding=binding(entry, manifest, stream),
               outcome_epoch=manifest['outcome_epoch'], stream=stream, launch_id=os.environ.get('STOWLM9_LAUNCH_ID'),
               pid=os.getpid(), status='error', passed=False, candidates=[], started_at=time.time(),
               attempt=int(os.environ.get('STOWLM9_ATTEMPT','1')))
    try:
        sys.path.insert(0, str(ROOT/'experiments/mvp/frontier'))
        try:
            if stream == 'rule':
                plan, detail = finish_rule(instance)
            else:
                import planner
                if planner.VERSION != '0.9.0':
                    raise RuntimeError('frontier must remain 0.9.0')
                result, detail = planner.plan_and_check(instance, search_s=min(120.,6.*len(instance.load_list)), finalize_s=300., seed=0)
                plan = result.plan if result is not None else None
        except Exception as error:
            plan = None
            detail = dict(error=f'{type(error).__name__}: {error}',traceback=traceback.format_exc())
        row['planning'] = detail
        proposals = [(stream, plan)]
        if stream == 'rule' and target.exists():
            previous = read(target)
            if previous.get('passed') and previous.get('record'):
                proposals.append((previous['source'],MovePlan.model_validate(previous['record']['plan'])))
        if entry['baseline_record']:
            old_path = ROOT/entry['baseline_record']
            if sha(old_path) != entry['baseline_record_sha256']:
                raise RuntimeError('baseline changed')
            try:
                old_plan = MovePlan.model_validate(read(old_path)['plan'])
            except Exception as error:
                old_plan = None
                row['baseline_error'] = f'{type(error).__name__}: {error}'
            if old_plan is not None:
                try:
                    row['baseline_raw_moves'] = score_plan(instance, old_plan)
                except Exception as error:
                    row['baseline_raw_error'] = f'{type(error).__name__}: {error}'
            proposals.append(('best-d172',old_plan))
        winners = []
        for source, proposal in proposals:
            info = dict(source=source, passed=False)
            if proposal is not None:
                try:
                    canonical, compaction = canonicalize(instance, proposal)
                    record, checked = certify(instance, canonical, expected, check_timeout_s=900)
                    info.update(compaction=compaction, check=checked)
                    if record is not None:
                        cost = record['weighted_moves']
                        info.update(passed=True, moves=cost)
                        record.update(instance_path=entry['input'], input_sha256=entry['input_sha256'], source=source,
                                      outcome_epoch=manifest['outcome_epoch'])
                        winners.append((cost,source,record))
                        if source == 'best-d172':
                            row['baseline_moves'] = cost
                except Exception as error:
                    info['error'] = f'{type(error).__name__}: {error}'
            else:
                info['error'] = (row.get('baseline_error') if source=='best-d172' else detail.get('error')) or 'no candidate plan'
            row['candidates'].append(info)
        if winners:
            # Explicit stable tie break; labels never depend on process completion order.
            cost, source, record = min(winners, key=lambda item:(item[0],item[1] != 'best-d172',item[1]))
            row.update(passed=True, moves=cost, source=source, record=record)
        outcome_epoch.assert_current(expected)
        if sources() != manifest['sources'] or sha(path) != entry['input_sha256']:
            raise RuntimeError('worker input/code drift')
        row['status'] = 'complete'
        row['retry_timeout_only'] = timeout_only(row)
    except BaseException as error:
        row.update(status='error', passed=False, error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
        row.pop('record', None)
    finally:
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()
        row.update(finished_at=time.time(), wall_s=time.time()-row['started_at'])
        write(target, row)
    print(json.dumps({k:row.get(k) for k in ('key','status','passed','moves','source','wall_s','error')}),flush=True)
    return 0 if row['status'] == 'complete' else 1


def export(stream):
    from export import export_stream
    result = export_stream(HERE/'manifest.json',stream,root=HERE)
    print(json.dumps(result,default=str),flush=True)


def launch_export(stream, launch_id):
    directory = HERE/stream
    log = (directory/'export.log').open('a')
    child = subprocess.Popen([sys.executable,str(HERE/'run.py'),'export',stream],cwd=ROOT,
                              stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
    log.close()
    write(directory/'export-owner.json',dict(pid=child.pid,launch_id=launch_id,started_at=time.time()))
    return child


def timeout_only(row):
    if row.get('status') == 'error':
        return True
    plan = row.get('planning') or {}
    text = json.dumps(plan).lower()
    return any(word in text for word in ('timeout','timed out','time limit','deadline','budget_exhausted'))


def supervise(stream):
    import fcntl
    import signal
    directory = HERE/stream
    directory.mkdir(parents=True,exist_ok=True)
    lock = (directory/'controller.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest = prepare()
    for marker in ('EXPORT.DONE','EXPORT.FAILED'):
        (directory/marker).unlink(missing_ok=True)
    launch_id = uuid.uuid4().hex
    os.environ['STOWLM9_LAUNCH_ID'] = launch_id
    write(directory/'owner.json',dict(pid=os.getpid(),launch_id=launch_id,started_at=time.time(),manifest_id=manifest['manifest_id']))
    entries = manifest['entries'][:1700] if stream == 'rule' else manifest['entries']
    pending = deque(index for index,entry in enumerate(entries)
        if not (directory/'rows'/f"{entry['key']}.json").exists()
        or not reusable(read(directory/'rows'/f"{entry['key']}.json"),entry,manifest,stream))
    children = {}
    completed = len(entries)-len(pending)
    last_export = -1
    exporter = None
    last_report = 0
    first20_exported = False
    deadline = datetime.fromisoformat(manifest['rule_deadline']).timestamp()
    while pending or children or stream == 'frontier':
        controls = read(HERE/'control.json') if (HERE/'control.json').exists() else {}
        policy = controls.get(stream,{})
        paused = bool(policy.get('paused',stream=='frontier'))
        workers = int(policy.get('workers',3 if stream=='rule' else 2))
        cutoff = stream=='rule' and time.time()>=deadline
        # Only dedicated child sessions created and recorded by this supervisor.
        for pid,item in children.items():
            child,index,started,frozen = item
            if paused != frozen and child.poll() is None:
                os.killpg(pid,signal.SIGSTOP if paused else signal.SIGCONT)
                item[3] = paused
        while pending and len(children)<workers and not cutoff and not paused:
            index=pending.popleft();entry=entries[index]
            previous_path=directory/'rows'/f"{entry['key']}.json"
            attempt=1
            if previous_path.exists():
                previous=read(previous_path);attempt=previous.get('attempt',1)+1
                write(directory/'attempts'/entry['key']/f"{time.time_ns()}.json",previous)
            env=dict(os.environ,STOWLM9_ATTEMPT=str(attempt))
            log_path=directory/'logs'/f"{entry['key']}.log";log_path.parent.mkdir(exist_ok=True)
            with log_path.open('a') as log:
                child=subprocess.Popen([sys.executable,str(HERE/'run.py'),'one',stream,str(index)],cwd=ROOT,
                    env=env,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
            children[child.pid]=[child,index,time.time(),False]
            write(directory/'owned'/f'{child.pid}.json',dict(pid=child.pid,pgid=child.pid,index=index,key=entry['key'],launch_id=launch_id,started_at=time.time()))
        for pid,(child,index,started,frozen) in list(children.items()):
            if child.poll() is not None:
                write(directory/'receipts'/launch_id/f'{index}.json',dict(pid=pid,index=index,exitcode=child.returncode,finished_at=time.time()))
                completed+=1;del children[pid]
                row_path=directory/'rows'/f"{entries[index]['key']}.json"
                if stream=='rule' and row_path.exists():
                    result=read(row_path)
                    if timeout_only(result) and not result.get('passed') and result.get('attempt',1)<3:
                        pending.append(index)
        if exporter is not None and exporter.poll() is not None:
            if exporter.returncode:
                first20_exported=False
                write(directory/'EXPORT.FAILED',dict(pid=exporter.pid,exitcode=exporter.returncode,at=time.time()))
            exporter=None
        winners=sum(read(p).get('passed') is True for p in (directory/'rows').glob('*.json')) if stream=='rule' else 0
        milestone=stream=='rule' and winners>=20 and not first20_exported
        if not paused and exporter is None and ((completed and completed//200>last_export) or milestone):
            exporter=launch_export(stream,launch_id);last_export=completed//200
            if milestone:first20_exported=True
        write(directory/'status.json',dict(pid=os.getpid(),launch_id=launch_id,stream=stream,workers=workers,
            paused=paused,completed=completed,total=len(entries),pending=len(pending),active=list(children),
            cutoff=cutoff,updated_at=time.time(),export_pid=exporter.pid if exporter else None,
            rule_plan_s=300,rule_finisher_s=900))
        if time.time()-last_report>=30:
            from report import render
            try:render()
            except Exception as error:print('report error',repr(error),flush=True)
            if stream=='rule':
                from rule_readout import render as readout
                try:readout(HERE)
                except Exception as error:print('readout error',repr(error),flush=True)
            last_report=time.time()
        if cutoff:break
        if not pending and not children and not paused:break
        time.sleep(2)
    if exporter is not None:exporter.wait()
    final=launch_export(stream,launch_id);code=final.wait()
    write(directory/('EXPORT.DONE' if code==0 else 'EXPORT.FAILED'),dict(export_exitcode=code,at=time.time(),launch_id=launch_id,
        completed=completed,active_at_cutoff=list(children),partial=bool(pending or children)))
    for child,index,started,frozen in children.values():child.wait()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare','supervise','one','export'))
    parser.add_argument('stream', nargs='?', choices=('rule','frontier'))
    parser.add_argument('index',nargs='?',type=int)
    args = parser.parse_args()
    if args.command == 'prepare':
        manifest = prepare()
        print(json.dumps(dict(manifest_id=manifest['manifest_id'],maps=len(manifest['entries']),corpus_count=manifest['corpus_count'],excluded_val=len(manifest['excluded_val']))))
    elif args.command == 'one':
        raise SystemExit(one(args.stream,args.index))
    elif args.command == 'supervise':
        supervise(args.stream)
    else:
        export(args.stream)


if __name__ == '__main__':
    main()

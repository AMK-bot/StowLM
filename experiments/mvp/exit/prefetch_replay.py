"""D135 CPU-only replay warming; never edit source or read a live cache file.

The unmodified sequential consumer has a non-atomic writer. Workers therefore
check in private directories and publish complete entries with atomic replace.
The durable epoch shelf is also copied into forthcoming generation directories.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import multiprocessing
import os
from pathlib import Path
import shutil
import tempfile
import time

ROOT=Path(__file__).resolve().parents[3]
OWNER='mvp-exit/prefetch-replay'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if Path(name).exists():Path(name).unlink()
def write(path,value):atomic(path,(json.dumps(value,indent=2,allow_nan=False)+'\n').encode())


@contextmanager
def owner_lock(path):
    with Path(path).open('a+') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def services():
    from llm.exit.replay import checker_epoch,ensure_current_replay
    from llm.exit.identity import identity_guard
    from llm.exit.runner import source_pins
    from llm.exit.checker_watchdog import _unavailable_reason
    from stowage.checker import report_matches_inputs
    from stowage.model import MovePlan,CheckReport
    path=ROOT/'experiments/mvp/sft/dataset/build.py'
    spec=importlib.util.spec_from_file_location('_prefetch_builder',path)
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    return dict(epoch=checker_epoch,ensure=ensure_current_replay,guard=identity_guard,
        pins=source_pins,unavailable=_unavailable_reason,bound=report_matches_inputs,
        Plan=MovePlan,Report=CheckReport,load=builder.load_triple)


def load_job(path,s):
    path=Path(path).resolve()
    instance,plan,report,digest,input_path=s['load'](path)
    s['guard'](ROOT/'experiments/mvp/corpus').require_allowed(instance,role='train',
        raw_sha=digest,input_path=input_path)
    key=hashlib.sha256((instance.model_dump_json()+plan.model_dump_json()).encode()).hexdigest()
    return instance,plan,report,key


def dependency_id():
    from llm.exit.replay import outcome_dependency_id
    return outcome_dependency_id()


def validated_bytes(path,instance,epoch,s):
    data=Path(path).read_bytes();row=json.loads(data)
    plan=s['Plan'].model_validate(row['plan']);report=s['Report'].model_validate(row['report'])
    if (row.get('exit_checker_epoch')!=epoch or row.get('exit_outcome_dependency_id')!=dependency_id()
            or s['unavailable'](report)
            or report.passed is not True or not s['bound'](report,instance,plan)
            or row.get('instance')!=instance.model_dump(mode='json')):
        raise RuntimeError('prefetch entry not current, complete, passed and input-bound')
    return data


def bind_shelf(shelf,epoch,pins):
    """A checker epoch alone does not bind securing/look-ahead/mask code."""
    path=Path(shelf)/'source-manifest.json'
    expected=dict(schema='exit-prefetch-source-v1',checker_epoch=epoch,sources=pins)
    if path.exists():
        if read(path)!=expected:
            raise RuntimeError('prefetch shelf source identity changed; use a fresh --out')
    else:
        if list(Path(shelf).glob('*.record.json')):
            raise RuntimeError('unbound existing prefetch shelf; use a fresh --out')
        write(path,expected)
    return expected


def worker(job):
    started=time.monotonic();s=services();path=Path(job['path'])
    instance,plan,report,key=load_job(path,s)
    if key!=job['key'] or sha(path)!=job['record_sha'] or s['pins']()!=job['pins']:
        raise RuntimeError('prefetch source or selected record changed')
    epoch=job['epoch'];shelf=Path(job['shelf'])/(key+'.record.json')
    with tempfile.TemporaryDirectory(prefix='prefetch-',dir=Path(job['work'])) as private:
        private=Path(private);target=private/(key+'.record.json')
        if shelf.exists():atomic(target,validated_bytes(shelf,instance,epoch,s))
        checked_plan,checked_report,rechecked=s['ensure'](path,instance,plan,report,private)
        # Already-current input records need no check and therefore no cache write.
        if not target.exists():
            write(target,dict(instance=instance.model_dump(mode='json'),
                plan=checked_plan.model_dump(mode='json'),report=(checked_report.model_dump(mode='json')
                    if hasattr(checked_report,'model_dump') else checked_report),
                exit_checker_epoch=epoch,exit_outcome_dependency_id=dependency_id(),replay_source=str(path)))
        data=validated_bytes(target,instance,epoch,s)
        if (s['epoch']()!=epoch or s['pins']()!=job['pins'] or sha(path)!=job['record_sha']
                or load_job(path,s)[0].model_dump_json()!=instance.model_dump_json()
                or load_job(path,s)[1].model_dump_json()!=plan.model_dump_json()):
            raise RuntimeError('prefetch input/checker changed before publication')
        atomic(shelf,data)
    return dict(key=key,path=str(path),rechecked=rechecked,wall_s=time.monotonic()-started)


def safe_phase(loop):
    """Yield during decode/fitness/audits; warm during CPU datasets or GPU updates."""
    reports=sorted(Path(loop).glob('gen-*/phase-status.json'))
    if not reports:return False
    status=read(reports[-1])
    return any(p.get('status')=='running' and
        (p['name']=='dataset' or p['name'].endswith(('_train','_dpo')))
        for p in status.get('phases',[]))


def publish_shelf(shelf,loop,record_keys=None):
    """No reads of live cache files, only atomic publication of trusted shelf bytes."""
    shelf=Path(shelf)
    record_keys=record_keys or {}
    count=0
    # Only materialize rows a generation actually sampled. Copying the whole
    # multi-GB pool into every possible future ratio would waste disk and time.
    for selection in Path(loop).glob('gen-*/replay-selection.json'):
        targets=list(selection.parent.glob('data-replay-*/replay-current-epoch'))
        targets.append(selection.parent/'data-replay-0.67/replay-current-epoch')
        for record in read(selection)['selected_records']:
            key=record_keys.get(str(Path(record).resolve()))
            if key is None:continue
            source=shelf/(key+'.record.json')
            if not source.exists():continue
            missing=[directory/source.name for directory in set(targets)
                     if not (directory/source.name).exists()]
            if missing:
                data=source.read_bytes()
                for target in missing:atomic(target,data);count+=1
    return count


def seed_completed_selection(selection,shelf,s,epoch):
    """Read only a finished dataset's immutable cache; never a live writer."""
    directory=Path(selection).parent;status_path=directory/'phase-status.json'
    if not status_path.exists():return 0
    phases=read(status_path).get('phases',[])
    if (not any(p['name']=='dataset' and p['status']=='completed' for p in phases)
            or any(p['name']=='dataset' and p['status']=='running' for p in phases)):
        return 0
    count=0
    for path in read(selection)['selected_records']:
        instance,_,_,key=load_job(path,s)
        if (Path(shelf)/(key+'.record.json')).exists():continue
        for cache in directory.glob('data-replay-*/replay-current-epoch'):
            source=cache/(key+'.record.json')
            if source.exists():
                data=validated_bytes(source,instance,epoch,s)
                atomic(Path(shelf)/source.name,data);count+=1;break
    return count


def capacity(requested):
    from llm.exit import resources
    cores,ram,_=resources.ledger_totals((ROOT/'experiments/fleet/ledger.md').read_text())
    live=resources.probe()
    return max(0,min(requested,math.floor(16-cores),math.floor((112-ram)/2),
                     math.floor(16-live['load_1m'])))


@contextmanager
def cpu_lease(out,workers,estimate):
    from llm.exit import resources
    old=resources.OWNER;resources.OWNER=OWNER
    try:
        # CPU-only admission; unlike public reservation(), this does NOT acquire
        # the resident model's GPU execution lock. All usual capacity checks run.
        with resources._reservation(out,cores=workers,ram_gb=2*workers,gpu=False,
                estimate_s=estimate):yield
    finally:resources.OWNER=old


def run(args):
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                 'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','STOWAGE_IN_PORT_WORKERS'):
        os.environ[name]='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    work=Path(args.out).resolve();work.mkdir(parents=True,exist_ok=True)
    # All --out directories share one CPU owner and ledger row. A local-only
    # lock would allow a second output directory to overwrite that reservation.
    with owner_lock(ROOT/'experiments/mvp/exit/.replay-prefetch.owner.lock'):
        s=services();epoch=s['epoch']();pins=s['pins']()
        shelf=work/'epochs'/epoch;shelf.mkdir(parents=True,exist_ok=True)
        bind_shelf(shelf,epoch,pins)
        state_path=work/'status.json'
        state=(read(state_path) if state_path.exists() else {})
        if state.get('epoch')!=epoch:state=dict(epoch=epoch,completed={},failed={})
        state.update(pid=os.getpid(),requested_workers=args.workers,state='preparing',pins=pins)
        write(state_path,state)
        (ROOT/'experiments/mvp/exit/replay-prefetch.pid').write_text(str(os.getpid())+'\n')
        # Durable shelf entries came from private fully checked writes. Recover
        # their routing index without repeating costly instance admission here;
        # every consumer still validates exact instance/report bindings.
        record_keys={}
        for source in shelf.glob('*.record.json'):
            cached=read(source)
            if cached.get('exit_checker_epoch')!=epoch:raise RuntimeError('shelf epoch differs')
            record=str(Path(cached['replay_source']).resolve());key=source.name.removesuffix('.record.json')
            record_keys[record]=key
            state['completed'].setdefault(key,dict(key=key,path=record,seeded_completed_cache=True))
        state['seeded_completed_selection']=len(record_keys)
        selection=read(args.selection)['selected_records'];records=list(selection)
        if args.all_gold:
            from llm.exit.runner import saved_gold_records
            records+=list(map(str,saved_gold_records(Path(args.gold),split='train')))
        records=list(dict.fromkeys(str(Path(p).resolve()) for p in records))
        records=[p for p in records if p not in record_keys and p not in state['failed']]
        state.update(total_records=len(records)+len(record_keys),pending=len(records));write(state_path,state)
        seen=set(state['completed'])|set(state['failed'])
        shard=0
        while records or args.all_gold:
            if s['epoch']()!=epoch or s['pins']()!=pins:
                state.update(state='stopped_source_drift');write(state_path,state);return
            publish_shelf(shelf,args.loop_out,record_keys)
            if (work/'STOP').exists():state.update(state='stopped');write(state_path,state);return
            if not records:
                state.update(state='warm_complete_watching_selections',pending=0);write(state_path,state)
                time.sleep(5);continue
            # --standalone: no loop is running, so there is no decode/audit phase to yield to.
            n=capacity(args.workers) if args.standalone or safe_phase(args.loop_out) else 0
            if n<1:
                state.update(state='waiting_for_loop_capacity',pending=len(records));write(state_path,state)
                time.sleep(5);continue
            batch=[]
            while records and len(batch)<n:
                record=records.pop(0)
                try:
                    _,_,_,key=load_job(record,s)
                    record_keys[record]=key
                    if key in seen:continue
                    seen.add(key)
                    batch.append(dict(path=record,key=key,record_sha=sha(record),
                        epoch=epoch,pins=pins,shelf=str(shelf),work=str(work)))
                except Exception as error:state['failed'][record]=str(error)
            if not batch:continue
            directory=work/f'shard-{shard:05d}';directory.mkdir(exist_ok=True)
            state.update(state='running',workers=n,pending=len(records)+len(batch),shard=shard,
                utc=datetime.now(timezone.utc).isoformat());write(state_path,state)
            try:
                # APPROXIMATION: reserve 60s/check and 2GB/worker, based on the
                # measured gen0 72-row stale replay phase (1063s incl mask work).
                with cpu_lease(directory,n,60):
                    with ProcessPoolExecutor(n,mp_context=multiprocessing.get_context('spawn')) as pool:
                        futures={pool.submit(worker,job):job for job in batch}
                        for future in as_completed(futures):
                            job=futures[future]
                            try:state['completed'][job['key']]=future.result()
                            except Exception as error:state['failed'][job['key']]=dict(path=job['path'],error=str(error))
                            write(state_path,state)
            except RuntimeError as error:
                records=[job['path'] for job in batch]+records
                for job in batch:seen.discard(job['key'])
                state.update(state='waiting_for_capacity',error=str(error));write(state_path,state);time.sleep(5)
            shard+=1
        if s['epoch']()!=epoch or s['pins']()!=pins:raise RuntimeError('source drift before final publication')
        published=publish_shelf(shelf,args.loop_out,record_keys)
        state.update(state='completed',published_last_pass=published,pending=0);write(state_path,state)
        print(json.dumps(dict(state=state['state'],completed=len(state['completed']),failed=len(state['failed']))),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection',type=Path,required=True)
    parser.add_argument('--loop-out',type=Path,required=True)
    parser.add_argument('--out',type=Path,default=ROOT/'experiments/mvp/exit/replay-prefetch')
    parser.add_argument('--gold',type=Path,default=ROOT/'experiments/mvp/gold')
    parser.add_argument('--workers',type=int,default=6,choices=range(1,7))
    parser.add_argument('--all-gold',action='store_true')
    parser.add_argument('--standalone',action='store_true',
        help='Work with no live loop phase (D139); ledger lease, capacity, epoch shelf and atomic publishing are unchanged')
    run(parser.parse_args())


if __name__=='__main__':main()

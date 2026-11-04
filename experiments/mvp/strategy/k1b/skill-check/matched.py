"""D205 fresh k=1 judging. Three processes rendezvous per map; fail closed on D206 admission.

Run with UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python
experiments/mvp/strategy/k1b/skill-check/matched.py [--wait-admission].
No previous arm verdict is read. All writes stay under skill-check/.
"""
from __future__ import annotations

import argparse
import fcntl
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from admission import probe as state_probe, validate_snapshot

K1B = HERE.parent
ROOT = K1B.parents[3]
OUT = HERE / 'matched'
ARMS = {'8.2': K1B, 'null': K1B / 'null', 'K1r': K1B / 'k1r'}
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
           'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS',
           'RAYON_NUM_THREADS', 'POLARS_MAX_THREADS')
for key in THREADS:
    os.environ[key] = '1'
os.environ.update(COMPACT_FINISHER_S='600', PYTHONDONTWRITEBYTECODE='1',
                  UV_CACHE_DIR=str(ROOT / '.uv-cache'), TMPDIR=str(HERE / 'tmp'),
                  STOWAGE_VESSEL_CACHE_DIR=str(HERE / 'vessel-cache'))
os.environ.pop('STOWAGE_BALLAST_V4', None)
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
os.environ['CUDA_VISIBLE_DEVICES'] = ''
sys.dont_write_bytecode = True
STOP = False
CONTROLLER_LOCK = None
LOCK_HELD = False
STATUS = OUT / 'status.json'


def request_stop(signum, frame):
    # Drain a released cohort rather than leaving V4 descendants behind.
    global STOP
    STOP = True


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(row, indent=1, sort_keys=True) + '\n')
    temporary.replace(path)


def probe():
    """Only a fresh sanctioned fleet measurement grants admission."""
    return state_probe()


def admission(wait):
    while True:
        if STOP:
            return None
        row = probe()
        with (OUT / 'admission.jsonl').open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
        if row['admitted']:
            return row
        write(STATUS, dict(status='blocked_admission', updated_at=now(), probe=row,
                                       reason='D206 requires age < 120 s, load1 <= 45 and popcount(mask) >= 8'))
        if not wait:
            return None
        time.sleep(30)


def manifest():
    sealed = json.loads((K1B / 'manifest.json').read_text())
    maps = []
    for rel, digest in sorted(sealed['inputs'].items()):
        if not rel.startswith('experiments/mvp/corpus/val/') or 'test' in Path(rel).parts:
            raise ValueError(f'non-validation input: {rel}')
        if sha(ROOT / rel) != digest:
            raise ValueError(f'input changed: {rel}')
        iid = json.loads((ROOT / rel).read_text())['id']
        maps.append(dict(instance_id=iid, input=rel, input_sha256=digest,
                         samples={arm: sha(folder / 'candidates' / iid / 'sample-00.json')
                                  for arm, folder in ARMS.items()}))
    if len(maps) != 55 or len({job['instance_id'] for job in maps}) != 55:
        raise ValueError('expected 55 distinct validation maps')
    scripts = [K1B / 'judge.py', K1B / 'kcurve.py', K1B.parent / 'compact.py']
    return dict(decision='D205', maps=maps, finisher_s=600, workers=3,
                order='one map at a time; three fresh arm processes released together',
                code={str(p.relative_to(ROOT)): sha(p) for p in scripts})


def assert_binding(man, job, arm):
    for rel, digest in man['code'].items():
        if sha(ROOT / rel) != digest:
            raise RuntimeError(f'judge code changed: {rel}')
    if sha(ROOT / job['input']) != job['input_sha256']:
        raise RuntimeError('input changed')
    if sha(ARMS[arm] / 'candidates' / job['instance_id'] / 'sample-00.json') != job['samples'][arm]:
        raise RuntimeError('stored sample changed')


def worker(arm, index, batch):
    sys.path[:0] = [str(K1B), str(K1B.parent), str(ROOT / 'src'), str(ROOT)]
    import compact
    import judge
    from stowage.io import load_instance
    from stowage.model import MovePlan
    man = json.loads((OUT / 'manifest.json').read_text())
    job = man['maps'][index]
    iid = job['instance_id']
    row = dict(arm=arm, instance_id=iid, input=job['input'], sample=0, complete=False,
               passed=False, moves=None, timed_out=False, eligible=False, error=None,
               nice=os.getpriority(os.PRIO_PROCESS, 0), binding=dict(input_sha256=job['input_sha256'], sample_sha256=job['samples'][arm],
                            code=man['code'], finisher_s=600), pid=os.getpid())
    try:
        if row['nice'] != 0:
            raise RuntimeError('worker must have normal priority')
        assert_binding(man, job, arm)
        identity = compact.run_identity()
        row['binding'].update(identity)
        instance = load_instance(ROOT / job['input'])
        sample = json.loads((ARMS[arm] / 'candidates' / iid / 'sample-00.json').read_text())
        entries = []
        if sample['plan'] is not None:
            plan = MovePlan.model_validate(sample['plan'])
            compacted, info = compact.compact_plan(instance, plan)
            better = compacted is not None and info['compact_raw']['total'] < info['before']['total']
            chosen = compacted if better else plan
            signature = judge.sha_text(json.dumps([m.model_dump(mode='json') for m in chosen.moves], sort_keys=True))
            entries.append(dict(sample=0, cost=info['compact_raw']['total'] if better else info['before']['total'],
                                plan=chosen, signature=signature))
            row.update(eligible=True, compacted=better, cost=entries[0]['cost'], signature=signature)
        write(batch / f'{arm}.ready.json', dict(pid=os.getpid(), identity=identity, eligible=row['eligible']))
        while not (batch / 'start.json').exists():
            if (batch / 'abort.json').exists():
                raise RuntimeError('map rendezvous aborted')
            time.sleep(.1)
        release = json.loads((batch / 'start.json').read_text())
        row['release_admission'] = release['admissions'][arm]
        row['admission'] = validate_snapshot(row['release_admission'])
        row['released_at'] = release['released_at']
        if not row['admission']['admitted']:
            raise RuntimeError('trial admission invalid or expired at actual start: ' + '; '.join(row['admission']['errors']))
        row['started_at'] = now()
        for entry in judge.rank(entries):
            check, finished, report = compact.finish_and_check(instance, entry['plan'], budget_s=600)
            if check.get('passed'):
                compact.admit_mask(check, compact.mask_ok(instance, finished.plan))
            row.update(check)
            if check.get('passed'):
                artifacts = OUT / 'plans' / iid
                write(artifacts / f'{arm}.json', json.loads(finished.model_dump_json()))
                write(artifacts / f'{arm}.report.json', json.loads(report.model_dump_json()))
                row['winner'] = dict(plan_sha256=sha(artifacts / f'{arm}.json'),
                                     report_sha256=sha(artifacts / f'{arm}.report.json'))
        assert_binding(man, job, arm)
        from llm.exit import outcome_epoch
        if outcome_epoch.epoch_id(outcome_epoch.snapshot()) != identity['outcome_epoch']:
            raise RuntimeError('outcome epoch changed during trial')
        row['complete'] = True
    except Exception as error:
        row.update(error=f'{type(error).__name__}: {error}', complete=False, passed=False, moves=None)
    row['ended_at'] = now()
    write(OUT / 'rows' / iid / f'{arm}.json', row)
    return 0 if row['complete'] else 1


def completed_map(man, job):
    """Resume only whole, source-bound, valid cohorts; never overwrite outcomes."""
    from matched_report import valid_admission
    paths = [OUT / 'rows' / job['instance_id'] / f'{arm}.json' for arm in ARMS]
    if not any(p.exists() for p in paths):
        return False
    if not all(p.exists() for p in paths):
        raise RuntimeError('partial cohort must be reviewed before resume')
    identity = json.loads((OUT / 'cohort-identity.json').read_text())
    for arm, path in zip(ARMS, paths):
        row = json.loads(path.read_text())
        expected = dict(input_sha256=job['input_sha256'], sample_sha256=job['samples'][arm],
                        code=man['code'], finisher_s=600, **identity)
        if (row.get('complete') is not True or row.get('error') is not None
                or row.get('binding') != expected or row.get('arm') != arm
                or row.get('instance_id') != job['instance_id'] or row.get('sample') != 0
                or not valid_admission(row)):
            raise RuntimeError(f'invalid historical cohort row: {path}')
        assert_binding(man, job, arm)
        if row.get('passed'):
            for suffix, key in (('.json', 'plan_sha256'), ('.report.json', 'report_sha256')):
                if sha(OUT / 'plans' / job['instance_id'] / f'{arm}{suffix}') != row['winner'][key]:
                    raise RuntimeError('historical winner artifact changed')
    return True


def planned_map_limit(man):
    path = OUT / 'stop-plan.json'
    if not path.exists():
        return len(man['maps'])
    plan = json.loads(path.read_text())
    if (plan.get('decision') != 'D213' or plan.get('map_limit') != 20
            or plan.get('instance_ids') != [j['instance_id'] for j in man['maps'][:20]]
            or plan.get('manifest_sha256') != sha(OUT / 'manifest.json')):
        raise RuntimeError('invalid fixed-prefix stop plan')
    return 20


def main():
    global CONTROLLER_LOCK, LOCK_HELD, STATUS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-admission', action='store_true')
    parser.add_argument('--probe-only', action='store_true')
    parser.add_argument('--resume-d206', action='store_true')
    parser.add_argument('--worker', choices=ARMS)
    parser.add_argument('--index', type=int)
    parser.add_argument('--batch', type=Path)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (HERE / 'tmp').mkdir(exist_ok=True)
    if args.worker:
        return worker(args.worker, args.index, args.batch)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    new = manifest()
    target = OUT / 'manifest.json'
    if target.exists() and json.loads(target.read_text()) != new:
        raise RuntimeError('matched manifest differs; keep prior rows and use a separately reviewed cohort')
    write(target, new)
    if args.probe_only:
        return 0 if admission(False) else 2
    if os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise RuntimeError('normal priority (nice 0) required')
    if args.resume_d206:
        migration = json.loads((OUT / 'migration-D206.json').read_text())
        for job in new['maps']:
            if completed_map(new, job):
                continue
            old = OUT / 'batches' / job['instance_id']
            if not (old / 'fence.json').exists() or (old / 'start.json').exists():
                raise RuntimeError('obsolete queue not safely fenced')
        STATUS = OUT / 'status-D206.json'
    CONTROLLER_LOCK = (OUT / ('controller-D206.lock' if args.resume_d206 else 'controller.lock')).open('a+')
    try:
        fcntl.flock(CONTROLLER_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('another matched controller already holds the queue lock')
    LOCK_HELD = True
    if args.resume_d206:
        write(OUT / 'active-controller.json', dict(pid=os.getpid(), status_path=str(STATUS), policy='D206', at=now()))
    write(OUT / 'controller.json', dict(pid=os.getpid(), started_at=now(), admission_source=str(state_probe.__module__),
                                       sample0_priority=True))
    write(STATUS, dict(status='queued', updated_at=now(), pid=os.getpid(),
                                   reason='Sample-0 judge started by user order; admission uses fresh fleet core-state'))
    limit = planned_map_limit(new)
    for index, job in enumerate(new['maps'][:limit]):
        iid = job['instance_id']
        paths = [OUT / 'rows' / iid / f'{arm}.json' for arm in ARMS]
        if args.resume_d206 and completed_map(new, job):
            continue
        if any(p.exists() for p in paths):
            raise RuntimeError(f'prior fresh rows exist for {iid}; preserve them; do not silently reuse or mix cohorts')
        if not admission(args.wait_admission):
            return 2
        batch = OUT / ('batches-D206' if args.resume_d206 else 'batches') / iid
        if batch.exists():
            raise RuntimeError(f'prior batch exists: {batch}; review before restarting')
        batch.mkdir(parents=True)
        children = []
        for arm in ARMS:
            log = (batch / f'{arm}.log').open('a')
            process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--worker', arm,
                                        '--index', str(index), '--batch', str(batch)], cwd=ROOT,
                                       env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT)
            log.close()
            children.append(process)
            with (OUT / 'owned-pids.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(pid=process.pid, map=iid, arm=arm, started_at=now())) + '\n')
        while not all((batch / f'{arm}.ready.json').exists() for arm in ARMS):
            if STOP or any(p.poll() is not None for p in children):
                write(batch / 'abort.json', dict(reason='worker failed during preparation'))
                for process in children:
                    process.wait()
                raise RuntimeError('worker preparation failed; see rows and batch logs')
            time.sleep(.2)
        identities = [json.loads((batch / f'{arm}.ready.json').read_text())['identity'] for arm in ARMS]
        identity_path = OUT / 'cohort-identity.json'
        frozen = json.loads(identity_path.read_text()) if identity_path.exists() else identities[0]
        if any(identity != frozen for identity in identities):
            write(batch / 'abort.json', dict(reason='unequal outcome identity'))
            for process in children:
                process.wait()
            raise RuntimeError('unequal outcome identities')
        write(identity_path, frozen)
        admissions = {}
        while True:
            if STOP:
                write(batch / 'abort.json', dict(reason='controller stop requested before release'))
                for process in children:
                    process.wait()
                write(STATUS, dict(status='stopped', updated_at=now(), map=iid))
                return 130
            admissions = {arm: probe() for arm in ARMS}
            with (OUT / 'admission.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(map=iid, admissions=admissions)) + '\n')
            if all(row['admitted'] for row in admissions.values()):
                break
            write(STATUS, dict(status='blocked_admission', updated_at=now(), map=iid, probes=admissions))
            if not args.wait_admission:
                write(batch / 'abort.json', dict(reason='D206 admission failed at rendezvous'))
                for process in children:
                    process.wait()
                return 2
            time.sleep(30)
        write(batch / 'start.json', dict(released_at=now(), admissions=admissions))
        write(STATUS, dict(status='running', map=iid, index=index, updated_at=now()))
        codes = [process.wait() for process in children]
        if any(codes):
            raise RuntimeError(f'matched cohort has trial errors: {codes}; do not interpret as plan failures')
        print(json.dumps(dict(map=iid, completed_maps=index + 1)), flush=True)
        if STOP:
            write(STATUS, dict(status='stopped_after_cohort', updated_at=now(), map=iid))
            return 130
    write(STATUS, dict(status='complete', updated_at=now(), maps=limit, total_maps=55, trials=3*limit, decision='D213' if limit == 20 else 'D205'))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        if LOCK_HELD and not ('--worker' in sys.argv):
            write(STATUS, dict(status='failed', updated_at=now(), error=f'{type(error).__name__}: {error}'))
        raise

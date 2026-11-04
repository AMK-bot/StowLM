"""D217 operational wrapper: two fixed disjoint curve queues after sampling.

The immutable primary's curve function, ranking, evaluator and 600-second
admission-controlled finisher are called unchanged. This wrapper only verifies
readiness, assigns alternate sorted maps to two lanes, and serializes reporting.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = HERE/'small'
STATE = OUT/'curves-sharded'
FENCE = OUT/'sample-evaluations'
EXPECTED_WEIGHTS = 'da15edecf1061c2e2014317ccaf53190f6c2997b5f225f8ca39bd5174a6559d6'
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
             'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS',
             'RAYON_NUM_THREADS', 'POLARS_MAX_THREADS'):
    os.environ[name] = '1'
os.environ.update(CUDA_VISIBLE_DEVICES='', TOKENIZERS_PARALLELISM='false',
                  UV_CACHE_DIR=str(ROOT/'.uv-cache'))
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    tmp.replace(path)


def utc():
    return datetime.now(timezone.utc).isoformat()


def lock_is_free(path):
    if not path.exists():
        return True
    with path.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(stream, fcntl.LOCK_UN)
    return True


def fence():
    STATE.mkdir(parents=True, exist_ok=True)
    payload = dict(control_only=True, purpose='Stop immutable primary before any curve finisher; hand off to two verified disjoint curve queues',
                   created_at=utc(), pid=os.getpid(), primary_pid=json.loads((OUT/'process.json').read_text())['pid'])
    with FENCE.open('x') as stream:
        stream.write(json.dumps(payload, indent=2)+'\n')
    write(STATE/'fence-receipt.json', dict(path=str(FENCE.relative_to(ROOT)), sha256=sha(FENCE), **payload))
    print('Installed regular-file fence before curve evaluations', flush=True)


def verify(*, require_idle=True):
    errors = []
    manifest_path = OUT/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['weights_sha256'] != EXPECTED_WEIGHTS or sha(HERE/'frozen-source/fitted.json') != EXPECTED_WEIGHTS:
        errors.append('frozen weights changed')
    for rel, digest in manifest['code'].items():
        if sha(ROOT/rel) != digest:
            errors.append('source changed: '+rel)
    inputs = manifest['inputs']
    if len(inputs)!=55:
        errors.append('expected 55 inputs')
    samples, maps = {}, {}
    for rel, digest in inputs.items():
        if not rel.startswith('experiments/mvp/corpus/val/') or 'test' in rel:
            raise ValueError('nonvalidation input')
        if sha(ROOT/rel)!=digest:
            errors.append('input changed: '+rel)
        iid = json.loads((ROOT/rel).read_text())['id']
        maps[rel] = iid
        expected = [f'sample-{i:02d}.json' for i in range(16)]
        paths = sorted((OUT/'samples'/iid).glob('*.json'))
        if [p.name for p in paths]!=expected:
            errors.append(f'{iid}: sample inventory differs from 16 indices')
            continue
        for i,p in enumerate(paths):
            row = json.loads(p.read_text())
            meta = row['metadata']
            if row['input']!=rel or row['instance_id']!=iid or row['sample']!=i or meta['seed']!=i or meta['greedy'] or meta['temperature']!=1 or meta['weights_sha256']!=EXPECTED_WEIGHTS:
                errors.append(f'{iid}/{p.name}: sample recipe differs')
            complete = row['plan'] is not None
            if complete != (meta['status']=='complete'):
                errors.append(f'{iid}/{p.name}: plan/status inconsistency')
            if complete:
                if 'prepared' not in row or 'chosen_plan' not in row or 'signature' not in row:
                    errors.append(f'{iid}/{p.name}: complete sample not prepared')
                else:
                    signature = hashlib.sha256(json.dumps(row['chosen_plan']['moves'],sort_keys=True).encode()).hexdigest()
                    if signature != row['signature']:
                        errors.append(f'{iid}/{p.name}: chosen move signature differs')
            samples[str(p.relative_to(ROOT))] = sha(p)
    if len(samples)!=880:
        errors.append(f'{len(samples)}/880 samples verified')
    actual_samples = {str(p.relative_to(ROOT)) for p in (OUT/'samples').glob('*/*.json')}
    if actual_samples != set(samples):
        errors.append('sample files exist outside the expected frozen inventory')
    if len(list((OUT/'greedy').glob('*.json')))!=55:
        errors.append('greedy queue not yet complete')
    if require_idle:
        for name,path in [('primary',OUT/'queue.lock'), ('reverse sampler',HERE/'reverse-sampler/queue.lock')]:
            if not lock_is_free(path):
                errors.append(name+' still owns its queue lock')
        helper = HERE/'reverse-sampler'
        if (helper/'process.json').exists() and not (helper/'worker-exit.json').exists():
            errors.append('reverse sampler exit receipt missing')
    ordered = sorted(inputs)
    shards = [{rel:inputs[rel] for rel in ordered[i::2]} for i in range(2)]
    assert len(shards[0])==28 and len(shards[1])==27
    assert not set(shards[0]) & set(shards[1]) and set(shards[0]) | set(shards[1]) == set(inputs)
    row = dict(checked_at=utc(), ready=not errors, errors=errors, primary_manifest_sha256=sha(manifest_path),
               wrapper_sha256=sha(Path(__file__)), inputs=inputs, maps=maps, samples=samples,
               weights_sha256=EXPECTED_WEIGHTS, shards=shards, shard_sizes=[28,27],
               assignment='alternating indices in sorted frozen input paths; fixed before curve outcomes',
               workers=2, native_threads=1, gpu=False)
    evidence = [OUT/'process.json', OUT/'curve-compaction-migration.json',
                *sorted((HERE/'reverse-sampler').glob('*.json')),
                HERE/'reverse_sampler.py']
    row['generator_receipts'] = {str(p.relative_to(ROOT)):sha(p) for p in evidence if p.is_file()}
    write(STATE/'readiness.json', row)
    if errors:
        raise RuntimeError('curve queues not ready: '+'; '.join(errors[:12]))
    return row


def locked_reports(module):
    original = module.publish_report
    report_path = HERE/'SMALL.md'

    def publish():
        with (STATE/'report.lock').open('a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            original_write = Path.write_text

            def atomic_report(path, content, *args, **kwargs):
                if path == report_path:
                    helper_used = (HERE/'reverse-sampler/process.json').exists()
                    runtime = ('Greedy ran serially; sampling used the primary queue plus a temporary reverse sampler, '
                               if helper_used else 'Greedy and sampling ran serially; ')
                    runtime += 'final curves use two fixed disjoint queues. Overall CPU worker limit two; native threads one.'
                    content = content.replace('One serial worker, native threads one.', runtime)
                    tmp = path.with_name(path.name+f'.{os.getpid()}.tmp')
                    result = original_write(tmp, content, *args, **kwargs)
                    tmp.replace(path)
                    return result
                return original_write(path, content, *args, **kwargs)

            Path.write_text = atomic_report
            try:
                original()
            finally:
                Path.write_text = original_write
    module.publish_report = publish
    return publish


def worker(shard):
    import small_runner as main
    from rule import ROOT as rule_root
    import compact
    assert rule_root == ROOT
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE/f'shard-{shard}.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        ready = json.loads((STATE/'ready-to-run.json').read_text())
        if ready['wrapper_sha256']!=sha(Path(__file__)) or ready['primary_manifest_sha256']!=sha(OUT/'manifest.json'):
            raise RuntimeError('sealed operational source changed')
        for rel,digest in ready['samples'].items():
            if sha(ROOT/rel)!=digest:
                raise RuntimeError('prepared sample changed: '+rel)
        identity = compact.run_identity()
        primary = json.loads((OUT/'manifest.json').read_text())
        if any(primary[k]!=v for k,v in identity.items()):
            raise RuntimeError('runtime outcome epoch changed')
        for rel,digest in primary['code'].items():
            if sha(ROOT/rel)!=digest:
                raise RuntimeError('runtime source changed: '+rel)
        publish = locked_reports(main)
        receipt = dict(pid=os.getpid(), shard=shard, started_at=utc(),
                       inputs=ready['shards'][shard], source_receipt_sha256=sha(STATE/'ready-to-run.json'),
                       workers=1, native_threads=1, total_worker_limit=2, **identity)
        write(STATE/f'shard-{shard}-process.json', receipt)
        main.curves(ready['shards'][shard])
        publish()
        write(STATE/f'shard-{shard}-DONE.json', dict(**receipt, completed_at=utc()))


def supervise():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE/'supervisor.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        ready = verify()
        if FENCE.is_file():
            receipt = json.loads((STATE/'fence-receipt.json').read_text())
            if sha(FENCE)!=receipt['sha256']:
                raise RuntimeError('curve fence changed')
            FENCE.rename(STATE/'retired-sample-evaluations-fence.json')
        elif FENCE.exists() and not FENCE.is_dir():
            raise RuntimeError('unexpected curve evaluation path')
        write(STATE/'ready-to-run.json', ready)
        children = []
        for shard in range(2):
            command = ['uv','run','--offline','--no-sync','python','-B',str(Path(__file__)), '--shard', str(shard)]
            log = (STATE/f'shard-{shard}.log').open('ab')
            child = subprocess.Popen(command, cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT)
            children.append((child,log))
        write(STATE/'supervisor-process.json', dict(pid=os.getpid(), started_at=utc(),children=[c.pid for c,_ in children],
                                                  source_receipt_sha256=sha(STATE/'ready-to-run.json')))
        codes = [c.wait() for c,_ in children]
        for _,log in children:
            log.close()
        if any(codes):
            write(STATE/'worker-errors.json',dict(exit_codes=codes,completed_at=utc()))
            raise RuntimeError('curve shard failed: '+str(codes))
        import small_runner as main
        publish = locked_reports(main)
        publish()
        rows = list((OUT/'curves').glob('*.json'))
        if len(rows)!=55:
            raise RuntimeError(f'{len(rows)}/55 final curve rows')
        write(STATE/'DONE.json',dict(completed_at=utc(), exit_codes=codes, curves=55,
                                   source_receipt_sha256=sha(STATE/'ready-to-run.json')))


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--fence', action='store_true')
    group.add_argument('--verify-only', action='store_true')
    group.add_argument('--supervise', action='store_true')
    group.add_argument('--shard', type=int, choices=(0,1))
    args = parser.parse_args()
    if args.fence:
        fence()
    elif args.verify_only:
        verify()
    elif args.supervise:
        supervise()
    else:
        worker(args.shard)


if __name__ == '__main__':
    main()

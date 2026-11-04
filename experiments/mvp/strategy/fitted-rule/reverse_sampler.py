"""D217: one extra T=1 decode/preparation worker, after the scale lane exits.

Reads the primary small queue in reverse map order. It never judges, finishes,
replaces a sample, or changes the frozen rule. A three-map separation from the
primary forward sampler avoids overlapping publication; exclusive hard-link
publication remains the final race guard.
"""
from __future__ import annotations
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = HERE / 'reverse-sampler'
SMALL = HERE / 'small'
WEIGHTS = 'da15edecf1061c2e2014317ccaf53190f6c2997b5f225f8ca39bd5174a6559d6'
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
            'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS', 'RAYON_NUM_THREADS', 'POLARS_MAX_THREADS'):
    os.environ[key] = '1'
os.environ.update(UV_CACHE_DIR=str(ROOT / '.uv-cache'), CUDA_VISIBLE_DEVICES='', TOKENIZERS_PARALLELISM='false')
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.{time.time_ns()}.tmp')
    temporary.write_text(json.dumps(row, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def event(kind, **details):
    with (OUT / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(event=kind, utc=utc(), pid=os.getpid(), **details), sort_keys=True) + '\n')


def publish_exclusive(path, row):
    """A ready sample appears in one atomic step; an existing file always wins."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    temporary = path.with_name(path.name + f'.{os.getpid()}.{time.time_ns()}.tmp')
    try:
        with temporary.open('x') as stream:
            stream.write(json.dumps(row, indent=2, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            return False
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def primary_state(indices, reverse_index):
    """Read every seed; no stale or unknown state authorizes new work."""
    try:
        state = json.loads((SMALL / 'status.json').read_text())
    except (OSError, ValueError) as exc:
        return dict(allowed=False, reason=f'primary status unavailable: {exc}')
    stage = state.get('stage')
    if stage in ('curve', 'curves', 'complete') or str(stage).endswith('_complete'):
        return dict(allowed=False, reason=f'primary stage {stage}', primary=state)
    if stage == 'greedy':
        return dict(allowed=True, forward_index=-1, primary=state)
    if stage != 'stochastic' or state.get('instance_id') not in indices:
        return dict(allowed=False, reason='unknown primary stage or instance', primary=state)
    forward = indices[state['instance_id']]
    if forward >= reverse_index - 3:
        return dict(allowed=False, reason='three-map separation reached', forward_index=forward, primary=state)
    return dict(allowed=True, forward_index=forward, primary=state)


def seal():
    """Bind to the primary's exact 55 inputs and source/physics identities."""
    from rule import WEIGHTS_SHA256
    import compact
    manifest_path = SMALL / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    assert WEIGHTS_SHA256 == WEIGHTS == manifest['weights_sha256']
    assert sha(HERE / 'frozen-source/fitted.json') == WEIGHTS
    assert manifest['stochastic_seeds'] == list(range(16)) and manifest['stochastic_temperature'] == 1
    files = manifest['inputs']
    assert len(files) == 55
    ids = {}
    for rel, digest in files.items():
        assert rel.startswith('experiments/mvp/corpus/val/small/') and 'test' not in rel
        assert sha(ROOT / rel) == digest, f'changed input {rel}'
        ids[rel] = json.loads((ROOT / rel).read_text())['id']
    assert len(set(ids.values())) == 55
    for rel, digest in manifest['code'].items():
        assert sha(ROOT / rel) == digest, f'changed primary source {rel}'
    identity = compact.run_identity()
    assert all(manifest.get(key) == value for key, value in identity.items()), 'primary source identity changed'
    binding = dict(primary_manifest_sha256=sha(manifest_path), primary_code=manifest['code'],
                   helper_sha256=sha(__file__), weights_sha256=WEIGHTS, inputs=files, source_identity=identity,
                   seeds=list(range(16)), temperature=1, greedy=False, workers=1, native_threads=1,
                   order='reverse of sorted primary input paths', separation_maps=3,
                   publication='exclusive os.link of complete prepared row; existing raw/prepared file never replaced')
    path = OUT / 'receipt.json'
    if path.exists():
        assert json.loads(path.read_text())['binding'] == binding, 'helper receipt changed; refusing mixed-source resume'
    else:
        write(path, dict(created_utc=utc(), binding=binding))
    return files, ids, binding


def scale_released():
    """The owned scale worker must have exited and passed its artifact audit."""
    process = json.loads((HERE / 'scale/process.json').read_text())
    exited = json.loads((HERE / 'scale/worker-exit.json').read_text())
    audit = json.loads((HERE / 'scale/verification.json').read_text())
    assert exited['pid'] == process['pid'] and exited['exit_code'] == 0
    assert audit['verified'] is True and audit['maps'] == 18
    assert (HERE / 'scale/DONE.json').exists()
    return dict(pid=process['pid'], exit=exited, audit_sha256=sha(HERE / 'scale/verification.json'))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        released = scale_released()
        files, ids, binding = seal()
        from rule import decode_instance
        from evaluate import prepare
        from stowage.io import load_instance
        ordered = sorted(files)
        indices = {ids[rel]: index for index, rel in enumerate(ordered)}
        write(OUT / 'process.json', dict(pid=os.getpid(), started_utc=utc(), workers=1,
                                        native_threads=1, scale_released=released))
        event('start', scale_pid=released['pid'])
        published = skipped = discarded = 0
        reason = 'all reverse samples visited'
        stop = False
        for index in range(len(ordered) - 1, -1, -1):
            rel = ordered[index]
            assert sha(ROOT / rel) == files[rel]
            for source, digest in binding['primary_code'].items():
                assert sha(ROOT / source) == digest, f'changed source {source}'
            inst = None
            for seed in range(16):
                state = primary_state(indices, index)
                if not state['allowed']:
                    reason, stop = state['reason'], True
                    event('stop_admission', reverse_index=index, sample=seed, **state)
                    break
                target = SMALL / 'samples' / ids[rel] / f'sample-{seed:02d}.json'
                if target.exists():
                    skipped += 1
                    continue
                write(OUT / 'status.json', dict(stage='stochastic', pid=os.getpid(), utc=utc(),
                                                instance_id=ids[rel], input=rel, reverse_index=index,
                                                sample=seed, primary=state, published=published,
                                                skipped=skipped, discarded=discarded))
                if inst is None:
                    inst = load_instance(ROOT / rel)
                plan, metadata = decode_instance(inst, seed=seed, greedy=False)
                if target.exists():
                    discarded += 1
                    event('discard_existing_after_decode', instance_id=ids[rel], sample=seed)
                    continue
                row = dict(instance_id=ids[rel], input=rel, sample=seed, metadata=metadata,
                           plan=plan.model_dump(mode='json') if plan is not None else None)
                if plan is not None:
                    chosen, info = prepare(inst, plan)
                    row['prepared'] = info
                    row['chosen_plan'] = chosen.model_dump(mode='json')
                    row['signature'] = hashlib.sha256(json.dumps(
                        [move.model_dump(mode='json') for move in chosen.moves], sort_keys=True).encode()).hexdigest()
                # Primary may have advanced while decoding/preparing this sample.
                state = primary_state(indices, index)
                if not state['allowed']:
                    reason, stop = state['reason'], True
                    discarded += 1
                    event('discard_gap_closed', instance_id=ids[rel], sample=seed, **state)
                    break
                if target.exists() or not publish_exclusive(target, row):
                    discarded += 1
                    event('discard_existing_before_publication', instance_id=ids[rel], sample=seed)
                    continue
                published += 1
                event('published', instance_id=ids[rel], sample=seed, target=str(target.relative_to(ROOT)),
                      sample_sha256=sha(target), complete=plan is not None, decode_seconds=metadata['decode_seconds'])
                print(json.dumps(dict(instance_id=ids[rel], sample=seed, complete=plan is not None,
                                      published=published, reverse_index=index)), flush=True)
            if stop:
                break
        final = dict(stage='complete', pid=os.getpid(), utc=utc(), reason=reason,
                     published=published, skipped=skipped, discarded=discarded)
        write(OUT / 'status.json', final)
        write(OUT / 'DONE.json', final)
        event('complete', **{key: value for key, value in final.items() if key not in ('pid', 'utc')})


if __name__ == '__main__':
    main()

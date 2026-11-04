"""D84 resumable small campaign; fresh source imports and train-first phases.

The parent is idle while one single-thread producer runs per shard. Completed
instance/audit pairs retain their measured source epoch; source edits retry only
the interrupted draw. No loading plan is certified by this generator workflow.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import build
import campaign

HOLD_FILE = Path(__file__).with_name('d90-hold.json')
CAPACITY_FILE = Path(__file__).with_name('capacity.json')
STOP_FILE = Path(__file__).with_name('STOP-D126.json')


def worker_cap():
    return max(1, min(4, int(json.loads(CAPACITY_FILE.read_text())['max_workers']))) if CAPACITY_FILE.exists() else 4


def priority_handoff(root):
    """Replace the idle old wave with a live two-worker, val-first stream."""
    if STOP_FILE.exists():
        raise SystemExit(75)
    if os.environ.get('MVP_CORPUS_STREAM_OWNER') or not CAPACITY_FILE.exists():
        return
    policy = json.loads(CAPACITY_FILE.read_text())
    if not policy.get('val_first'):
        return
    import fcntl
    path = Path(root)/'.runtime/small/priority-owner.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        # Other old-wave callers wait idle until the new stream has finished.
        fcntl.flock(handle, fcntl.LOCK_EX)
        specs = [s for s in campaign.jobs(root) if s['rung']=='small']
        if all(completed(s, root) for s in specs):
            return
        env = dict(os.environ, MVP_CORPUS_STREAM_OWNER='1')
        argv = [sys.executable, str(Path(__file__).resolve()), '--root', str(root),
                '--workers', str(worker_cap()), '--val-first']
        child = subprocess.Popen(argv, env=env)
        build.atomic_json(Path(root)/'.runtime/small/priority-owner.json',
            dict(pid=child.pid,parent_pid=os.getpid(),workers=worker_cap(),val_first=True))
        if child.wait():
            raise RuntimeError('priority stream failed; inspect priority-owner.json and campaign.json')

def normalize_allocation_metadata(root):
    """Repair only stale v1 token estimates after an older evaluation worker.

    The published freeze already validated these identical request inventories.
    No data/audit changes: update the one metadata digest it actually changed.
    """
    allocation_path = root / 'token_allocation.json'
    if not allocation_path.exists():
        return
    value = json.loads(allocation_path.read_text())
    if value['allocation'].get('method') == 'D84 explicit qualified-small counts; preserved accepted tiny':
        return
    inventories = {s: json.loads((root / f'{s}.requested.json').read_text()) for s in ('train', 'val')}
    if not all(v.get('resize_decision') == 'D84' for v in inventories.values()):
        return
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    assert manifest['hashes']['token_allocation.json'] == build.sha256(allocation_path)
    requests_before = {s: build.sha256(root / f'{s}.requested.json') for s in inventories}
    campaign.prepare(Path(inventories['train']['token_allocation_source']), root,
        ship_settings=inventories['train']['ship_settings'],
        small_train=inventories['train']['counts']['small'],
        small_val=inventories['val']['counts']['small'], migrate=True)
    assert requests_before == {s: build.sha256(root / f'{s}.requested.json') for s in inventories}
    manifest['hashes']['token_allocation.json'] = build.sha256(allocation_path)
    manifest['metadata_repairs'] = ['D84 counts replace historical v1 token estimates; instance/audit hashes unchanged']
    build.atomic_json(manifest_path, manifest)


def completed(spec, root):
    path = build.shard_path(spec, root)
    audit = path.with_suffix('.audit.json')
    if not audit.exists():
        return False
    value = json.loads(audit.read_text())
    if (value.get('config') != spec or value.get('accepted') is not True
            or value.get('rule_freeze_v1_enabled') is not False
            or not path.exists() or build.sha256(path) != value['instance_sha256']):
        raise ValueError(f'invalid committed small shard: {path}')
    return True


def run_one(spec, root, log_dir, retries):
    if STOP_FILE.exists():
        return dict(seed=spec['seed'], split=spec['split'], status='stopped_D126')
    if completed(spec, root):
        return dict(seed=spec['seed'], split=spec['split'], status='resumed')
    log = log_dir / f"seed-{spec['seed']:09d}.log"
    request = log.with_suffix('.request.json')
    build.atomic_json(request, spec)
    argv = [sys.executable, str(Path(__file__).resolve()), '--root', str(root),
            '--worker-request', str(request)]
    started = time.monotonic()
    pids = []
    for attempt in range(retries):
        with log.open('a') as output:
            offset = output.tell()
            child = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT)
            pids.append(child.pid)
            build.atomic_json(log.with_suffix('.runtime.json'),
                              dict(pid=child.pid, owner='mvp-corpus/small', seed=spec['seed'],
                                   attempt=attempt, parent_pid=os.getpid(), child_pids=pids))
            code = child.wait()
        if completed(spec, root):
            return dict(seed=spec['seed'], split=spec['split'], status='generated',
                        seconds=time.monotonic()-started, child_pids=pids)
        tail = log.read_text()[offset:]
        if not any(s in tail for s in ('source epoch changed', 'source changed during generation')):
            break
    return dict(seed=spec['seed'], split=spec['split'], status='failed', exit_code=code,
                log=str(log), child_pids=pids, seconds=time.monotonic()-started)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=build.CORPUS)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--retries', type=int, default=20)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--val-first', action='store_true')
    parser.add_argument('--worker-request', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if STOP_FILE.exists():
        build.atomic_json(Path(args.root)/'.runtime/small/D126-stop-receipts'/f'{os.getpid()}.json',
            dict(pid=os.getpid(),parent_pid=os.getppid(),decision='D126',status='stopped_before_generation',
                 at_utc=datetime.now(timezone.utc).isoformat()))
        print(json.dumps({'status':'stopped_D126','pid':os.getpid(),'stop_file':str(STOP_FILE)}),flush=True)
        raise SystemExit(75)
    if args.worker_request:
        spec = json.loads(args.worker_request.read_text())
        priority_handoff(args.root)
        # Fresh children of an already-running wave also observe the live cap.
        # Waiting children hold no generator/physics state and consume no core.
        import fcntl
        lock = Path(args.root)/'.runtime/small/single-producer.lock'
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open('a') as handle:
            if worker_cap() == 1:
                fcntl.flock(handle, fcntl.LOCK_EX)
            result = build.generate_one((spec, str(args.root), build.source_snapshot()))
        print(json.dumps(result), flush=True)
        raise SystemExit(0 if result['status'] != 'failed' else 2)
    hold = HOLD_FILE
    if hold.exists():
        print(json.dumps({'status': 'held_for_D90_inventory_amendment', 'hold': str(hold)}), flush=True)
        raise SystemExit(75)
    assert 1 <= args.workers <= 4 and args.retries > 0
    args.workers = min(args.workers, worker_cap())
    if args.root.resolve() == build.CORPUS.resolve():
        ledger = build.ROOT/'experiments/fleet/ledger.md'
        content = ledger.read_text()
        rows = []
        for line in content.splitlines(keepends=True):
            if line.startswith('| mvp-corpus/d90-small-'):
                cells = line.split('|')
                cells[3], cells[4] = f' {args.workers} ', f' {args.workers*2} '
                line = '|'.join(cells)
            rows.append(line)
        ledger.write_text(''.join(rows))
    normalize_allocation_metadata(args.root)
    log_dir = args.root / '.runtime/small'
    log_dir.mkdir(parents=True, exist_ok=True)
    state_path = log_dir / 'campaign.json'
    started = time.monotonic()
    state = dict(pid=os.getpid(), workers=args.workers, no_gpu=True,
                 started_at_utc=datetime.now(timezone.utc).isoformat(), outcomes=[],
                 requested_nice=10, stage_environment={'generation': {'STOWAGE_BALLAST_V4': None},
                                                       'finalization': {'STOWAGE_BALLAST_V4': '1'}})
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    except OSError as error:
        state['nice_error'] = str(error)
    state['effective_nice'] = os.getpriority(os.PRIO_PROCESS, 0)
    specs = [s for s in campaign.jobs(args.root) if s['rung'] == 'small']
    if args.val_first:
        specs.sort(key=lambda s:s['split']!='val')
    if args.limit is not None:
        specs = [s for s in specs if not completed(s, args.root)][:args.limit]
    state['requested'] = len(specs)
    build.atomic_json(state_path, state)
    # APPROXIMATION: fresh imports per draw cost startup time but avoid pinning
    # stale production code while other executives land opt-in fixes.
    for split in (('val','train') if args.val_first else ('train','val')):
        phase = [s for s in specs if s['split'] == split]
        state.update(phase=split, phase_requested=len(phase))
        build.atomic_json(state_path, state)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run_one, s, args.root, log_dir, args.retries) for s in phase]
            for future in as_completed(futures):
                outcome = future.result()
                state['outcomes'].append(outcome)
                state['elapsed_wall_s'] = time.monotonic()-started
                build.atomic_json(state_path, state)
                print(json.dumps(outcome, sort_keys=True), flush=True)
    state.update(complete=all(completed(s, args.root) for s in specs),
                 ended_at_utc=datetime.now(timezone.utc).isoformat(),
                 elapsed_wall_s=time.monotonic()-started)
    build.atomic_json(state_path, state)
    print(json.dumps({k: v for k, v in state.items() if k != 'outcomes'}), flush=True)
    if not state['complete']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()

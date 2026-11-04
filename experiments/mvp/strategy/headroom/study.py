#!/usr/bin/env python3
"""Detached D175 supervisor: checked pilot, full64 continuation, live report."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
from report import render
from run import atomic_json


def refresh(out, manifest):
    text = render(manifest, out)
    temporary = HERE/'HEADROOM.md.tmp'
    temporary.write_text(text)
    temporary.replace(HERE/'HEADROOM.md')
    pids = set()
    for progress in (out/'maps').glob('*/progress.jsonl'):
        for line in progress.read_text().splitlines():
            try:
                pid = json.loads(line).get('pid')
                if pid:
                    pids.add(pid)
            except (ValueError, TypeError):
                pass
    atomic_json(HERE/'workers.json', {'recorded_worker_pids': sorted(pids), 'updated_at': time.time(), 'note': 'Historical owned map worker PIDs, not guaranteed still active; no process discovery.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=HERE/'runs/study')
    parser.add_argument('--workers', type=int, choices=(1,2,3), default=3)
    parser.add_argument('--skip-pilot', action='store_true')
    parser.add_argument('--bootstrap-two', action='store_true')
    parser.add_argument('--await-pilot', type=Path, action='append', default=[])
    parser.add_argument('--first-wave', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    out.relative_to(HERE)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = HERE/'train_maps.json'
    manifest = json.loads(manifest_path.read_text())
    atomic_json(HERE/'supervisor.json', {'pid': os.getpid(), 'start': time.time(), 'out': str(out), 'workers': args.workers})
    stages = ['full'] if args.skip_pilot else ['pilot','full']
    if args.first_wave:
        stages = ['first-wave', 'full']
    if args.bootstrap_two:
        stages.insert(0, 'bootstrap')
    for stage in stages:
        if stage != 'bootstrap' and args.await_pilot:
            while not all(path.is_file() for path in args.await_pilot):
                refresh(out, manifest)
                time.sleep(30)
        run_stage = 'pilot' if stage == 'bootstrap' else 'full' if stage == 'first-wave' else stage
        command = [sys.executable, str(HERE/'run.py'), '--manifest', str(manifest_path), '--out', str(out), '--workers', str(args.workers), '--stage', run_stage]
        if stage == 'bootstrap':
            for item in manifest['maps'][1:3]:
                command.extend(['--only', item['map_id']])
        if stage == 'first-wave':
            selected = {}
            for item in manifest['maps']:
                selected.setdefault((item['size'], item['ship']), item['map_id'])
            for map_id in selected.values():
                command.extend(['--only', map_id])
        with (out/'controller.log').open('a') as log:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        atomic_json(out/'launch.json', {'pid': child.pid, 'supervisor_pid': os.getpid(), 'command': command, 'time': time.time(), 'stage': stage})
        atomic_json(HERE/'status.json', {'stage': stage, 'controller_pid': child.pid, 'supervisor_pid': os.getpid(), 'started_at': time.time(), 'complete': False})
        print(json.dumps({'stage': stage, 'controller_pid': child.pid}), flush=True)
        while child.poll() is None:
            try:
                refresh(out, manifest)
            except Exception as error:
                print(f'report refresh: {type(error).__name__}: {error}', flush=True)
            time.sleep(30)
        refresh(out, manifest)
        print(json.dumps({'stage': stage, 'exit': child.returncode}), flush=True)
        if child.returncode:
            atomic_json(HERE/'status.json', {'stage': stage, 'exit_code': child.returncode, 'complete': False, 'error': 'controller exited; inspect controller.log; resume same command after resolving cause'})
            return child.returncode
    refresh(out, manifest)
    atomic_json(HERE/'status.json', {'stage': 'finished', 'complete': True, 'finished_at': time.time()})
    ledger = HERE/'LEDGER.md'
    with ledger.open('a') as stream:
        stream.write(f'\nCompleted at {datetime.now(timezone.utc).isoformat()}; compute reservation released. See status.json and full-completion.json for per-map errors.\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

"""GPU-owner-only launch; CPU default, explicit Claude lease, exact-child wall stop."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from . import train
from .admission import STATE, admit, environment

HERE, ROOT, ADAPTER = train.HERE, train.ROOT, train.ADAPTER


def gpu_lease(path, *, now=None, seconds=2700):
    """Claude creates this file after the reader releases exclusive GPU use."""
    try:
        lease = json.loads(Path(path).read_text())
        now = time.time() if now is None else now
        if (lease.get('task') != 'intake_lora' or lease.get('approved') is not True
                or lease.get('authorized_by', '').casefold() != 'claude'
                or not float(lease['issued_at']) <= now
                or not float(lease['expires_at']) > now + seconds):
            return None
        return lease
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None


def wait_admission(lease_path, *, state_path=STATE, seconds=2700,
                   sleep=time.sleep, clock=time.time):
    # Both must be ready at the same moment. Invalid/stale/missing state never
    # aborts a detached launcher and never bypasses either admission condition.
    while True:
        now = clock()
        cpu, lease = admit(state_path, now=now), gpu_lease(lease_path, now=now, seconds=seconds)
        if cpu is not None and lease is not None:
            return cpu, lease
        sleep(5)


def prelaunch():
    cfg, model, rows, valid, report = train.data_preflight()
    preflight = json.loads((HERE / 'tokenizer-preflight.json').read_text())
    for key in ('config_sha256', 'manifest_sha256', 'data_sha256', 'selected_train_ids_sha256'):
        if preflight.get(key) != report.get(key):
            raise ValueError('tokenizer preflight does not match current recipe/data')
    if not preflight.get('prompt_prefix_verified') or preflight.get('device_loaded') is not False:
        raise ValueError('CPU tokenizer preflight required before GPU launch')
    if ((ADAPTER.exists() and any(ADAPTER.iterdir()))
            or (HERE / 'final-freeze.json').exists()
            or (HERE / 'sealed/SCORE_CONSUMED.json').exists()
            or (HERE / 'results/sealed-final/freeze.json').exists()):
        raise ValueError('fresh run only; nonempty adapter or final freeze forbids training')
    train.freeze_recipe()
    return cfg


def record_stop(reason, elapsed, returncode=None):
    """A hard kill exposes only the latest complete checkpoint, never final loss."""
    ADAPTER.mkdir(parents=True, exist_ok=True)
    report = {'reason': reason, 'elapsed_seconds': elapsed, 'returncode': returncode,
              'selected_budget_completed': False, 'full_corpus_epoch_completed': False,
              'source_adapter': None, 'hard_wall_enforced': True,
              'checkpoint_usable': False, 'at': datetime.now(timezone.utc).isoformat()}
    weights, progress = ADAPTER / 'adapters.safetensors', ADAPTER / 'progress.json'
    if weights.is_file() and progress.is_file():
        previous = json.loads(progress.read_text())
        digest = train.sha256(weights)
        if previous.get('adapter_sha256') == digest:
            report.update(checkpoint_usable=True, adapter_sha256=digest,
                          rows_completed=previous['checkpoint_rows_completed'],
                          optimizer_updates=previous['optimizer_updates'])
    # A completed worker owns its STOPPED.json. A killed worker cannot write it.
    with (ADAPTER / 'STOPPED.json').open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    return report


def supervise(lease_path):
    cfg = json.loads((HERE / 'config.json').read_text())
    train.validate_recipe(cfg)
    cpu, _ = wait_admission(lease_path, seconds=cfg['hard_stop_seconds'])
    cfg = prelaunch()
    # Exclusive local marker prevents two launchers from racing into one folder.
    with (HERE / 'gpu-started.json').open('x') as stream:
        stream.write(json.dumps({'pid': os.getpid(), 'at': datetime.now(timezone.utc).isoformat(),
                                 'admission': cpu, 'gpu_lease_sha256': train.sha256(lease_path),
                                 'recipe_freeze_sha256': train.sha256(HERE / 'recipe-freeze.json')}) + '\n')
    # Recheck after prelaunch hashing; the lease must still cover the hard wall.
    wait_admission(lease_path, seconds=cfg['hard_stop_seconds'])
    command = [sys.executable, '-m', 'experiments.mvp.pipeline.intake_lora.gpu_run',
               '--worker', '--gpu-lease', str(Path(lease_path).resolve())]
    env = {**environment(), 'INTAKE_SUPERVISOR_PID': str(os.getpid())}
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, check=False,
                                timeout=cfg['hard_stop_seconds'])
    except subprocess.TimeoutExpired:
        # subprocess.run killed and reaped only its exact child handle.
        if (ADAPTER / 'STOPPED.json').exists():
            report = json.loads((ADAPTER / 'STOPPED.json').read_text())
            report.update(hard_wall_enforced=True, supervisor_elapsed_seconds=time.monotonic() - started)
            (HERE / 'supervisor-stop.json').write_text(json.dumps(report, indent=2) + '\n')
        else:
            report = record_stop('supervisor hard wall: exact owned worker killed', time.monotonic() - started)
        print(json.dumps(report, indent=2))
        return 124
    if result.returncode:
        if not (ADAPTER / 'STOPPED.json').exists():
            record_stop('worker failed; no restart or optimizer resume',
                        time.monotonic() - started, result.returncode)
        return result.returncode
    train.check_recipe_freeze()
    report = json.loads((ADAPTER / 'STOPPED.json').read_text())
    print(json.dumps({**report, 'hard_wall_enforced': True,
                      'supervisor_elapsed_seconds': time.monotonic() - started}, indent=2))
    return 0


def worker(lease_path):
    started = time.monotonic()
    if os.environ.get('INTAKE_SUPERVISOR_PID') != str(os.getppid()):
        raise ValueError('GPU worker requires its owned hard-wall supervisor')
    cfg = json.loads((HERE / 'config.json').read_text())
    # The parent already required coverage for all 2700 seconds. Requiring full
    # coverage again after subprocess startup would reject an otherwise valid lease.
    wait_admission(lease_path, seconds=0)
    train.check_recipe_freeze()
    cfg, model, rows, valid, manifest = train.data_preflight()
    print(json.dumps(train.run_train(rows, valid, model, cfg, manifest, started=started)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--train-only', action='store_true')
    mode.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--gpu-lease', type=Path)
    args = parser.parse_args()
    if args.train_only or args.worker:
        if args.gpu_lease is None:
            parser.error('--gpu-lease from Claude is required; no GPU slot is inferred')
        if args.worker:
            worker(args.gpu_lease)
            return
        raise SystemExit(supervise(args.gpu_lease))
    print(json.dumps({'device_loaded': False, 'sealed_read': False,
                      'hard_stop_seconds': 2700, 'source_adapter': None,
                      'adapter': str(ADAPTER),
                      'policy': 'Preparation only until Claude supplies an explicit exclusive GPU lease.'}, indent=2))


if __name__ == '__main__':
    main()

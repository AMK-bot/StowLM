"""Explicit Claude/Metal host launcher for the leader-installed D137 restart.

Does not install patches. All GPU work remains behind the existing controller's
shared lock, owner-release, fresh-code smoke, review, ledger and memory gates.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from state import ROOT, DEFAULT_OUT, DEFAULT_MANIFEST, atomic_json, initialize_schedule, prior_schedule_paths, validate_installed_manifest

DEFAULT_CHOICE = DEFAULT_OUT/'parent-choice.json'


def thread_environment():
    os.environ['STOWLM_MLX_CACHE_LIMIT_GB'] = '2'
    # verify_candidate refuses an inherited value, which would void every teacher row (D142 item 8).
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                 'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    return os.environ.copy()


@contextmanager
def free_lock(path):
    with Path(path).open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f'D137 host/controller lock occupied: {path}') from error
        try:yield
        finally:fcntl.flock(lock, fcntl.LOCK_UN)


def load_choice(path):
    """D142 F6: the parent comes only from the audited 8.1 vs 8.2 choice, written by parent_audit.py."""
    choice = json.loads(Path(path).read_text())
    if choice.get('schema') != 'exit-d142-parent-choice-v1' or choice.get('parent_label') not in ('8.1','8.2'):
        raise ValueError('parent choice receipt is missing or malformed; run parent_audit.py first')
    return choice


def controller_command(args, choice):
    parent = Path(choice['parent'])
    return [sys.executable, str(Path(__file__).with_name('controller.py')),
        '--out', str(args.out), '--installed-manifest', str(args.installed_manifest),
        '--parent', str(parent), '--completion-marker', str(parent.parent/'DONE.json'),
        '--baseline-label', choice['parent_label'], '--comparison-adapter', choice['comparison_adapter'],
        '--gens',str(args.gens),'--maps',str(args.maps),'--k',str(args.k),
        '--B',str(args.batch_size),'--population',str(args.population),
        '--steps',str(args.steps),'--max-workers',str(args.max_workers)]


def launch(args):
    args.out, args.installed_manifest = args.out.resolve(), args.installed_manifest.resolve()
    if args.out != DEFAULT_OUT.resolve():
        raise ValueError('D137 restart is fenced to its fresh deployment-d137 output')
    installed = validate_installed_manifest(args.installed_manifest)
    env = thread_environment()
    choice = load_choice(args.parent_choice)
    command = controller_command(args, choice)
    if args.check_only:
        prior, sources = __import__('state').union_prior_reservations(prior_schedule_paths(out=args.out))
        return dict(mode='cpu_read_only_launch_plan', command=command, installed_manifest=installed,
            parent=choice['parent'], baseline_label=choice['parent_label'], parent_choice=choice,
            prior_schedule_sources=sources, inherited_exclusions={name:len(prior[name])
                for name in ('used','used_seeds','used_ids')},
            audit_clock='frozen first generation launch + 330 minutes', gpu_launched=False)
    from llm.exit.runtime import preflight_mlx_device
    device = preflight_mlx_device()  # Must fail before modifying a queue without Metal.
    args.out.mkdir(parents=True, exist_ok=True)
    with free_lock(args.out/'.host-d137.lock'):
        # A running controller owns its own lock; never interrupt or supersede it.
        with free_lock(args.out/'.deployment.lock'), free_lock(args.out/'.runner.lock'):
            if any((args.out/name).exists() for name in ('deployment.cancel','terminal.json')):
                raise RuntimeError('D137 output cancelled/terminal; no automatic resurrection')
        record_path = args.out/'controller-launch.json'
        if record_path.exists():
            record = json.loads(record_path.read_text())
            try:os.kill(record['pid'], 0)
            except ProcessLookupError:pass
            except PermissionError:return dict(record, existing_controller=True)
            else:return dict(record, existing_controller=True)
        reservations = initialize_schedule(args.out, prior_schedule_paths(out=args.out))
        validate_installed_manifest(args.installed_manifest)
        stamp = datetime.now(timezone.utc).isoformat()
        with (args.out/'controller.log').open('a') as stream:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                stderr=subprocess.STDOUT, start_new_session=True)
        record = dict(schema='exit-d137-host-launch-v1', pid=process.pid, command=command,
            utc=stamp, device=device, installed_manifest=installed,
            decision='D137/D142 fresh teacher-population loop from the audited parent',
            baseline_label=choice['parent_label'], parent_choice=choice,
            cache_limit_gb=2, max_workers=args.max_workers,
            audit_clock_policy='freeze first actual generation launch plus 330 minutes',
            prior_reservation_sources=reservations['sources'])
        (args.out/'controller.pid').write_text(f'{process.pid}\n')
        try:
            with free_lock(args.out/'.gen0-watcher.lock'):
                start_watcher = True
        except RuntimeError:
            start_watcher = False
        if start_watcher:
            with (args.out/'gen0-watcher.log').open('a') as stream:
                watcher = subprocess.Popen([sys.executable,
                    str(ROOT/'experiments/mvp/exit/watch_gen0.py'),'--out',str(args.out)],
                    cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
                    start_new_session=True)
            record['watcher_pid'] = watcher.pid
        atomic_json(record_path, record)
    return record


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--out',type=Path,default=DEFAULT_OUT)
    value.add_argument('--installed-manifest',type=Path,default=DEFAULT_MANIFEST)
    value.add_argument('--parent-choice',type=Path,default=DEFAULT_CHOICE)
    value.add_argument('--gens',type=int,default=6)
    value.add_argument('--maps',type=int,default=12)
    value.add_argument('--k',type=int,choices=range(6,9),default=6)
    value.add_argument('--B','--batch-size',dest='batch_size',type=int,default=8)
    value.add_argument('--population',type=int,choices=(2,3),default=2)
    value.add_argument('--steps',type=int,default=100)
    value.add_argument('--max-workers',type=int,choices=(2,3,4),default=4)
    value.add_argument('--check-only',action='store_true')
    return value


def main():
    args = parser().parse_args()
    if min(args.gens,args.maps,args.steps,args.batch_size) < 1:
        raise ValueError('generation/map/update/batch counts must be positive')
    print(json.dumps(launch(args),indent=2),flush=True)


if __name__ == '__main__':main()

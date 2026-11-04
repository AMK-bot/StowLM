"""D129 recovery queue; run explicitly from the leader's Metal-capable session.

This launcher starts no sampling itself. Parent completion, fresh-code smoke,
GPU ownership, numeric ledger/capacity, securing and cache gates remain in the
unchanged controller/runner path. It never cancels the original deployment.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = ROOT/'experiments/mvp/exit/deployment-d129'
PARENT = ROOT/'experiments/mvp/sft/stowlm-v1/adapter'
COMPLETION = PARENT.parent/'DONE.json'


@contextmanager
def _free_lock(path):
    with path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f'existing recovery process holds {path.name}; wait for its exit') from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def launch(*, out=None, prepared=None):
    """Explicit host-only entry point; return recorded controller/watcher PIDs."""
    os.environ['STOWLM_MLX_CACHE_LIMIT_GB'] = '2'
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                 'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    from llm.exit.runtime import preflight_mlx_device
    device = preflight_mlx_device()  # Fail before changing queue state without Metal.
    out = Path(out or DEFAULT_OUT).resolve()
    prepared = Path(prepared or out/'salvage-prepared.json').resolve()
    if out == (ROOT/'experiments/mvp/exit/deployment').resolve():
        raise ValueError('D129 recovery needs a new output directory; original deployment is immutable')
    if not prepared.is_file():
        raise FileNotFoundError(f'epoch-reverified prepared manifest not found: {prepared}')
    if not prepared.is_relative_to(out):
        raise ValueError('prepared manifest must reside inside the recovery output directory')
    if not isinstance(json.loads(prepared.read_text()), dict):
        raise ValueError('prepared manifest must be a JSON object')
    out.mkdir(parents=True, exist_ok=True)
    with _free_lock(out/'.host-recovery.lock'):
        # These are only this new output's locks. The shared GPU lock and other
        # executives' ledger rows remain controller admission gates below.
        with _free_lock(out/'.deployment.lock'), _free_lock(out/'.runner.lock'):
            if (out/'deployment.cancel').exists() or (out/'terminal.json').exists():
                raise RuntimeError('recovery directory is cancelled/terminal; choose a new output directory')
        try:
            with _free_lock(out/'.gen0-watcher.lock'):
                start_watcher = True
        except RuntimeError:
            start_watcher = False  # Existing new-output watcher retains ownership.
        command = [sys.executable, str(ROOT/'experiments/mvp/exit/deploy_when_ready.py'),
            '--out', str(out), '--recover-prepared', str(prepared),
            '--parent', str(PARENT), '--completion-marker', str(COMPLETION),
            '--gens','8','--maps','48','--k','8','--B','8','--population','2','--steps','120']
        env = os.environ.copy()
        env['STOWLM_MLX_CACHE_LIMIT_GB'] = '2'
        for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                     'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
            env[name] = '1'
        stamp = datetime.now(timezone.utc).isoformat()
        with (out/'controller.log').open('a') as stream:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                stderr=subprocess.STDOUT, start_new_session=True)
        record = dict(pid=process.pid, command=command, utc=stamp,
            decision='D129 epoch-reverified recovery from 8.1', cache_limit_gb=2,
            device=device, out=str(out), prepared=str(prepared))
        (out/'controller.pid').write_text(f'{process.pid}\n')
        if start_watcher:
            with (out/'gen0-watcher.log').open('a') as stream:
                watcher = subprocess.Popen([sys.executable,
                    str(ROOT/'experiments/mvp/exit/watch_gen0.py'), '--out', str(out)],
                    cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
                    start_new_session=True)
            record['watcher_pid'] = watcher.pid
        (out/'controller-launch.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--recover-prepared', type=Path)
    args = parser.parse_args()
    return launch(out=args.out, prepared=args.recover_prepared)


if __name__ == '__main__':
    main()

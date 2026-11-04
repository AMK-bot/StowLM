"""Launch an explicit versioned profile; never silently choose solver budgets."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def validate_selectors(arguments):
    """A frozen profile owns all solver settings; only job selectors may follow."""
    allowed={'--methods','--size','--occupancy','--map-seed','--shard-index',
             '--shard-count','--out','--dev','--overwrite','--help'}
    for token in arguments:
        if token.startswith('--') and token.split('=',1)[0] not in allowed:
            raise ValueError(f'Profile solver settings cannot be overridden; unsupported selector: {token.split("=",1)[0]}')


def development_deferred(arguments, marker=None):
    """Defer new queued development jobs without signalling an active worker."""
    marker=Path(marker) if marker is not None else HERE/'PAUSE-DEV'
    return '--dev' in arguments and marker.exists()


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--profile', default='PROFILE.json')
    args, remaining = parser.parse_known_args()
    validate_selectors(remaining)
    if development_deferred(remaining):
        print(json.dumps(dict(status='development deferred',marker=str(HERE/'PAUSE-DEV'),
                              reason=(HERE/'PAUSE-DEV').read_text().strip())),flush=True)
        return
    path = (HERE / args.profile).resolve()
    if not path.is_relative_to(HERE):
        raise ValueError('Profile must be local')
    profile = json.loads(path.read_text())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    command = [sys.executable, str(HERE/'runner.py'), '--profile-sha256', digest]
    for section in ('work_config', 'finishing_config', 'method_config'):
        command += ['--'+section.replace('_','-'), json.dumps(profile.get(section, {}), sort_keys=True)]
    for key, value in profile.get('native_settings', {}).items():
        if value is not None:
            command += ['--'+key.replace('_','-'), str(value)]
    command += remaining
    env = os.environ.copy()
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS',
                 'MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','STOWAGE_IN_PORT_WORKERS'):
        env[name] = '1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    print(json.dumps(dict(pid=os.getpid(), profile=str(path), profile_sha256=digest)), flush=True)
    child = subprocess.Popen(command, env=env)
    print(json.dumps(dict(runner_pid=child.pid)), flush=True)
    raise SystemExit(child.wait())


if __name__ == '__main__':
    main()

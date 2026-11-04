"""Run the frozen primary profile; extra arguments select/resume runner jobs."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    profile_path = HERE / 'PROFILE.json'
    profile = json.loads(profile_path.read_text())
    if '--help' not in sys.argv:
        manifest = json.loads((HERE / 'cohort/MANIFEST.json').read_text())
        rows = manifest['maps']
        expected = set(range(331401000, 331401072))
        if len(rows) != 72 or {r['seed'] for r in rows} != expected:
            raise ValueError('Generate all 72 predeclared maps before primary measurement')
    command = [sys.executable, str(HERE / 'runner.py'),
        '--work-config', json.dumps(profile['work_config'], sort_keys=True),
        '--finishing-config', json.dumps(profile['finishing_config'], sort_keys=True)]
    for key, value in profile['native_settings'].items():
        if value is not None:
            command += ['--' + key.replace('_', '-'), str(value)]
    command += sys.argv[1:]
    environment = os.environ.copy()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                 'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS', 'NUMEXPR_NUM_THREADS'):
        environment[name] = '1'
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    print(json.dumps(dict(pid=os.getpid(), profile_sha256=hashlib.sha256(profile_path.read_bytes()).hexdigest())), flush=True)
    child = subprocess.Popen(command, env=environment)
    print(json.dumps(dict(runner_pid=child.pid)), flush=True)
    raise SystemExit(child.wait())


if __name__ == '__main__':
    main()

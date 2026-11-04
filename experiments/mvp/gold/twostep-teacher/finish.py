"""D151 end of run: wait for this launch's workers, then admit + export (d151.py), the blocking 30-winner audit, and the
8.3 build. Each step must succeed; the first failure writes FAILED.json and stops, leaving the ledger row. Markers from
an earlier run are removed first, and readiness is READY-83.json with this run's uuid and launch id.
Usage: finish.py  (run detached; safe to rerun after a fix)
"""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time

import d151

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PYTHON = str(ROOT/'.venv/bin/python')
ENV = {**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONPATH': str(HERE), **{k: '1' for k in d151.THREADS}}
ENV.pop('STOWAGE_BALLAST_V4', None)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def step(name, *command):
    with open(HERE/f'finish-{name}.log', 'w') as log:
        subprocess.run([PYTHON, *map(str, command)], cwd=ROOT, env=ENV, stdout=log, stderr=subprocess.STDOUT, check=True)


def release_ledger():
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True)
                              if not line.startswith('| d150-twostep-teacher/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- d150-twostep-teacher (fix-t8-sft, 30 Oct): D151 teacher run, audit and 8.3 build, ≤ 6 CPU workers, no GPU; '
                      f'estimate 2 h, finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')


def main():
    for name in ('COMPLETE.json', 'FAILED.json', 'READY-83.json'):
        (HERE/name).unlink(missing_ok=True)
    while any(alive(int(p)) for p in (HERE/'pids.txt').read_text().split()):
        time.sleep(15)
    try:
        step('admit', HERE/'d151.py', 'admit')
        step('audit', HERE/'audit_sample.py', 30, 150, 3)
        step('best', HERE/'best.py')
        step('build', HERE/'build_83.py', 4)
    except Exception as error:
        (HERE/'FAILED.json').write_text(json.dumps(dict(error=f'{type(error).__name__}: {error}',
                                                        at=datetime.now().astimezone().isoformat()), indent=1)+'\n')
        raise
    release_ledger()
    d151.write(HERE/'READY-83.json', dict(uuid=d151.read(HERE/'d151-manifest.json')['uuid'], launch=d151.read(HERE/'launch.json')['launch_id'],
                                          at=datetime.now().astimezone().isoformat(), data=str(ROOT/'experiments/mvp/sft/stowlm-v3/data')))


if __name__ == '__main__':
    main()

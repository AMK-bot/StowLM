"""Detached CPU-only D172 supervisor. Resume valid rows, retry operational failures, never build/train.
Run with the repository Python; records launches/PIDs and leaves FAILED.json on any blocking gate failure.
"""
from datetime import datetime
import os
from pathlib import Path
import subprocess
import time
import traceback
import sys

import d151

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUN = HERE/'d172'
PYTHON = str(ROOT/'.venv/bin/python')
ENV = {**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONPATH': str(HERE), **{k:'1' for k in d151.THREADS}}
ENV.pop('STOWAGE_BALLAST_V4', None)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def ledger_done():
    import fcntl
    ledger = ROOT/'experiments/fleet/ledger.md'
    with ledger.open('r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        content = ''.join(line for line in stream.read().splitlines(keepends=True)
                          if not line.startswith('| teacher-rest/d172 |'))
        stream.seek(0)
        stream.write(content)
        stream.truncate()
    with (ROOT/'experiments/fleet/history.md').open('a') as stream:
        stream.write(f'\n- teacher-rest/d172: 196 new full comparisons + 1,252 same-epoch D151 winners; '
                     f'5 CPU workers, no GPU; estimate 60–100 min; completed '
                     f'{datetime.now().astimezone().isoformat(timespec="minutes")}; '
                     f'launches and elapsed seconds in twostep-teacher/d172/supervisor-state.json.\n')


def main():
    started = d151.read(RUN/'manifest.json')['run_start']
    attempts = []
    for attempt in range(1, 4):
        if not (attempt == 1 and '--attach' in sys.argv[1:]):
            with (RUN/'launch.log').open('a') as log:
                subprocess.run([PYTHON, str(HERE/'launch.py'), '5', '--run-dir', str(RUN)],
                               cwd=ROOT, env=ENV, stdout=log, stderr=subprocess.STDOUT, check=True)
        launch = d151.read(RUN/'launch.json')
        pids = [int(p) for p in (RUN/'pids.txt').read_text().split()]
        attempts.append(dict(attempt=attempt, launch_id=launch['launch_id'], pids=pids, started=time.time()))
        print(f'launch {attempt}: {launch["launch_id"]} PIDs {pids}', flush=True)
        receipts = RUN/'receipts'/launch['launch_id']
        while not all((receipts/f'shard-{s}.json').exists() for s in range(launch['workers'])):
            if not any(alive(p) for p in pids):
                raise RuntimeError('workers exited without exact receipts (epoch/crash); inspect shard logs')
            time.sleep(10)
        manifest = d151.read(RUN/'manifest.json')
        from d172_prepare import row_problem
        invalid = []
        for entry in manifest['entries']:
            target = RUN/'rows'/(d151.key(entry['case'])+'.json')
            problem = row_problem(d151.read(target), entry, manifest, RUN) if target.exists() else 'missing'
            if problem:
                invalid.append(dict(case=entry['case'], reason=problem))
        state = dict(attempts=attempts, invalid=invalid, elapsed_s=time.time()-started)
        d151.write(RUN/'supervisor-state.json', state)
        if not invalid:
            break
        if attempt == 3:
            raise RuntimeError(f'{len(invalid)} incomplete rows after 3 launches; no export: {invalid[:3]}')
        print(f'{len(invalid)} incomplete rows; resume only those under a fresh UUID', flush=True)
        while any(alive(p) for p in pids):
            time.sleep(2)
    while any(alive(p) for p in pids):
        time.sleep(2)
    with (RUN/'finish.log').open('w') as log:
        subprocess.run([PYTHON, str(HERE/'d172_finish.py'), str(RUN), '--workers', '5'], cwd=ROOT, env=ENV,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    d151.write(RUN/'supervisor-state.json', dict(attempts=attempts, complete=True, elapsed_s=time.time()-started))
    (RUN/'leader-report.md').write_text(
        'D172 complete. Export: `../best-d172/`; exact coverage 1,448 maps (735 small + 713 tiny).\n\n'
        '196 new full comparisons and 1,252 verified D151 winners under the same epoch; zero exclusions. '
        'Blocking 30-winner audit passed. See `summary.md` for per-ship counts and two-step wins.\n\n'
        'CPU only, 5 workers; no training dataset or training. Existing D151 gold-only winners were retained.\n')
    ledger_done()
    print(f'COMPLETE: {RUN/"COMPLETE.json"}; elapsed {time.time()-started:.0f}s', flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        d151.write(RUN/'FAILED.json', dict(complete=False, error=f'{type(error).__name__}: {error}',
                   at=datetime.now().astimezone().isoformat(), traceback=traceback.format_exc()))
        (RUN/'leader-report.md').write_text(f'D172 blocked; no completion claimed. {type(error).__name__}: {error}\n'
                                           'Inspect FAILED.json, supervisor.log and shard logs; resume valid rows under the same manifest.\n')
        raise

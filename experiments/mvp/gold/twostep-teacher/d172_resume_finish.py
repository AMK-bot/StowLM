"""Detached leader-authorized D172 finish only: three CPU audit workers, no solver reruns or training."""
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

import d151

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUN = HERE/'d172'


def main():
    started = time.time()
    env = {**os.environ, 'PYTHONHASHSEED':'0', 'PYTHONPATH':str(HERE), **{key:'1' for key in d151.THREADS}}
    env.pop('STOWAGE_BALLAST_V4', None)
    with (RUN/'finish-authorized-exclusions.log').open('a') as log:
        child = subprocess.Popen([str(ROOT/'.venv/bin/python'), str(HERE/'d172_finish.py'), str(RUN), '--workers', '3'],
                                 cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        (RUN/'finish-authorized-exclusions.pid').write_text(str(child.pid)+'\n')
        if child.wait() != 0:
            raise RuntimeError('authorized finish failed; inspect finish-authorized-exclusions.log and FAILED.json')
    completion = d151.read(RUN/'COMPLETE.json')
    if not (completion['complete'] and completion['audit_passed'] and completion['exported'] == 1446
            and completion['exported_small'] == 733 and completion['exported_tiny'] == 713 and completion['excluded'] == 2):
        raise RuntimeError('unexpected final completion counts')
    ledger = ROOT/'experiments/fleet/ledger.md'
    with ledger.open('r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        content = ''.join(line for line in stream.read().splitlines(keepends=True)
                          if not line.startswith('| teacher-rest/d172 |'))
        stream.seek(0)
        stream.write(content)
        stream.truncate()
    with (ROOT/'experiments/fleet/history.md').open('a') as stream:
        stream.write(f'\n- teacher-rest/d172: 1,446 exported (733 small + 713 tiny); 2 leader-authorized finisher-timeout exclusions; '
                     f'blocking audit 30/30; resumed finish used 3 CPU workers, no GPU, '
                     f'{time.time()-started:.0f}s; completed {datetime.now().astimezone().isoformat(timespec="minutes")}. '
                     'Original 196-map teacher run used 5 CPU workers, 3 launches, 9,862s versus 60–100min estimate.\n')
    d151.write(RUN/'resume-finish-complete.json', dict(completion, finish_elapsed_s=time.time()-started,
                                                       finished_at=datetime.now().astimezone().isoformat()))
    (RUN/'RUNNING-EXCLUSION-RESUME.json').unlink(missing_ok=True)
    print(json.dumps(completion), flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        d151.write(RUN/'resume-finish-FAILED.json', dict(error=f'{type(error).__name__}: {error}',
                                                      traceback=traceback.format_exc()))
        raise

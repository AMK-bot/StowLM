"""D224 GPU chain, detached: wait for the reader v2 training process, score sealed v2 once, run reader v3 (4 h hard stop, sealed v3 once, adoption), check the adopted adapter
through brief's GlimmerParser, then 8.5a training and decode. Reader v3 and 8.5a run whatever v2's gates say. A failed step is logged and the chain goes on; no step retrains.
Usage: python -m experiments.mvp.reader.gpu_chain <v2_train_pid>
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PYTHON = str(ROOT/'.venv/bin/python')
V2 = 'experiments.mvp.reader.'
V3 = V2+'v3.'
LOG = HERE/'gpu-chain.log'
OUT = HERE/'gpu-chain.out'
K85A = ROOT/'experiments/mvp/strategy/k1b'
RUN = ROOT/'experiments/mvp/sft/stowlm-85a'
ADMISSION_WAIT_S = 2*3600
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'RAYON_NUM_THREADS')
ENV = {**os.environ, 'UV_CACHE_DIR': str(ROOT/'.uv-cache'), 'TOKENIZERS_PARALLELISM': 'false', 'PYTHONDONTWRITEBYTECODE': '1', **{k: '1' for k in THREADS}}
ENV.pop('STOWAGE_BALLAST_V4', None)


def cores():
    return subprocess.run(['sysctl', '-n', 'kern.sched_recommended_cores'], capture_output=True, text=True).stdout.strip()


def log(**fields):
    with open(LOG, 'a') as stream:
        stream.write(json.dumps(dict(at=datetime.now().astimezone().isoformat(timespec='seconds'), cores=cores(), **fields))+'\n')


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def step(*command):
    with open(OUT, 'a') as stream:
        code = subprocess.run([PYTHON, '-m', *command], cwd=ROOT, env=ENV, stdout=stream, stderr=subprocess.STDOUT).returncode
    log(event='step', command=list(command), code=code)
    return code


def wait_admission():
    """v3's CPU admission only reads the shared state; wait for it, and never bypass it."""
    from experiments.mvp.reader.v3 import train
    deadline = time.monotonic()+ADMISSION_WAIT_S
    while True:
        try:
            return train.check_admission()
        except ValueError as error:
            if time.monotonic() >= deadline:
                log(event='admission-timeout', error=str(error))
                return None
            time.sleep(30)


def steps(*commands):
    """In order, stopping at the first failure; True if all succeeded."""
    return all(step(*command) == 0 for command in commands)


def main():
    pid = int(sys.argv[1])
    while alive(pid):
        time.sleep(15)
    log(event='v2-training-ended')
    results = dict(v2=steps([V2+'gpu_run_v2', '--score-final', '--spark-summary', 'experiments/mvp/reader/sealed-v2/spark-summary.json'], [V2+'freeze_v2', '--stage', 'final', '--check']))
    (RUN/'reader-v2.DONE').write_text(json.dumps(dict(ok=results['v2'], at=datetime.now().astimezone().isoformat()), indent=1)+'\n')
    wait_admission()
    results['v3_train'] = steps([V3+'freeze', '--stage', 'recipe'], [V3+'freeze', '--stage', 'recipe', '--check'], [V3+'gpu_run', '--train-only'])
    results['v3_score'] = steps([V3+'gpu_run', '--score-final', '--spark-summary', 'experiments/mvp/reader/v3/sealed/spark-summary.json'], [V3+'freeze', '--stage', 'final', '--check'])
    results['parser'] = step(V2+'parser_check') == 0
    log(event='readers-done', results=results)
    with open(K85A/'k85a.log', 'a') as stream:
        code = subprocess.run([PYTHON, str(K85A/'k85a.py'), '--now', '--after-leader'], cwd=ROOT, env={**ENV, 'PYTHONPATH': f"{K85A}:{K85A.parent}"}, stdout=stream, stderr=subprocess.STDOUT).returncode
    log(event='k85a-ended', code=code)


if __name__ == '__main__':
    main()

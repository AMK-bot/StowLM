"""D183 K1b, detached and resumable. Stages: 8.2 16-sample decode with every candidate stored; two-step at 600 s finished, then
paired in COMPACT.md; model and baseline judged; K1B.md. The manifest is sealed before any stage and compared on every resume.
Usage: driver.py <twostep_pids.txt> [workers=4]
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import compact
import judge
import k1b_report

ROOT = HERE.parents[3]
PYTHON = str(ROOT/'.venv/bin/python')
ADAPTER = 'experiments/mvp/sft/stowlm-v2/adapter'
VAL = ('experiments/mvp/valsplit/audit_small30.txt', 'experiments/mvp/valsplit/reserve_small25.txt')
SHARDS = int(os.environ.get('K1B_GPU_SHARDS', 3))
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')
ENV = {**os.environ, 'STOWLM_MLX_CACHE_LIMIT_GB': '2', 'UV_CACHE_DIR': str(ROOT/'.uv-cache'), 'PYTHONPATH': f'{HERE}:{HERE.parent}', **{k: '1' for k in THREADS}}
ENV.pop('STOWAGE_BALLAST_V4', None)
FLAGS = ['--n-samples', '16', '--temperature', '0.7', '--greedy-first', '--mask-workers', '0', '--time-limit', '7200', '--d79', '--securing', '--lookahead',
         '--ballast-v4-finalize', '--backtrack', '32', '--candidate-finalize-s', '600', '--no-last-resort', '--no-repair']
SCRIPTS = (HERE/'driver.py', HERE/'judge.py', HERE/'k1b_report.py', HERE.parent/'compact.py', ROOT/'experiments/eval-c1/run_c1.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def log(text):
    print(f'{datetime.now():%H:%M:%S} {text}', flush=True)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def seal(files):
    code = ('import json,hashlib;from llm.exit.runner import source_pins;from llm.exit.replay import checker_epoch;'
            'print(json.dumps(dict(source_pins_sha256=hashlib.sha256(json.dumps(source_pins(),sort_keys=True).encode()).hexdigest(),checker_epoch=checker_epoch())))')
    identity = json.loads(subprocess.run([PYTHON, '-c', code], cwd=ROOT, env=ENV, capture_output=True, text=True, check=True).stdout.splitlines()[-1])
    manifest = dict(decision='D183', flags=FLAGS, shards=SHARDS, seed=0, finisher_s=compact.BUDGET_S, inputs={f: sha(ROOT/f) for f in sorted(files)},
                    adapter={p.name: sha(p) for p in sorted((ROOT/ADAPTER).iterdir()) if p.is_file()},
                    scripts={str(p.relative_to(ROOT)): sha(p) for p in SCRIPTS}, **identity)
    path = HERE/'manifest.json'
    if path.exists():
        if json.loads(path.read_text()) != manifest:
            raise SystemExit('manifest differs; refusing to resume')
    else:
        path.write_text(json.dumps(manifest, indent=1, sort_keys=True)+'\n')


def decode(files):
    groups = [sorted(files)[i::SHARDS] for i in range(SHARDS)]

    def one(i):
        (HERE/'decode').mkdir(exist_ok=True)
        with open(HERE/f'decode/s{i}.log', 'a') as stream:
            return subprocess.run([PYTHON, 'experiments/eval-c1/run_c1.py', '--run-dir', str(HERE/f'decode/s{i}'), '--adapter', ADAPTER, '--loose', *groups[i], *FLAGS,
                                   '--store-candidates', str(HERE/'candidates'), '--decode-only'], cwd=ROOT, env=ENV, stdout=stream, stderr=subprocess.STDOUT).returncode
    with ThreadPoolExecutor(SHARDS) as pool:
        codes = list(pool.map(one, range(SHARDS)))
    if any(codes):
        raise SystemExit(f'decode failed: {codes}')


def main():
    files = [line for name in VAL for line in (ROOT/name).read_text().split()]
    pids, workers = [int(p) for p in Path(sys.argv[1]).read_text().split()], int(sys.argv[2]) if len(sys.argv) > 2 else 4
    seal(files)
    if not (HERE/'decode.DONE').exists():
        decode(files)
        (HERE/'decode.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')
    log('decode done')
    while any(alive(p) for p in pids):
        time.sleep(30)
    k1b_report.pair_compact()
    log('COMPACT.md paired with two-step at 600 s')
    os.environ.update({k: '1' for k in THREADS})
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    jobs = judge.jobs(files)
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(judge.judge, jobs))
    log(f'judged {len(rows)}')
    k1b_report.write_report(HERE/'K1B.md', json.loads((HERE/'manifest.json').read_text()))
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith(('| k1b/', '| compact/'))))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- k1b/run (fix-t8-sft, 30 Oct): D183 decode, judge and report; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (HERE/'K1B.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()

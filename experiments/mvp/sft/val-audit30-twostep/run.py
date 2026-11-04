"""Two-step on the 30 val audit maps, shards under <budget>/ with ablation/twostep_val.py (D79 C2 off, V4 scoped to
finalize, one finisher attempt, full checker, no repair). CPU only, one thread per worker. Resumable.
Usage: uv run python experiments/mvp/sft/val-audit30-twostep/run.py <finalize_s> [workers=4]
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LIST = ROOT/'experiments/mvp/valsplit/audit_small30.txt'
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')


def main():
    budget, workers = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 4
    out = HERE/budget
    out.mkdir(exist_ok=True)
    paths = LIST.read_text().split()
    env = {**os.environ, **{k: '1' for k in THREADS}}
    env.pop('STOWAGE_BALLAST_V4', None)
    jobs = []
    for i in range(workers):
        shard = out/f'shard-{i}.txt'
        shard.write_text('\n'.join(paths[i::workers])+'\n')
        with open(out/f'worker-{i}.log', 'a') as log:
            jobs.append(subprocess.Popen([sys.executable, str(ROOT/'experiments/mvp/sft/ablation/twostep_val.py'),
                                          str(shard), str(out), budget], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
    (out/'workers.pid').write_text('\n'.join(str(j.pid) for j in jobs)+'\n')
    if any(j.wait() for j in jobs):
        raise SystemExit('a worker failed; see worker-*.log')
    (out/'DONE').write_text(f'{len(list(out.glob("*.json")))} maps\n')


if __name__ == '__main__':
    main()

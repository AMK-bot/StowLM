"""D152: two-step v4.4 on a frozen val list with the val-audit30-twostep recipe (ablation/twostep_val.py, finisher 120 s).
Usage: run_twostep.py <list.txt> <set_name> [workers=2]
"""
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')
paths = Path(sys.argv[1]).read_text().split()
assert all('/corpus/val/' in p for p in paths)
workers = int(sys.argv[3]) if len(sys.argv) > 3 else 2
out = HERE/'twostep'/sys.argv[2]
out.mkdir(parents=True, exist_ok=True)
env = {**os.environ, **{k: '1' for k in THREADS}}
env.pop('STOWAGE_BALLAST_V4', None)
jobs = []
for i in range(workers):
    shard = out/f'shard-{i}.txt'
    shard.write_text('\n'.join(paths[i::workers])+'\n')
    with open(out/f'worker-{i}.log', 'a') as log:
        jobs.append(subprocess.Popen([sys.executable, str(ROOT/'experiments/mvp/sft/ablation/twostep_val.py'), str(shard), str(out), '120'],
                                     cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
(out/'workers.pid').write_text('\n'.join(str(j.pid) for j in jobs)+'\n')
if any(j.wait() for j in jobs):
    raise SystemExit('a worker failed')
(out/'DONE').write_text(f'{len(list(out.glob("*.json")))} maps\n')

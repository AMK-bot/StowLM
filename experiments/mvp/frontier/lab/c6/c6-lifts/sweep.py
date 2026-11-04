"""Frozen paired batches through the unchanged lab/score.py CLI.

Two maps per batch; reverse arm order every batch to reduce temporal load bias.
Only score.py workers do numerical work. All child master PIDs are recorded.
"""
import hashlib
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LAB = HERE.parents[1]
STATE = HERE/'SWEEP_STATE.json'


def write(data):
    STATE.write_text(json.dumps(data, indent=2)+'\n')


def verify_freeze():
    manifest = json.loads((HERE/'FROZEN.json').read_text())
    for name, expected in manifest['sha256'].items():
        actual = hashlib.sha256((HERE/name).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f'Frozen source changed: {name}')


def run(set_name, list_path, arm, batch):
    verify_freeze()
    marker = LAB/'c1/leader/BENCH-RUNNING'
    while marker.exists():
        write(dict(stage='waiting_for_benchmark', set=set_name, arm=arm, batch=batch))
        print('Waiting for leader benchmark before starting two workers', flush=True)
        time.sleep(30)
    module = 'baseline.py' if arm == 'baseline' else 'method.py'
    out = HERE/'results'/f'{arm}-{set_name}'
    cmd = ['nice', '-n', '5', str(ROOT/'.venv/bin/python'), str(LAB/'score.py'),
           '--set', str(list_path), '--method', f'{HERE/module}:propose',
           '--finish', f'{HERE/module}:finish', '--budget-s', '240',
           '--finalize-s', '120', '--tries', '2', '--workers', '2', '--seed', '0', '--out', str(out)]
    process = subprocess.Popen(cmd, cwd=ROOT)
    with (HERE/'STARTED_PIDS.jsonl').open('a') as handle:
        handle.write(json.dumps(dict(pid=process.pid, set=set_name, arm=arm, batch=batch, command=cmd))+'\n')
    state = dict(stage='running', set=set_name, arm=arm, batch=batch, pid=process.pid, started=time.time())
    write(state)
    while True:
        try:
            code = process.wait(timeout=45)
            break
        except subprocess.TimeoutExpired:
            print(f'[sweep] {set_name} batch {batch} {arm}, {time.time()-state["started"]:.0f}s', flush=True)
    if code:
        raise RuntimeError(f'Score failed: {set_name}/{batch}/{arm}, status {code}')


def aggregate(set_name):
    sys.path.insert(0, str(LAB))
    from catalog import load_baseline
    from summary import summarize
    for arm in ('baseline', 'candidate'):
        out = HERE/'results'/f'{arm}-{set_name}'
        rows = []
        for path in sorted(out.glob('*.json')):
            data = json.loads(path.read_text())
            if isinstance(data, dict) and data.get('instance_id'):
                rows.append(data)
        (out/'summary.json').write_text(json.dumps(summarize(rows, load_baseline(), seed=0), indent=2)+'\n')
    command = [str(ROOT/'.venv/bin/python'), str(HERE/'compare.py'),
               '--baseline', str(HERE/'results'/f'baseline-{set_name}'),
               '--candidate', str(HERE/'results'/f'candidate-{set_name}'),
               '--out', str(HERE/'results'/f'paired-{set_name}.json')]
    subprocess.run(command, cwd=ROOT, check=True)


def bound_job(path):
    sys.path[:0] = [str(HERE), str(LAB)]
    from bound import lower_bound
    from catalog import ensure_safe
    from stowage.io import load_instance
    path = ensure_safe(path)
    row = lower_bound(load_instance(path), budget_s=15., seed=0)
    row.update(input=path, input_sha256=hashlib.sha256((ROOT/path).read_bytes()).hexdigest())
    return row


def bounds(paths, batch):
    primary = set((HERE/'primary28.txt').read_text().splitlines())
    out = HERE/'results/bounds-primary28'
    out.mkdir(parents=True, exist_ok=True)
    selected = []
    for path in paths:
        if path in primary:
            selected.append(path)
    if not selected:
        return
    while (LAB/'c1/leader/BENCH-RUNNING').exists():
        write(dict(stage='waiting_for_benchmark_before_bounds', batch=batch))
        time.sleep(30)
    write(dict(stage='bounds', batch=batch, inputs=selected))
    with ProcessPoolExecutor(2, mp_context=mp.get_context('spawn'), max_tasks_per_child=1) as pool:
        for future in as_completed([pool.submit(bound_job, path) for path in selected]):
            row = future.result()
            (out/f'{row["instance_id"]}.json').write_text(json.dumps(row, indent=2)+'\n')
            print(f'[bound] {row["instance_id"]}: {row["lower_bound"]}', flush=True)


def main():
    os.environ.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1',
                      MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1', STOWAGE_IN_PORT_WORKERS='1',
                      PYTHONDONTWRITEBYTECODE='1', STOWAGE_VESSEL_CACHE_DIR=str(HERE/'vessel-cache'),
                      PYTHONPATH=':'.join(map(str, [ROOT/'src', ROOT/'experiments/mvp/frontier', LAB, HERE])))
    for set_name, list_path in [('all48', HERE/'all48.txt'),
                               ('quick_tiny', LAB/'sets/quick_tiny.txt'),
                               ('quick_small', LAB/'sets/quick_small.txt')]:
        paths = [line for line in list_path.read_text().splitlines() if line and not line.startswith('#')]
        for batch, start in enumerate(range(0, len(paths), 2)):
            selection = HERE/'active-batch.txt'
            selection.write_text('\n'.join(paths[start:start+2])+'\n')
            arms = ('baseline', 'candidate') if batch % 2 == 0 else ('candidate', 'baseline')
            for arm in arms:
                run(set_name, selection, arm, batch)
            if set_name == 'all48':
                bounds(paths[start:start+2], batch)
        aggregate(set_name)
    verify_freeze()
    write(dict(stage='complete', finished=time.time()))


if __name__ == '__main__':
    main()

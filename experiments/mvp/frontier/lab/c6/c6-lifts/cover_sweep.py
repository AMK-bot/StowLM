"""Cold coupled c5/cover continuation, two workers and 300 added seconds/map."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
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
STATE = HERE/'COVER_SWEEP_STATE.json'


def state(**values):
    STATE.write_text(json.dumps(values, indent=2)+'\n')


def verify():
    for filename in ('FROZEN.json', 'COVER_FROZEN.json'):
        manifest = json.loads((HERE/filename).read_text())
        for name, expected in manifest['sha256'].items():
            if hashlib.sha256((HERE/name).read_bytes()).hexdigest() != expected:
                raise RuntimeError(f'Frozen source changed: {name}')


def workers():
    return 1 if (LAB/'c1/leader/BENCH-RUNNING').exists() else 2


def score_batch(set_name, paths, batch):
    verify()
    selection = HERE/'cover-active-batch.txt'
    selection.write_text('\n'.join(paths)+'\n')
    output = HERE/'results'/f'cover-{set_name}'
    os.environ['COVER_RUN_OUT'] = str(output)
    command = ['nice', '-n', '5', str(ROOT/'.venv/bin/python'), str(LAB/'score.py'),
               '--set', str(selection), '--method', f'{HERE / "cover_method.py"}:propose',
               '--finish', f'{HERE / "cover_method.py"}:finish', '--budget-s', '780',
               '--finalize-s', '1', '--tries', '1', '--workers', str(workers()),
               '--seed', '0', '--out', str(output/'candidate')]
    process = subprocess.Popen(command, cwd=ROOT)
    with (HERE/'COVER_STARTED_PIDS.jsonl').open('a') as handle:
        handle.write(json.dumps(dict(pid=process.pid, command=command, set=set_name, batch=batch))+'\n')
    state(stage='score', set=set_name, batch=batch, pid=process.pid, started=time.time())
    while True:
        try:
            code = process.wait(timeout=45)
            break
        except subprocess.TimeoutExpired:
            print(f'[cover sweep] {set_name} batch{batch} running', flush=True)
    if code:
        raise RuntimeError(f'Score exit {code}')


def bound_job(path):
    sys.path[:0] = [str(HERE), str(LAB)]
    from bound_cover import lower_bound
    from catalog import ensure_safe
    from stowage.io import load_instance
    path = ensure_safe(path)
    row = lower_bound(load_instance(path), budget_s=60., seed=0)
    row.update(input=path, input_sha256=hashlib.sha256((ROOT/path).read_bytes()).hexdigest())
    return row


def bounds(set_name, paths, batch):
    output = HERE/'results'/f'cover-{set_name}'
    selected = []
    for path in paths:
        # Only read current-run pass flags. The certificate reads only the map.
        for row_path in (output/'baseline').glob('*.json'):
            row = json.loads(row_path.read_text())
            if row.get('input') == path and row.get('passed'):
                target = output/'bounds'/f'{row["instance_id"]}.json'
                if not target.exists():
                    selected.append(path)
    if not selected:
        return
    verify()
    (output/'bounds').mkdir(parents=True, exist_ok=True)
    state(stage='bounds', set=set_name, batch=batch, inputs=selected)
    with ProcessPoolExecutor(workers(), mp_context=mp.get_context('spawn'), max_tasks_per_child=1) as pool:
        for future in as_completed([pool.submit(bound_job, p) for p in selected]):
            row = future.result()
            target = output/'bounds'/f'{row["instance_id"]}.json'
            target.write_text(json.dumps(row, indent=2)+'\n')
            print(f'[cover bound] {row["instance_id"]}: {row["lower_bound"]}', flush=True)


def main():
    os.environ.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1',
                      MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1', STOWAGE_IN_PORT_WORKERS='1',
                      PYTHONDONTWRITEBYTECODE='1', STOWAGE_VESSEL_CACHE_DIR=str(HERE/'vessel-cache'),
                      PYTHONPATH=':'.join(map(str, [ROOT/'src', ROOT/'experiments/mvp/frontier', LAB, HERE])))
    sets = [('all48', HERE/'all48.txt'), ('quick_tiny', LAB/'sets/quick_tiny.txt'),
            ('quick_small', LAB/'sets/quick_small.txt'), ('shared5', HERE/'shared5.txt')]
    for name, list_path in sets:
        paths = [p for p in list_path.read_text().splitlines() if p and not p.startswith('#')]
        for batch, start in enumerate(range(0, len(paths), 2)):
            selection = paths[start:start+2]
            score_batch(name, selection, batch)
            # Certificates for medium, and tiny certification controls.
            bounds(name, selection, batch)
        subprocess.run([str(ROOT/'.venv/bin/python'), str(HERE/'cover_report.py')], cwd=ROOT, check=True)
    verify()
    state(stage='complete', finished=datetime.now(timezone.utc).isoformat())


if __name__ == '__main__':
    main()

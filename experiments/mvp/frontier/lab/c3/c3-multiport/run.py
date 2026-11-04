"""Fresh TRAIN cohort runner; reuse score.evaluate, bypass only its path allowlist.

Inputs must be in this experiment's frozen cohort manifest. Every retained plan
still passes the unchanged score.evaluate independent stowage.checker.check.
Serial child processes isolate the reference's process-local monkeypatches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from time import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LAB = HERE.parents[1]
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
             'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
for path in (ROOT / 'src', LAB.parent, LAB, HERE):
    sys.path.insert(0, str(path))
import score

# Native ballast workers use multiprocessing.spawn and re-import this entry
# point. Record only our own child/parent PIDs, without inspecting other sessions.
if __name__ == '__mp_main__' and '--worker' in sys.argv:
    _out = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else 'results/main'
    _pid_output = (HERE / _out).resolve()
    _pid_output.relative_to(HERE)
    score.write_json(_pid_output / 'pids' / f'native-{os.getpid()}.json',
                     dict(pid=os.getpid(), parent_pid=os.getppid(), argv=sys.argv,
                          started=time(), kind='our native spawned worker'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def implementation(arm):
    if arm == 'frontier09':
        path = LAB / 'ref' / 'frontier09.py'
    elif arm == 'engine':
        path = HERE / 'frozen_engine' / 'lab_method.py'
    else:
        path = HERE / 'graph_method.py'
    return path


def worker(args):
    from stowage.io import load_instance
    path = (ROOT / args.input).resolve()
    path.relative_to(HERE / 'cohort')
    frozen = json.loads((HERE / 'cohort-manifest.json').read_text())
    entry = next((r for r in frozen['maps'] if (ROOT / r['path']).resolve() == path), None)
    if entry is None or digest(path) != entry['sha256']:
        raise ValueError('input is absent from the frozen cohort or its content changed')
    if (args.size, args.ports) != (entry['size'], entry['ports']):
        raise ValueError('input stratum does not match cohort manifest')
    instance = load_instance(path)
    if instance.id != entry['instance_id']:
        raise ValueError('instance ID differs from frozen cohort')
    arm = args.arm
    method_path = implementation(arm)
    budget = (min(120., max(20., 6 * len(instance.load_list))) if arm == 'frontier09'
              else (375. if args.size == 'medium' else 120.) if arm == 'engine'
              else (180. if args.size == 'medium' else 45. if args.size == 'small' else 15.))
    config = dict(input=args.input, seed=0, budget_s=budget, finalize_s=300., tries=6 if arm == 'graph' else 3,
                  method=score.plugin_info(f'{method_path}:propose'), order=None,
                  finish=score.plugin_info(f'{HERE / "graph_finish.py"}:finish' if arm == 'graph'
                                           else f'{method_path}:finish'))
    files = [method_path]
    if arm in ('engine', 'graph'):
        files += sorted((HERE / 'frozen_engine').rglob('*.py'))
    if arm == 'graph':
        files += [HERE / 'graph_model.py', HERE / 'graph_finish.py']
    code_hash = hashlib.sha256(''.join(digest(p) for p in files).encode()).hexdigest()
    config['implementation_sha256'] = code_hash
    config['native_sha256'] = score.source_sha256()
    config['runner_sha256'] = digest(HERE / 'run.py')
    config['cohort_sha256'] = digest(HERE / 'cohort-manifest.json')
    config['size'], config['ports'] = args.size, args.ports
    params = hashlib.sha256()
    for parameter in sorted(p for p in (ROOT / 'params').rglob('*') if p.is_file()):
        params.update(str(parameter.relative_to(ROOT)).encode())
        params.update(parameter.read_bytes())
    config['params_sha256'] = params.hexdigest()
    key = score.run_key(config, digest(path), {})
    output = (HERE / args.out).resolve()
    output.relative_to(HERE)
    score.write_json(output / 'pids' / f'{arm}-{instance.id}.json',
                     dict(pid=os.getpid(), parent_pid=os.getppid(), arm=arm,
                          instance_id=instance.id, started=time()))
    target = output / arm / f'{instance.id}.json'
    cached = score.cached_row(target, key)
    if cached is not None:
        print(json.dumps(dict(arm=arm, id=instance.id, cached=True)), flush=True)
        return
    print(json.dumps(dict(arm=arm, id=instance.id, pid=os.getpid(), started=time(), budget=budget)), flush=True)
    row = score.evaluate(instance, {'size': args.size}, config)
    meta = row.get('method_metadata', {})
    record = meta.get('frontier09', {}) if arm == 'frontier09' else meta.get('engine', {})
    replay = bool(row.get('tries'))
    if arm == 'frontier09':
        replay |= any(record.get(k) is not None for k in ('twostep_objective', 'blocked_objective'))
        replay |= bool(record.get('yard_first_built') or record.get('runs'))
        replay |= any(r.get('objective') is not None for r in record.get('masters', []))
    elif arm == 'engine':
        replay |= bool(record.get('replay_legal_stows'))
        replay |= '"replay_legal": true' in json.dumps(record)
    elif arm == 'graph':
        replay |= bool(meta.get('replay_legal_stows'))
    row.update(arm=arm, ports=args.ports, replay_legal_complete=replay, complete=True,
               run_key=key, config=config, input_sha256=digest(path))
    score.write_json(target, row)
    print(json.dumps(dict(arm=arm, id=instance.id, passed=row['passed'], lifts=row['lifts'],
                          wall_s=round(row['wall_s'], 2), reason=row.get('failure_reason'))), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--arm', choices=['frontier09', 'engine', 'graph'])
    parser.add_argument('--arms', default='frontier09,engine,graph')
    parser.add_argument('--input')
    parser.add_argument('--size')
    parser.add_argument('--ports', type=int)
    parser.add_argument('--out', default='results/main')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    manifest = json.loads((HERE / 'cohort-manifest.json').read_text())
    rows = manifest['maps']
    if len(rows) != 39:
        raise ValueError(f'cohort generation is incomplete: {len(rows)}/39 maps')
    if args.limit:
        rows = rows[:args.limit]
    output = (HERE / args.out).resolve()
    output.relative_to(HERE)
    output.mkdir(parents=True, exist_ok=True)
    score.write_json(output / 'process.json', dict(pid=os.getpid(), started=time(), workers=1,
                     reason_over_20_minutes='39 fresh maps × three full planners, historical search/finish floors retained',
                     benchmark_sentinel=str(LAB / 'c1/leader/BENCH-RUNNING')))
    for row in rows:
        for arm in args.arms.split(','):
            command = [sys.executable, str(HERE / 'run.py'), '--worker', '--arm', arm,
                       '--input', row['path'], '--size', row['size'], '--ports', str(row['ports']),
                       '--out', args.out]
            child = subprocess.Popen(command, cwd=ROOT, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            score.write_json(output / 'active-child.json', dict(pid=child.pid, arm=arm, input=row['path'], started=time()))
            status = child.wait()
            if status:
                raise RuntimeError(f'worker {child.pid} exited {status}: {arm} {row["path"]}')
            summarize = score.import_plugin(score.plugin_info(f'{HERE / "report.py"}:summarize'))
            summarize(output)
    score.write_json(output / 'complete.json', dict(finished=time(), maps=len(rows), arms=args.arms))


if __name__ == '__main__':
    main()

"""Run complete 21-map quick and fresh E9 control under one two-slot gate.

Each unchanged scorecard parent has two spawned workers. A common fcntl gate
admits at most two one-map calls across both pools. This is a shared observed
machine period, not a claim of matched load for each paired map. Source is
stamped before launch and must remain unchanged throughout both arms.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
FRONTIER = ENGINE.parent
ROOT = FRONTIER.parents[2]
LAB = FRONTIER/'lab'
MARKER = LAB/'c1/leader/BENCH-RUNNING'
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
           'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS')


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    temporary.replace(path)


def engine_manifest():
    return {str(path.relative_to(ENGINE)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(ENGINE.rglob('*')) if path.is_file()
            and (path.suffix in ('.py', '.toml') or path.name == 'approved-maps.json'
                 or str(path.relative_to(ENGINE)) in ('peel_ladder/copied-manifest.json',
                    'peel_ladder/assignment-frozen-hashes.json'))
            if 'results' not in path.relative_to(ENGINE).parts
            and not any(part.startswith('.pytest') for part in path.relative_to(ENGINE).parts)}


def control_sources():
    paths = list(FRONTIER.glob('*.py'))+list((ROOT/'src/stowage').rglob('*.py'))
    paths += list((ROOT/'params').rglob('*.toml'))
    paths += [LAB/'c1/leader/method_e9_val.py', LAB/'c1/leader/method_e9_base.py',
              LAB/'c1/leader/e9fix.py', LAB/'ref/frontier09.py',
              LAB/'score.py', LAB/'catalog.py', LAB/'summary.py']
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))}


def control_maps(load_set):
    paths = load_set('quick')
    if len(paths) != 21 or len(set(paths)) != 21:
        raise ValueError('both arms require exactly the fixed full 21-map quick set')
    return paths


def input_manifest(paths, ensure_safe):
    answer = {}
    for path in paths:
        relative = ensure_safe(path)
        answer[relative] = hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
    return answer


def command(arm, out, map_list):
    method = ENGINE/'lab_method.py' if arm == 'engine' else LAB/'c1/leader/method_e9_val.py'
    return ['nice', '-n', '5', str(ROOT/'.venv/bin/python'), str(HERE/'safe_score.py'),
            '--set', str(map_list), '--method', f'{method}:propose', '--finish', f'{method}:finish',
            '--budget-s', '900', '--finalize-s', '600' if arm == 'engine' else '300',
            '--tries', '3', '--seed', '0', '--workers', '2', '--out', str(out)]


def environment(arm, gate_dir):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.update({key: '1' for key in THREADS})
    env['STOWAGE_VESSEL_CACHE_DIR'] = str(ENGINE/'results/vessel-cache-0.13.0'/arm)
    env['PYTHONPATH'] = os.pathsep.join(str(path) for path in (ROOT/'src', FRONTIER, LAB, ROOT))
    env['FRONTIER_ADMISSION_DIR'] = str(gate_dir)
    env['FRONTIER_ADMISSION_ARM'] = arm
    env['FRONTIER_ADMISSION_MARKER'] = str(MARKER)
    return env


def load_proxy(running_arms):
    row = dict(unix_s=time.time(), monotonic_s=time.monotonic(),
               running_arms=sorted(running_arms), marker=MARKER.exists(),
               scope='os.getloadavg 1/5/15-minute system-load proxy; not a CPU-process count or per-map load match')
    try:
        row.update(status='measured_proxy', load_average=list(os.getloadavg()))
    except (OSError, AttributeError) as error:
        row.update(status='unavailable', load_average=None, error=f'{type(error).__name__}: {error}')
    return row


def _finish_arm(arm, child, record, outs, began):
    row = record['arms'][arm]
    if child.poll() is not None and row['phase'] == 'running':
        row.update(phase='complete' if child.returncode == 0 else 'failed',
                   returncode=child.returncode,
                   elapsed_s=time.monotonic()-began-row['launch_elapsed_s'])
        write(outs[arm]/'run.json', dict(row, controller_pid=os.getpid(),
              engine_bundle_digest=record['engine_bundle_digest']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--poll-s', type=float, default=5.)
    parser.add_argument('--engine-out', type=Path, default=ENGINE/'results/quick-0.13.0')
    parser.add_argument('--fresh-out', type=Path, default=ENGINE/'results/fresh-e9-quick-0.13.0')
    parser.add_argument('--record', type=Path, default=ENGINE/'results/quick-0.13.0-controller.json')
    parser.add_argument('--gate-dir', type=Path, default=ENGINE/'results/quick-0.13.0-admission')
    args = parser.parse_args(argv)
    if not 0. < args.poll_s <= 60.:
        parser.error('poll-s must be positive and at most sixty seconds')
    if MARKER.exists():
        parser.error('leader benchmark marker is active; refuse parent launch')
    outs = dict(engine=args.engine_out.resolve(), fresh_e9=args.fresh_out.resolve())
    gate_dir, controller_path = args.gate_dir.resolve(), args.record.resolve()
    if len(set(outs.values())) != 2 or any(not path.is_relative_to(ENGINE.resolve())
            for path in [*outs.values(), gate_dir, controller_path]):
        parser.error('distinct output directories, gate and record must remain inside engine/')
    if any(path.exists() and any(path.iterdir()) for path in [*outs.values(), gate_dir]):
        parser.error('use fresh output and gate directories; no silent row or lease reuse')
    if any(one.is_relative_to(two) or two.is_relative_to(one)
           for one, two in ((outs['engine'], outs['fresh_e9']), (outs['engine'], gate_dir),
                            (outs['fresh_e9'], gate_dir))):
        parser.error('output and gate directories must be disjoint')
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(FRONTIER), str(LAB)]
    from engine.safe_catalog import install as install_public_catalog
    install_public_catalog()
    from catalog import load_set, ensure_safe
    from engine.provenance import stamp_adapter, source_digest
    from engine.bench.admission import journal
    paths = control_maps(load_set)
    inputs = input_manifest(paths, ensure_safe)
    stamp_adapter()
    source, native_control = engine_manifest(), control_sources()
    source_digest_value = source_digest()
    write(ENGINE/'measured-source-manifest-0.13.0.json', source)
    map_list = ENGINE/'results/quick-0.13.0-maps.txt'
    map_list.parent.mkdir(parents=True, exist_ok=True)
    map_list.write_text(''.join(path+'\n' for path in paths))
    gate_dir.mkdir(parents=True, exist_ok=True)
    record = dict(controller_pid=os.getpid(), phase='prepared', busy_worker_cap=2,
        spawned_workers_per_arm=2, marker_admission_cap=1,
        marker_transition='Already admitted calls drain; a returning marker prevents second-slot new admissions',
        budget_s=900., engine_finalize_s=600., fresh_finalize_s=300., tries=3, seed=0,
        comparison_scope='Same full quick inputs and shared two-slot observed period; NOT per-map matched load. Historical stored 0.9 is unequal effort/load.',
        fresh_budget_rule='Original method_e9_val: min(120,max(20,6*n_loads)) search;300s V4;3 tries',
        arms={}, load_proxy_samples=[], quick_maps=paths, input_sha256=inputs,
        engine_source_sha256=source, engine_bundle_digest=source_digest_value,
        native_control_source_sha256=native_control, gate_dir=str(gate_dir),
        runtime_binding_sha256=hashlib.sha256(json.dumps(dict(engine=source,
            native_control=native_control, inputs=inputs), sort_keys=True).encode()).hexdigest(),
        manifest='experiments/mvp/frontier/engine/measured-source-manifest-0.13.0.json')
    children, logs = {}, []
    began = time.monotonic()
    write(controller_path, record)
    try:
        for arm, out in outs.items():
            if MARKER.exists():
                raise RuntimeError('benchmark marker appeared before all parent launches')
            out.mkdir(parents=True, exist_ok=True)
            log = (out/'score.log').open('w'); logs.append(log)
            launch = command(arm, out, map_list)
            child = subprocess.Popen(launch, cwd=ROOT, env=environment(arm, gate_dir),
                                     stdout=log, stderr=subprocess.STDOUT)
            children[arm] = child
            record['arms'][arm] = dict(score_pid=child.pid, command=launch, out=str(out),
                phase='running', launched_unix=time.time(), launch_elapsed_s=time.monotonic()-began)
            record['phase'] = 'running'
            write(controller_path, record)
            write(out/'run.json', dict(record['arms'][arm], controller_pid=os.getpid(),
                  engine_bundle_digest=source_digest_value))
            print(json.dumps(record['arms'][arm]), flush=True)
        while any(child.poll() is None for child in children.values()):
            record['load_proxy_samples'].append(load_proxy(
                [arm for arm, child in children.items() if child.poll() is None]))
            for arm, child in children.items():
                _finish_arm(arm, child, record, outs, began)
            record['admission'] = journal(gate_dir)
            write(controller_path, record)
            time.sleep(args.poll_s)
        for arm, child in children.items():
            _finish_arm(arm, child, record, outs, began)
        record['load_proxy_samples'].append(load_proxy([]))
        record['admission'] = journal(gate_dir)
        record.update(elapsed_s=time.monotonic()-began,
            source_unchanged=engine_manifest() == source and control_sources() == native_control,
            inputs_unchanged=input_manifest(paths, ensure_safe) == inputs)
        entries = [event for event in record['admission']['events'] if event['event'] == 'enter']
        record['admitted_maps_by_arm'] = {
            arm: sum(event['arm'] == arm for event in entries) for arm in outs}
        record['admission_verified'] = (all(count == len(paths)
            for count in record['admitted_maps_by_arm'].values())
            and record['admission']['max_admitted'] <= 2
            and not record['admission'].get('unfinished_leases'))
        record['shared_admitted_period_observed'] = record['admission']['overlap_s'] > 0.
        success = (record['source_unchanged'] and record['inputs_unchanged']
                   and all(child.returncode == 0 for child in children.values())
                   and record['admission_verified'])
        record['phase'] = 'complete' if success else 'failed'
    except BaseException as error:
        record.update(phase='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                      error=f'{type(error).__name__}: {error}', elapsed_s=time.monotonic()-began)
        # Only exact owned parents are signalled. Their executors own and drain
        # their already-running workers; no PID-pattern or descendant killing.
        for child in children.values():
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
        while any(child.poll() is None for child in children.values()):
            record['phase'] = 'draining_after_error'
            record['load_proxy_samples'].append(load_proxy(
                [arm for arm, child in children.items() if child.poll() is None]))
            record['admission'] = journal(gate_dir)
            write(controller_path, record)
            time.sleep(args.poll_s)
        record['phase'] = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
        raise
    finally:
        for log in logs:
            log.close()
        record['elapsed_s'] = time.monotonic()-began
        record['admission'] = journal(gate_dir)
        write(controller_path, record)
    print(json.dumps(dict(phase=record['phase'], elapsed_s=record['elapsed_s'],
                          source_unchanged=record.get('source_unchanged'),
                          inputs_unchanged=record.get('inputs_unchanged'),
                          admission=record['admission'], arms=record['arms'])), flush=True)
    return 0 if record['phase'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())

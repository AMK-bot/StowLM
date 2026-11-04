"""Wait for release, stamp 0.12, then run quick plus a concurrent fresh E9 arm.

Two scorecard parents each have two workers: at most four busy workers. This
controller neither enumerates processes nor starts load generators. Stop only
its recorded children if interrupted. Do not edit engine source after launch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
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
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    temporary.replace(path)


def engine_manifest():
    return {str(path.relative_to(ENGINE)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(ENGINE.rglob('*.py')) if 'results' not in path.relative_to(ENGINE).parts}


def control_sources():
    paths = list(FRONTIER.glob('*.py'))+list((ROOT/'src/stowage').rglob('*.py'))
    paths += [LAB/'c1/leader/method_e9_val.py', LAB/'c1/leader/method_e9_base.py',
              LAB/'c1/leader/e9fix.py', LAB/'ref/frontier09.py', LAB/'score.py', LAB/'catalog.py']
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))}


def control_maps(load_set):
    paths = load_set('quick_tiny')+load_set('quick_small')
    if len(paths) != 18 or len(set(paths)) != 18:
        raise ValueError('fresh control requires exactly the fixed 8 tiny + 10 small maps')
    return paths


def command(arm, out, control_list):
    method = ENGINE/'lab_method.py' if arm == 'engine' else LAB/'c1/leader/method_e9_val.py'
    return ['nice', '-n', '5', str(ROOT/'.venv/bin/python'), str(LAB/'score.py'),
            '--set', 'quick' if arm == 'engine' else str(control_list),
            '--method', f'{method}:propose', '--finish', f'{method}:finish',
            '--budget-s', '900', '--finalize-s', '600' if arm == 'engine' else '300',
            '--tries', '3', '--seed', '0', '--workers', '2', '--out', str(out)]


def environment(arm):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.update({key: '1' for key in THREADS})
    env['STOWAGE_VESSEL_CACHE_DIR'] = str(ENGINE/'results/vessel-cache-0.12.0'/arm)
    env['PYTHONPATH'] = os.pathsep.join(str(path) for path in (ROOT/'src', FRONTIER, LAB, ROOT))
    return env


def load_proxy(running_arms):
    """System load averages are telemetry, never process-count qualification."""
    row = dict(unix_s=time.time(), monotonic_s=time.monotonic(),
               running_arms=sorted(running_arms),
               scope='os.getloadavg 1/5/15-minute system-load proxy; NOT evidence of twelve other CPU-busy processes')
    try:
        row.update(status='measured_proxy', load_average=list(os.getloadavg()))
    except (OSError, AttributeError) as error:
        row.update(status='unavailable', load_average=None, error=f'{type(error).__name__}: {error}')
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--poll-s', type=float, default=5.)
    parser.add_argument('--engine-out', type=Path, default=ENGINE/'results/quick-0.12.0')
    parser.add_argument('--fresh-out', type=Path, default=ENGINE/'results/fresh-e9-quick-0.12.0')
    args = parser.parse_args(argv)
    if not 0. < args.poll_s <= 60.:
        parser.error('poll-s must be positive and at most sixty seconds')
    outs = dict(engine=args.engine_out.resolve(), fresh_e9=args.fresh_out.resolve())
    if len(set(outs.values())) != 2 or any(not path.is_relative_to(ENGINE.resolve()) for path in outs.values()):
        parser.error('distinct output directories must remain inside engine/')
    if any(path.exists() and any(path.iterdir()) for path in outs.values()):
        parser.error('use fresh output directories; this controller must not silently reuse scorecard rows')
    controller_path = ENGINE/'results/quick-0.12.0-controller.json'
    record = dict(controller_pid=os.getpid(), phase='waiting_for_leader', busy_workers=4,
                  workers_per_arm=2, budget_s=900., engine_finalize_s=600., tries=3, seed=0,
                  comparison_scope='fresh E9 control at concurrent observed machine period; historical rows are unequal effort/load',
                  fresh_budget_rule='original method_e9_val: min(120,max(20,6*n_loads)) search;300s V4;3 tries',
                  arms={}, load_proxy_samples=[])
    wait_started = time.monotonic()
    write(controller_path, record)
    print(json.dumps(record), flush=True)
    while MARKER.exists():
        time.sleep(args.poll_s)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(FRONTIER), str(LAB)]
    from catalog import load_set
    from engine.provenance import stamp_adapter, source_digest
    paths = control_maps(load_set)
    stamp_adapter()
    source = engine_manifest()
    native_control = control_sources()
    # Same plain path->SHA format as the existing 0.11 measured manifest.
    write(ENGINE/'measured-source-manifest-0.12.0.json', source)
    record.update(engine_source_sha256=source, engine_bundle_digest=source_digest(),
                  native_control_source_sha256=native_control,
                  manifest='experiments/mvp/frontier/engine/measured-source-manifest-0.12.0.json',
                  waited_s=time.monotonic()-wait_started)
    control_list = ENGINE/'results/fresh-e9-quick-0.12.0-maps.txt'
    control_list.write_text(''.join(path+'\n' for path in paths))
    record['fresh_maps'] = paths
    children, logs = {}, []
    began = time.monotonic()
    try:
        for arm, out in outs.items():
            if MARKER.exists():
                raise RuntimeError('benchmark marker returned before all arm admissions')
            out.mkdir(parents=True, exist_ok=True)
            log_path = out/'score.log'
            log = log_path.open('w'); logs.append(log)
            argv = command('engine' if arm == 'engine' else 'fresh_e9', out, control_list)
            child = subprocess.Popen(argv, cwd=ROOT, env=environment(arm), stdout=log, stderr=subprocess.STDOUT)
            children[arm] = child
            record['arms'][arm] = dict(score_pid=child.pid, command=argv, out=str(out),
                phase='running', launched_unix=time.time(), launch_elapsed_s=time.monotonic()-began)
            record['phase'] = 'running'
            write(controller_path, record)
            write(out/'run.json', dict(record['arms'][arm], controller_pid=os.getpid(),
                engine_bundle_digest=record['engine_bundle_digest']))
            print(json.dumps(record['arms'][arm]), flush=True)
        while any(child.poll() is None for child in children.values()):
            record['load_proxy_samples'].append(load_proxy(
                [arm for arm, child in children.items() if child.poll() is None]))
            for arm, child in children.items():
                row = record['arms'][arm]
                if child.poll() is not None and row['phase'] == 'running':
                    row.update(phase='complete' if child.returncode == 0 else 'failed',
                               returncode=child.returncode,
                               elapsed_s=time.monotonic()-began-row['launch_elapsed_s'])
                    write(outs[arm]/'run.json', dict(row, controller_pid=os.getpid(),
                          engine_bundle_digest=record['engine_bundle_digest']))
            write(controller_path, record)
            time.sleep(args.poll_s)
        for arm, child in children.items():
            row = record['arms'][arm]
            if row['phase'] == 'running':
                row.update(phase='complete' if child.returncode == 0 else 'failed',
                           returncode=child.returncode,
                           elapsed_s=time.monotonic()-began-row['launch_elapsed_s'])
            write(outs[arm]/'run.json', dict(row, controller_pid=os.getpid(),
                  engine_bundle_digest=record['engine_bundle_digest']))
        record['load_proxy_samples'].append(load_proxy([]))
        record.update(elapsed_s=time.monotonic()-began,
            source_unchanged=engine_manifest() == source and control_sources() == native_control)
        record['phase'] = 'complete' if record['source_unchanged'] and all(child.returncode == 0 for child in children.values()) else 'failed'
    except BaseException as error:
        record.update(phase='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                      error=f'{type(error).__name__}: {error}', elapsed_s=time.monotonic()-began)
        raise
    finally:
        # These are the exact Popen children started and recorded above.
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        for log in logs:
            log.close()
        write(controller_path, record)
    print(json.dumps(dict(phase=record['phase'], elapsed_s=record['elapsed_s'],
                          source_unchanged=record.get('source_unchanged'),
                          arms=record['arms'])), flush=True)
    print('Compare with bench/compare.py QUICK --engine11 --fresh-e9 FRESH; stored0.9 is historical unequal effort/load.', flush=True)
    return 0 if record['phase'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())

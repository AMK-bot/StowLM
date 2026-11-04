"""D192 TRAIN fill ladder: immutable CPU runtime, resumable paired full checks.

Three map workers plus their owned V4 children use at most six compute
processes. Admission can be reduced live with {"active_map_limit": 0..3}.
No experiment is launched by importing or checking this module.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import hashlib
import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
THREAD_VARS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
               'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS')
for _name in THREAD_VARS:
    os.environ[_name] = '1'
os.environ['STOWAGE_IN_PORT_WORKERS'] = '1'
CONFIG = dict(small_budget=120, medium_budget=300, small_finisher=600,
              medium_finisher=1200, planner_seed=0)
SHIP_ORDER = ('feeder_500', 'feeder_800', 'feeder_1700')
FILL_ORDER = (40, 60, 80, 50, 70)
MASS_ORDER = ('low', 'high')


def read_json(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.tmp-{os.getpid()}')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def manifest_jobs(path):
    manifest = read_json(path)
    if manifest.get('split') != 'train':
        raise ValueError('Only an explicit TRAIN manifest is admitted')
    jobs, keys = [], set()
    for item in manifest['maps']:
        row = dict(item)
        key = row['map_id']
        if not key or Path(key).name != key or key in keys:
            raise ValueError(f'Invalid/duplicate map_id: {key}')
        keys.add(key)
        if row.get('generation_status', 'accepted') != 'accepted':
            continue  # Kept in the frozen manifest/report planned denominator.
        value = row.get('input', row.get('path'))
        source = (ROOT / value).resolve()
        relative = source.relative_to(ROOT)
        if any(p in {'val', 'validation', 'test', 'showcase', 'archive'} for p in relative.parts):
            raise ValueError(f'Forbidden partition: {relative}')
        if not source.is_relative_to(HERE / 'maps' / 'train') or source.suffix != '.json':
            raise ValueError(f'Not a fillsweep TRAIN map: {relative}')
        expected = row.get('input_sha256', row.get('sha256'))
        if not expected or digest(source) != expected:
            raise ValueError(f'Input digest mismatch: {relative}')
        row.update(input=str(relative), input_sha256=expected)
        jobs.append(row)
    if not jobs:
        raise ValueError('No accepted TRAIN maps')
    return manifest, jobs


def scheduling_key(row):
    ship_index = SHIP_ORDER.index(row['ship'])
    replicate = int(row['seed']) - (192_200_000 + 100 * ship_index)
    if replicate not in range(3):
        raise ValueError('Scheduling requires preregistered root seeds')
    return (replicate, FILL_ORDER.index(int(row['fill_target'])),
            MASS_ORDER.index(row['mass_level']), ship_index)


def freeze_scheduling(out, rows):
    ordered = sorted(rows, key=scheduling_key)
    policy = dict(format='D192-metadata-breadth-schedule-v1',
                  ordering=['replicate', 'fill', 'mass', 'ship'],
                  replicate_order=[0, 1, 2], fill_order=list(FILL_ORDER),
                  mass_order=list(MASS_ORDER), ship_order=list(SHIP_ORDER),
                  map_ids=[row['map_id'] for row in ordered],
                  selection='Metadata only; preserve all grid cells, inputs and first committed outcomes')
    path = out / 'scheduling.json'
    if path.exists():
        previous = read_json(path)
        if any(previous.get(key) != value for key, value in policy.items()):
            raise RuntimeError('Frozen scheduling grid/policy changed')
    else:
        if (out / 'run.json').exists():
            raise RuntimeError('Cannot add scheduling after planner admission')
        atomic_json(path, dict(policy, frozen_at=time.time()))
    return digest(path)


def amend_runner(out, reason):
    if (out / 'run.json').exists():
        raise RuntimeError('Runner amendments are forbidden after planner admission')
    runtime = out / 'runtime'
    data = read_json(runtime / 'data-snapshot.json')
    path = runtime / 'runner-amendment.json'
    previous = read_json(path) if path.exists() else None
    record = dict(runner_sha256=digest(__file__), reason=reason, recorded_at=time.time(),
                  data_snapshot_sha256=digest(runtime / 'data-snapshot.json'),
                  initial_snapshot_sha256=digest(runtime / 'snapshot.json'),
                  prior_runner_sha256=previous['runner_sha256'] if previous else data['runner_sha256'])
    if previous:
        atomic_json(runtime / 'amendments' / f"{time.time_ns()}.json", previous)
    atomic_json(path, record)


def runtime_sources():
    suffixes = {'.py', '.toml', '.json', '.yaml', '.yml', '.csv', '.tsv', '.npz', '.npy'}
    files = {p for base in (ROOT / 'src', ROOT / 'params') for p in base.rglob('*')
             if p.is_file() and p.suffix in suffixes and '__pycache__' not in p.parts}
    for directory in ('blockrule', 'headroom'):
        files.update(p for p in (HERE.parent / directory).iterdir()
                     if p.is_file() and p.suffix in {'.py', '.json'})
    files.update((HERE.parent / 'compact.py', ROOT / 'pyproject.toml',
                  ROOT / 'experiments/mvp/corpus_tools/build.py'))
    if (ROOT / 'uv.lock').exists():
        files.add(ROOT / 'uv.lock')
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(files)}


def runtime_data_sources():
    """External hull bytes are inputs even though they live outside src/params."""
    required = ('data/raw/kcs/kcs.fix', 'data/raw/dtc/DTC-scaled.stl.gz')
    for relative in required:
        if not (ROOT / relative).is_file():
            raise RuntimeError(f'Missing required hull dependency: {relative}')
    files = {p for directory in ('data/raw/kcs', 'data/raw/dtc')
             for p in (ROOT / directory).rglob('*') if p.is_file()}
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(files)}


def freeze_data(runtime, original):
    certificate = runtime / 'data-snapshot.json'
    if certificate.exists():
        data = read_json(certificate)
        for relative, expected in data['sources'].items():
            if digest(runtime / relative) != expected:
                raise RuntimeError(f'Frozen external dependency changed: {relative}')
        if data['initial_snapshot_sha256'] != digest(runtime / 'snapshot.json'):
            raise RuntimeError('Initial runtime snapshot changed')
        amendment_path = runtime / 'runner-amendment.json'
        if amendment_path.exists():
            amendment = read_json(amendment_path)
            if (amendment['data_snapshot_sha256'] != digest(certificate)
                    or amendment['initial_snapshot_sha256'] != digest(runtime / 'snapshot.json')
                    or amendment['runner_sha256'] != digest(__file__)):
                raise RuntimeError('Runner amendment does not match pinned runtime/code')
            return dict(data, runner_sha256=amendment['runner_sha256'])
        if data['runner_sha256'] != digest(__file__):
            raise RuntimeError('Runner changed after supplemental runtime freeze')
        return data
    if (runtime.parent / 'run.json').exists():
        raise RuntimeError('Cannot add missing dependencies after planner admission')
    sources = runtime_data_sources()
    for relative in sources:
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    for relative, expected in sources.items():
        if digest(runtime / relative) != expected or digest(ROOT / relative) != expected:
            raise RuntimeError(f'External dependency changed while freezing: {relative}')
    data = dict(sources=sources, frozen_at=time.time(),
                initial_snapshot_sha256=digest(runtime / 'snapshot.json'),
                original_runner_sha256=original['runner_sha256'], runner_sha256=digest(__file__),
                amendment='Pin omitted external hull assets before any planner admission; original src/params snapshot retained')
    atomic_json(certificate, data)
    return data


def freeze_runtime(out):
    runtime = out / 'runtime'
    certificate = runtime / 'snapshot.json'
    if certificate.exists():
        record = read_json(certificate)
        for relative, expected in record['sources'].items():
            if digest(runtime / relative) != expected:
                raise RuntimeError(f'Frozen runtime changed: {relative}')
        data = freeze_data(runtime, record)
        record = dict(record, runner_sha256=data['runner_sha256'],
                      data_sources=data['sources'], data_snapshot_sha256=digest(runtime / 'data-snapshot.json'))
        return runtime, record
    sources = runtime_sources()
    for relative in sources:
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    for relative, expected in sources.items():
        if digest(runtime / relative) != expected or digest(ROOT / relative) != expected:
            raise RuntimeError(f'Source changed while freezing: {relative}; retry before launch')
    record = dict(sources=sources, frozen_at=time.time(),
                  runner_sha256=digest(__file__), python=sys.version)
    atomic_json(certificate, record)
    data = freeze_data(runtime, record)
    record = dict(record, runner_sha256=data['runner_sha256'],
                  data_sources=data['sources'], data_snapshot_sha256=digest(runtime / 'data-snapshot.json'))
    return runtime, record


def load_runtime(runtime):
    runtime = Path(runtime)
    helpers = runtime / 'experiments' / 'mvp' / 'strategy'
    sys.path[:0] = [str(helpers / 'blockrule'), str(helpers / 'headroom'),
                   str(helpers), str(runtime / 'src'), str(runtime)]
    study = importlib.import_module('study')
    # study/headroom bootstrap their own frozen paths. Reassert frozen src
    # precedence before loading any planner module; output/input paths stay real.
    sys.path[:0] = [str(runtime / 'src'), str(runtime)]
    study.ROOT = ROOT
    study.headroom.ROOT = ROOT
    return study


@contextmanager
def owned_children(folder):
    """Inventory only children owned by this multiprocessing parent."""
    stopped = threading.Event()
    def inventory():
        while not stopped.wait(1):
            for child in mp.active_children():
                if child.pid:
                    atomic_json(folder / 'owned' / f'{child.pid}.json',
                                dict(pid=child.pid, parent_pid=os.getpid(),
                                     role='owned solver child', observed_at=time.time()))
    thread = threading.Thread(target=inventory, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=2)


def harness_errors(arms):
    errors = []
    for arm, row in arms.items():
        if row.get('status') == 'exception':
            errors.append(dict(arm=arm, stage='planner', error=row.get('message', 'Planner exception')))
        for variant in ('raw', 'compact'):
            certificate = row.get(variant) or {}
            if certificate.get('error'):
                errors.append(dict(arm=arm, variant=variant, stage='certification', error=certificate['error']))
    return errors


def commit_completion(out, maps, errors):
    atomic_json(Path(out) / 'completion.json', dict(maps=maps, errors=errors, ended_at=time.time()))
    if errors:
        raise RuntimeError(f'Campaign has {len(errors)} harness failure(s); see completion.json. Committed outcomes retained.')


def run_map(job, out, config, runtime):
    study = load_runtime(runtime)
    from layout import BlockModel, _HOOK_MODULES
    from capacity_candidate import capacity_relaxed_layout
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.solver.ballast_deadline import shutdown_worker
    out = Path(out)
    folder = out / 'maps' / job['map_id']
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'summary.json').exists():
        previous = read_json(folder / 'summary.json')
        if previous.get('complete'):
            return previous
        atomic_json(folder / 'interruptions' / f'{time.time_ns()}.json',
                    dict(previous_summary_sha256=digest(folder / 'summary.json'),
                         previous_pid=previous.get('pid'), resumed_at=time.time(),
                         previous_error=previous.get('error'),
                         policy='Reuse every committed eval/check; retry only interrupted uncommitted work'))
    atomic_json(folder / 'worker.json', dict(pid=os.getpid(), started_at=time.time()))
    atomic_json(out / 'owned' / f'{os.getpid()}.json',
                dict(pid=os.getpid(), role='map worker', observed_at=time.time()))
    summary = dict(job=job, pid=os.getpid(), complete=False, arms={}, selected={})
    def save():
        errors = harness_errors(summary['arms'])
        if errors:
            summary.update(harness_errors=errors, complete=False)
            summary.setdefault('error', f'{len(errors)} committed harness failure(s); see harness_errors and retained eval/check artifacts')
        summary['updated_at'] = time.time()
        atomic_json(folder / 'summary.json', summary)
    try:
        if digest(ROOT / job['input']) != job['input_sha256']:
            raise ValueError('Input changed after preregistration')
        instance = load_instance(ROOT / job['input'])
        if instance.size_class != 'medium':
            raise ValueError('Only medium-size decision regions are admitted')
        model = BlockModel.from_instance(instance)
        for module in _HOOK_MODULES:
            importlib.import_module(module)
        with owned_children(folder), rule_freeze_v1(enabled=False):
            layout = capacity_relaxed_layout(instance, model=model)
            atomic_json(folder / 'layout.json', layout.to_dict())
            for arm, restriction in (('default', None), ('rule', layout)):
                row = study.headroom.evaluate(instance, model, restriction, arm, arm, folder, config)
                # Uncertified internal costs are deliberately absent from the
                # study outputs; all exposed move counts come from full checks.
                row.pop('raw_moves', None)
                atomic_json(folder / 'evals' / f'{arm}.json', row)
                summary['arms'][arm] = row
                save()
                for variant in ('raw', 'compact'):
                    row[variant] = study.certify(instance, model, row, folder,
                                                 config['medium_finisher'], variant)
                    certificate = row[variant]
                    for key in ('finished_path', 'report_path'):
                        if certificate.get(key):
                            certificate[key.replace('_path', '_sha256')] = digest(ROOT / certificate[key])
                    atomic_json(folder / 'checks' / f'{arm}-{variant}.json', certificate)
                    save()
            default, rule = summary['arms']['default'], summary['arms']['rule']
            for variant in ('raw', 'compact'):
                base = default['raw'] if variant == 'raw' else study.best_variant(default['raw'], default['compact'])
                restricted = rule['raw'] if variant == 'raw' else study.best_variant(rule['raw'], rule['compact'])
                summary['selected'][variant] = dict(default=base, rule=restricted,
                                                     planner=study.choose(base, restricted))
            summary['complete'] = not summary.get('error')
    except Exception as error:
        summary.update(error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
    finally:
        shutdown_worker()
        save()
    return summary


def judging_phases():
    base = HERE.parent / 'k1b'
    return [dict(arm=arm, cpu_judging=(base / arm / 'decode.DONE').exists()
                 and not (base / arm / done).exists())
            for arm, done in (('null', 'NULL.DONE'), ('k1c', 'K1C.DONE'))]


def admission_limit(path, workers, load_limit):
    limit = workers
    if path.exists():
        limit = min(limit, int(read_json(path)['active_map_limit']))
    if not 0 <= limit <= 3:
        raise ValueError('Admission active_map_limit must be in 0..3')
    if any(row['cpu_judging'] for row in judging_phases()) or os.getloadavg()[0] > load_limit:
        return 0
    return limit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=HERE / 'cohort.json')
    parser.add_argument('--out', type=Path, default=HERE / 'runs' / 'main')
    parser.add_argument('--workers', type=int, choices=(1, 2, 3), default=3)
    parser.add_argument('--admission-file', type=Path, default=HERE / 'control.json')
    parser.add_argument('--load-limit', type=float, default=1.5 * (os.cpu_count() or 1))
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--freeze-only', action='store_true', help='Pin runtime without loading any input or running a planner')
    parser.add_argument('--amend-runner', help='Record an explicitly authorized runner amendment before planner admission')
    args = parser.parse_args(argv)
    if args.load_limit <= 0:
        parser.error('Load limit must be positive')
    out = args.out.resolve()
    if not out.is_relative_to(HERE / 'runs'):
        parser.error('Output must be within fillsweep/runs')
    if args.amend_runner:
        amend_runner(out, args.amend_runner)
    if args.freeze_only:
        runtime, snapshot = freeze_runtime(out)
        if args.manifest.exists():
            freeze_scheduling(out, read_json(args.manifest)['maps'])
        print(json.dumps(dict(runtime=str(runtime), source_files=len(snapshot['sources']))))
        return
    manifest, jobs = manifest_jobs(args.manifest)
    jobs.sort(key=scheduling_key)
    if not (HERE / 'PREREG.md').exists():
        raise RuntimeError('PREREG.md must exist before any run')
    if args.dry_run:
        print(json.dumps(dict(maps=len(jobs), planned=len(manifest['maps']), config=CONFIG,
                              max_compute_processes=2*args.workers), indent=2))
        return
    out.mkdir(parents=True, exist_ok=True)
    if args.detach:
        command = [sys.executable, str(Path(__file__).resolve()),
                   *[value for value in sys.argv[1:] if value != '--detach']]
        with (out / 'controller.log').open('a') as log:
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        atomic_json(out / 'launch.json', dict(pid=process.pid, command=command, started_at=time.time()))
        print(json.dumps(dict(pid=process.pid, out=str(out))))
        return
    # The lightweight helper is itself frozen before controller/worker imports.
    runtime, snapshot = freeze_runtime(out)
    scheduling_sha256 = freeze_scheduling(out, manifest['maps'])
    study = load_runtime(runtime)
    record = dict(config=CONFIG, jobs=jobs, split='train', manifest_sha256=digest(args.manifest),
                  prereg_sha256=digest(HERE / 'PREREG.md'), sources=snapshot['sources'],
                  runner_sha256=snapshot['runner_sha256'], runtime=str(runtime.relative_to(ROOT)),
                  data_sources=snapshot['data_sources'], data_snapshot_sha256=snapshot['data_snapshot_sha256'],
                  scheduling_sha256=scheduling_sha256,
                  runner_amendment_sha256=digest(runtime / 'runner-amendment.json')
                      if (runtime / 'runner-amendment.json').exists() else None,
                  rule_freeze_v1=False, feasibility_extra={'global_screen': 0},
                  cpu_only=True, no_training=True, max_map_workers=args.workers,
                  max_compute_processes=2*args.workers, native_threads={v: '1' for v in THREAD_VARS})
    with study.headroom.controller_lock(out):
        if (out / 'run.json').exists() and read_json(out / 'run.json') != record:
            raise RuntimeError('Frozen run configuration changed; use a new output directory')
        atomic_json(out / 'run.json', record)
        atomic_json(out / 'controller.json', dict(pid=os.getpid(), workers=args.workers,
                    started_at=time.time(), admission_file=str(args.admission_file), load_limit=args.load_limit))
        pending = list(jobs)
        errors = []
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context('spawn')) as pool:
            futures = {}
            while pending or futures:
                limit = admission_limit(args.admission_file, args.workers, args.load_limit)
                while pending and len(futures) < limit:
                    job = pending.pop(0)
                    futures[pool.submit(run_map, job, str(out), CONFIG, str(runtime))] = job
                atomic_json(out / 'admission.json', dict(active_maps=len(futures), active_limit=limit,
                            pending=len(pending), loadavg=os.getloadavg()[0], updated_at=time.time(),
                            judging_phases=judging_phases(),
                            max_compute_processes=2*args.workers))
                if not futures:
                    time.sleep(10)
                    continue
                completed, _ = wait(futures, timeout=10, return_when=FIRST_COMPLETED)
                for future in completed:
                    job = futures.pop(future)
                    try:
                        result = future.result()
                        error = result.get('error')
                        complete = result.get('complete', False)
                        if not complete and not error:
                            error = 'Map returned incomplete without a diagnostic'
                    except Exception as error_object:
                        error = f'{type(error_object).__name__}: {error_object}'
                        complete = False
                    if error:
                        errors.append(dict(map_id=job['map_id'], error=error))
                    print(json.dumps(dict(map_id=job['map_id'], complete=complete, error=error)), flush=True)
        commit_completion(out, len(jobs), errors)


if __name__ == '__main__':
    main()

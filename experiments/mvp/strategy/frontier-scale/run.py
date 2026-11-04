"""Unedited Frontier 0.9.0 calls, passive timing, and fresh-process certification.

Only this experiment's frozen TRAIN inputs and runtime are admitted. A raw
planner outcome is checkpointed before audits so resume never repeats it.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import functools
import hashlib
import json
import multiprocessing.process
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.dont_write_bytecode = True
THREAD_VARS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
               'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS',
               'BLIS_NUM_THREADS', 'NUMBA_NUM_THREADS')
for name in THREAD_VARS:
    os.environ[name] = '1'
os.environ['STOWAGE_IN_PORT_WORKERS'] = '1'
os.environ['UV_CACHE_DIR'] = str(ROOT / '.uv-cache')
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['CUDA_VISIBLE_DEVICES'] = ''


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.tmp-{os.getpid()}')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
    tmp.replace(path)


def activate():
    from snapshot import activate_runtime, verify_snapshot
    verify_snapshot()
    activate_runtime()
    return HERE / 'runtime'


@contextmanager
def record_owned_processes(folder):
    """Record exact processes created by the finisher before any owned cleanup."""
    original = multiprocessing.process.BaseProcess.start

    @functools.wraps(original)
    def start(process, *args, **kwargs):
        result = original(process, *args, **kwargs)
        write(folder / 'owned' / f'{process.pid}.json',
              dict(pid=process.pid, parent_pid=os.getpid(), name=process.name,
                   started_at=now(), role='owned multiprocessing child'))
        return result

    multiprocessing.process.BaseProcess.start = start
    try:
        yield
    finally:
        multiprocessing.process.BaseProcess.start = original


class Timings:
    def __init__(self, folder, metadata):
        self.folder = folder
        self.metadata = metadata
        self.events = []
        self.candidates = []
        self.context = dict(label=None, supplemental=False, blocked=False)
        self.restore = []

    def save(self, active=None):
        write(self.folder / 'progress.json', dict(
            pid=os.getpid(), updated_at=now(), active_stage=active,
            stage_events=self.events, candidates=self.candidates))

    def event(self, stage, began, budget=None, status=None, limit=None, **details):
        self.events.append(dict(stage=stage, seconds=round(time.perf_counter()-began, 6),
                                budget_s=budget, status=status, limit=limit,
                                details={**self.context, **details}))
        self.save()

    def patch(self, obj, attr, function):
        self.restore.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, function)

    def capture(self, result, finish_row, label):
        if result is None or not finish_row.get('passed'):
            return
        path = self.folder / 'candidates' / f'{len(self.candidates):03d}-{label}.json'
        row = {**self.metadata, 'passed': True, 'weighted': finish_row['weighted'],
               'label': label, 'plan': json.loads(result.plan.model_dump_json())}
        write(path, row)
        self.candidates.append(dict(label=label, path=str(path), sha256=digest(path),
                                    weighted=row['weighted']))
        self.save()

    def install(self, planner):
        import columns
        import master
        problem_original = planner.Problem.__init__

        @functools.wraps(problem_original)
        def problem_init(owner, *args, **kwargs):
            began = time.perf_counter()
            self.save('initialization')
            result = problem_original(owner, *args, **kwargs)
            self.event('initialization', began, status='returned')
            return result

        self.patch(planner.Problem, '__init__', problem_init)
        two_original = planner.TwoStepPlanner.plan

        @functools.wraps(two_original)
        def two(owner, *args, **kwargs):
            if self.context['blocked']:
                return two_original(owner, *args, **kwargs)
            began = time.perf_counter()
            self.save('twostep_plan')
            result = two_original(owner, *args, **kwargs)
            metrics = dict(result.metrics)
            timed = result.status == 'timeout' or metrics.get('preplan_status') == 'timeout'
            self.event('twostep_plan', began, kwargs.get('time_limit_s'), result.status,
                       'time_limit' if timed else None, message=result.message, metrics=metrics)
            return result

        self.patch(planner.TwoStepPlanner, 'plan', two)
        blocked_original = planner.blocked_plan

        @functools.wraps(blocked_original)
        def blocked(*args, **kwargs):
            began = time.perf_counter()
            previous = self.context['blocked']
            self.context['blocked'] = True
            self.save('blocked_plan')
            try:
                result = blocked_original(*args, **kwargs)
            finally:
                self.context['blocked'] = previous
            metrics = dict(result.metrics)
            timed = result.status == 'timeout' or metrics.get('preplan_status') == 'timeout'
            self.event('blocked_plan', began, kwargs.get('time_limit_s'), result.status,
                       'time_limit' if timed else None, message=result.message, metrics=metrics)
            return result

        self.patch(planner, 'blocked_plan', blocked)
        compact_original = planner.compacted

        @functools.wraps(compact_original)
        def compact(*args, **kwargs):
            began = time.perf_counter()
            self.save('compaction')
            result = compact_original(*args, **kwargs)
            self.event('compaction', began, status='returned')
            return result

        self.patch(planner, 'compacted', compact)
        yard_original = planner._yard_first

        @functools.wraps(yard_original)
        def yard(*args, **kwargs):
            began = time.perf_counter()
            self.save('yard_first')
            result = yard_original(*args, **kwargs)
            _, attempts = result
            limit = 'rounds_limit' if attempts >= kwargs['rounds'] else 'time_limit'
            self.event('yard_first', began, kwargs['time_limit_s'], 'returned', limit,
                       attempts=attempts, max_rounds=kwargs['rounds'], built=len(result[0]))
            return result

        self.patch(planner, '_yard_first', yard)

        def model_wrapper(original, stage):
            @functools.wraps(original)
            def model(*args, **kwargs):
                began = time.perf_counter()
                self.save(stage)
                result = original(*args, **kwargs)
                status = result.status
                if status == 'time_limit':
                    limit = 'time_limit'
                elif status in {'UNKNOWN', 'FEASIBLE'}:
                    limit = 'solver_unproven_budget'
                elif status == 'cuts_exhausted':
                    limit = 'cuts_limit'
                elif status in {'OPTIMAL', 'INFEASIBLE', 'no_order'}:
                    limit = None
                else:
                    limit = 'unknown_termination'
                details = {key: getattr(result, key) for key in ('objective', 'bound', 'cuts', 'unplaced', 'variables')
                           if hasattr(result, key)}
                self.event(stage, began, kwargs['time_limit_s'], status, limit,
                           deck_tiers=kwargs.get('deck_tiers'), solver_workers=kwargs.get('workers'), **details)
                return result
            return model

        self.patch(planner, 'solve_master', model_wrapper(planner.solve_master, 'master'))
        self.patch(columns, 'solve_columns', model_wrapper(columns.solve_columns, 'columns'))

        def solver_wrapper(original, stage):
            @functools.wraps(original)
            def solve(owner, *args, **kwargs):
                began = time.perf_counter()
                result = original(owner, *args, **kwargs)
                status = result[1]
                limit = 'solver_unproven_budget' if status in {'UNKNOWN', 'FEASIBLE'} else None
                self.event(stage, began, kwargs['time_limit_s'], status, limit,
                           nested=True, solver_workers=kwargs.get('workers'))
                return result
            return solve

        self.patch(master.MasterModel, 'solve', solver_wrapper(master.MasterModel.solve, 'slot_solver'))
        self.patch(columns.ColumnPlanner, 'solve', solver_wrapper(columns.ColumnPlanner.solve, 'column_solver'))
        search_original = planner.search

        @functools.wraps(search_original)
        def search(*args, **kwargs):
            began = time.perf_counter()
            self.save('search')
            result = search_original(*args, **kwargs)
            self.event('search', began, kwargs['time_limit_s'], 'deadline', 'time_limit',
                       iterations=result[1].iterations, best=result[0][0].objective)
            return result

        self.patch(planner, 'search', search)
        finalize_original = planner.finalize

        @functools.wraps(finalize_original)
        def finalize(*args, **kwargs):
            began = time.perf_counter()
            self.save('finalize')
            result = finalize_original(*args, **kwargs)
            metrics = dict(result.metrics)
            cause = str(metrics.get('not_found_cause', ''))
            if cause.startswith('timeout:'):
                limit = 'time_limit'
            elif cause.startswith('search:bounded_exhaustion'):
                limit = 'candidate_exhaustion'
            elif metrics.get('ballast_status') == 'infeasible_proven':
                limit = None
            elif cause.startswith('unknown:') or (result.status != 'ok' and not cause):
                limit = 'unknown_termination'
            else:
                limit = None
            self.event('finalize', began, kwargs['time_limit_s'], result.status, limit,
                       metrics=metrics, message=result.message)
            return result

        self.patch(planner, 'finalize', finalize)
        check_original = planner.check

        @functools.wraps(check_original)
        def check(*args, **kwargs):
            began = time.perf_counter()
            self.save('check')
            result = check_original(*args, **kwargs)
            self.event('check', began, status='passed' if result.passed else 'failed',
                       weighted=float(result.count.weighted_nonproductive) if result.count is not None else None)
            return result

        self.patch(planner, 'check', check)
        finish_original = planner._finish

        @functools.wraps(finish_original)
        def finish(*args, **kwargs):
            previous = self.context['label']
            self.context['label'] = kwargs['label']
            try:
                result, row = finish_original(*args, **kwargs)
                self.capture(result, row, kwargs['label'])
                return result, row
            finally:
                self.context['label'] = previous

        self.patch(planner, '_finish', finish)

    def uninstall(self):
        for obj, attr, original in reversed(self.restore):
            setattr(obj, attr, original)


def audited(row):
    return (all(row.get(k) is True for k in ('passed', 'legal', 'mask_ok', 'complete', 'all_loaded'))
            and row.get('weighted') == row.get('stored'))


def audit_one(candidate, target):
    runtime = activate()
    # audit.py imports no planner and invokes check/apply_plan/mask replay only.
    sys.path.insert(0, str(runtime / 'experiments/mvp/frontier'))
    from audit import one
    started = time.perf_counter()
    try:
        row = one(str(candidate))
        row.update(label=read(candidate)['label'], candidate_sha256=digest(candidate),
                   auditor_pid=os.getpid(), audited_at=now(), fresh_process=True,
                   no_repair=True, audit_wall_s=time.perf_counter()-started)
    except Exception as error:
        row = dict(label=read(candidate)['label'], passed=False, error=f'{type(error).__name__}: {error}',
                   traceback=traceback.format_exc(), candidate_sha256=digest(candidate),
                   auditor_pid=os.getpid(), fresh_process=True, no_repair=True)
    write(target, row)


def run_audits(folder, row):
    started = time.perf_counter()
    audits = []
    for index, item in enumerate(row['candidates']):
        candidate = Path(item['path'])
        assert digest(candidate) == item['sha256']
        target = folder / 'audits' / f'{index:03d}-{item["label"]}.json'
        if target.exists():
            result = read(target)
            assert result['candidate_sha256'] == item['sha256']
        else:
            command = [shutil.which('uv'), 'run', '--offline', '--no-sync', 'python',
                       str(Path(__file__).resolve()), 'audit', '--candidate', str(candidate), '--output', str(target)]
            with (folder / 'audit.log').open('a') as stream:
                child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                         stdout=stream, stderr=subprocess.STDOUT)
                write(folder / 'owned' / f'{child.pid}.json',
                      dict(pid=child.pid, parent_pid=os.getpid(), command=command, started_at=now(), role='fresh auditor'))
                code = child.wait()
            if code or not target.exists():
                raise RuntimeError(f'Auditor {child.pid} exited {code}; resume audits without rerunning planner')
            result = read(target)
        audits.append(result)
    row['audits'] = audits
    row['audit_wall_s'] = round(time.perf_counter()-started, 6)
    row['audited_passed'] = bool(row.get('passed')) and any(a['label'] == 'final' and audited(a) for a in audits)
    row['audit_failures'] = [a for a in audits if not audited(a)]
    row['complete'] = True
    row['completed_at'] = now()
    row['total_wall_s'] = row['wall_s'] + row.get('supplemental_wall_s', 0.) + row['audit_wall_s']
    write(folder / 'result.json', row)


def work(budget, map_id):
    manifest = read(HERE / 'manifest.json')
    job = next(j for j in manifest['jobs'] if j['map_id'] == map_id)
    if budget == 'x4':
        assert map_id in manifest['budget_x4_map_ids']
    folder = HERE / 'runs' / budget / map_id
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'result.json').exists():
        return
    if (folder / 'raw.json').exists():
        row = read(folder / 'raw.json')
        assert row['manifest_sha256'] == digest(HERE / 'manifest.json')
        run_audits(folder, row)
        return
    runtime = activate()
    import planner
    from run_maps import source_sha256
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    assert planner.VERSION == manifest['frontier_version'] == '0.9.0'
    assert source_sha256() == manifest['source_sha256']
    assert not rule_freeze_v1_enabled(), 'Frontier 0.9 runs the legacy rule catalogue (no resequencing/repair)'
    assert digest(runtime / job['input']) == job['input_sha256']
    assert digest(ROOT / job['input']) == job['input_sha256']
    instance = load_instance(runtime / job['input'])
    assert len(instance.load_list) == job['n_boxes']
    settings = manifest['budget_x4' if budget == 'x4' else 'base_budget']
    search_s = settings['search_seconds_per_box'] * job['n_boxes']
    finalize_s = settings['finisher_seconds_per_start']
    row = dict(instance_id=instance.id, input=job['input'], input_sha256=job['input_sha256'],
               n_boxes=job['n_boxes'], budget=budget, route=job['route'],
               search_s=search_s, finalize_s=finalize_s, seed=0,
               version=planner.VERSION, source_sha256=source_sha256(),
               manifest_sha256=digest(HERE / 'manifest.json'),
               runtime_snapshot_sha256=digest(HERE / 'runtime-snapshot.json'),
               worker_pid=os.getpid(), started_at=now(), passed=False)
    metadata = {k: row[k] for k in ('instance_id', 'input', 'input_sha256')}
    timings = Timings(folder, metadata)
    timings.install(planner)
    began = time.perf_counter()
    try:
        with record_owned_processes(folder):
            result, record = getattr(planner, job['route'])(
                instance, search_s=search_s, finalize_s=finalize_s, seed=0)
            row['wall_s'] = round(time.perf_counter()-began, 6)
            row.update(record)
            row['passed'] = result is not None
            if result is not None:
                winning = next(t for t in reversed(record['tries']) if t.get('passed'))
                row['weighted'] = winning['weighted']
                row['plan'] = json.loads(result.plan.model_dump_json())
                from tables import origin
                analysis = dict(row)
                if job['route'] == 'plan_big' and winning['label'] == 'big':
                    analysis['runs'] = [dict(start=record['start'], start_objective=record['start_objective'],
                                             best=winning['search_objective'])]
                    row['origin_normalization'] = dict(
                        reason='plan_big omits runs; adapt the returned candidate for unchanged tables.origin',
                        runs=analysis['runs'])
                row['origin'] = origin(analysis)
                timings.capture(result, winning, 'final')
            if job['route'] == 'plan_big':
                # This comparator is outside the unchanged route and cannot be
                # chosen as Frontier's fallback or influence its search.
                timings.context['supplemental'] = True
                extra = time.perf_counter()
                try:
                    from stowage.feasibility import FeasibilityConfig
                    blocked = planner.blocked_plan(instance, seed=0,
                        config=FeasibilityConfig(extra={'global_screen': 0}), time_limit_s=240)
                    row['blocked_status'] = blocked.status
                    row['blocked_baseline_note'] = 'supplemental same-run baseline; 240s construction, same finisher; not fed into Frontier'
                    if blocked.plan is not None:
                        compacted = planner.compacted(instance, blocked.plan)
                        problem = planner.Problem(instance)
                        scored = problem.score(tuple(problem.space.from_plan(instance, compacted)))
                        if scored is not None:
                            row['blocked_objective'] = scored.objective
                            _, finished = planner._finish(instance, scored.plan, seed=0,
                                                          finalize_s=finalize_s, label='blocked')
                            row['blocked_finish'] = dict(finished, search_objective=scored.objective)
                except Exception as error:
                    row['supplemental_error'] = f'{type(error).__name__}: {error}'
                row['supplemental_wall_s'] = round(time.perf_counter()-extra, 6)
                timings.context['supplemental'] = False
    except Exception as error:
        row.update(error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc(),
                   wall_s=round(time.perf_counter()-began, 6), passed=False)
    finally:
        timings.uninstall()
        # The library disposes only its owned warm worker, whose PID was
        # recorded by record_owned_processes. No unrelated process is touched.
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()
    row.update(stage_events=timings.events, candidates=timings.candidates,
               ended_planning_at=now(), source_sha256_after=source_sha256())
    assert row['source_sha256_after'] == manifest['source_sha256']
    write(folder / 'raw.json', row)
    run_audits(folder, row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    worker = sub.add_parser('work')
    worker.add_argument('--budget', choices=('base', 'x4'), required=True)
    worker.add_argument('--map-id', required=True)
    audit = sub.add_parser('audit')
    audit.add_argument('--candidate', type=Path, required=True)
    audit.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'work':
        work(args.budget, args.map_id)
    else:
        assert args.candidate.resolve().is_relative_to(HERE / 'runs')
        assert args.output.resolve().is_relative_to(HERE / 'runs')
        audit_one(args.candidate, args.output)


if __name__ == '__main__':
    main()

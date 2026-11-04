"""D175 CPU headroom search. All solver restrictions are process-local, outside src/.

Run --stage pilot for a checked first read, then --stage full with the same output
directory to resume. --detach records the owned controller PID and redirects its
output. At most three map workers and their three owned V4 children run together.
Search costs are explicitly unverified; only check.passed rows expose moves.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
import hashlib
import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace

THREAD_VARS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
               'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS')
for _name in THREAD_VARS:
    os.environ[_name] = '1'
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(HERE), str(ROOT / 'src')]
VERSION = 1


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.tmp-{os.getpid()}')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def allowed_input(value):
    """Resolve first so symlinks and traversal cannot enter evaluation partitions."""
    path = (ROOT / value).resolve()
    relative = path.relative_to(ROOT)
    parts = relative.parts
    if any(p in {'test', 'val', 'validation', 'showcase'} for p in parts):
        raise ValueError(f'evaluation partition is forbidden: {relative}')
    train = parts[:4] == ('experiments', 'mvp', 'corpus', 'train')
    archive_train = (parts[:4] == ('experiments', 'mvp', 'corpus', 'archive')
                     and 'train' in parts[4:])
    if not (train or archive_train) or path.suffix != '.json' or '.audit.' in path.name:
        raise ValueError(f'not an allowlisted training instance: {relative}')
    if not path.is_file():
        raise ValueError(f'input does not exist: {relative}')
    return str(relative)


def manifest_jobs(path):
    manifest = read_json(path)
    rows = manifest.get('maps', manifest.get('instances', [])) if isinstance(manifest, dict) else manifest
    jobs, keys = [], set()
    for item in rows:
        row = {'input': item} if isinstance(item, str) else dict(item)
        relative = allowed_input(row.get('input', row.get('path')))
        key = row.get('map_id', row.get('key')) or f"{Path(relative).stem}-{hashlib.sha256(relative.encode()).hexdigest()[:10]}"
        if not key or Path(key).name != key or key in keys:
            raise ValueError(f'invalid or duplicate map_id: {key}')
        keys.add(key)
        actual = digest(ROOT / relative)
        declared = row.get('sha256', row.get('input_sha256'))
        if not declared or declared != actual:
            raise ValueError(f'allowlist SHA256 missing or mismatched for {relative}')
        jobs.append(dict(map_id=key, input=relative, input_sha256=actual))
    if not jobs:
        raise ValueError('manifest has no maps')
    return jobs


def source_snapshot():
    # Checkers, hull physics, and rule data are as consequential as the planner.
    # Hash the complete implementation plus its parameter/configuration inputs.
    suffixes = {'.py', '.toml', '.json', '.yaml', '.yml', '.csv', '.tsv'}
    files = {p for base in (ROOT / 'src', ROOT / 'params') for p in base.rglob('*')
             if p.is_file() and p.suffix in suffixes and '__pycache__' not in p.parts}
    files.update((HERE / 'layout.py', HERE / 'run.py', ROOT / 'pyproject.toml'))
    if (ROOT / 'uv.lock').is_file():
        files.add(ROOT / 'uv.lock')
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(files)}


class EvaluationDeadline(BaseException):
    pass


@contextmanager
def evaluation_watchdog(seconds):
    """Bound an individual CPU solve; no external process discovery or killing."""
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise EvaluationDeadline()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def event(folder, event_type, **fields):
    with (folder / 'progress.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(event=event_type, time=time.time(), pid=os.getpid(), **fields), default=str) + '\n')


def candidate_id(layout, prefix):
    serial = layout.to_dict()
    # Names and witness provenance should not prevent a duplicate permission mask
    # being recognized in local search. Random draws themselves remain recorded.
    payload = serial.get('allowed', serial)
    return prefix + '-' + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]


def evaluate(instance, model, layout, layout_id, kind, folder, config):
    from layout import audit_layout, audit_prestow, constraint_rule
    from stowage.feasibility import FeasibilityConfig
    from stowage.moves import count_moves
    from stowage.solver.twostep import TwoStepPlanner
    target = folder / 'evals' / f'{layout_id}.json'
    if target.exists():
        return read_json(target)
    seconds = config['medium_budget'] if instance.size_class == 'medium' else config['small_budget']
    row = dict(layout_id=layout_id, kind=kind, layout=layout.to_dict() if layout else None,
               status='not_started', has_plan=False, layout_honoured=False,
               solver_complete=False, raw_moves=None, budget_seconds=seconds)
    started = time.perf_counter()
    event(folder, 'eval_start', layout_id=layout_id, kind=kind)
    try:
        # The default has no restriction hook. Both arms use the D79 screen config.
        context = constraint_rule(instance, layout, model=model) if layout is not None else nullcontext()
        with evaluation_watchdog(max(10., 2 * seconds + 5)), context:
            result = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen': 0})).plan(
                instance, seed=config['planner_seed'], time_limit_s=seconds)
        row.update(status=result.status, message=result.message, metrics=result.metrics,
                   has_plan=result.plan is not None)
        carrier_violations = []
        if layout is not None and result.metrics.get('preplan_status') == 'ok':
            prestow = SimpleNamespace(slots=[SimpleNamespace(**slot)
                                            for slot in result.metrics.get('preplan_slots', [])])
            carrier_violations = audit_prestow(instance, prestow, layout, model=model)
        row['carrier_layout_violations'] = carrier_violations
        if result.plan is not None:
            violations = audit_layout(instance, result.plan, layout, model=model) if layout else []
            row.update(layout_honoured=not violations, layout_violations=violations,
                       solver_complete=result.status == 'ok' and not violations and not carrier_violations)
            plan_path = folder / 'plans' / f'{layout_id}.json'
            atomic_json(plan_path, result.model_dump(mode='json'))
            row['result_path'] = str(plan_path)
            if row['solver_complete']:
                row['raw_moves'] = count_moves(instance, result.plan).weighted_nonproductive
                row['plan_fingerprint'] = hashlib.sha256(json.dumps(
                    result.plan.model_dump(mode='json', exclude={'id', 'provenance'}),
                    sort_keys=True).encode()).hexdigest()
    except EvaluationDeadline:
        row.update(status='watchdog_timeout', message='Individual two-step watchdog expired; no complete result retained.')
    except Exception as error:
        row.update(status='exception', message=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
    row['eval_seconds'] = round(time.perf_counter() - started, 6)
    atomic_json(target, row)
    event(folder, 'eval_end', layout_id=layout_id, status=row['status'], seconds=row['eval_seconds'])
    return row


def finish_check(instance, model, row, folder, config):
    from layout import BlockLayout, audit_layout
    from stowage.checker import check
    from stowage.model import PlannerResult
    from stowage.planner import finalize
    target = folder / 'checks' / f"{row['layout_id']}.json"
    if target.exists():
        return dict(row, check=read_json(target))
    if not row.get('solver_complete'):
        return row
    result = PlannerResult.model_validate(read_json(row['result_path']))
    result = result.model_copy(update={'plan': result.plan.model_copy(update={'ballast': None})})
    record = dict(passed=False, checker_attempted=True, finalize_status='not_started',
                  finisher_budget_seconds=config['finisher_budget'])
    started = time.perf_counter()
    event(folder, 'check_start', layout_id=row['layout_id'])
    previous = os.environ.get('STOWAGE_BALLAST_V4')
    os.environ['STOWAGE_BALLAST_V4'] = '1'
    try:
        result = finalize(instance, result, time_limit_s=config['finisher_budget'])
        record.update(finalize_status=result.status, message=result.message, metrics=result.metrics,
                      finisher_seconds=round(time.perf_counter() - started, 6))
        if result.status == 'ok' and result.plan is not None:
            check_started = time.perf_counter()
            report = check(instance, result.plan)
            layout = BlockLayout.from_dict(row['layout']) if row['layout'] else None
            violations = audit_layout(instance, result.plan, layout, model=model) if layout else []
            record.update(passed=report.passed is True and not violations,
                          checker_passed=report.passed is True, layout_violations=violations,
                          rules=[v.rule_id for v in report.violations],
                          check_seconds=round(time.perf_counter() - check_started, 6))
            atomic_json(folder / 'reports' / f"{row['layout_id']}.json", report.model_dump(mode='json'))
            if record['passed']:
                record['moves'] = report.count.weighted_nonproductive
                record['count'] = report.count.model_dump(mode='json')
                finished = folder / 'finished' / f"{row['layout_id']}.json"
                atomic_json(finished, result.model_dump(mode='json'))
                record['finished_path'] = str(finished)
        record['timed_out'] = 'timeout' in str(result.metrics.get('not_found_cause', ''))
    except Exception as error:
        record.update(finalize_status='exception', message=f'{type(error).__name__}: {error}',
                      traceback=traceback.format_exc(), finisher_seconds=round(time.perf_counter() - started, 6))
    finally:
        if previous is None:
            os.environ.pop('STOWAGE_BALLAST_V4', None)
        else:
            os.environ['STOWAGE_BALLAST_V4'] = previous
    atomic_json(target, record)
    event(folder, 'check_end', layout_id=row['layout_id'], passed=record['passed'], seconds=time.perf_counter() - started)
    return dict(row, check=record)


def ranked(rows):
    return sorted((r for r in rows if r.get('solver_complete')), key=lambda r: (r['raw_moves'], r['eval_seconds'], r['layout_id']))


def share_certificates(instance, model, rows, folder):
    """Reuse a full-check certificate only for identical raw physical plans.

    Every recipient's own layout is independently audited against the finished
    certified plan. This is an explicitly shared certificate, not a new check.
    """
    from layout import BlockLayout, audit_layout
    from stowage.model import PlannerResult
    certificates = {}
    for row in rows:
        check = row.get('check') or {}
        if check.get('passed') and row.get('plan_fingerprint') and not check.get('certified_from_layout_id'):
            certificates.setdefault(row['plan_fingerprint'], row)
    for row in rows:
        if (row.get('check') or {}).get('passed') or not row.get('solver_complete'):
            continue
        source = certificates.get(row.get('plan_fingerprint'))
        if source is None:
            continue
        certificate = dict(source['check'])
        result = PlannerResult.model_validate(read_json(certificate['finished_path']))
        layout = BlockLayout.from_dict(row['layout']) if row['layout'] else None
        violations = audit_layout(instance, result.plan, layout, model=model) if layout else []
        if violations:
            continue
        certificate.update(certified_from_layout_id=source['layout_id'], checker_attempted=False,
                           certificate_kind='same raw physical plan; full checked finished plan; independently audited layout',
                           recipient_layout_violations=[])
        if row.get('check'):
            certificate['previous_own_attempt'] = row['check']
        row['check'] = certificate
        atomic_json(folder / 'checks' / f"{row['layout_id']}.json", certificate)


def run_map(job, out, config, stage):
    startup_started = time.perf_counter()
    from layout import (BlockLayout, BlockModel, _HOOK_MODULES, layout_from_plan,
                        local_neighbours, random_layouts)
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from stowage.rules.catalogue import rule_freeze_v1
    folder = Path(out) / 'maps' / job['map_id']
    folder.mkdir(parents=True, exist_ok=True)
    event(folder, 'map_start', stage=stage)
    # Recheck input digests inside the worker; never silently use changed maps.
    if digest(ROOT / allowed_input(job['input'])) != job['input_sha256']:
        raise ValueError(f"input changed: {job['input']}")
    instance = load_instance(ROOT / job['input'])
    model = BlockModel.from_instance(instance)
    # Import the same rule modules before every arm. Otherwise the first
    # unhooked baseline pays cold imports that constrained arms avoid.
    for module in _HOOK_MODULES:
        importlib.import_module(module)
    summary = dict(**job, size=instance.size_class, block_count=len(model.blocks),
                   n_loads=len(instance.load_list), block_model=model.to_dict(),
                   baseline=None, layouts=[], complete=False, stage=stage)
    def save():
        for r in [summary['baseline'], *summary['layouts']]:
            if r is not None:
                path = folder / 'checks' / f"{r['layout_id']}.json"
                if path.exists():
                    r['check'] = read_json(path)
        summary['updated_at'] = time.time()
        atomic_json(folder / 'summary.json', summary)
    try:
        with rule_freeze_v1(enabled=False):
            warmup_id = f'warmup-{os.getpid()}-{time.time_ns()}'
            warmup = evaluate(instance, model, None, warmup_id, 'warmup_discarded', folder, config)
            summary['warmup'] = warmup
            summary['cold_setup_and_warmup_seconds'] = round(time.perf_counter()-startup_started, 6)
            save()
            baseline = evaluate(instance, model, None, 'default', 'default', folder, config)
            summary['baseline'] = baseline
            save()
            summary['baseline'] = finish_check(instance, model, baseline, folder, config)
            save()
            base_plan = None
            if baseline.get('solver_complete'):
                base_plan = PlannerResult.model_validate(read_json(baseline['result_path'])).plan
            cache = folder / 'random_layouts.json'
            if cache.exists():
                layouts = [BlockLayout.from_dict(x) for x in read_json(cache)['layouts']]
            else:
                started = time.perf_counter()
                layouts = random_layouts(instance, count=config['random'], seed=config['seed'], base_plan=base_plan, model=model)
                atomic_json(cache, dict(layouts=[x.to_dict() for x in layouts], seconds=time.perf_counter()-started))
            summary['random_geometry_feasible_count'] = len(layouts)
            if len(layouts) < config['random']:
                summary['generation_issue'] = f"Requested {config['random']} random feasible layouts, obtained {len(layouts)}."
            checkpoint = min(config['checkpoint'], len(layouts))
            for i, layout in enumerate(layouts[:checkpoint]):
                row = evaluate(instance, model, layout, f'random-{i:03d}', 'random', folder, config)
                summary['layouts'].append(row)
                save()
            # A genuine hill-climb starts from the permission map derived from
            # default two-step, then takes improving restriction neighbours.
            if base_plan is not None and config['local']:
                default_layout = layout_from_plan(instance, base_plan, model=model, name='default-derived')
                current = evaluate(instance, model, default_layout, 'default-derived', 'local_seed', folder, config)
                summary['layouts'].append(current)
                save()
                used = {candidate_id(default_layout, 'local')}
                for iteration in range(config['local_rounds']):
                    neighbourhood = local_neighbours(instance, default_layout, seed=config['seed']+iteration,
                                                     limit=config['local'], model=model)
                    candidates = []
                    for layout in neighbourhood:
                        identifier = candidate_id(layout, 'local')
                        if identifier in used:
                            continue
                        used.add(identifier)
                        row = evaluate(instance, model, layout, identifier, 'local', folder, config)
                        summary['layouts'].append(row)
                        candidates.append((row, layout))
                        save()
                    valid = [(r,l) for r,l in candidates if r.get('solver_complete')]
                    best = min(valid, key=lambda pair: pair[0]['raw_moves']) if valid else None
                    if best is None or (current.get('solver_complete') and best[0]['raw_moves'] >= current['raw_moves']):
                        break
                    current, default_layout = best
            # First checked default and best candidate are durable before the
            # remaining random draws; full resumes these checks without repeats.
            options = ranked(summary['layouts'])
            if options:
                checked = finish_check(instance, model, options[0], folder, config)
                options[0].update(checked)
            share_certificates(instance, model, [summary['baseline'], *summary['layouts']], folder)
            save()
            if stage == 'pilot':
                summary['pilot_complete'] = True
                save()
                return summary
            for i, layout in enumerate(layouts[checkpoint:], start=checkpoint):
                summary['layouts'].append(evaluate(instance, model, layout, f'random-{i:03d}', 'random', folder, config))
                save()
            seen, chosen = set(), []
            for row in ranked(summary['layouts']):
                fingerprint = row.get('plan_fingerprint', row['layout_id'])
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                chosen.append(row)
                if len(chosen) == config['top']:
                    break
            for row in chosen:
                row.update(finish_check(instance, model, row, folder, config))
                save()
            share_certificates(instance, model, [summary['baseline'], *summary['layouts']], folder)
            summary.update(complete=len(layouts) >= config['random'], search_complete=True)
            save()
    except Exception as error:
        summary.update(error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
        save()
    finally:
        # V4 owns and records this child; release only that known worker.
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()
        event(folder, 'map_end', complete=summary.get('complete'), error=summary.get('error'))
    return summary


@contextmanager
def controller_lock(out):
    path = out / 'controller.lock'
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        previous = read_json(path)
        try:
            os.kill(previous['pid'], 0)
        except ProcessLookupError:
            path.unlink()
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        else:
            raise RuntimeError(f"controller PID {previous['pid']} is still present; refusing overlapping run")
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(dict(pid=os.getpid(), started_at=time.time()), stream)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--stage', choices=('pilot', 'full'), default='full')
    parser.add_argument('--workers', type=int, choices=(1,2,3), default=3)
    parser.add_argument('--random', type=int, default=64)
    parser.add_argument('--local', type=int, default=8)
    parser.add_argument('--local-rounds', type=int, default=2)
    parser.add_argument('--checkpoint', type=int, default=16)
    parser.add_argument('--small-budget', type=float, default=15.)
    parser.add_argument('--medium-budget', type=float, default=60.)
    parser.add_argument('--finisher-budget', type=float, default=600.)
    parser.add_argument('--top', type=int, default=3)
    parser.add_argument('--seed', type=int, default=175)
    parser.add_argument('--planner-seed', type=int, default=0)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--only', action='append', default=[])
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if min(args.random, args.checkpoint, args.top, args.local_rounds) < 1 or args.local < 0:
        parser.error('counts must be positive (local can be zero)')
    if not 0 < args.finisher_budget <= 600 or min(args.small_budget, args.medium_budget) <= 0:
        parser.error('positive budgets required and V4 budget must be <=600 seconds')
    out = args.out.resolve()
    out.relative_to(HERE)
    out.mkdir(parents=True, exist_ok=True)
    jobs = manifest_jobs(args.manifest)
    config = {k: getattr(args, k) for k in ('random','local','local_rounds','checkpoint','small_budget',
                                         'medium_budget','finisher_budget','top','seed','planner_seed')}
    record = dict(version=VERSION, config=config, maps=jobs, sources=source_snapshot(),
                  rule_freeze_v1=False, feasibility_extra={'global_screen': 0},
                  finisher='V4 finalize only, no repair', cpu_only=True,
                  native_threads={name:'1' for name in THREAD_VARS}, max_map_workers=3)
    if args.only:
        jobs = [j for j in jobs if j['map_id'] in args.only]
        missing = set(args.only) - {j['map_id'] for j in jobs}
        if missing:
            parser.error(f'unknown map IDs: {sorted(missing)}')
    if args.limit is not None:
        jobs = jobs[:args.limit]
    if args.dry_run:
        print(json.dumps(dict(jobs=jobs, run=record), indent=2))
        return
    if args.detach:
        command = [sys.executable, str(Path(__file__).resolve()), *[x for x in sys.argv[1:] if x != '--detach']]
        with (out / 'controller.log').open('a') as log:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        atomic_json(out / 'launch.json', dict(pid=child.pid, command=command, time=time.time()))
        print(json.dumps(dict(detached_pid=child.pid, log=str(out/'controller.log'))))
        return
    with controller_lock(out):
        manifest = out / 'run.json'
        if manifest.exists() and read_json(manifest) != record:
            raise RuntimeError('Run configuration, source hashes, or input manifest changed; use a new output directory.')
        atomic_json(manifest, record)
        atomic_json(out / 'controller.json', dict(pid=os.getpid(), stage=args.stage,
                                                  workers=args.workers, started_at=time.time()))
        with (out / 'ledger.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(event='start', time=time.time(), pid=os.getpid(), stage=args.stage,
                                         map_workers=args.workers, max_compute_processes=2*args.workers,
                                         maps=len(jobs), no_gpu=True, no_training=True))+'\n')
        errors = []
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context('spawn')) as pool:
            tasks = {pool.submit(run_map, job, str(out), config, args.stage): job for job in jobs}
            for future in as_completed(tasks):
                job = tasks[future]
                try:
                    row = future.result()
                    print(json.dumps(dict(map_id=job['map_id'], complete=row.get('complete'),
                                          pilot_complete=row.get('pilot_complete'), error=row.get('error'))), flush=True)
                    if row.get('error'):
                        errors.append(dict(map_id=job['map_id'], error=row['error']))
                except Exception as error:
                    errors.append(dict(map_id=job['map_id'], error=f'{type(error).__name__}: {error}'))
                    print(traceback.format_exc(), flush=True)
        atomic_json(out / f'{args.stage}-completion.json', dict(time=time.time(), maps=len(jobs), errors=errors))
        print(json.dumps(dict(stage=args.stage, maps=len(jobs), errors=errors)), flush=True)


if __name__ == '__main__':
    main()

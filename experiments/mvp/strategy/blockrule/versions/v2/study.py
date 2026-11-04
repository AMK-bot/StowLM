"""D185 paired CPU study; headroom's process-local restriction and solve wrapper.

Train precedes a single frozen validation campaign. All raw, compacted, finished
and rejected artifacts are durable. Only a full checker certificate exposes cost.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
HEADROOM = HERE.parent / 'headroom'
sys.path[:0] = [str(HERE), str(ROOT), str(HEADROOM), str(HERE.parent), str(ROOT / 'src')]
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
             'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS'):
    os.environ[name] = '1'
os.environ['STOWAGE_IN_PORT_WORKERS'] = '1'
import run as headroom
from run import atomic_json, digest, read_json


def jobs_from_manifest(path):
    manifest = read_json(path)
    jobs = manifest['maps']
    keys = set()
    for row in jobs:
        value = row.get('input', row.get('path'))
        path = (ROOT / value).resolve()
        parts = path.relative_to(ROOT).parts
        if any(x in parts for x in ('test', 'showcase', 'archive')):
            raise ValueError(f'Forbidden partition: {value}')
        local_train = path.is_relative_to(HERE / 'maps') and manifest['split'] == 'train'
        corpus_split = len(parts) > 3 and parts[:3] == ('experiments','mvp','corpus') and parts[3] == manifest['split']
        if not (local_train or corpus_split) or manifest['split'] not in ('train','val'):
            raise ValueError(f'Input does not match declared split: {value}')
        expected = row.get('sha256', row.get('input_sha256'))
        if not expected or digest(path) != expected:
            raise ValueError(f'Input hash mismatch: {value}')
        row['input'] = str(path.relative_to(ROOT))
        row['input_sha256'] = expected
        if row['map_id'] in keys:
            raise ValueError('Duplicate map id')
        keys.add(row['map_id'])
    return manifest, jobs


def certify(instance, model, row, folder, budget, variant):
    """Finalize then full-check; retain every returned plan, even failed ones."""
    from layout import BlockLayout, audit_layout
    from stowage.checker import check
    from stowage.model import PlannerResult
    from stowage.planner import finalize
    identifier = row['layout_id'] + '-' + variant
    target = folder / 'checks' / (identifier + '.json')
    if target.exists():
        return read_json(target)
    record = dict(passed=False, variant=variant, arm=row['layout_id'],
                  solver_complete=row.get('solver_complete', False),
                  finisher_budget_seconds=budget, checker_attempted=False)
    if not row.get('solver_complete'):
        record['finalize_status'] = 'not_reached'
        atomic_json(target, record)
        return record
    result = PlannerResult.model_validate(read_json(row['result_path']))
    if variant == 'compact':
        from compact import compact_plan
        started = time.perf_counter()
        plan, info = compact_plan(instance, result.plan)
        record.update(compaction=info, compaction_seconds=time.perf_counter()-started)
        if plan is None:
            record['finalize_status'] = 'compaction_unavailable'
            atomic_json(target, record)
            return record
        result = result.model_copy(update={'plan': plan})
        atomic_json(folder / 'plans' / (identifier + '.json'), result.model_dump(mode='json'))
    result = result.model_copy(update={'plan': result.plan.model_copy(update={'ballast': None})})
    previous = os.environ.get('STOWAGE_BALLAST_V4')
    os.environ['STOWAGE_BALLAST_V4'] = '1'
    started = time.perf_counter()
    try:
        result = finalize(instance, result, time_limit_s=budget)
        record.update(finalize_status=result.status, message=result.message,
                      metrics=result.metrics, finisher_seconds=time.perf_counter()-started)
        # Every finalizer result is retained, including partial/rejected plans.
        finished = folder / 'finished' / (identifier + '.json')
        atomic_json(finished, result.model_dump(mode='json'))
        record['finished_path'] = str(finished.relative_to(ROOT))
        if result.plan is not None:
            started_check = time.perf_counter()
            report = check(instance, result.plan)
            layout = BlockLayout.from_dict(row['layout']) if row['layout'] else None
            issues = audit_layout(instance, result.plan, layout, model=model) if layout else []
            record.update(checker_attempted=True, checker_passed=report.passed is True,
                          passed=result.status == 'ok' and report.passed is True and not issues,
                          layout_violations=issues, rules=[v.rule_id for v in report.violations],
                          checker_seconds=time.perf_counter()-started_check)
            report_path = folder / 'reports' / (identifier + '.json')
            atomic_json(report_path, report.model_dump(mode='json'))
            record['report_path'] = str(report_path.relative_to(ROOT))
            if record['passed']:
                record.update(moves=report.count.weighted_nonproductive,
                              count=report.count.model_dump(mode='json'))
    except Exception as exc:
        record.update(error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    finally:
        if previous is None:
            os.environ.pop('STOWAGE_BALLAST_V4', None)
        else:
            os.environ['STOWAGE_BALLAST_V4'] = previous
    atomic_json(target, record)
    return record


def choose(default, rule):
    if rule.get('passed') and (not default.get('passed') or rule['moves'] < default['moves']):
        return {**rule, 'arm': 'rule'}
    return {**default, 'arm': 'default'}


def best_variant(raw, compact):
    return compact if compact.get('passed') and (not raw.get('passed') or compact['moves'] < raw['moves']) else raw


def run_map(job, out, config):
    from layout import BlockModel, _HOOK_MODULES
    from capacity_candidate import capacity_relaxed_layout as port_blocking_layout
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1
    folder = Path(out) / 'maps' / job['map_id']
    folder.mkdir(parents=True, exist_ok=True)
    if digest(ROOT / job['input']) != job['input_sha256']:
        raise ValueError('Input changed after freeze')
    instance = load_instance(ROOT / job['input'])
    model = BlockModel.from_instance(instance)
    for name in _HOOK_MODULES:
        importlib.import_module(name)
    summary = dict(job=job, pid=os.getpid(), complete=False, arms={}, selected={})
    atomic_json(folder / 'worker.json', dict(pid=os.getpid(), started_at=time.time()))
    try:
        with rule_freeze_v1(enabled=False):
            layout = port_blocking_layout(instance, model=model)
            atomic_json(folder / 'layout.json', layout.to_dict())
            for arm, restriction in (('default', None), ('rule', layout)):
                row = headroom.evaluate(instance, model, restriction, arm, arm, folder, config)
                summary['arms'][arm] = row
                atomic_json(folder / 'summary.json', summary)
                budget = config['medium_finisher'] if instance.size_class == 'medium' else config['small_finisher']
                for variant in ('raw', 'compact'):
                    row[variant] = certify(instance, model, row, folder, budget, variant)
                    atomic_json(folder / 'summary.json', summary)
            default, rule = summary['arms']['default'], summary['arms']['rule']
            for variant in ('raw', 'compact'):
                # Compaction is an optional improvement on both sides; retain
                # each original if compaction fails or does not improve it.
                base = default['raw'] if variant == 'raw' else best_variant(default['raw'], default['compact'])
                restricted = rule['raw'] if variant == 'raw' else best_variant(rule['raw'], rule['compact'])
                summary['selected'][variant] = dict(default=base, rule=restricted,
                                                     planner=choose(base, restricted))
            summary['complete'] = True
    except Exception as exc:
        summary.update(error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    finally:
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()
        atomic_json(folder / 'summary.json', summary)
    return summary


def source_snapshot():
    hashes = headroom.source_snapshot()
    for path in [HERE/'study.py', HERE/'rule.py', HERE.parent/'compact.py',
                 HERE/'capacity_candidate.py', HERE/'capacity_train.py', HERE/'capacity-policy.json']:
        if path.is_file():
            hashes[str(path.relative_to(ROOT))] = digest(path)
    return hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1,2,3), default=2)
    parser.add_argument('--small-budget', type=float, default=120)
    parser.add_argument('--medium-budget', type=float, default=300)
    parser.add_argument('--small-finisher', type=float, default=600)
    parser.add_argument('--medium-finisher', type=float, default=1200)
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    manifest, jobs = jobs_from_manifest(args.manifest)
    out = args.out.resolve()
    if not out.is_relative_to(HERE):
        parser.error('Output must be inside blockrule')
    out.mkdir(parents=True, exist_ok=True)
    if args.detach:
        command = [sys.executable, str(Path(__file__).resolve()), *[a for a in sys.argv[1:] if a != '--detach']]
        with (out/'controller.log').open('a') as log:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        atomic_json(out/'launch.json', dict(pid=child.pid, command=command, started_at=time.time()))
        print(json.dumps(dict(pid=child.pid, out=str(out))))
        return
    config = {name: getattr(args, name) for name in ('small_budget','medium_budget','small_finisher','medium_finisher')}
    config['planner_seed'] = 0
    record = dict(config=config, sources=source_snapshot(), jobs=jobs, split=manifest['split'],
                  manifest_sha256=digest(args.manifest), rule_freeze_v1=False,
                  feasibility_extra={'global_screen':0}, cpu_only=True, no_training=True)
    with headroom.controller_lock(out):
        if (out/'run.json').exists() and read_json(out/'run.json') != record:
            raise RuntimeError('Run inputs/config/source changed; use new directory')
        atomic_json(out/'run.json', record)
        atomic_json(out/'controller.json', dict(pid=os.getpid(), workers=args.workers, started_at=time.time()))
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context('spawn')) as pool:
            futures = {pool.submit(run_map, job, out, config): job for job in jobs}
            errors = []
            for future in as_completed(futures):
                job = futures[future]
                try:
                    row = future.result()
                    if row.get('error'):
                        errors.append(dict(map_id=job['map_id'], error=row['error']))
                    print(json.dumps(dict(map_id=job['map_id'], complete=row.get('complete'), error=row.get('error'))), flush=True)
                except Exception as exc:
                    errors.append(dict(map_id=job['map_id'], error=str(exc)))
        atomic_json(out/'completion.json', dict(maps=len(jobs), errors=errors, ended_at=time.time()))


if __name__ == '__main__':
    main()

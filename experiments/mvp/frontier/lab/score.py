"""Shared validation scorecard. See README.md for the plug-in API and output schema."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
from time import perf_counter

# Keep imported solver/checker trees read-only, including Python bytecode caches.
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

# Set these before importing any numerical library, including in spawned workers.
for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
              'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[_name] = '1'

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for _path in (ROOT / 'src', HERE.parent, HERE):
    sys.path.insert(0, str(_path))


def _jsonable(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


def write_json(path, row):
    """Replace an output atomically; interrupted writes cannot become cache hits."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(_jsonable(row), indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def plugin_info(spec):
    """Resolve a file:function without importing user code in the parent process."""
    if not spec:
        return None
    filename, separator, function = spec.rpartition(':')
    if not separator or not filename or not function:
        raise ValueError(f'expected path.py:function, got {spec!r}')
    path = Path(filename).expanduser().resolve()
    return dict(path=str(path), function=function,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def import_plugin(info):
    path = Path(info['path'])
    # Make sibling helper imports work for standalone experiment files.
    sys.path.insert(0, str(path.parent))
    name = '_frontier_lab_plugin_' + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f'cannot import {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    function = getattr(module, info['function'])
    if not callable(function):
        raise TypeError(f'{path}:{info["function"]} is not callable')
    return function


def source_sha256():
    """Record runner, shared catalog and the source actually used for scoring/checking."""
    digest = hashlib.sha256()
    paths = list((ROOT / 'src' / 'stowage').rglob('*.py'))
    paths += list(HERE.parent.glob('*.py'))
    paths += [HERE / name for name in ('score.py', 'catalog.py', 'summary.py')]
    for path in sorted(set(paths)):
        if path.exists():
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def run_key(config, input_sha256, baseline):
    """An existing row resumes only the same input, baseline, code, plug-ins and budgets."""
    payload = dict(config=config, input_sha256=input_sha256, baseline=baseline)
    return hashlib.sha256(json.dumps(_jsonable(payload), sort_keys=True).encode()).hexdigest()


def cached_row(path, key):
    try:
        row = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if row.get('run_key') == key and row.get('complete') is True:
        return row
    return None


def _parts(count):
    return dict(relocations=count.relocations, hatch_now=count.hatch_cover_lifts,
                hatch_later=sum(f.hatch_cover_lifts for f in count.future),
                restow_now=count.restow_lifts,
                restow_later=sum(f.restow_lifts for f in count.future))


def _finished_actions(instance, plan, scored):
    """A finisher plug-in may change the stow; report the plan that actually passed."""
    if not hasattr(plan, 'moves'):
        return scored.actions  # lightweight test doubles
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    return tuple(a for a in get_action_space('boxslot').from_plan(instance, plan)
                 if isinstance(a, Place))


def _actions(candidate, instance, problem, order, context, config):
    from alns import Sequencer
    from stowage.actions.space import Place
    if isinstance(candidate, dict):
        assignment = dict(candidate)
        actions = None
    else:
        actions = tuple(candidate)
        if not all(isinstance(a, Place) for a in actions):
            raise ValueError('candidate must contain only Place actions')
        assignment = {a.box: a.slot for a in actions}
        if len(assignment) != len(actions):
            raise ValueError('candidate places a box more than once')
    if set(assignment) != set(instance.load_list):
        raise ValueError('candidate must place every load-list box exactly once')
    if not all(isinstance(slot, str) for slot in assignment.values()):
        raise ValueError('assignment slots must be strings')
    if order is not None:
        actions = order(instance, assignment, seed=config['seed'],
                        budget_s=config['budget_s'], context=context)
    elif actions is None:
        actions = Sequencer(problem).order(assignment)
    if actions is None:
        raise ValueError('load-order stage returned no order')
    actions = tuple(actions)
    if (not all(isinstance(a, Place) for a in actions)
            or len(actions) != len(assignment)
            or {a.box: a.slot for a in actions} != assignment):
        raise ValueError('load-order stage must preserve the complete assignment')
    return actions


def evaluate(instance, baseline, config, *, method=None, order=None, finish=None,
             problem=None, checker=None):
    """Run one already-loaded instance. Injection points keep safety tests inexpensive."""
    from alns import Problem
    from stowage.checker import check
    from planner import _finish
    started = perf_counter()
    row = dict(instance_id=instance.id, input=config.get('input'),
               size=baseline.get('size') or getattr(instance, 'size_class', None), n_boxes=len(instance.load_list), passed=False,
               lifts=None, weighted=None, parts=None, assignment=None, plan=None,
               propose_s=0., order_score_s=0., finish_s=0., wall_s=0.,
               status='error', failure_reason=None, tries=[], candidate_errors=[])
    context = dict(problem=problem or Problem(instance), stored=baseline.get('stored'),
                   stored_lifts=baseline.get('lifts'), stored_plan=baseline.get('plan'),
                   baseline=baseline, instance_path=config.get('input'),
                   finalize_s=config['finalize_s'], tries=config['tries'], method_metadata={})
    checker = checker or check
    try:
        method = method or import_plugin(config['method'])
        order = order or (import_plugin(config['order']) if config.get('order') else None)
        finish = finish or (import_plugin(config['finish']) if config.get('finish') else None)
        stage_started = perf_counter()
        try:
            candidates = method(instance, seed=config['seed'], budget_s=config['budget_s'], context=context)
            candidates = list(candidates) if candidates is not None else []
        finally:
            row['propose_s'] = perf_counter() - stage_started
        ranked, seen = [], set()
        stage_started = perf_counter()
        for index, candidate in enumerate(candidates):
            try:
                actions = _actions(candidate, instance, context['problem'], order, context, config)
                key = tuple((a.box, a.slot) for a in actions)
                if key in seen:
                    continue
                seen.add(key)
                scored = context['problem'].score(actions)
                if scored is None:
                    raise ValueError('problem.score rejected the candidate as illegal')
                ranked.append((scored.objective, index, scored))
            except Exception as error:
                row['candidate_errors'].append(dict(candidate=index, reason=f'{type(error).__name__}: {error}'))
        row['order_score_s'] = perf_counter() - stage_started
        ranked.sort(key=lambda item: (item[0], item[1]))
        for rank, (objective, index, scored) in enumerate(ranked[:config['tries']]):
            attempt = dict(rank=rank, candidate=index, search_lifts=objective, passed=False)
            stage_started = perf_counter()
            try:
                if finish is None:
                    outcome = _finish(instance, scored.plan, seed=config['seed'],
                                      finalize_s=config['finalize_s'], label=f'lab-{rank}')
                else:
                    outcome = finish(instance, scored.plan, seed=config['seed'],
                                     finalize_s=config['finalize_s'], label=f'lab-{rank}', context=context)
                result, metadata = outcome if isinstance(outcome, tuple) and len(outcome) == 2 else (outcome, {})
                attempt['finisher'] = _jsonable(metadata)
                if result is None:
                    attempt['failure_reason'] = (metadata or {}).get('message') or 'finisher returned no plan'
                    continue
                if hasattr(result, 'status') and result.status != 'ok':
                    attempt['failure_reason'] = f'finisher status {result.status}: {getattr(result, "message", "")}'
                    continue
                plan = result.plan if hasattr(result, 'plan') else result
                # Never trust a plug-in's claimed pass. This is the sole pass gate.
                report = checker(instance, plan)
                attempt['passed'] = bool(report.passed)
                if not report.passed:
                    attempt['violations'] = sorted({v.rule_id for v in report.violations})
                    attempt['failure_reason'] = 'full checker rejected finished plan'
                    continue
                finished_actions = _finished_actions(instance, plan, scored)
                row.update(passed=True, status='passed', lifts=float(report.count.weighted_nonproductive),
                           weighted=float(report.count.weighted_nonproductive), parts=_parts(report.count),
                           assignment={a.box: a.slot for a in finished_actions},
                           actions=[dict(box=a.box, slot=a.slot) for a in finished_actions],
                           plan=_jsonable(plan), failure_reason=None)
                attempt['lifts'] = row['lifts']
                break
            except Exception as error:
                attempt['failure_reason'] = f'{type(error).__name__}: {error}'
            finally:
                attempt['finish_s'] = perf_counter() - stage_started
                row['finish_s'] += attempt['finish_s']
                row['tries'].append(attempt)
        if not row['passed']:
            row['status'] = 'failed' if ranked else 'no_candidates'
            row['failure_reason'] = (row['tries'][-1].get('failure_reason') if row['tries'] else
                                     'no legal complete candidates returned')
    except Exception as error:
        row.update(status='method_error', passed=False, failure_reason=f'{type(error).__name__}: {error}')
    finally:
        row['method_metadata'] = _jsonable(context['method_metadata'])
        row['wall_s'] = perf_counter() - started
    return row


def one(job):
    from catalog import ensure_safe
    from stowage.io import load_instance
    rel, out_dir, base_by_id, config = job
    # Validate before reading either bytes or parsed contents, including on resume.
    rel = ensure_safe(rel)
    path = ROOT / rel
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    config = dict(config, input=rel)
    try:
        instance = load_instance(path)
    except Exception as error:
        # A malformed approved input is a recorded failure, never a crashed pool.
        identity = path.stem
        baseline = base_by_id.get(identity, {})
        key = run_key(config, sha, baseline)
        target = Path(out_dir) / f'{identity}.json'
        existing = cached_row(target, key)
        if existing is not None:
            return existing, True
        row = dict(instance_id=identity, input=rel, size=baseline.get('size'),
                   passed=False, lifts=None, assignment=None, parts=None, propose_s=0.,
                   finish_s=0., wall_s=0., status='input_error', failure_reason=f'{type(error).__name__}: {error}',
                   run_key=key, complete=True, input_sha256=sha, config=config,
                   baseline_passed=baseline.get('passed'), baseline_lifts=baseline.get('lifts'))
        write_json(target, row)
        return row, False
    baseline = base_by_id.get(instance.id, {})
    key = run_key(config, sha, baseline)
    target = Path(out_dir) / f'{instance.id}.json'
    existing = cached_row(target, key)
    if existing is not None:
        return existing, True
    try:
        row = evaluate(instance, baseline, config)
    except Exception as error:
        row = dict(instance_id=instance.id, input=rel, size=baseline.get('size'),
                   passed=False, lifts=None, assignment=None, parts=None, propose_s=0.,
                   finish_s=0., wall_s=0., status='error', failure_reason=f'{type(error).__name__}: {error}')
    row.update(run_key=key, complete=True, input_sha256=sha, config=config,
               baseline_passed=baseline.get('passed'), baseline_lifts=baseline.get('lifts'))
    write_json(target, row)
    return row, False


def main(argv=None):
    from catalog import load_baseline, load_set
    from summary import summarize, print_summary
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--set', required=True, help='quick, quick_tiny, quick_small, quick_medium, medium12, or list file')
    parser.add_argument('--method', required=True, help='path.py:function')
    parser.add_argument('--order', help='optional path.py:function for the load-order stage')
    parser.add_argument('--finish', help='optional path.py:function for the finisher stage')
    parser.add_argument('--budget-s', type=float, default=60.)
    parser.add_argument('--finalize-s', type=float, default=300.)
    parser.add_argument('--tries', type=int, default=3)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=1)
    parser.add_argument('--out', required=True)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args(argv)
    if args.budget_s < 0 or args.finalize_s <= 0 or args.tries < 1:
        parser.error('budget-s must be nonnegative; finalize-s and tries must be positive')
    baseline = load_baseline()
    paths = list(dict.fromkeys(load_set(args.set)))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    config = dict(method=plugin_info(args.method), order=plugin_info(args.order), finish=plugin_info(args.finish),
                  budget_s=args.budget_s, finalize_s=args.finalize_s, tries=args.tries,
                  seed=args.seed, source_sha256=source_sha256())
    jobs = [(rel, str(out), baseline, config) for rel in paths]
    rows = []
    def collect(value):
        row, cached = value
        rows.append(row)
        print(f'{row["instance_id"]}: {"cached " if cached else ""}{row["status"]} '
              f'lifts={row.get("lifts")} wall={row.get("wall_s", 0):.2f}s', flush=True)
    if args.workers == 1:
        for job in jobs:
            collect(one(job))
    else:
        with ProcessPoolExecutor(args.workers, mp_context=mp.get_context('spawn'), max_tasks_per_child=1) as pool:
            for future in as_completed([pool.submit(one, job) for job in jobs]):
                collect(future.result())
    rows.sort(key=lambda row: row['instance_id'])
    summary = summarize(rows, baseline, seed=args.seed)
    write_json(out / 'summary.json', summary)
    print_summary(summary)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

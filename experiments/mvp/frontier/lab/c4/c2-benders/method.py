"""Cold construction and exact finishing through the shared lab scorecard.

Only the live instance, seed and current-run Problem enter construction. Stored
plans/assignments/recipes in the runner context are deliberately ignored. The
runner independently replay-scores every proposed complete stow and fully checks
the final returned plan. All private artifacts stay below this experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
from time import monotonic, monotonic_ns

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
ROOT = LAB.parents[3]
MAP_BUDGET_S = 1200.
CHECK_RESERVE_S = 60.
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
              'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS'):
    os.environ[_name] = '1'


def _safe(value):
    if hasattr(value, 'model_dump'):
        return _safe(value.model_dump(mode='json'))
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, 'item'):
        return _safe(value.item())
    return repr(value)


def _write(path, value):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE):
        raise ValueError('artifact output must remain inside c4/c2-benders')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(_safe(value), indent=2, sort_keys=True,
                                    allow_nan=False)+'\n')
    temporary.replace(path)


def _sources():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(HERE.glob('*.py'))
            if not path.name.startswith('test_') and path.name != 'audit.py'}


def _dependencies():
    sources = {}
    for module in tuple(sys.modules.values()):
        filename = getattr(module, '__file__', None)
        if not filename:
            continue
        path = Path(filename).resolve()
        if (path.suffix == '.py' and path.is_relative_to(ROOT) and path.is_file()
                and (path.is_relative_to(ROOT/'src') or path.is_relative_to(LAB)
                     or path.parent == LAB.parent)):
            sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return sources


def _snapshot(metadata):
    metadata['load_average_after'] = list(os.getloadavg())
    metadata['implementation_sha256_after'] = _sources()
    metadata['implementation_unchanged'] = (metadata['implementation_sha256_after'] ==
                                             metadata['implementation_sha256'])
    metadata['runtime_dependency_sha256_after'] = _dependencies()
    before = metadata.get('runtime_dependency_sha256', {})
    metadata['loaded_dependencies_unchanged'] = all(
        metadata['runtime_dependency_sha256_after'].get(path) == sha for path, sha in before.items())


def _load(name):
    key = '_c4_c2_benders_adapter_'+name
    if key not in sys.modules:
        path = HERE/(name+'.py')
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def _detail_directory(instance, seed):
    root = Path(os.environ.get('C4_DETAIL_DIR', HERE/'results/details')).resolve()
    if not root.is_relative_to(HERE):
        raise ValueError('C4_DETAIL_DIR must remain inside this experiment')
    identity = re.sub(r'[^A-Za-z0-9_.-]', '_', instance.id)
    return root/f'{identity}-s{seed}-p{os.getpid()}-{monotonic_ns()}'


def _binding(meta):
    """Retain named failed margins even when the finisher returns no pass."""
    candidates = (meta.get('bindings') or meta.get('binding_margins') or
                  meta.get('binding_rules') or meta.get('binding_rule'))
    if candidates:
        return _safe(candidates)
    report = meta.get('full_checker_report') or meta.get('report')
    if hasattr(report, 'model_dump'):
        report = report.model_dump(mode='json')
    if isinstance(report, dict):
        return [dict(rule_id=v.get('rule_id'), measured=v.get('measured'),
                     limit=v.get('limit'), unit=v.get('unit'), message=v.get('message'))
                for v in report.get('violations', []) if v.get('severity') == 'hard']
    return []


def _screen(instance, plan):
    from stowage.moves import apply_plan
    name = '_c4_adapter_securing_bound'
    if name not in sys.modules:
        path = LAB/'c1/fx-finish/early_bound.py'
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise ValueError('cold candidate lost legal replay at securing screen')
    return sys.modules[name].securing_bound(instance, replay.final_state.ship)


def _quality(record):
    meta = record['public']['metadata']
    kg = meta.get('cargo_kg_m')
    # All strengthened constructors share the same physical quality ranking;
    # naming a new constructor must not demote it behind a plain packing.
    return (int(meta.get('kind') in ('plain', 'legacy_plain')), kg if isinstance(kg, (int, float)) else math.inf,
            record['scored'].objective)


def propose(instance, *, seed, budget_s, context):
    started = monotonic()
    directory = _detail_directory(instance, seed)
    metadata = dict(entry='method.py:propose/finish', cold=True,
        owned_worker_pid=os.getpid(), load_average_start=list(os.getloadavg()),
        runtime_historical_artifacts_read=False, stored_context_used=False,
        seed=seed, instance_id=instance.id, detail_directory=str(directory.relative_to(ROOT)),
        map_budget_s=MAP_BUDGET_S, independent_check_reserve_s=CHECK_RESERVE_S,
        budget_policy='one cooperative deadline; caller owns hard process watchdog',
        constructor_budget_s=min(max(0., budget_s), MAP_BUDGET_S-CHECK_RESERVE_S),
        complete_replay_legal_stows=0, distinct_complete_assignments=0,
        full_checker_passes_claimed_by_adapter=0, candidates=[], finish_attempts=[],
        implementation_sha256=_sources(), comparison='development; no stored-row solver-gain claim')
    context.setdefault('method_metadata', {})['cold_pipeline'] = metadata
    private = dict(started=started, deadline=started+MAP_BUDGET_S,
                   directory=directory, metadata=metadata, records=[])
    context['_c4_cold_pipeline'] = private
    # This whitelist prevents constructor access to runner historical context.
    fresh = {}
    if context.get('problem') is not None:
        fresh['problem'] = context['problem']
    constructor_started = monotonic()
    legacy_records, legacy_meta = [], None
    # Original pricing can discover substantially different complete patterns
    # on the smaller dense ships. Its target is derived from its own cold
    # plain packing. Larger instances retain the faster C2-first portfolio.
    if (metadata['constructor_budget_s'] >= 240. and not instance.onboard.placements
            and len(instance.load_list) <= 150):
        legacy_records, legacy_meta = _load('legacy_cold').legacy_candidates(
            instance, seed=seed,
            budget_s=min(210., .7*metadata['constructor_budget_s']), context=fresh)
        strengthened = [r for r in legacy_records
                        if r.get('metadata', {}).get('kind') == 'legacy_native_physics']
        if strengthened:
            fresh['cold_compact_after_legacy'] = True
            fresh['cold_live_scored'] = strengthened[-1]['scored']
    remaining_constructor = max(0., metadata['constructor_budget_s']-
                                 (monotonic()-constructor_started))
    result = _load('cold').cold_candidates(instance, seed=seed,
        budget_s=remaining_constructor, context=fresh)
    if isinstance(result, tuple) and len(result) in (2, 3):
        records, aggregate = result[0], result[-1]
        if len(result) == 3:
            metadata['constructor_metadata'] = _safe(result[1])
    else:
        raise ValueError('cold_candidates must return (records, metadata)')
    records = legacy_records+list(records)
    if legacy_meta is not None:
        aggregate = dict(legacy=legacy_meta, cgscale=aggregate,
            variants=list(legacy_meta.get('variants', []))+list(aggregate.get('variants', [])),
            cold_start=True, stored_data_consulted=False,
            complete_stows=len(records), full_checker_passes=None)
    metadata['constructor'] = _safe(aggregate)
    if fresh.get('problem') is not None:
        context['problem'] = fresh['problem']
    actions, assignments = [], set()
    for ordinal, record in enumerate(records):
        scored = record['scored']
        if record.get('metadata', {}).get('complete_replay_legal') is not True:
            raise ValueError('constructor record lacks an explicit real-replay completeness gate')
        candidate_id = str(record.get('candidate_id', f'cold-{ordinal:03d}'))
        sequence = tuple(scored.actions)
        identity = tuple(sorted((a.box, a.slot) for a in sequence))
        if (len(identity) != len(instance.load_list) or
                {box for box, _ in identity} != set(instance.load_list)):
            raise ValueError('constructor emitted incomplete or duplicate-box candidate')
        if identity in assignments:
            continue
        assignments.add(identity)
        candidate_path = directory/f'cold-{ordinal:03d}-plan.json'
        _write(candidate_path, scored.plan)
        public = dict(candidate_id=candidate_id, runner_candidate_index=len(actions),
            complete_replay_legal=True,
            gate='cold constructor real replay; runner independently replay-scores actions',
            full_checker_passed=None, search_lifts=float(scored.objective),
            plan_path=str(candidate_path.relative_to(ROOT)),
            plan_sha256=hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
            metadata=_safe(record.get('metadata', {})))
        metadata['candidates'].append(public)
        private['records'].append(dict(scored=scored, public=public))
        actions.append(sequence)
    metadata.update(complete_replay_legal_stows=len(actions),
                    distinct_complete_assignments=len(assignments),
                    construction_wall_s=monotonic()-started,
                    runtime_dependency_sha256=_dependencies())
    _write(directory/'construction.json', metadata)
    return actions


def finish(instance, plan, *, seed=0, finalize_s=1080., label='c4-cold', context=None):
    """Try only this run's cold plans within their common map deadline."""
    context = context if context is not None else {}
    private = context.get('_c4_cold_pipeline')
    if private is None:
        raise ValueError('finish requires fresh propose context; no stored-plan fallback')
    metadata, directory = private['metadata'], private['directory']
    deadline = min(private['deadline']-CHECK_RESERVE_S, monotonic()+max(0., finalize_s))
    records = private['records']
    if not records:
        return None, dict(status='no_cold_candidates', message='No complete cold stow')
    # Prioritize actual low-KG physics construction rather than the runner's
    # cheapest-lift order: near-feasible stows need a substantial exact budget.
    metadata['portfolio_work_caps'] = dict(securing_screens=len(records),
        exact_finishes=2, preferred_first_finish_max_s=900.)
    ordered = []
    for record in sorted(records, key=_quality):
        if monotonic() >= deadline:
            break
        before = monotonic()
        screen = _screen(instance, record['scored'].plan)
        record['public']['securing_screen_s'] = monotonic()-before
        record['public']['securing_screen'] = _safe(screen)
        if screen.get('proof'):
            record['public']['ballast_verdict'] = 'proved_cannot_for_fixed_stow'
            record['public']['full_checker_passed'] = None
        else:
            ordered.append(record)
    metadata['certified_fixed_stow_rejections'] = sum(
        c.get('ballast_verdict') == 'proved_cannot_for_fixed_stow' for c in metadata['candidates'])
    metadata['unattempted_finish_candidate_ids'] = [r['public']['candidate_id'] for r in ordered[2:]]
    fallback = None
    for ordinal, record in enumerate(ordered[:2]):
        remaining = deadline-monotonic()
        if remaining <= 0.:
            break
        candidate_dir = directory/f'finish-{ordinal:03d}'
        share = (min(remaining, 900., max(600., .8*remaining))
                 if ordinal == 0 and len(ordered) > 1 else remaining)
        before = monotonic()
        attempt = dict(candidate_id=record['public']['candidate_id'],
                       budget_s=share, complete_replay_legal=True, full_checker_passed=None)
        metadata['finish_attempts'].append(attempt)
        try:
            candidate, details = _load('ballast').finish_candidate(instance,
                record['scored'].plan, seed=seed+ordinal, budget_s=share, out=candidate_dir)
            details = details or {}
            attempt.update(finisher=_safe(details), binding_rules=_binding(details),
                           full_checker_passed=details.get('full_checker_passed'))
            if candidate is not None:
                path = candidate_dir/'adapter-candidate-plan.json'
                _write(path, candidate)
                attempt.update(candidate_plan_path=str(path.relative_to(ROOT)),
                               candidate_plan_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                fallback = candidate
            if candidate is not None and details.get('full_checker_passed') is True:
                metadata['full_checker_passes_claimed_by_adapter'] += 1
                attempt['wall_s'] = monotonic()-before
                metadata['pipeline_wall_s_before_runner_check'] = monotonic()-private['started']
                _snapshot(metadata)
                _write(directory/'pipeline.json', metadata)
                return candidate, dict(status='candidate_passed_finisher_check',
                    independent_runner_check_required=True, cold_pipeline=metadata)
        except Exception as error:
            attempt.update(status='finisher_error', error=f'{type(error).__name__}: {error}')
        finally:
            attempt['wall_s'] = monotonic()-before
            _snapshot(metadata)
            _write(directory/'pipeline.json', metadata)
    metadata.update(pipeline_wall_s_before_runner_check=monotonic()-private['started'],
                    remaining_map_budget_s=max(0., private['deadline']-monotonic()))
    _snapshot(metadata)
    _write(directory/'pipeline.json', metadata)
    # Returning a failed best candidate lets the independent runner retain its
    # real failed rule IDs. It never turns this finisher result into acceptance.
    return fallback, dict(status='unknown', message='No finisher full-checker pass',
        independent_runner_check_required=True, cold_pipeline=metadata)


def main():
    print(json.dumps(dict(owned_pipeline_pid=os.getpid(), load_average=list(os.getloadavg()))), flush=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--set', default='first', choices=('first', 'medium12', 'quick'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--budget-s', type=float, default=120.)
    parser.add_argument('--finalize-s', type=float, default=1080.)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--workers', type=int, default=1, choices=(1, 2))
    args = parser.parse_args()
    if args.workers > 1 and (LAB/'c1/leader/BENCH-RUNNING').exists():
        parser.error('benchmark marker requires one busy process across this team')
    out = args.out.resolve()
    if not out.is_relative_to(HERE):
        parser.error('output must remain inside c4/c2-benders')
    if out.exists() and any(out.glob('*.json')):
        parser.error('use a fresh output directory; helper changes are not in shared-runner cache keys')
    os.environ['C4_DETAIL_DIR'] = str(out/'details')
    sys.path[:0] = [str(ROOT/'src'), str(LAB.parent), str(LAB)]
    import score
    selected = str(HERE/'first-map.txt') if args.set == 'first' else args.set
    return score.main(['--set', selected, '--method', str(Path(__file__))+':propose',
        '--finish', str(Path(__file__))+':finish', '--budget-s', str(args.budget_s),
        '--finalize-s', str(args.finalize_s), '--tries', '1', '--workers', str(args.workers),
        '--seed', str(args.seed), '--out', str(out)])


if __name__ == '__main__':
    raise SystemExit(main())

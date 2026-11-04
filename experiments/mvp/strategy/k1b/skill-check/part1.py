"""D205: deterministic, serial pre-finisher audit of all stored K1b samples.

No finisher, timeout, worker pool, or prior verdict is used. Only outputs below
this directory are written. Resume requires the exact source/input binding.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
import hashlib
import importlib
import json
from math import comb
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
K1B = HERE.parent
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
             'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS',
             'RAYON_NUM_THREADS', 'POLARS_MAX_THREADS'):
    os.environ[name] = '1'
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'cache/vessels')
sys.path[:0] = [str(ROOT/'experiments/mvp/strategy'), str(ROOT/'experiments/mvp/frontier')]

import compact
from alns import Problem, _mask_ok
from sight import sight_clear
from stowage.checker.rules import check_static_rules
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.moves import apply_plan
from stowage.checker import visibility
from stowage.vessel import fleet, fleet_cache
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.conventions import canonical_vessel_id

ARMS = {'8.2': K1B, 'null': K1B/'null', 'K1r': K1B/'k1r'}
VALIDATED_MEMO = False
ORIGINAL_LOAD_VESSEL = fleet.load_vessel
VESSEL_MEMO = {}
VALIDATED_VESSEL_MEMO = False


def sha(data):
    return hashlib.sha256(data).hexdigest()


def publish(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)


def memo_geometry_vessel(name, *, with_hull=True):
    """Exact production-key memo; every caller owns an independent deep copy."""
    global VALIDATED_VESSEL_MEMO
    if with_hull:
        return ORIGINAL_LOAD_VESSEL(name, with_hull=True)
    canonical = canonical_vessel_id(name)
    payload = fleet._parameter_bytes(canonical)
    cache_slice = 'full.rule-freeze-v1' if rule_freeze_v1_enabled() else 'full'
    key = fleet_cache._cache_key(canonical, cache_slice, False, payload)
    if key not in VESSEL_MEMO:
        # Exceptions are never memoized. Keep all original model validation,
        # cache/provenance checks and raw imdg declaration handling on misses.
        VESSEL_MEMO[key] = ORIGINAL_LOAD_VESSEL(name, with_hull=False).model_copy(deep=True)
    result = VESSEL_MEMO[key].model_copy(deep=True)
    if not VALIDATED_VESSEL_MEMO:
        independent = ORIGINAL_LOAD_VESSEL(name, with_hull=False)
        assert result.model_dump_json() == independent.model_dump_json()
        assert result is not VESSEL_MEMO[key]
        assert not result.cells or result.cells[0] is not VESSEL_MEMO[key].cells[0]
        publish(HERE/'vessel-memo-validation.json', dict(name=canonical, production_key=key,
                full_serialization_exact_equal=True, deep_copy_isolated=True,
                independent_original_sha256=sha(independent.model_dump_json().encode()),
                memo_sha256=sha(result.model_dump_json().encode())))
        VALIDATED_VESSEL_MEMO = True
    return result


fleet.load_vessel = memo_geometry_vessel


def exact(a, b):
    n = a+b
    return min(1., 2*sum(comb(n, i) for i in range(min(a, b)+1))/2**n) if n else 1.


def progress(status, done, **details):
    publish(HERE/'part1-status.json', dict(status=status, pid=os.getpid(), processed=done,
            sample0_complete=done >= 165, updated_at=datetime.now(timezone.utc).isoformat(), **details))


def wait_supplementary(done, arm, iid, sample):
    """Yield all supplementary CPU until the matched cohort has finished."""
    while True:
        try:
            matched = json.loads((HERE/'matched/status.json').read_text())
        except (OSError, ValueError) as error:
            matched = dict(status='unavailable', error=f'{type(error).__name__}: {error}')
        try:
            # The shared module may be published after this serial worker starts.
            name = 'sharedadmission' if (HERE/'sharedadmission.py').exists() else 'admission'
            admission = importlib.import_module(name).probe()
        except Exception as error:
            admission = dict(admitted=False, errors=[f'{type(error).__name__}: {error}'])
        try:
            mask = admission.get('core_mask', admission.get('mask'))
            load = admission.get('load1')
            n_cores = int(mask).bit_count()
            spare = not isinstance(mask, bool) and int(mask) == mask and load+1 <= n_cores
        except (TypeError, ValueError, OverflowError):
            n_cores, spare = None, False
        allowed = matched.get('status') == 'complete' and admission.get('admitted') is True and spare
        progress('supplementary_running' if allowed else 'supplementary_waiting', done,
                 arm=arm, instance_id=iid, sample=sample, admission=admission,
                 matched_status=matched.get('status'), recommended_core_count=n_cores,
                 spare_core=spare, admission_policy='fresh<120s; mask>=4095; load1<=30; load1+1<=popcount(mask); matched complete')
        if allowed:
            return admission
        time.sleep(20)


def source_binding():
    paths = [Path(__file__), K1B/'kcurve.py', K1B/'judge.py',
             *sorted((ROOT/'src').rglob('*.py')),
             *sorted((ROOT/'params').rglob('*.toml')),
             ROOT/'experiments/mvp/strategy/compact.py',
             ROOT/'experiments/mvp/frontier/alns.py', ROOT/'experiments/mvp/frontier/sight.py']
    values = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in paths}
    # Timing/reporting-only runner changes can resume previously bound checks;
    # preserve their full source manifest and verify every imported source.
    old_path = HERE/'source-hashes.json'
    allowed = set()
    if old_path.exists():
        old = json.loads(old_path.read_text())
        runner = str(Path(__file__).relative_to(ROOT))
        if {k:v for k,v in old.items() if k != runner} == {k:v for k,v in values.items() if k != runner}:
            digest = sha(json.dumps(old, sort_keys=True).encode())
            allowed.add(digest)
            publish(HERE/'source-manifests'/f'{digest}.json', old)
    publish(HERE/'source-hashes.json', values)
    current = sha(json.dumps(values, sort_keys=True).encode())
    allowed.add(current)
    for archive in (HERE/'source-manifests').glob('*.json'):
        previous = json.loads(archive.read_text())
        runner = str(Path(__file__).relative_to(ROOT))
        if {k:v for k,v in previous.items() if k != runner} == {k:v for k,v in values.items() if k != runner}:
            allowed.add(archive.stem)
    return current, allowed


def deterministic(instance, problem, plan):
    global VALIDATED_MEMO
    timings = {}
    started = time.monotonic()
    replay = apply_plan(instance, plan)
    timings['move_replay_s'] = round(time.monotonic()-started, 4)
    aboard = {p.container_id for p in replay.final_state.ship.placements}
    missing = sorted(set(instance.load_list)-aboard)
    started = time.monotonic()
    original = visibility._loading_condition
    memo = {}
    instance_key = sha(instance.model_dump_json().encode())

    def memo_loading(arg_instance, ship):
        key = (instance_key if arg_instance is instance else sha(arg_instance.model_dump_json().encode()),
               sha(ship.model_dump_json().encode()))
        if key not in memo:
            memo[key] = original(arg_instance, ship)
        return memo[key]

    if not VALIDATED_MEMO:
        ship = replay.final_state.ship.model_copy(update={'tank_masses_kg': dict(replay.final_state.ship.tank_masses_kg)})
        first = original(instance, ship)
        second = memo_loading(instance, ship)
        third = memo_loading(instance, ship)
        fields = ('mass_kg', 'lcg_m', 'tcg_m', 'kg_m', 'gm_m', 't_aft_m', 't_fwd_m', 'heel_rad')
        assert all(getattr(first, f) == getattr(second, f) == getattr(third, f) for f in fields)
        assert first.geometry is second.geometry and second is third
        publish(HERE/'memo-validation.json', dict(instance_id=instance.id, chosen_sha256=sha(plan.model_dump_json().encode()),
                input_key=instance_key, exact_numeric_equal=True, geometry_identical=True, cache_hit_identity=True,
                fields={f:getattr(first,f) for f in fields}, source_function='stowage.checker.visibility._loading_condition'))
        VALIDATED_MEMO = True
    visibility._loading_condition = memo_loading
    try:
        clear, sight = sight_clear(instance, plan, {})
    finally:
        visibility._loading_condition = original
    timings['sight_lashing_s'] = round(time.monotonic()-started, 4)
    print(json.dumps(dict(stage='sight_lashing_done', instance_id=instance.id, timings=timings)), flush=True)
    started = time.monotonic()
    static = check_static_rules(instance, replay.final_state.ship, call_index=instance.call_index)
    static_rules = sorted({v.rule_id for v in static.violations if v.severity == 'hard'})
    static_ok = static.ran and not static.error and not static_rules
    timings['static_s'] = round(time.monotonic()-started, 4)
    print(json.dumps(dict(stage='static_done', instance_id=instance.id, timings=timings)), flush=True)
    started = time.monotonic()
    try:
        actions = list(problem.space.from_plan(instance, plan))
        frontier_mask = _mask_ok(problem, actions)
    except Exception as error:
        frontier_mask = f'{type(error).__name__}: {error}'
    timings['frontier_mask_s'] = round(time.monotonic()-started, 4)
    return dict(move_legal=replay.legal, load_complete=not missing, missing_loads=missing,
                sight_clear=clear, sight=sight, static_ok=bool(static_ok), static_rules=static_rules,
                static_ran=static.ran, static_error=static.error,
                frontier_mask=frontier_mask, timings=timings,
                all_clear=bool(replay.legal and not missing and clear and static_ok
                               and frontier_mask is True))


def summary(rows, interim=False):
    stats = {}
    fields = ('move_legal', 'load_complete', 'sight_clear', 'static_ok', 'frontier_mask', 'all_clear')
    for arm in ARMS:
        stats[arm] = {}
        for label, subset in [('all', [r for r in rows if r['arm'] == arm]),
                              ('stochastic', [r for r in rows if r['arm'] == arm and r['sample'] > 0]),
                              *[(str(i), [r for r in rows if r['arm'] == arm and r['sample'] == i]) for i in range(16)]]:
            complete = sum(r['complete'] for r in subset)
            stats[arm][label] = dict(n=len(subset), complete=complete, **{
                f: dict(count=sum(r.get(f) is True for r in subset),
                        rate_all=sum(r.get(f) is True for r in subset)/len(subset) if subset else None,
                        rate_complete=sum(r.get(f) is True for r in subset)/complete if complete else None)
                for f in fields})
    pairs = []
    zero = {a: {r['instance_id']: r for r in rows if r['arm'] == a and r['sample'] == 0} for a in ARMS}
    for a, b in combinations(ARMS, 2):
        for field in ('complete', *fields):
            only_a = sum(zero[a][i].get(field) is True and zero[b][i].get(field) is not True for i in zero[a])
            only_b = sum(zero[b][i].get(field) is True and zero[a][i].get(field) is not True for i in zero[a])
            pairs.append(dict(a=a, b=b, field=field, only_a=only_a, only_b=only_b, exact_p=exact(only_a, only_b)))
    result = dict(n_rows=len(rows), n_maps=55, native_threads=1, workers=1, reference_tanks={},
                  generated_utc=datetime.now(timezone.utc).isoformat(), stats=stats, sample0_pairs=pairs,
                  errors=[{k:r.get(k) for k in ('arm','instance_id','sample','error')} for r in rows if r.get('error')])
    stem = 'part1-sample0' if interim else 'part1'
    result['interim'] = interim
    publish(HERE/f'{stem}-summary.json', result)
    lines = ['# D205 Part 1: deterministic plan audit', '',
             f"{'Sample-0 interim: 165 stored samples (55 validation maps × 3 arms)' if interim else 'All 2,640 stored samples (55 validation maps × 16 indices × 3 arms)'}, one serial CPU worker. "
             'No clock-dependent solver or finisher ran. Compaction and strict-cheaper selection are identical to `kcurve.py`. '
             'The frontier sight/lashing gate uses `sight_clear(instance, chosen, {})`, hence the same default tanks for every arm. '
             'A clear gate is a deterministic screening result, not a finisher pass.', '',
             'Sample 0 is greedy for 8.2 and null; K1r sample 0 is random. Missing plans count as failures in all-map denominators. '
             'Additional checks: move replay legality, every load-list box aboard, static cargo rules, and frontier action mask replay. '
             'All-clear requires every check. The judge’s token-mask replay is post-finisher and is excluded from this pre-finisher audit.', '',
             'A per-gate memo caches `_loading_condition` by exact full instance and ship JSON digests. '
             'Visibility and lashing request identical arguments; the original function computes each unique value. '
             '`memo-validation.json` records exact scalar equality against an independent original evaluation and identical geometry. '
             'The wrapper is restored after each gate. No tolerance, solver, or physics formula changes.', '',
             'Geometry-only parent-vessel loads additionally memoize successful original results by the exact production '
             '`fleet_cache._cache_key`, including current source/parameter/dependency inputs and freeze-specific slice. '
             'Every caller receives an independent deep copy. `vessel-memo-validation.json` verifies exact whole-model '
             'serialization against an independent original load and copy isolation.', '',
             '| Arm | Sample | Complete | Sight/lashing clear / 55 | Clear / complete | All checks / 55 |',
             '|---|---:|---:|---:|---:|---:|']
    for arm in ARMS:
        for i in range(1 if interim else 16):
            s = stats[arm][str(i)]
            lines.append(f"| {arm} | {i}{' (greedy)' if i == 0 and arm != 'K1r' else ' (random)' if i == 0 else ''} | {s['complete']} | {s['sight_clear']['count']}/55 ({s['sight_clear']['rate_all']:.1%}) | {s['sight_clear']['count']}/{s['complete']} ({s['sight_clear']['rate_complete']:.1%}) | {s['all_clear']['count']}/55 |")
    lines += ['', 'Sample 0 paired exact two-sided sign tests (missing or false is a failure):', '',
              '| Check | A | B | Only A | Only B | Exact p |', '|---|---|---|---:|---:|---:|']
    for p in pairs:
        lines.append(f"| {p['field']} | {p['a']} | {p['b']} | {p['only_a']} | {p['only_b']} | {p['exact_p']:.8g} |")
    lines += ['', f"Processing errors: {len(result['errors'])}. Exact p = min(1, 2 × lower binomial tail among discordant pairs); no multiplicity correction.", '',
              'Per-candidate audit rows include source/input/chosen-plan hashes and check details. '
              '`prepared/{arm}/{instance_id}.json` contains each nonmissing sample-0 chosen MovePlan for the matched judge. '
              'Full machine-readable rates and all pairwise checks are in `part1-summary.json`.']
    if not interim:
        lines += ['', 'Pooled rates (candidate-level descriptive rates; candidates on the same map are dependent):', '',
                  '| Arm | Samples | Complete | Sight/lashing clear | All checks clear |', '|---|---|---:|---:|---:|']
        for arm in ARMS:
            for label in ('all', 'stochastic'):
                s = stats[arm][label]
                lines.append(f"| {arm} | {label} | {s['complete']}/{s['n']} | {s['sight_clear']['count']}/{s['n']} ({s['sight_clear']['rate_all']:.1%}); {s['sight_clear']['count']}/{s['complete']} among complete | {s['all_clear']['count']}/{s['n']} |")
    (HERE/f'{stem}.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({a:stats[a]['0'] for a in ARMS}, sort_keys=True), flush=True)


def main():
    (HERE/'part1.pid').write_text(str(os.getpid())+'\n')
    progress('sample0_running', 0)
    started = time.monotonic()
    manifest = json.loads((K1B/'manifest.json').read_text())
    files = manifest['inputs']
    assert len(files) == 55
    # Read existing immutable cache entries through local symlinks. Any atomic
    # cache replacement creates/replaces the local entry, never its source.
    cache_dir = HERE/'cache/vessels'
    cache_dir.mkdir(parents=True, exist_ok=True)
    for existing in (ROOT/'experiments/cache/vessels').glob('*.json'):
        local = cache_dir/existing.name
        if not local.exists() and not local.is_symlink():
            local.symlink_to(existing)
    sources_sha256, allowed_sources = source_binding()
    rows = []
    for phase, rel, expected in [(phase, rel, expected) for phase in ('sample0', 'stochastic') for rel, expected in files.items()]:
        assert '/corpus/val/' in rel and '/test/' not in rel and 'test-v3' not in rel
        input_sha = sha((ROOT/rel).read_bytes())
        assert input_sha == expected, f'input changed: {rel}'
        instance = load_instance(ROOT/rel)
        problem = Problem(instance)
        cache = {}
        for arm, folder in ARMS.items():
            paths = sorted((folder/'candidates'/instance.id).glob('sample-*.json'))
            assert len(paths) == 16, (arm, instance.id, len(paths))
            for i, path in enumerate(paths):
                if (phase == 'sample0') != (i == 0):
                    continue
                if phase == 'stochastic':
                    wait_supplementary(len(rows), arm, instance.id, i)
                else:
                    progress('sample0_running', len(rows), arm=arm, instance_id=instance.id, sample=i)
                raw = path.read_bytes()
                sample = json.loads(raw)
                assert sample['sample'] == i
                binding = dict(input=rel, input_sha256=input_sha, source=str(path.relative_to(ROOT)),
                               source_sha256=sha(raw), deterministic_source_sha256=sources_sha256)
                target = HERE/'rows'/arm/instance.id/f'sample-{i:02d}.json'
                if target.exists():
                    old = json.loads(target.read_text())
                    old_binding = old.get('binding', {})
                    if old_binding.get('deterministic_source_sha256') in allowed_sources and {k:v for k,v in old_binding.items() if k != 'deterministic_source_sha256'} == {k:v for k,v in binding.items() if k != 'deterministic_source_sha256'}:
                        rows.append(old)
                        continue
                row = dict(arm=arm, instance_id=instance.id, sample=i, binding=binding,
                           complete=sample['plan'] is not None, source_status=sample.get('status'))
                if row['complete']:
                    try:
                        plan = MovePlan.model_validate(sample['plan'])
                        tick = time.monotonic()
                        compacted, info = compact.compact_plan(instance, plan)
                        row['compaction_s'] = round(time.monotonic()-tick, 4)
                        print(json.dumps(dict(stage='compaction_done', arm=arm, instance_id=instance.id, sample=i, compaction_s=row['compaction_s'])), flush=True)
                        better = compacted is not None and info['compact_raw']['total'] < info['before']['total']
                        chosen = compacted if better else plan
                        encoded = chosen.model_dump_json().encode()
                        row.update(compacted=better, compaction=info, chosen_sha256=sha(encoded),
                                   cost=info['compact_raw']['total'] if better else info['before']['total'],
                                   signature=sha(json.dumps([m.model_dump(mode='json') for m in chosen.moves], sort_keys=True).encode()))
                        if i == 0:
                            dest = HERE/'prepared'/arm/f'{instance.id}.json'
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(encoded)
                            row['prepared_path'] = str(dest.relative_to(ROOT))
                        key = sha(json.dumps(chosen.model_dump(mode='json'), sort_keys=True).encode())
                        if key not in cache:
                            cache[key] = deterministic(instance, problem, chosen)
                        row.update(cache[key])
                    except Exception as error:
                        row['error'] = f'{type(error).__name__}: {error}'
                publish(target, row)
                rows.append(row)
            print(json.dumps(dict(arm=arm, instance_id=instance.id, done=len(rows), wall_s=round(time.monotonic()-started, 1))), flush=True)
        if phase == 'sample0' and len(rows) == 165:
            summary(rows, interim=True)
    assert len(rows) == 2640
    summary(rows)
    progress('complete', len(rows))


if __name__ == '__main__':
    main()

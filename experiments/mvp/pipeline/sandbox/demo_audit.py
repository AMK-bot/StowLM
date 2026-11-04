"""Frozen, outcome-blind judge drags on the exact synthetic demo instance."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
from pathlib import Path
import math
import os
import random
import statistics
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .benchmark import HERE, ROOT
from .holdout import read, write, now, capacity, admitted, positions

FIXTURES = HERE / 'fixtures'
SEED = 170730
SHOWCASE_EXCLUSIONS = {('QZXU0000014', '0220582'), ('QZXU0000225', '0010186'),
                       ('QZXU0000821', '0100288')}


def execution_sources():
    paths = [HERE / name for name in ('moves.py', 'physics.py', 'securing.py', 'visibility.py',
                                     'explain.py', 'demo_audit.py', 'demo_cache.py')]
    paths.extend(p for p in (ROOT / 'src/stowage').rglob('*')
                 if p.is_file() and p.suffix in ('.py', '.json', '.toml'))
    paths.extend(p for p in (ROOT / 'params').rglob('*')
                 if p.is_file() and p.suffix in ('.json', '.toml'))
    return {str(p.relative_to(ROOT)): sha256_file(p) for p in sorted(set(paths))}


def admit(deadline):
    while time.time() < deadline:
        if (FIXTURES / 'demo-audit.cancel').exists():
            raise RuntimeError('Demo audit cancelled; frozen requests retained')
        try:
            state = capacity()
            if admitted(state) and not (FIXTURES / 'demo-audit.pause').exists():
                return state
        except (OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(2)
    raise RuntimeError('Demo audit admission deadline reached; frozen requests retained')


def prior_requests():
    """All recorded earlier drags, including demos, are excluded by pair."""
    requests = set(SHOWCASE_EXCLUSIONS)
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get('box_id'), str) and isinstance(value.get('target_slot'), str):
                requests.add((value['box_id'], value['target_slot']))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for name in ('demo.json', 'clear190.json', 'clear190-current.json', 'clear190-final.json',
                 'holdout.manifest.json'):
        path = FIXTURES / name
        if path.exists():
            visit(read(path))
    return requests


def candidate_pool(instance, plan, excluded):
    """Only geometry and cargo facts; no replay, fast or full candidate verdict."""
    from stowage.conventions import cells_of_slot
    from stowage.moves import apply_plan
    from .clear_benchmark import candidates
    ship = apply_plan(instance, plan).final_state.ship
    sources = {p.container_id: p.slot for p in ship.placements}
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    masses = sorted(boxes[b].gross_mass_kg for b in sources)
    heavy_threshold = masses[math.floor(.75 * (len(masses) - 1))]
    span = max(c.lcg_m for c in cells.values()) - min(c.lcg_m for c in cells.values())
    far_threshold = span * .3
    def location(slot):
        footprint = cells_of_slot(slot)
        return (statistics.mean(cells[c].lcg_m for c in footprint),
                sorted({stacks[cells[c].stack_id].hatch_cover_id or 'fixed-deck' for c in footprint}))
    pool = []
    for kind, (box, target) in candidates(instance, plan):
        if (box, target) in excluded:
            continue
        source_x, source_hatches = location(sources[box])
        target_x, target_hatches = location(target)
        distance = abs(target_x - source_x)
        tags = []
        if distance >= far_threshold:
            tags.append('far')
        if boxes[box].gross_mass_kg >= heavy_threshold:
            tags.append('heavy')
        if source_hatches != target_hatches:
            tags.append('cross_hatch')
        if tags:
            pool.append({'kind': kind, 'box_id': box, 'target_slot': target,
                         'source_slot': sources[box], 'gross_mass_kg': boxes[box].gross_mass_kg,
                         'longitudinal_distance_m': distance, 'source_hatches': source_hatches,
                         'target_hatches': target_hatches, 'tags': tags})
    return pool, {'heavy_mass_threshold_kg': heavy_threshold,
                  'far_distance_threshold_m': far_threshold,
                  'far_definition': 'At least 30% of modeled longitudinal cell span',
                  'heavy_definition': 'Top cargo-mass quartile, ties included',
                  'cross_hatch_definition': 'Different exact set of hatch cover panel IDs'}


def select_requests(pool, seed=SEED):
    """Ten per assigned stratum; five swaps and five empty targets in each."""
    draw = list(range(len(pool)))
    random.Random(seed).shuffle(draw)
    selected, used, boxes = [], set(), Counter()
    # Prefer 30 different boxes; relax to two per box only if a stratum needs it.
    for category in ('far', 'heavy', 'cross_hatch'):
        for kind in ('swap', 'empty'):
            needed = 5
            for max_per_box in (1, 2):
                for index in draw:
                    row = pool[index]
                    if (index in used or category not in row['tags'] or row['kind'] != kind
                            or boxes[row['box_id']] >= max_per_box):
                        continue
                    selected.append(dict(row, request_index=len(selected), draw_index=index,
                                         assigned_stratum=category))
                    used.add(index)
                    boxes[row['box_id']] += 1
                    needed -= 1
                    if needed == 0:
                        break
                if needed == 0:
                    break
            if needed:
                raise ValueError(f'Insufficient geometry-only {category}/{kind} candidates')
    if len(boxes) < 20:
        raise ValueError('Need at least 20 distinct dragged boxes')
    return selected, draw


def freeze(source, *, seed=SEED):
    from stowage.model import Instance, MovePlan
    source = Path(source).resolve()
    original = read(source)
    instance, plan = Instance.model_validate(original['instance']), MovePlan.model_validate(original['plan'])
    instance_sha, plan_sha = sha256_json(instance.model_dump(mode='json')), sha256_json(plan.model_dump(mode='json'))
    protocol = {'schema': 'stowlm.sandbox-demo-audit-protocol/1', 'synthetic': True,
                'created_utc': now(), 'created_unix': time.time(), 'seed': seed,
                'source_path': str(source.relative_to(ROOT)), 'source_file_sha256': sha256_file(source),
                'instance_sha256': instance_sha, 'plan_sha256': plan_sha, 'n_boxes': len(instance.load_list),
                'target_requests': 30, 'assigned_strata': {'far': 10, 'heavy': 10, 'cross_hatch': 10},
                'kinds_per_stratum': {'swap': 5, 'empty': 5}, 'minimum_distinct_boxes': 20,
                'maximum_requests_per_box': 2,
                'selection': 'Seeded geometry/cargo-only candidates. No candidate replay, fast screen or full outcome selects requests.',
                'cache_selection': 'Fixed request indices 0..24 before any candidate full outcomes; no outcome filtering.',
                'post_outcome_policy': 'No gate tuning, request replacement, or outcome-dependent cache selection.',
                'candidate_full_checks_started': 0, 'pid': os.getpid()}
    write(FIXTURES / 'demo-audit.protocol.json', protocol, exclusive=True)
    write(FIXTURES / 'demo-audit.source.json', original, exclusive=True)
    excluded = prior_requests()
    pool, definitions = candidate_pool(instance, plan, excluded)
    requests, draw = select_requests(pool, seed)
    write(FIXTURES / 'demo-audit.draw.json', {'seed': seed, 'pool': pool, 'draw': draw,
          'definitions': definitions, 'excluded_prior_pairs': [list(v) for v in sorted(excluded)]}, exclusive=True)
    manifest = {'schema': 'stowlm.sandbox-demo-audit/1', 'synthetic': True,
                'frozen_utc': now(), 'frozen_unix': time.time(), 'seed': seed,
                'source_sha256': sha256_file(FIXTURES / 'demo-audit.source.json'),
                'protocol_sha256': sha256_file(FIXTURES / 'demo-audit.protocol.json'),
                'draw_sha256': sha256_file(FIXTURES / 'demo-audit.draw.json'),
                'instance_sha256': instance_sha, 'plan_sha256': plan_sha,
                'n_boxes': len(instance.load_list), 'requests': requests,
                'cache_request_indices': list(range(25)), 'candidate_full_checks_started': 0,
                'source_code_at_freeze': execution_sources()}
    write(FIXTURES / 'demo-audit.manifest.json', manifest, exclusive=True)
    with (FIXTURES / 'demo-audit.manifest.sha256').open('x') as stream:
        stream.write(sha256_file(FIXTURES / 'demo-audit.manifest.json') + '\n')
    print({'frozen': 30, 'boxes': len({r['box_id'] for r in requests}),
           'manifest_sha256': sha256_file(FIXTURES / 'demo-audit.manifest.json')}, flush=True)


def verify_manifest():
    manifest = read(FIXTURES / 'demo-audit.manifest.json')
    digest = sha256_file(FIXTURES / 'demo-audit.manifest.json')
    if digest != (FIXTURES / 'demo-audit.manifest.sha256').read_text().strip():
        raise ValueError('Frozen manifest changed')
    for stem in ('source', 'protocol', 'draw'):
        if manifest[f'{stem}_sha256'] != sha256_file(FIXTURES / f'demo-audit.{stem}.json'):
            raise ValueError(f'Frozen {stem} changed')
    return manifest, digest


def ready(note):
    from stowage.model import Instance, MovePlan, CheckReport
    from stowage.checker.full import report_matches_inputs
    from stowage.versions import component_versions
    if not note.strip():
        raise ValueError('Root baseline-passed and verdict-code-stable note required')
    manifest, digest = verify_manifest()
    protocol = read(FIXTURES / 'demo-audit.protocol.json')
    if sha256_file(ROOT / protocol['source_path']) != protocol['source_file_sha256']:
        raise ValueError('Original demo source changed after freeze')
    source = read(FIXTURES / 'demo-audit.source.json')
    baseline_path = FIXTURES / 'demo_hero_baseline.json'
    baseline = read(baseline_path)
    report = CheckReport.model_validate(baseline['baseline_report'])
    if (baseline['source_sha256'] != protocol['source_file_sha256'] or not report.passed
            or not report_matches_inputs(report, Instance.model_validate(source['instance']),
                                         MovePlan.model_validate(source['plan']))):
        raise ValueError('Exact refreshed demo needs a passing, input-bound baseline')
    pins = execution_sources()
    write(FIXTURES / 'demo-audit.readiness.json', {'created_utc': now(), 'created_unix': time.time(),
          'manifest_sha256': digest, 'note': note, 'source_sha256': pins,
          'source_fingerprint': sha256_json(pins), 'candidate_full_checks_started': 0,
          'component_versions': component_versions(),
          'fresh_baseline_sha256': sha256_file(baseline_path)}, exclusive=True)


def verify_ready():
    from stowage.versions import component_versions
    manifest, digest = verify_manifest()
    record = read(FIXTURES / 'demo-audit.readiness.json')
    if (record['manifest_sha256'] != digest or record['source_sha256'] != execution_sources()
            or record['component_versions'] != component_versions()):
        raise ValueError('Execution dependencies changed after pre-full readiness')
    return manifest, digest, record


def _init(deadline):
    global _instance, _plan, _manifest, _manifest_sha, _ready, _deadline
    from stowage.model import Instance, MovePlan
    _manifest, _manifest_sha, _ready = verify_ready()
    source = read(FIXTURES / 'demo-audit.source.json')
    _instance, _plan = Instance.model_validate(source['instance']), MovePlan.model_validate(source['plan'])
    _deadline = deadline
    print({'worker_pid': os.getpid()}, flush=True)


def _measure(frozen):
    from .moves import prepare_move, try_move, _snapshot, _same_placements
    from .explain import explain_verdict
    import stowage.checker
    from stowage.checker.full import report_matches_inputs
    verify_ready()
    load = admit(_deadline)
    prepared = prepare_move(_instance, _plan, frozen['box_id'], frozen['target_slot'])
    reached = _same_placements(prepared.replay.final_state.ship, prepared.ship)
    expected_instance, expected_plan = (_instance, prepared.plan) if reached else _snapshot(prepared)
    expected_instance_sha = sha256_json(expected_instance.model_dump(mode='json'))
    expected_plan_sha = sha256_json(expected_plan.model_dump(mode='json'))
    calls, screens = [], []
    canonical = stowage.checker.check
    def capture(instance, plan, *args, **kwargs):
        started = time.time()
        clock = time.perf_counter()
        report = canonical(instance, plan, *args, **kwargs)
        calls.append({'started_unix': started, 'wall_s': time.perf_counter() - clock,
                      'instance_sha256': sha256_json(instance.model_dump(mode='json')),
                      'plan_sha256': sha256_json(plan.model_dump(mode='json')),
                      'input_binding_valid': report_matches_inputs(report, instance, plan),
                      'report': report.model_dump(mode='json')})
        return report
    def screen(verdict):
        screens.append({'verdict': verdict.model_dump(mode='json'), 'load_after': capacity()})
    started_unix, started_utc = time.time(), now()
    stowage.checker.check = capture
    try:
        full = try_move(_instance, _plan, frozen['box_id'], frozen['target_slot'], on_fast=screen)
    finally:
        stowage.checker.check = canonical
    if len(calls) != 1 or len(screens) != 1:
        raise RuntimeError('Each audit drag requires one fresh canonical call and one fast frame')
    call = calls[0]
    if (not call['input_binding_valid'] or call['instance_sha256'] != expected_instance_sha
            or call['plan_sha256'] != expected_plan_sha):
        raise RuntimeError('Canonical call did not judge exact requested candidate or its diagnostic snapshot')
    verify_ready()
    return {'request_index': frozen['request_index'], 'box_id': frozen['box_id'],
            'target_slot': frozen['target_slot'], 'kind': frozen['kind'], 'assigned_stratum': frozen['assigned_stratum'],
            'manifest_sha256': _manifest_sha, 'started_unix': started_unix, 'started_utc': started_utc,
            'completed_utc': now(), 'pid': os.getpid(), 'admission': load, 'load_after': capacity(),
            'fast_load_after': screens[0]['load_after'], 'fast': screens[0]['verdict'],
            'full': full.model_dump(mode='json'), 'explanation': explain_verdict(full).model_dump(mode='json'),
            'requested_state_sha256': positions(prepared.ship),
            'candidate_plan_sha256': sha256_json(prepared.plan.model_dump(mode='json')),
            'canonical_call': call, 'canonical_invocations': 1, 'full_origin': 'fresh_canonical_call',
            'checker_input_kind': 'candidate_plan' if reached else 'diagnostic_requested_stow_snapshot',
            'diagnostic_snapshot_cannot_approve_original_plan': not reached,
            'replay_legal': prepared.replay.legal, 'replay_executed': prepared.replay.executed,
            'source_fingerprint': _ready['source_fingerprint'],
            'readiness_sha256': sha256_file(FIXTURES / 'demo-audit.readiness.json'),
            'native_thread_caps': {k: os.environ.get(k) for k in
                 ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')}}


def summarize(rows):
    clears = [r for r in rows if r['fast']['legal']]
    false = [r for r in clears if not r['full']['legal']]
    def timing(values):
        values = sorted(values)
        return {'median_s': statistics.median(values), 'p95_s': values[math.ceil(.95*len(values))-1], 'max_s': max(values)} if values else None
    outcomes = Counter(r['full']['gauges'].get('outcome') or 'check_unavailable' for r in rows)
    return {'moves': len(rows), 'fresh_canonical_checks': len(rows), 'fast_clear': len(clears),
            'false_legal': len(false), 'false_legal_fraction_of_fast_clear': len(false)/len(clears) if clears else None,
            'false_legal_rules': dict(Counter(rule for r in false for rule in {b['rule_id'] for b in r['full']['broken']})),
            'outcomes': dict(outcomes), 'legal': sum(r['full']['legal'] for r in rows),
            'diagnostic_snapshots': sum(r['checker_input_kind'] != 'candidate_plan' for r in rows),
            'strata': dict(Counter(r['assigned_stratum'] for r in rows)), 'kinds': dict(Counter(r['kind'] for r in rows)),
            'fast_response': timing([r['fast']['wall_s'] for r in rows]),
            'full_api_response': timing([r['full']['wall_s'] for r in rows]),
            'canonical_checker': timing([r['canonical_call']['wall_s'] for r in rows]),
            'load1_min': min((r['admission']['load1'] for r in rows), default=None),
            'load1_max': max((r['admission']['load1'] for r in rows), default=None)}


def measure(*, workers=4, deadline_s=7200):
    from .demo_cache import build_cache
    manifest, digest, readiness = verify_ready()
    directory = FIXTURES / 'demo-audit.rows'
    directory.mkdir(exist_ok=True)
    rows = [read(p) for p in sorted(directory.glob('request-*.json'))]
    if any(r['manifest_sha256'] != digest or r['source_fingerprint'] != readiness['source_fingerprint'] for r in rows):
        raise ValueError('Cannot mix cohorts or execution versions')
    done = {r['request_index'] for r in rows}
    pending = [r for r in manifest['requests'] if r['request_index'] not in done]
    write(FIXTURES / f'demo-audit.measure-start-{os.getpid()}.json',
          {'started_utc': now(), 'started_unix': time.time(), 'pid': os.getpid(),
           'workers': workers, 'manifest_sha256': digest}, exclusive=True)
    def save(row):
        if row['started_unix'] <= manifest['frozen_unix']:
            raise ValueError('Candidate full outcome predates freeze')
        write(directory / f"request-{row['request_index']:03d}.json", row, exclusive=True)
        rows.append(row)
        data = {'schema': 'stowlm.sandbox-demo-audit-results/1', 'synthetic': True, 'mode': 'recorded',
                'label': 'Recorded answer', 'manifest_sha256': digest, 'complete': len(rows) == 30,
                'summary': summarize(rows), 'moves': sorted(rows, key=lambda r:r['request_index'])}
        write(FIXTURES / 'demo-audit.results.json', data)
        print({'completed': len(rows), 'index': row['request_index'], 'fast': row['fast']['legal'],
               'full': row['full']['legal'], 'outcome': row['full']['gauges'].get('outcome')}, flush=True)
    deadline = time.time() + deadline_s
    if workers == 1:
        _init(deadline)
        for request in pending:
            save(_measure(request))
    else:
        errors = []
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'),
                                 initializer=_init, initargs=(deadline,)) as pool:
            for future in as_completed([pool.submit(_measure, r) for r in pending]):
                try:
                    save(future.result())
                except Exception as exc:
                    errors.append(f'{type(exc).__name__}: {exc}')
        if errors:
            raise RuntimeError('; '.join(sorted(set(errors))))
    build_cache(FIXTURES / 'demo-audit.results.json', FIXTURES / 'drag-cache.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('freeze', 'ready', 'measure'))
    parser.add_argument('--source', type=Path, default=FIXTURES / 'demo_source_hero.json')
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--note', default='')
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=4)
    parser.add_argument('--deadline-s', type=float, default=7200)
    args = parser.parse_args()
    print({'pid': os.getpid(), 'phase': args.phase}, flush=True)
    if args.phase == 'freeze':
        freeze(args.source, seed=args.seed)
    elif args.phase == 'ready':
        ready(args.note)
    else:
        measure(workers=args.workers, deadline_s=args.deadline_s)


if __name__ == '__main__':
    main()

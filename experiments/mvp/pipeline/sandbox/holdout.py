"""Frozen, fresh screen-clear holdout. No candidate full outcomes select requests."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import types
import multiprocessing
import os
from pathlib import Path
import random
import statistics
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .benchmark import HERE, ROOT

FIXTURES = HERE/'fixtures'
DEFAULT_SOURCE = ROOT/'experiments/mvp/pipeline/lattice/screening/current-v3/084ffe943b997966.json'
SEED = 177017
GATES = ('moves.py', 'physics.py', 'securing.py', 'visibility.py')


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data, *, exclusive=False):
    path = Path(path)
    payload = json.dumps(data, indent=2, allow_nan=False)+'\n'
    if exclusive:
        with path.open('x') as stream:
            stream.write(payload)
    else:
        tmp = path.with_name(path.name+f'.{os.getpid()}.tmp')
        tmp.write_text(payload)
        tmp.replace(path)


def capacity():
    state = read(ROOT/'experiments/fleet/core-state.json')
    return dict(state, age_s=time.time()-state['t'])


def admitted(state):
    return (int(state['mask']).bit_count() >= 8 and 0 <= state['load1'] <= 45
            and 0 <= state['age_s'] < 120)


def admit(deadline):
    while True:
        if time.time() >= deadline or (FIXTURES/'holdout.cancel').exists():
            raise RuntimeError('Holdout cancelled or deadline reached; frozen requests are retained')
        try:
            state = capacity()
            if not (FIXTURES/'holdout.pause').exists() and admitted(state):
                return state
        except (OSError, KeyError, TypeError, ValueError):
            pass
        time.sleep(2)


def snapshot_sources(*, archive=None):
    paths = [ROOT/'src/stowage/versions.py', ROOT/'src/stowage/conventions.py', HERE/'holdout.py',
             HERE/'clear_benchmark.py', *[HERE/name for name in GATES]]
    for folder in ('src/stowage/checker', 'src/stowage/moves', 'src/stowage/model',
                   'experiments/mvp/frontier/engine'):
        paths.extend(p for p in (ROOT/folder).glob('*.py') if not p.name.startswith('test_'))
    paths.extend(p for p in (ROOT/'experiments/mvp/frontier/engine').glob('*manifest*.json'))
    # Optional implementation helpers are attribution, not extra rule gates.
    paths.extend(p for p in HERE.glob('*perf*.py') if not p.name.startswith('test_'))
    pins = {str(p.relative_to(ROOT)): sha256_file(p) for p in sorted(set(paths))}
    if archive:
        archive.mkdir(parents=True, exist_ok=False)
        for p in paths:
            relative = p.relative_to(ROOT)
            target = archive/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(p.read_bytes())
    return pins


def code_content(code):
    """Canonical CodeType values, independent of marshal string-intern flags."""
    def constant(value):
        if isinstance(value, types.CodeType):
            return ['code', code_content(value)]
        if isinstance(value, bytes):
            return ['bytes', value.hex()]
        if isinstance(value, tuple):
            return ['tuple', [constant(v) for v in value]]
        if isinstance(value, frozenset):
            return ['frozenset', sorted((constant(v) for v in value), key=repr)]
        if isinstance(value, float):
            return ['float', value.hex()]
        if isinstance(value, complex):
            return ['complex', value.real.hex(), value.imag.hex()]
        if value is Ellipsis:
            return ['ellipsis']
        return [type(value).__name__, value]
    names = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals',
             'co_stacksize', 'co_flags', 'co_code', 'co_consts', 'co_names', 'co_varnames',
             'co_filename', 'co_name', 'co_qualname', 'co_firstlineno', 'co_linetable',
             'co_exceptiontable', 'co_freevars', 'co_cellvars')
    return {name: constant(getattr(code, name)) for name in names}


def code_fingerprint(code):
    return sha256_json(code_content(code))


def gate_functions():
    from . import moves, physics, securing, visibility
    return [moves.prepare_move, moves._screen, moves.try_move,
            physics.fast_check, securing.screen_securing, visibility.screen_visibility]


def runtime_gate_pins():
    return {f'{f.__module__}.{f.__name__}': code_fingerprint(f.__code__)
            for f in gate_functions()}


def archived_gate_proof(sources):
    archive = FIXTURES/'holdout.gates-v3'
    proof = {}
    for name in GATES:
        relative = str((HERE/name).relative_to(ROOT))
        old = archive/relative
        if sha256_file(old) != sources[relative] or sha256_file(HERE/name) != sources[relative]:
            raise ValueError('Original v3 gate source bytes changed')
        compiled = compile(old.read_bytes(), str(HERE/name), 'exec')
        functions = {v.co_name: v for v in compiled.co_consts if isinstance(v, types.CodeType)}
        for function in gate_functions():
            if function.__code__.co_filename != str(HERE/name):
                continue
            original = functions[function.__name__]
            current = function.__code__
            if code_content(original) != code_content(current):
                raise ValueError('Loaded function differs from compiled archived v3 gate')
            proof[f'{function.__module__}.{function.__name__}'] = code_fingerprint(current)
    if len(proof) != 6:
        raise ValueError('Incomplete archived gate proof')
    return proof


def positions(ship):
    return sha256_json(sorted((p.container_id, p.slot) for p in ship.placements))


def prior_requests():
    requests = set()
    for path in FIXTURES.glob('clear190*.json'):
        data = read(path)
        for key in ('moves', 'selected'):
            requests.update((r['box_id'], r['target_slot']) for r in data.get(key, [])
                            if 'box_id' in r and 'target_slot' in r)
    return requests


def freeze(source_row=DEFAULT_SOURCE, *, deadline_s=14400, seed=SEED):
    from stowage.io import load_instance
    from stowage.model import Instance, MovePlan, CheckReport
    from stowage.checker import check
    from stowage.checker.full import report_matches_inputs
    from stowage.versions import component_versions
    from .moves import prepare_move, _screen
    from .clear_benchmark import candidates
    deadline = time.time()+deadline_s
    source_row = Path(source_row)
    artifact = read(source_row)
    instance_path = ROOT/artifact['path']
    if 'sealed' in str(instance_path):
        raise ValueError('Sealed sources are forbidden')
    instance, plan = load_instance(instance_path), MovePlan.model_validate(artifact['plan'])
    instance_sha, plan_sha = sha256_json(instance.model_dump(mode='json')), sha256_json(plan.model_dump(mode='json'))
    old_identities = []
    for name in ('envelope190.json', 'largest354.json', 'demo_source190.json', 'demo_source190.final.json'):
        old = read(FIXTURES/name)
        old_identities.append({'fixture': name,
            'instance_sha256': sha256_json(Instance.model_validate(old['instance']).model_dump(mode='json')),
            'plan_sha256': sha256_json(MovePlan.model_validate(old['plan']).model_dump(mode='json'))})
    if any(instance_sha == r['instance_sha256'] or plan_sha == r['plan_sha256'] for r in old_identities):
        raise ValueError('Holdout must use a different instance and plan')
    # Seed and policy are durably fixed before candidate enumeration or screening.
    protocol = {'schema': 'stowlm.sandbox-holdout-protocol/1', 'synthetic': True,
                'created_utc': now(), 'created_unix': time.time(), 'seed': seed,
                'source_row': str(source_row.relative_to(ROOT)), 'source_row_sha256': sha256_file(source_row),
                'instance_source': str(instance_path.relative_to(ROOT)),
                'instance_sha256': instance_sha, 'plan_sha256': plan_sha,
                'n_boxes': len(instance.load_list), 'different_from': old_identities,
                'target_requests': 40, 'quotas': {'swap': 20, 'empty': 20}, 'max_requests_per_box': 2,
                'minimum_distinct_boxes': 20,
                'selection': 'Seeded permutation of all geometry/replay candidate requests; replay and current v3 fast gates only. '
                             'No candidate full outcome is available during selection. No replacement after freeze.',
                'primary': 'Fresh full-check disagreement among these 40 frozen screen-cleared requests; no reused candidate verdicts.',
                'post_outcome_policy': 'No gates or features tuned from holdout full outcomes; report actual misses and rule families.',
                'pid': os.getpid(), 'native_thread_caps': {k: os.environ.get(k) for k in
                    ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')}}
    write(FIXTURES/'holdout.protocol.json', protocol, exclusive=True)
    # Preload every gate before snapshotting; later disk-only performance edits
    # cannot hot-reload this process's captured v3 functions.
    runtime_pins = runtime_gate_pins()
    sources = snapshot_sources(archive=FIXTURES/'holdout.gates-v3')
    snapshot = {'synthetic': True, 'source_result': str(source_row.relative_to(ROOT)),
                'instance': instance.model_dump(mode='json'), 'plan': plan.model_dump(mode='json'),
                'instance_sha256': instance_sha, 'plan_sha256': plan_sha,
                'source_report': artifact.get('checker'), 'source_artifact_status': artifact.get('status')}
    write(FIXTURES/'holdout.source.json', snapshot, exclusive=True)
    source_report = CheckReport.model_validate(artifact['checker'])
    if not source_report.passed or not report_matches_inputs(source_report, instance, plan):
        raise ValueError('Existing source checker report is not clean or not input-bound')
    load = admit(deadline)
    baseline_started = now()
    start = time.perf_counter()
    baseline = check(instance, plan)
    baseline_s = time.perf_counter()-start
    if not baseline.passed or not report_matches_inputs(baseline, instance, plan):
        raise ValueError('Fresh canonical baseline failed; no candidates can be measured')
    baseline_data = {'started_utc': baseline_started, 'completed_utc': now(),
                     'wall_s': baseline_s, 'admission': load, 'load_after': capacity(),
                     'report': baseline.model_dump(mode='json'), 'source_sha256': sources}
    write(FIXTURES/'holdout.baseline.json', baseline_data, exclusive=True)
    print(json.dumps({'baseline_passed': True, 'boxes': len(instance.load_list), 'baseline_s': baseline_s,
                      'pid': os.getpid()}), flush=True)
    excluded = prior_requests()
    pool = [(kind, box, target) for kind, (box, target) in candidates(instance, plan)
            if (box, target) not in excluded]
    # Candidate generation is geometry-only. The entire seeded draw is stored
    # before the first candidate screen, not inferred from accepted requests.
    random.Random(seed).shuffle(pool)
    draw = {'seed': seed, 'created_utc': now(), 'pool': pool, 'pool_sha256': sha256_json(pool),
            'excluded_prior_pairs': [list(r) for r in sorted(excluded)],
            'protocol_sha256': sha256_file(FIXTURES/'holdout.protocol.json')}
    write(FIXTURES/'holdout.draw.json', draw, exclusive=True)
    selected, counts, boxes, states = [], Counter(), Counter(), set()
    state = {'phase': 'selecting', 'pid': os.getpid(), 'source_sha256': sources,
             'screened': 0, 'selected': selected, 'candidate_full_checks_started': 0}
    for draw_index, (kind, box, target) in enumerate(pool):
        if counts[kind] >= 20 or boxes[box] >= 2:
            continue
        load = admit(deadline)
        started = time.perf_counter()
        prepared = prepare_move(instance, plan, box, target)
        state['screened'] += 1
        if not prepared.replay.legal:
            continue
        fast = _screen(prepared, started)
        end_load = capacity()
        if not fast.legal:
            continue
        requested, reached = positions(prepared.ship), positions(prepared.replay.final_state.ship)
        if requested != reached or requested in states:
            continue
        row = {'request_index': len(selected), 'draw_index': draw_index, 'kind': kind,
               'box_id': box, 'target_slot': target, 'instance_sha256': instance_sha,
               'candidate_plan_sha256': sha256_json(prepared.plan.model_dump(mode='json')),
               'requested_state_sha256': requested, 'reached_state_sha256': reached,
               'frozen_fast': fast.model_dump(mode='json'), 'fast_admission': load,
               'fast_load_after': end_load, 'selected_utc': now()}
        selected.append(row)
        counts[kind] += 1
        boxes[box] += 1
        states.add(requested)
        write(FIXTURES/'holdout.state.json', state)
        print(json.dumps({'selected': len(selected), 'counts': dict(counts), 'boxes': len(boxes),
                          'screened': state['screened']}), flush=True)
        if len(selected) == 40:
            break
    if len(selected) != 40 or len(boxes) < 20 or counts != {'swap': 20, 'empty': 20}:
        raise ValueError(f'Insufficient distinct fast-clear candidates: {len(selected)}, {dict(counts)}')
    if runtime_gate_pins() != runtime_pins:
        raise RuntimeError('Loaded v3 gate functions changed during selection')
    manifest = {'schema': 'stowlm.sandbox-holdout/1', 'synthetic': True,
                'frozen_utc': now(), 'frozen_unix': time.time(), 'seed': seed,
                'source_fixture': 'holdout.source.json', 'source_sha256': sha256_file(FIXTURES/'holdout.source.json'),
                'instance_sha256': instance_sha, 'plan_sha256': plan_sha, 'n_boxes': len(instance.load_list),
                'baseline_sha256': sha256_file(FIXTURES/'holdout.baseline.json'),
                'protocol_sha256': sha256_file(FIXTURES/'holdout.protocol.json'),
                'draw_sha256': sha256_file(FIXTURES/'holdout.draw.json'),
                'component_versions': component_versions(), 'source_sha256_files': sources,
                'runtime_gate_code_sha256': runtime_pins, 'requests': selected,
                'candidate_full_checks_started': 0, 'distinct_boxes': len(boxes), 'counts': dict(counts),
                'full_policy': 'All 40 fresh canonical checks after this immutable freeze; no candidate result reuse; no replacement.',
                'engine_version': artifact.get('engine_version'),
                'engine_manifest_sha256': artifact.get('engine_manifest_sha256')}
    write(FIXTURES/'holdout.manifest.json', manifest, exclusive=True)
    digest = sha256_file(FIXTURES/'holdout.manifest.json')
    with (FIXTURES/'holdout.manifest.sha256').open('x') as stream:
        stream.write(digest+'\n')
    state.update(phase='frozen_waiting_for_performance_ready', manifest_sha256=digest)
    write(FIXTURES/'holdout.state.json', state)
    print(json.dumps({'frozen': True, 'manifest_sha256': digest, 'requests': 40,
                      'distinct_boxes': len(boxes), 'counts': dict(counts)}), flush=True)


def recover_freeze(*, deadline_s=14400):
    """Seal the exact durable 40 after a proven marshal-only guard false alarm."""
    from stowage.model import Instance, MovePlan
    from stowage.versions import component_versions
    from .moves import prepare_move, _screen
    state_path = FIXTURES/'holdout.state.json'
    state, protocol = read(state_path), read(FIXTURES/'holdout.protocol.json')
    audit = read(FIXTURES/'holdout.marshal-audit.json')
    rows = state['selected']
    if (len(rows) != 40 or state['candidate_full_checks_started'] != 0
            or sha256_file(state_path) != audit['state_sha256']
            or sha256_json(rows) != audit['preserved_requests_sha256']):
        raise ValueError('Recovery must preserve the independently audited original 40')
    preservation = {'created_utc': now(), 'state_sha256': sha256_file(state_path),
                    'requests_sha256': sha256_json(rows), 'draw_sha256': sha256_file(FIXTURES/'holdout.draw.json'),
                    'protocol_sha256': sha256_file(FIXTURES/'holdout.protocol.json'),
                    'marshal_audit_sha256': sha256_file(FIXTURES/'holdout.marshal-audit.json'),
                    'candidate_full_checks_started': 0}
    preservation_path = FIXTURES/'holdout.recovery-preservation.json'
    if preservation_path.exists():
        original = read(preservation_path)
        if any(original[k] != preservation[k] for k in ('state_sha256', 'requests_sha256', 'draw_sha256', 'protocol_sha256', 'marshal_audit_sha256')):
            raise ValueError('Recovery preservation changed')
        preservation = original
    else:
        write(preservation_path, preservation, exclusive=True)
    # Original archive remains byte-for-byte intact. Recorder repair and unrelated
    # observational perf helpers have separate present-day readiness attribution.
    for name, digest in state['source_sha256'].items():
        if sha256_file(FIXTURES/'holdout.gates-v3'/name) != digest:
            raise ValueError('Original archive changed')
        if name.endswith('/holdout.py') or (Path(name).parent == HERE.relative_to(ROOT) and 'perf' in Path(name).name):
            continue
        if sha256_file(ROOT/name) != digest:
            raise ValueError(f'Original execution dependency changed: {name}')
    stable = archived_gate_proof(state['source_sha256'])
    recovery = {'completed_utc': now(), 'preserved_requests_sha256': sha256_json(rows),
                'candidate_full_checks_started': 0, 'stable_gate_pins': stable,
                'scope': 'No resampling or replacement. Original40 recorded v3 screens retained.',
                'rescreen_policy': 'Root approved no redundant40 rescreen: all original gate bytes and six loaded/compiled code contents match. Initial recovery rescreen was interrupted at admission during low-load hold; no candidate fulls.'}
    write(FIXTURES/'holdout.recovery-proof.json', recovery, exclusive=True)
    artifact = read(ROOT/protocol['source_row'])
    manifest = {'schema': 'stowlm.sandbox-holdout/1', 'synthetic': True,
                'frozen_utc': now(), 'frozen_unix': time.time(), 'seed': protocol['seed'],
                'source_fixture': 'holdout.source.json', 'source_sha256': sha256_file(FIXTURES/'holdout.source.json'),
                'instance_sha256': protocol['instance_sha256'], 'plan_sha256': protocol['plan_sha256'],
                'n_boxes': protocol['n_boxes'], 'baseline_sha256': sha256_file(FIXTURES/'holdout.baseline.json'),
                'protocol_sha256': preservation['protocol_sha256'], 'draw_sha256': preservation['draw_sha256'],
                'component_versions': component_versions(), 'source_sha256_files': state['source_sha256'],
                'runtime_gate_code_sha256': stable, 'runtime_code_pin_capture': 'Recovery after selection; no original marshal hashes were persisted.',
                'recovery': {'preservation_sha256': sha256_file(FIXTURES/'holdout.recovery-preservation.json'),
                             'proof_sha256': sha256_file(FIXTURES/'holdout.recovery-proof.json'),
                             'reason': 'Python3.12 marshal string-intern flag changes falsely tripped guard; source and stable code unchanged; original40 retained.'},
                'requests': rows, 'candidate_full_checks_started': 0,
                'distinct_boxes': len({r['box_id'] for r in rows}), 'counts': dict(Counter(r['kind'] for r in rows)),
                'full_policy': 'All40 fresh canonical checks after freeze; no reuse/replacement/outcome tuning.',
                'engine_version': artifact.get('engine_version'), 'engine_manifest_sha256': artifact.get('engine_manifest_sha256')}
    write(FIXTURES/'holdout.manifest.json', manifest, exclusive=True)
    digest = sha256_file(FIXTURES/'holdout.manifest.json')
    with (FIXTURES/'holdout.manifest.sha256').open('x') as stream:
        stream.write(digest+'\n')
    print(json.dumps({'frozen': True, 'manifest_sha256': digest, 'requests': 40, 'boxes': manifest['distinct_boxes']}), flush=True)


def execution_sources():
    # Numerical inputs as well as code: all stowage modules, rule tables and
    # current parameter bytes. Observational perf helper edits are not gates.
    paths = [ROOT/name for name in snapshot_sources() if 'perf' not in Path(name).name]
    paths.extend(p for p in (ROOT/'src/stowage').rglob('*')
                 if p.is_file() and p.suffix in ('.py', '.json', '.toml'))
    paths.extend(p for p in (ROOT/'params').rglob('*') if p.is_file() and p.suffix in ('.toml', '.json'))
    return {str(p.relative_to(ROOT)): sha256_file(p) for p in sorted(set(paths))}


def readiness(ready_note):
    manifest, digest = verify_manifest()
    if not ready_note.strip():
        raise ValueError('Root readiness note required')
    pins = execution_sources()
    record = {'created_utc': now(), 'created_unix': time.time(), 'manifest_sha256': digest,
              'protocol_sha256': manifest['protocol_sha256'], 'ready_note': ready_note,
              'source_sha256': pins, 'source_fingerprint': sha256_json(pins),
              'runtime_gate_code_sha256': runtime_gate_pins(),
              'candidate_full_checks_started': 0,
              'capture_label': 'Supplemental exact dependencies captured after freeze, before first candidate canonical call.'}
    write(FIXTURES/'holdout.readiness.json', record, exclusive=True)
    print(json.dumps({'ready': True, 'fingerprint': record['source_fingerprint'], 'dependencies': len(pins)}), flush=True)


def verify_readiness():
    record = read(FIXTURES/'holdout.readiness.json')
    _, digest = verify_manifest()
    if record['manifest_sha256'] != digest or execution_sources() != record['source_sha256']:
        raise ValueError('Pre-full execution dependency fingerprint changed')
    if runtime_gate_pins() != record['runtime_gate_code_sha256']:
        raise ValueError('Stable runtime gates differ from readiness')
    return record


def verify_manifest():
    manifest = read(FIXTURES/'holdout.manifest.json')
    digest = (FIXTURES/'holdout.manifest.sha256').read_text().strip()
    if sha256_file(FIXTURES/'holdout.manifest.json') != digest:
        raise ValueError('Frozen manifest changed')
    if sha256_file(FIXTURES/manifest['source_fixture']) != manifest['source_sha256']:
        raise ValueError('Frozen source changed')
    for stem in ('protocol', 'draw', 'baseline'):
        if sha256_file(FIXTURES/f'holdout.{stem}.json') != manifest[f'{stem}_sha256']:
            raise ValueError(f'Frozen {stem} changed')
    return manifest, digest


def _worker_init(deadline):
    global _instance, _plan, _manifest, _manifest_sha, _deadline, _sources, _session
    from stowage.model import Instance, MovePlan
    from stowage.versions import component_versions
    _manifest, _manifest_sha = verify_manifest()
    source = read(FIXTURES/_manifest['source_fixture'])
    _instance, _plan = Instance.model_validate(source['instance']), MovePlan.model_validate(source['plan'])
    _deadline = deadline
    runtime_gate_pins()
    ready = verify_readiness()
    _sources = ready['source_sha256']
    _session = {'pid': os.getpid(), 'created_utc': now(), 'source_sha256': _sources,
                'runtime_gate_code_sha256': runtime_gate_pins(), 'component_versions': component_versions(),
                'readiness_sha256': sha256_file(FIXTURES/'holdout.readiness.json'),
                'native_thread_caps': {k: os.environ.get(k) for k in
                    ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')}}
    print(json.dumps({'worker_pid': os.getpid(), 'session_sha256': sha256_json(_session)}), flush=True)


def _measure(frozen):
    from .moves import try_move
    import stowage.checker
    from stowage.checker.full import report_matches_inputs
    verify_manifest()
    load = admit(_deadline)
    verify_readiness()
    started_utc, started_unix = now(), time.time()
    calls, screens = [], []
    canonical = stowage.checker.check

    def capture_check(instance, plan, *args, **kwargs):
        call_started = time.time()
        start = time.perf_counter()
        report = canonical(instance, plan, *args, **kwargs)
        calls.append({'started_unix': call_started, 'wall_s': time.perf_counter()-start,
                      'instance_sha256': sha256_json(instance.model_dump(mode='json')),
                      'plan_sha256': sha256_json(plan.model_dump(mode='json')),
                      'input_binding_valid': report_matches_inputs(report, instance, plan),
                      'report': report.model_dump(mode='json')})
        return report

    def capture_fast(verdict):
        screens.append({'verdict': verdict.model_dump(mode='json'), 'load_after': capacity()})

    # Instrument the canonical call; never change its arguments or report.
    stowage.checker.check = capture_check
    try:
        full = try_move(_instance, _plan, frozen['box_id'], frozen['target_slot'], on_fast=capture_fast)
    finally:
        stowage.checker.check = canonical
    if len(calls) != 1 or len(screens) != 1:
        raise RuntimeError('Expected exactly one fresh canonical full check and one fast callback')
    call = calls[0]
    if (not call['input_binding_valid'] or call['instance_sha256'] != frozen['instance_sha256']
            or call['plan_sha256'] != frozen['candidate_plan_sha256']):
        raise RuntimeError('Canonical full checker did not judge the exact frozen candidate')
    verify_readiness()
    if any(sha256_file(ROOT/name) != digest for name, digest in _sources.items()):
        raise RuntimeError('Execution source changed; preserve frozen requests and resume after coordination')
    fast = screens[0]['verdict']
    return {'request_index': frozen['request_index'], 'box_id': frozen['box_id'],
            'target_slot': frozen['target_slot'], 'kind': frozen['kind'],
            'manifest_sha256': _manifest_sha, 'started_utc': started_utc, 'started_unix': started_unix,
            'completed_utc': now(), 'admission': load, 'fast_load_after': screens[0]['load_after'],
            'load_after': capacity(), 'fast': fast, 'full': full.model_dump(mode='json'),
            'fast_s': fast['wall_s'], 'full_api_s': full.wall_s, 'canonical_checker_s': call['wall_s'],
            'canonical_call': call, 'canonical_invocations': 1, 'full_origin': 'fresh_canonical_call',
            'frozen_fast_legal': frozen['frozen_fast']['legal'],
            'runtime_fast_agrees_with_frozen_legal': fast['legal'] == frozen['frozen_fast']['legal'],
            'session': _session}


def aggregate(rows, manifest_sha, ready_note):
    def timing(key):
        if not rows:
            return None
        values = sorted(r[key] for r in rows)
        import math
        return {'median_s': statistics.median(values), 'p95_s': values[math.ceil(.95*len(values))-1], 'max_s': max(values)}
    misses = [r for r in rows if not r['full']['legal']]
    return {'synthetic': True, 'mode': 'recorded', 'manifest_sha256': manifest_sha,
            'implementation_ready_note': ready_note, 'complete': len(rows) == 40,
            'summary': {'moves': len(rows), 'fresh_canonical_checks': len(rows),
                        'frozen_screen_clear': len(rows), 'runtime_screen_clear': sum(r['fast']['legal'] for r in rows),
                        'runtime_screen_disagreements': sum(not r['runtime_fast_agrees_with_frozen_legal'] for r in rows),
                        'false_legal': len(misses), 'false_legal_fraction': len(misses)/len(rows) if rows else None,
                        'miss_rule_families': dict(Counter(rule for r in misses for rule in {b['rule_id'] for b in r['full']['broken']})),
                        'fast_response': timing('fast_s'), 'full_api_response': timing('full_api_s'),
                        'canonical_checker': timing('canonical_checker_s')}, 'moves': sorted(rows, key=lambda r:r['request_index'])}


def measure(*, ready_note, workers=4, deadline_s=14400):
    if not ready_note.strip():
        raise ValueError('The root performance-ready signal must be recorded before candidate checks')
    manifest, digest = verify_manifest()
    verify_readiness()
    if not manifest['candidate_full_checks_started'] == 0:
        raise ValueError('Invalid pre-outcome freeze')
    results_dir = FIXTURES/'holdout.rows'
    results_dir.mkdir(exist_ok=True)
    rows = []
    for path in sorted(results_dir.glob('request-*.json')):
        row = read(path)
        if row['manifest_sha256'] != digest or row['full_origin'] != 'fresh_canonical_call':
            raise ValueError('Cannot mix another cohort or reused candidate outcomes')
        rows.append(row)
    done = {r['request_index'] for r in rows}
    pending = [r for r in manifest['requests'] if r['request_index'] not in done]
    started = {'started_utc': now(), 'started_unix': time.time(), 'manifest_sha256': digest,
               'ready_note': ready_note, 'workers': workers, 'pid': os.getpid(),
               'source_sha256': snapshot_sources(), 'remaining': len(pending)}
    write(FIXTURES/f'holdout.measure-start-{os.getpid()}.json', started, exclusive=True)
    deadline = time.time()+deadline_s

    def save(row):
        if row['started_unix'] <= manifest['frozen_unix']:
            raise ValueError('Candidate full outcome predates the immutable request freeze')
        write(results_dir/f"request-{row['request_index']:03d}.json", row, exclusive=True)
        rows.append(row)
        data = aggregate(rows, digest, ready_note)
        write(FIXTURES/'holdout.results.json', data)
        print(json.dumps({'completed': len(rows), 'request_index': row['request_index'],
                          'fast_clear': row['fast']['legal'], 'full_legal': row['full']['legal'],
                          'rules': sorted({b['rule_id'] for b in row['full']['broken']}),
                          'fast_s': row['fast_s'], 'full_api_s': row['full_api_s'],
                          'canonical_checker_s': row['canonical_checker_s']}), flush=True)

    errors = []
    if workers == 1:
        _worker_init(deadline)
        for request in pending:
            save(_measure(request))
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'),
                                 initializer=_worker_init, initargs=(deadline,)) as pool:
            futures = [pool.submit(_measure, request) for request in pending]
            for future in as_completed(futures):
                try:
                    row = future.result()
                except Exception as exc:
                    errors.append(f'{type(exc).__name__}: {exc}')
                else:
                    save(row)
    if errors:
        raise RuntimeError('; '.join(sorted(set(errors))))
    write(FIXTURES/'holdout.results.json', aggregate(rows, digest, ready_note))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('freeze', 'recover', 'ready', 'measure'))
    parser.add_argument('--source-row', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=4)
    parser.add_argument('--deadline-s', type=float, default=14400)
    parser.add_argument('--ready-note', default='')
    args = parser.parse_args()
    print(json.dumps({'pid': os.getpid(), 'phase': args.phase}), flush=True)
    if args.phase == 'freeze':
        freeze(args.source_row, deadline_s=args.deadline_s, seed=args.seed)
    elif args.phase == 'recover':
        recover_freeze(deadline_s=args.deadline_s)
    elif args.phase == 'ready':
        readiness(args.ready_note)
    else:
        measure(ready_note=args.ready_note, workers=args.workers, deadline_s=args.deadline_s)


if __name__ == '__main__':
    main()

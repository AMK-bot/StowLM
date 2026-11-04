"""Clear-screen development cohort; every recorded drag runs the real full checker."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import time

from .benchmark import HERE, ROOT, admission, read_fixture
from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json


def placements_hash(ship):
    return sha256_json(sorted((p.container_id, p.slot) for p in ship.placements))


def candidates(instance, plan):
    """Deterministic mechanically plausible drags, selected without full outcomes."""
    from stowage.conventions import cells_of_slot, format_slot, parse_slot
    from stowage.moves import apply_plan
    reached = apply_plan(instance, plan).final_state.ship
    placements = reached.placements
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    occupied = {c: p.container_id for p in placements for c in cells_of_slot(p.slot)}
    loads = {m.container_id: i for i, m in enumerate(plan.moves) if m.kind == 'load'}
    source = {p.container_id: p.slot for p in placements}
    seen = set()

    def offer(box, slot):
        key = box, slot
        if source[box] == slot or key in seen:
            return None
        seen.add(key)
        return key

    # Adjacent loads seed swaps without choosing on any full-check outcome.
    for left, right in zip(plan.moves, plan.moves[1:]):
        if left.kind != 'load' or right.kind != 'load':
            continue
        a, b = boxes[left.container_id], boxes[right.container_id]
        if (a.length_ft, a.height_m) == (b.length_ft, b.height_m):
            key = offer(a.id, right.dst)
            if key:
                yield 'swap', key

    # Enumerate every size-compatible slot, including supported empty tops.
    slots = {20: [], 40: [], 45: []}
    for cell in cells.values():
        addr = parse_slot(cell.id)
        for length in slots:
            if not getattr(cell, f'accepts_{length}'):
                continue
            if length == 20:
                sid, footprint = cell.id, [cell.id]
            else:
                partner = format_slot(addr.bay + 2, addr.row, addr.tier)
                if partner not in cells or not getattr(cells[partner], f'accepts_{length}'):
                    continue
                sid, footprint = format_slot(addr.bay + 1, addr.row, addr.tier), [cell.id, partner]
            slots[length].append((sid, footprint))

    empty_by_box = []
    for p in sorted(placements, key=lambda p: (-loads.get(p.container_id, -1), p.container_id)):
        box = boxes[p.container_id]
        footprint = cells_of_slot(p.slot)
        # Removing a non-top box leaves a final support gap: skip before replay.
        if any(any(c in occupied for c in stacks[cells[f].stack_id].cell_ids[
                    stacks[cells[f].stack_id].cell_ids.index(f)+1:]) for f in footprint):
            continue
        original = cells[footprint[0]]
        options = []
        for sid, target in slots[box.length_ft]:
            if any(c in occupied for c in target):
                continue
            supported = True
            for cell_id in target:
                stack = stacks[cells[cell_id].stack_id]
                index = stack.cell_ids.index(cell_id)
                if index:
                    support = occupied.get(stack.cell_ids[index-1])
                    if support is None or support == box.id or loads.get(support, -1) >= loads.get(box.id, -1):
                        supported = False
                        break
            if supported:
                target_cell = cells[target[0]]
                options.append((abs(target_cell.lcg_m-original.lcg_m),
                                abs(target_cell.tcg_m-original.tcg_m), sid))
        empty_by_box.append([(box.id, sid) for _, _, sid in sorted(options)])

    # Visit different dragged boxes before returning to another target for one.
    from itertools import zip_longest
    for group in zip_longest(*empty_by_box):
        for request in group:
            if request:
                key = offer(*request)
                if key:
                    yield 'empty', key

    # Same-length, same-height, same-tier swaps are a larger independent stratum.
    for p in placements:
        box = boxes[p.container_id]
        compatible = [q for q in placements if q.container_id != box.id
                      and (boxes[q.container_id].length_ft, boxes[q.container_id].height_m,
                           parse_slot(q.slot).tier) == (box.length_ft, box.height_m, parse_slot(p.slot).tier)]
        compatible.sort(key=lambda q: (abs(boxes[q.container_id].gross_mass_kg-box.gross_mass_kg), q.slot))
        for q in compatible:
            key = offer(box.id, q.slot)
            if key:
                yield 'swap', key


def _write(output, data):
    output.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')


def discover(source, output, count=40, minimum_each=10):
    from stowage.model import Instance, MovePlan
    from .moves import prepare_move, _screen
    fixture = read_fixture(source)
    instance, plan = Instance.model_validate(fixture['instance']), MovePlan.model_validate(fixture['plan'])
    counts, empty_counts, selected, screened = Counter(), Counter(), [], 0
    prior = read_fixture(output) if output.exists() else None
    prior_rows = prior.get('moves', []) if prior else []
    data = {'synthetic': True, 'mode': 'recorded', 'label': 'Recorded development measurement',
            'created_utc': datetime.now(timezone.utc).isoformat(), 'n_boxes': len(instance.load_list),
            'source_fixture': source.name, 'source_sha256': sha256_file(source),
            'source_result': fixture['source_result'], 'pid': os.getpid(),
            'method': 'Distinct drags from the same unmodified 190-box base; chosen only by legal replay and fast screen; '
                      'no full outcomes consulted; supported empty tops and same-size swaps; native thread caps 1; '
                      'development challenge cohort, not a random-drag population estimate.',
            'screened': 0, 'selected': selected, 'moves': prior_rows, 'complete': False,
            'selection_version': 2, 'empty_drags_per_box_limit': 2,
            'selection_revision_reason': 'Round-robin empty drags broaden source-box coverage; no full outcomes used.',
            'selection_history': ([{'version': prior.get('selection_version', 1),
                                    'selected': prior['selected']}] if prior else [])}
    if prior_rows:
        data['legacy_source_capture_note'] = ('Initial completed swap rows predate pre-measurement source pinning; '
            'their code_sha256 was captured after each check. No historical checker hash is invented.')
    if prior and prior.get('measurement_sessions'):
        data['measurement_sessions'] = prior['measurement_sessions']
    _write(output, data)
    for kind, request in candidates(instance, plan):
        if counts[kind] >= count-minimum_each:
            continue
        if kind == 'empty' and empty_counts[request[0]] >= 2:
            continue
        load = admission()
        started = time.perf_counter()
        prepared = prepare_move(instance, plan, *request)
        screened += 1
        if prepared.replay.legal:
            screen = _screen(prepared, started)
            if screen.legal:
                requested = placements_hash(prepared.ship)
                reached = placements_hash(prepared.replay.final_state.ship)
                if requested != reached:
                    raise AssertionError('Clear replay did not reach requested placements')
                selected.append({'kind': kind, 'box_id': request[0], 'target_slot': request[1],
                                 'requested_state_sha256': requested, 'reached_state_sha256': reached,
                                 'screen_s': screen.wall_s, 'selection_admission': load})
                counts[kind] += 1
                if kind == 'empty':
                    empty_counts[request[0]] += 1
                print(json.dumps({'selected': len(selected), 'counts': counts, 'screened': screened}), flush=True)
                data.update(screened=screened, selected=selected)
                _write(output, data)
        if (len(selected) >= count and min(counts.values()) >= minimum_each
                and len({r['box_id'] for r in selected}) >= 20):
            data.update(discovery_complete=True, screened=screened)
            _write(output, data)
            return data
        if screened % 100 == 0:
            print(json.dumps({'screened': screened, 'counts': counts}), flush=True)
    raise ValueError(f'Not enough clear drags: {dict(counts)} of {screened} screened')


def summary(rows):
    false = [r for r in rows if r['fast']['legal'] and not r['full']['legal']]
    fast_clear = sum(r['fast']['legal'] for r in rows)
    def timing(key):
        values = sorted(r[key] for r in rows)
        return {'median_s': statistics.median(values), 'p95_s': values[__import__('math').ceil(.95*len(values))-1],
                'max_s': max(values)}
    return {'moves': len(rows), 'kinds': dict(Counter(r['kind'] for r in rows)),
            'distinct_dragged_boxes': len({r['box_id'] for r in rows}),
            'distinct_resulting_states': len({r['requested_state_sha256'] for r in rows}),
            'fast_clear': fast_clear, 'false_legal': len(false),
            'false_legal_fraction_of_fast_clear': len(false)/fast_clear if fast_clear else None,
            'false_legal_rules': dict(Counter(v['rule_id'] for r in false for v in r['full']['broken'])),
            'fast_response': timing('fast_s'), 'full_response': timing('full_s'),
            'load1_min': min(r['admission']['load1'] for r in rows),
            'load1_max': max(r['admission']['load1'] for r in rows)}


def _measure_init(source):
    global _instance, _plan, _pinned, _session, _session_id
    from stowage.model import Instance, MovePlan
    fixture = read_fixture(source)
    _instance, _plan = Instance.model_validate(fixture['instance']), MovePlan.model_validate(fixture['plan'])
    from stowage.versions import component_versions
    paths = [*sorted((ROOT/'src/stowage/checker').glob('*.py')),
             ROOT/'src/stowage/versions.py',
             *sorted(p for p in HERE.glob('*.py') if not p.name.startswith('test_'))]
    _pinned = {str(p.relative_to(ROOT)): sha256_file(p) for p in paths}
    _session = {'pid': os.getpid(), 'started_utc': datetime.now(timezone.utc).isoformat(),
               'source_sha256': _pinned, 'component_versions': component_versions(),
               'capture': 'Process source files pinned before first measurement; dependency imports may already exist.',
               'native_thread_caps': {name: os.environ.get(name) for name in
                                      ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')}}
    _session_id = sha256_json(_session)
    print(json.dumps({'worker_pid': os.getpid(), 'measurement_session': _session_id}), flush=True)


def _admit_measurement():
    """Admission can be paused/cancelled without signalling another process."""
    while True:
        if (HERE/'fixtures/clear190.cancel').exists():
            raise RuntimeError('Measurement cancelled through owned control file')
        if not (HERE/'fixtures/clear190.pause').exists():
            try:
                state = json.loads((ROOT/'experiments/fleet/core-state.json').read_text())
                age = time.time()-state['t']
                if int(state['mask']).bit_count() >= 8 and state['load1'] <= 45 and 0 <= age < 120:
                    return dict(state, age_s=age)
            except (OSError, ValueError, TypeError, KeyError):
                pass
        time.sleep(2)


def _measure(selected):
    from .moves import prepare_move, try_move
    request = selected['box_id'], selected['target_slot']
    load = _admit_measurement()
    # Every row gets fresh prepare, fresh screen and the real full checker.
    prepared = prepare_move(_instance, _plan, *request)
    candidate_plan_sha256 = sha256_json(prepared.plan.model_dump(mode='json'))
    instance_sha256 = sha256_json(_instance.model_dump(mode='json'))
    screens = []
    full = try_move(_instance, _plan, *request, on_fast=screens.append)
    fast = screens[0]
    row = dict(selected, admission=load,
                   candidate_plan_sha256=candidate_plan_sha256, instance_sha256=instance_sha256,
                   load_after=json.loads((ROOT/'experiments/fleet/core-state.json').read_text()),
                   pid=os.getpid(), requested_state_sha256=placements_hash(prepared.ship),
                   reached_state_sha256=placements_hash(prepared.replay.final_state.ship),
                   fast_s=fast.wall_s, full_s=full.wall_s,
                   fast=fast.model_dump(mode='json'), full=full.model_dump(mode='json'),
                   measurement_session=_session_id,
                   code_sha256={name: _pinned[str((HERE/name).relative_to(ROOT))]
                                for name in ('moves.py','physics.py','clear_benchmark.py')})
    if any(sha256_file(ROOT/name) != digest for name, digest in _pinned.items()):
        raise RuntimeError('Source changed during this process; keep prior rows and restart with a new pinned session')
    return row, _session


def record(source, output, workers=1):
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import multiprocessing
    data = read_fixture(output)
    if data['source_sha256'] != sha256_file(source):
        raise ValueError('Source changed; refuse checkpoint replay')
    rows = data['moves']
    selected_requests = {(r['box_id'], r['target_slot']) for r in data['selected']}
    if any((r['box_id'], r['target_slot']) not in selected_requests for r in rows):
        raise ValueError('Completed rows must remain in the revised selection')
    done = {(r['box_id'], r['target_slot']) for r in rows}
    pending = [r for r in data['selected'] if (r['box_id'], r['target_slot']) not in done]
    data['workers'] = workers

    def save(result):
        row, session = result
        data.setdefault('measurement_sessions', {})[row['measurement_session']] = session
        rows.append(row)
        data.update(moves=rows, summary=summary(rows))
        _write(output, data)
        print(json.dumps({'recorded': len(rows), 'kind': row['kind'], 'fast': row['fast']['legal'],
                          'full': row['full']['legal'], 'rules': [v['rule_id'] for v in row['full']['broken']],
                          'full_s': round(row['full_s'], 4), 'summary': data['summary']}), flush=True)

    if workers == 1:
        _measure_init(source)
        for selected in pending:
            save(_measure(selected))
    else:
        errors = []
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'),
                                 initializer=_measure_init, initargs=(source,)) as pool:
            futures = [pool.submit(_measure, selected) for selected in pending]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    errors.append(f'{type(exc).__name__}: {exc}')
                else:
                    save(result)
        if errors:
            raise RuntimeError('; '.join(sorted(set(errors))))
    data['complete'] = len(rows) >= 40 and all(r['fast']['legal'] for r in rows)
    _write(output, data)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['discover', 'record'])
    parser.add_argument('--source', type=Path, default=HERE/'fixtures/envelope190.json')
    parser.add_argument('--output', type=Path, default=HERE/'fixtures/clear190.json')
    parser.add_argument('--count', type=int, default=40)
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=1)
    args = parser.parse_args()
    if args.phase == 'discover':
        discover(args.source, args.output, args.count)
    else:
        record(args.source, args.output, args.workers)


if __name__ == '__main__':
    main()

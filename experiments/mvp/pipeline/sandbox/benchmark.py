"""Offline, admitted move timing and demo recording; never searches for a plan."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import random
import statistics
import time
import math
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def read_fixture(path):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt') as stream:
        return json.load(stream)


def admission():
    """A stale/busy fleet reading waits; it never turns into a benchmark failure."""
    while True:
        try:
            state = json.loads((ROOT / 'experiments/fleet/core-state.json').read_text())
            age = time.time() - state['t']
            if int(state['mask']).bit_count() >= 8 and state['load1'] <= 45 and 0 <= age < 120:
                return dict(state, age_s=age)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(10)


def candidates(instance, plan, count):
    """Deterministic, distinct swaps/moves: nearby, far, then seeded random."""
    from stowage.moves import apply_plan
    from stowage.conventions import parse_slot, format_slot
    from types import SimpleNamespace
    reached = apply_plan(instance, plan).final_state.ship
    placements = reached.placements
    cells = {c.id: c for c in instance.vessel.cells}
    slots_by_length = {}
    for length in (20, 40, 45):
        slots = {}
        for cell in cells.values():
            a = parse_slot(cell.id)
            if length == 20:
                if not cell.accepts_20:
                    continue
                sid, partner = cell.id, cell
            else:
                partner = cells.get(format_slot(a.bay+2, a.row, a.tier))
                if partner is None or not all(getattr(c, f'accepts_{length}') for c in (cell, partner)):
                    continue
                sid = format_slot(a.bay+1, a.row, a.tier)
            slots[sid] = SimpleNamespace(id=sid, tier=a.tier, lcg_m=(cell.lcg_m+partner.lcg_m)/2,
                                        tcg_m=cell.tcg_m, vcg_m=cell.vcg_base_m)
        slots_by_length[length] = slots
    boxes = {c.id: c for c in instance.containers}
    rng = random.Random(1707)
    seen = set()
    # Adjacent lifts of equal-height boxes at equal tiers have no dependency on
    # one another. Swapping them supplies plausible legal challenges; the real
    # replay and static screen still decide whether each is actually clear.
    for left, right in zip(plan.moves, plan.moves[1:]):
        if left.kind != 'load' or right.kind != 'load':
            continue
        a, b = boxes[left.container_id], boxes[right.container_id]
        if (a.length_ft != b.length_ft or a.height_m != b.height_m
                or parse_slot(left.dst).tier != parse_slot(right.dst).tier
                or a.dg or b.dg or a.oog or b.oog):
            continue
        key = (a.id,right.dst)
        if key not in seen:
            seen.add(key)
            yield key
    # Give the measured cohort real moves to empty, supported deck bases as
    # well as challenging swaps/random destinations. No no-op drags are used.
    from stowage.conventions import cells_of_slot
    occupied = {c for p in placements for c in cells_of_slot(p.slot)}
    base_cells = {s.cell_ids[0] for s in instance.vessel.stacks if s.on_deck and s.cell_ids}
    cells_by_column = {}
    for cell in occupied:
        a = parse_slot(cell)
        cells_by_column.setdefault((a.bay,a.row,a.on_deck), []).append(a.tier)
    covers = {c:stack.hatch_cover_id for stack in instance.vessel.stacks for c in stack.cell_ids}
    seeded = 0
    for p in reversed(placements):
        addresses = [parse_slot(c) for c in cells_of_slot(p.slot)]
        if not all(a.on_deck and a.tier == max(cells_by_column[(a.bay,a.row,a.on_deck)]) for a in addresses):
            continue
        options = [s for s in slots_by_length[boxes[p.container_id].length_ft].values()
                   if set(cells_of_slot(s.id)) <= base_cells
                   and not set(cells_of_slot(s.id)) & occupied]
        if not options:
            continue
        original = slots_by_length[boxes[p.container_id].length_ft][p.slot]
        source_cover = {covers[c] for c in cells_of_slot(p.slot)}
        options.sort(key=lambda s: ({covers[c] for c in cells_of_slot(s.id)} != source_cover,
                                    abs(s.lcg_m-original.lcg_m), abs(s.tcg_m-original.tcg_m), s.id))
        key = (p.container_id, options[0].id)
        if key not in seen:
            seen.add(key)
            yield key
            seeded += 1
        if seeded >= count:
            break
    for index in range(count * 40):
        p = placements[index % len(placements)]
        slots = slots_by_length[boxes[p.container_id].length_ft]
        slot = slots[p.slot]
        compatible = [s for s in slots.values() if s.id != slot.id]
        if index % 3 == 0:
            compatible.sort(key=lambda s: (abs(s.tier-slot.tier), abs(s.lcg_m-slot.lcg_m),
                                           abs(s.tcg_m-slot.tcg_m), s.id))
            target = compatible[(index // len(placements)) % min(8, len(compatible))]
        elif index % 3 == 1:
            compatible.sort(key=lambda s: (abs(s.tcg_m-slot.tcg_m), s.vcg_m), reverse=True)
            target = compatible[(index // len(placements)) % min(8, len(compatible))]
        else:
            target = rng.choice(compatible)
        key = (p.container_id, target.id)
        if key not in seen:
            seen.add(key)
            yield key


def summary(rows):
    def times(field):
        values = sorted(row[field] for row in rows)
        return {'median_s': statistics.median(values),
                'p95_s': values[math.ceil(.95*len(values))-1], 'max_s': max(values)}
    fast_legal = sum(row['fast']['legal'] for row in rows)
    false_legal = sum(row['fast']['legal'] and not row['full']['legal'] for row in rows)
    return {'moves': len(rows), 'full_legal': sum(row['full']['legal'] for row in rows),
            'full_illegal': sum(not row['full']['legal'] for row in rows),
            'fast_legal': fast_legal, 'false_legal': false_legal,
            'false_legal_fraction_of_fast_legal': false_legal/fast_legal if fast_legal else None,
            'false_legal_fraction_of_all_moves': false_legal/len(rows),
            'fast_response': times('fast_s'), 'full_response': times('full_s'),
            'load1_min': min(r['admission']['load1'] for r in rows),
            'load1_max': max(r['admission']['load1'] for r in rows)}


def _worker_init(source):
    global _worker_instance, _worker_plan
    from stowage.model import Instance, MovePlan
    fixture = read_fixture(source)
    _worker_instance = Instance.model_validate(fixture['instance'])
    _worker_plan = MovePlan.model_validate(fixture['plan'])


def _measure(request):
    from .moves import try_move
    from .explain import explain_verdict
    from experiments.mvp.pipeline.contract.schema import Mode
    box, target = request
    load = admission()
    screens = []
    full = try_move(_worker_instance, _worker_plan, box, target, on_fast=screens.append)
    fast = screens[0]
    end = json.loads((ROOT/'experiments/fleet/core-state.json').read_text())
    return {'box_id': box, 'target_slot': target, 'admission': load, 'load_after': end,
            'pid': os.getpid(), 'fast_s': fast.wall_s, 'full_s': full.wall_s,
            'fast': fast.model_dump(mode='json'), 'full': full.model_dump(mode='json'),
            'explanation': explain_verdict(full).model_copy(update={'mode': Mode.recorded}).model_dump(mode='json')}


def record(source, output, count=50, workers=3):
    from stowage.checker import check
    from stowage.model import Instance, MovePlan
    from .moves import prepare_move, _screen
    from experiments.mvp.pipeline.contract.schema import sha256_file
    source = Path(source)
    fixture = read_fixture(source)
    instance = Instance.model_validate(fixture['instance'])
    plan = MovePlan.model_validate(fixture['plan'])
    initial_admission = admission()
    start = time.perf_counter()
    base = check(instance, plan)
    base_s = time.perf_counter()-start
    if not base.passed:
        raise ValueError('Stored source failed fresh full checker: '+str([v.rule_id for v in base.violations]))
    print(json.dumps({'baseline_passed':True, 'baseline_s':base_s, 'pid':os.getpid()}), flush=True)
    # Deliberately stratify this development challenge set; these frequencies
    # are not an estimate of the population of arbitrary judge drags.
    clear, flagged = [], []
    target_clear = min(5, count//2)
    for index, request in enumerate(candidates(instance, plan, count)):
        if index % 10 == 0:
            admission()
        prepared = prepare_move(instance, plan, *request)
        is_clear = bool(prepared.replay.legal)
        if is_clear:
            is_clear = _screen(prepared,time.perf_counter()).legal
        if is_clear and len(clear) < target_clear:
            clear.append(request)
        elif not is_clear and len(flagged) < count-1:
            flagged.append(request)
        if len(clear) >= target_clear and len(flagged) >= count-target_clear:
            break
        if index % 20 == 0:
            print(json.dumps({'screened':index+1,'clear':len(clear),'flagged':len(flagged)}),flush=True)
        if index >= count*20:
            break
    requests = clear+flagged[:count-len(clear)]
    if len(requests) != count:
        raise ValueError(f'Need {count} distinct mixed candidates; found {len(clear)} clear, {len(flagged)} flagged')
    print(json.dumps({'selected_clear':len(clear),'selected_flagged':len(flagged)}), flush=True)
    rows = []
    data = {'synthetic': True, 'mode': 'recorded', 'label': 'Recorded development measurement',
            'created_utc': datetime.now(timezone.utc).isoformat(), 'source_fixture': source.name,
            'source_result': fixture['source_result'], 'n_boxes': len(instance.load_list),
            'baseline_passed': base.passed, 'baseline_check_s':base_s,
            'baseline_report': base.model_dump(mode='json'),
            'initial_admission': initial_admission,
            'workers': workers, 'pid':os.getpid(),
            'source_sha256':{name: sha256_file(HERE/name) for name in ('moves.py','physics.py','benchmark.py','explain.py')},
            'selected_fast_clear':len(clear), 'selected_fast_flagged':len(requests)-len(clear),
            'method': f'{workers} worker processes; native thread caps 1 each; same unmodified base for every distinct drag; '
                      f'stratified development set, {len(clear)} clear and {len(requests)-len(clear)} flagged fast screens; '
                      'fast latency and end-to-end full latency include preparation; no plan search.',
            'complete':False, 'moves': rows}
    Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')
    context = multiprocessing.get_context('spawn')
    with ProcessPoolExecutor(max_workers=workers, mp_context=context,
                             initializer=_worker_init, initargs=(source,)) as pool:
        futures = [pool.submit(_measure, request) for request in requests]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            data['summary'] = summary(rows)
            data['moves'] = rows
            Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')
            print(json.dumps({'move':len(rows),'box':row['box_id'],'fast':row['fast']['legal'],
                              'full':row['full']['legal'],'seconds':round(row['full_s'],4)}), flush=True)
    data['complete'] = (len(rows) == count and any(r['full']['legal'] for r in rows)
                        and any(not r['full']['legal'] for r in rows))
    Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')
    if not data['complete']:
        raise ValueError('Recorded cohort lacks a full-legal/full-illegal mixture')
    print(json.dumps(data['summary'], indent=2), flush=True)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=HERE/'fixtures/envelope190.json')
    parser.add_argument('--output', type=Path, default=HERE/'fixtures/benchmark190.json')
    parser.add_argument('--moves', type=int, default=50)
    parser.add_argument('--workers', type=int, default=3, choices=range(1,5))
    args = parser.parse_args()
    if args.moves < 50:
        parser.error('At least 50 moves are required')
    record(args.source, args.output, args.moves, args.workers)


if __name__ == '__main__':
    main()

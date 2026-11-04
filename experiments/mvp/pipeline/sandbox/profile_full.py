"""Single-process canonical full-check profiling, admitted at declared low load.

No checker part is skipped or replaced. cProfile records Python call costs;
the adjacent unprofiled call is the wall-time observation. This is an
experiment recorder, not a faster checker or an incremental approval path.
"""
from __future__ import annotations

import argparse
import cProfile
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pstats
import threading
import time

from .benchmark import HERE, ROOT, read_fixture


def reading():
    try:
        row = json.loads((ROOT / 'experiments/fleet/core-state.json').read_text())
        return dict(row, age_s=time.time() - row['t'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'error': str(error)}


def admitted(row, max_load):
    return (isinstance(row.get('load1'), (int, float))
            and row['load1'] <= max_load
            and int(row.get('mask', 0)).bit_count() >= 8
            and 0 <= row.get('age_s', -1) < 120)


def wait_low_load(max_load):
    while True:
        row = reading()
        if admitted(row, max_load):
            return row
        print(json.dumps({'waiting_for_load1_at_most': max_load, 'reading': row}), flush=True)
        time.sleep(10)


def _profile_rows(profiler):
    stats = pstats.Stats(profiler)
    rows = []
    for (filename, line, function), (primitive, calls, own, cumulative, _) in stats.stats.items():
        if '/stowage/' not in filename:
            continue
        rows.append({'file': filename.split('/src/')[-1], 'line': line,
                     'function': function, 'primitive_calls': primitive, 'calls': calls,
                     'own_s': own, 'cumulative_s': cumulative})
    rows.sort(key=lambda row: row['cumulative_s'], reverse=True)
    return {'total_python_profile_s': stats.total_tt,
            'top_stowage_cumulative': rows[:40],
            'top_stowage_self': sorted(rows, key=lambda row: row['own_s'], reverse=True)[:20],
            'checker_part_functions': [row for row in rows if row['function'] in {
                'check_stability', 'check_strength', 'check_visibility', 'check_lashing',
                'check_static_rules', 'check_plan_constraints', 'apply_plan',
                '_in_port', '_input_bindings'}]}


def measure(instance, plan, *, profiled, max_load):
    from stowage.checker import check, report_matches_inputs
    before = wait_low_load(max_load)
    samples = [before]
    stop = threading.Event()

    def sample():
        while not stop.wait(2):
            samples.append(reading())

    sampler = threading.Thread(target=sample, name='fleet-load-sampler', daemon=True)
    sampler.start()
    profiler = cProfile.Profile() if profiled else None
    started = time.perf_counter()
    try:
        if profiler:
            profiler.enable()
        report = check(instance, plan)
    finally:
        if profiler:
            profiler.disable()
        wall_s = time.perf_counter() - started
        stop.set()
        sampler.join()
    samples.append(reading())
    return report, {'profiled': profiled, 'wall_s': wall_s,
                    'passed': report.passed,
                    'input_binding_matches': report_matches_inputs(report, instance, plan),
                    'parts': sorted(report.parts),
                    'all_parts_ran': all(part.ran for part in report.parts.values()),
                    'broken_rule_ids': sorted({v.rule_id for v in report.violations}),
                    'count': report.count.model_dump(mode='json') if report.count else None,
                    'load_samples': samples,
                    'entire_readings_within_lowload_target': all(admitted(r, max_load) for r in samples),
                    'profile': _profile_rows(profiler) if profiler else None}


def _comparable(report):
    """All checker fields except freshly generated provenance timestamp/id."""
    row = report.model_dump(mode='json')
    row.pop('provenance', None)
    return row


def record(source, output, *, max_load=20.):
    from stowage.checker import report_matches_inputs
    from stowage.model import Instance, MovePlan
    from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
    from .moves import _screen, prepare_move
    fixture = read_fixture(source)
    instance = Instance.model_validate(fixture['instance'])
    plan = MovePlan.model_validate(fixture['plan'])
    # Previously measured genuine changed swaps, selected by their clear screen,
    # never by a full-check result. Each new profile revalidates the screen.
    recorded = read_fixture(HERE / 'fixtures/benchmark190.json')
    requests = [(r['box_id'], r['target_slot']) for r in recorded['moves'] if r['fast']['legal']][:2]
    out = {'synthetic': True, 'mode': 'recorded', 'created_utc': datetime.now(timezone.utc).isoformat(),
           'source_fixture': str(source), 'source_fixture_sha256': sha256_file(source),
           'instance_sha256': sha256_json(fixture['instance']), 'plan_sha256': sha256_json(fixture['plan']),
           'n_boxes': len(instance.load_list), 'pid': os.getpid(), 'workers': 1,
           'native_thread_caps': {name: os.environ.get(name) for name in (
               'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')},
           'max_admission_load1': max_load, 'method': 'canonical stowage.checker.check; no skipped parts; '
           'cProfile after adjacent unprofiled observation; same process and immutable source inputs; '
           'two-second fleet reading sampler; fixture/model parsing and candidate preparation excluded',
           'source_sha256': {'profile_full.py': sha256_file(__file__),
                             'full.py': sha256_file(ROOT / 'src/stowage/checker/full.py')},
           'complete': False, 'runs': []}

    def save():
        Path(output).write_text(json.dumps(out, indent=2, allow_nan=False) + '\n')

    save()
    base_report = None
    for index, request in enumerate([None, *requests]):
        wait_low_load(max_load)
        if request:
            prepared = prepare_move(instance, plan, *request)
            fast = _screen(prepared, time.perf_counter())
            if not fast.legal or not prepared.replay.legal:
                raise ValueError('Previously clear candidate no longer screens clear')
            candidate = prepared.plan
            detail = {'box_id': request[0], 'target_slot': request[1],
                      'kind': 'swap' if prepared.swapped_box else 'empty_slot',
                      'fast': fast.model_dump(mode='json'),
                      'base_report_matches_candidate_inputs': report_matches_inputs(base_report, instance, candidate),
                      'plan_sha256': sha256_json(candidate.model_dump(mode='json'))}
        else:
            candidate, detail = plan, {'kind': 'base'}
        plain_report, plain = measure(instance, candidate, profiled=False, max_load=max_load)
        if base_report is None:
            base_report = plain_report
        profiled_report, profiled = measure(instance, candidate, profiled=True, max_load=max_load)
        row = {'case': index, **detail, 'unprofiled': plain, 'profiled': profiled,
               'profiled_and_unprofiled_equal_except_provenance': _comparable(plain_report) == _comparable(profiled_report),
               'report': plain_report.model_dump(mode='json')}
        out['runs'].append(row)
        save()
        print(json.dumps({'completed_case': index, 'kind': detail['kind'],
                          'plain_s': plain['wall_s'], 'profiled_s': profiled['wall_s'],
                          'passed': plain_report.passed,
                          'lowload_readings': plain['entire_readings_within_lowload_target']}), flush=True)
    out['complete'] = True
    save()
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=HERE / 'fixtures/envelope190.json')
    parser.add_argument('--output', type=Path, default=HERE / 'fixtures/profile190_lowload.json')
    parser.add_argument('--max-load', type=float, default=20.)
    args = parser.parse_args()
    record(args.source, args.output, max_load=args.max_load)

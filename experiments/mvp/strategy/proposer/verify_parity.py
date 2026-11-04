"""CPU-only direct headroom vs text-adapter parity on two headroom layouts.

The input maps belong to headroom's train sample, outside the fresh small-map
proposer cohort. Both routes execute the planner, V4, checker and audits afresh.
No result from this verification is a proposer-arm result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from experiments.mvp.strategy.proposer.evaluator import DEFAULT_CONFIG, evaluate_text, headroom
from experiments.mvp.strategy.headroom.layout import BlockLayout, BlockModel
from experiments.mvp.strategy.proposer.format import format_layout, parse_layout

SAMPLES = (
    ('small-feeder_500-random_yard-030100620', 'random-011'),
    ('small-feeder_500-random_yard-030100608', 'random-008'),
)


def comparable(row):
    """Ignore wall times, generated IDs and paths, retain observable semantics."""
    check = row.get('check') or {}
    plan = None
    if row.get('result_path'):
        result = headroom.read_json(row['result_path'])
        plan = result['plan']
        plan = {key: value for key, value in plan.items() if key not in {'id', 'provenance'}}
    return dict(status=row['status'], solver_complete=row['solver_complete'],
                layout_honoured=row['layout_honoured'], raw_moves=row['raw_moves'],
                carrier_layout_violations=row.get('carrier_layout_violations'),
                layout_violations=row.get('layout_violations'), plan=plan,
                passed=check.get('passed'), checker_passed=check.get('checker_passed'),
                finalize_status=check.get('finalize_status'), rules=check.get('rules'),
                checked_layout_violations=check.get('layout_violations'),
                count=check.get('count'), moves=check.get('moves'))


def main(argv=None):
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.solver.ballast_deadline import shutdown_worker
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE / 'verification' / 'parity')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.relative_to(HERE)
    output.mkdir(parents=True, exist_ok=True)
    manifest = headroom.read_json(HERE.parent / 'headroom/train_maps.json')
    jobs = {job['map_id']: job for job in manifest['maps']}
    record = dict(passed=False, samples=[], config=DEFAULT_CONFIG, no_model_calls=True,
                  max_compute_workers=2, started_at=time.time())
    try:
        for map_id, layout_id in SAMPLES:
            job = jobs[map_id]
            path = ROOT / headroom.allowed_input(job['input'])
            assert headroom.digest(path) == job['sha256']
            instance = load_instance(path)
            model = BlockModel.from_instance(instance)
            source = HERE.parent / 'headroom/runs/study/maps' / map_id / 'evals' / f'{layout_id}.json'
            source_row = headroom.read_json(source)
            layout = BlockLayout.from_dict(source_row['layout'])
            text = format_layout(layout, instance)
            assert parse_layout(text, instance).fingerprint == layout.fingerprint
            direct_folder = output / map_id / 'direct'
            direct_folder.mkdir(parents=True, exist_ok=True)
            with rule_freeze_v1(enabled=False):
                direct = headroom.evaluate(instance, model, layout, layout_id, 'parity',
                                           direct_folder, DEFAULT_CONFIG)
                direct = headroom.finish_check(instance, model, direct, direct_folder, DEFAULT_CONFIG)
            adapted = evaluate_text(instance, text, output / map_id / 'text',
                                    attempt_id=layout_id)
            left, right = comparable(direct), comparable(adapted)
            mismatches = [key for key in left if left[key] != right[key]]
            sample = dict(map_id=map_id, layout_id=layout_id, input_sha256=job['sha256'],
                          layout_fingerprint=layout.fingerprint, mismatches=mismatches,
                          direct_passed=(direct.get('check') or {}).get('passed'),
                          text_passed=adapted['passed'], moves=adapted['moves'],
                          checked_evaluations=adapted['checked_evaluations'],
                          terminal_certificate_check_calls=adapted['terminal_certificate_check_calls'],
                          terminal_certificate_check_completed=adapted['terminal_certificate_check_completed'],
                          all_full_checker_invocations=adapted['all_full_checker_invocations'],
                          v4_internal_checker_work=adapted['v4_internal_checker_work'],
                          direct_eval_seconds=direct['eval_seconds'], text_eval_seconds=adapted['eval_seconds'])
            record['samples'].append(sample)
            headroom.atomic_json(output / 'verification.json', record)
            print(json.dumps(sample), flush=True)
            assert not mismatches, f'headroom/text mismatch: {mismatches}'
            assert adapted['passed'] and adapted['checked_evaluations'] == 1
        record['passed'] = True
    finally:
        shutdown_worker()
        record['finished_at'] = time.time()
        headroom.atomic_json(output / 'verification.json', record)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

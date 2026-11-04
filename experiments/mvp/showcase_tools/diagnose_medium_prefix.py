#!/usr/bin/env python3
"""Diagnose an actual saved medium prefix; never finish or approve departure.

Run one phase per externally supervised, one-core worker. The caller owns the
hard process deadline (recommended <=180 seconds). No solver or finisher runs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / 'experiments/mvp/runs/showcase-medium'
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
from run_hero import THREAD_ENV, hashes, isolate_caches, load_result, memory_stats, utc, write_json


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    result = hashes()
    for path in sorted((ROOT / 'params').rglob('*.toml')):
        result[str(path.relative_to(ROOT))] = sha(path)
    result[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, type=Path, help='Existing evidence case directory inside showcase-medium')
    parser.add_argument('--phase', required=True, choices=('check', 'mask'))
    args = parser.parse_args()
    case = args.case.resolve()
    if not case.is_relative_to(RUN.resolve()) or not case.is_dir():
        parser.error('--case must be an existing directory inside showcase-medium')
    instance_path = case / 'instance.json'
    prefix_path = case / 'most-loaded-prefix.result.json'
    if not instance_path.is_file() or not prefix_path.is_file():
        parser.error('Case requires instance.json and most-loaded-prefix.result.json')
    for key in THREAD_ENV:
        os.environ[key] = '1'
    os.environ.update(RAYON_NUM_THREADS='1', TOKENIZERS_PARALLELISM='false')
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(RUN / 'numerical-cache' / 'vessels')
    started = time.monotonic()
    output = case / f'prefix-{args.phase}.json'
    before = sources()
    record = {'status': 'running', 'phase': args.phase, 'started_at': utc(), 'pid': os.getpid(),
        'partial': True, 'passed': False, 'departure_approved': False, 'c2_global_screen': 0,
        'rule_freeze_v1': False, 'num_workers': 1, 'gpu': False,
        'instance_path': str(instance_path), 'instance_sha256': sha(instance_path),
        'prefix_path': str(prefix_path), 'prefix_sha256': sha(prefix_path),
        'source_hashes_before': before,
        'notice': 'Diagnostic of a saved incomplete prefix only; departure is not approved.'}
    write_json(output, record)
    try:
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        from stowage.io import load_instance
        if rule_freeze_v1_enabled():
            raise RuntimeError('Prefix diagnostic requires rule-freeze OFF')
        isolate_caches(RUN / 'numerical-cache')
        instance = load_instance(instance_path)
        result = load_result(prefix_path)
        if result.plan is None:
            raise RuntimeError('Saved prefix has no plan')
        if result.plan.instance_id != instance.id or result.plan.call_index != instance.call_index:
            raise RuntimeError('Saved prefix does not match selected instance/call')
        record.update(instance_id=instance.id, planner_status=result.status,
            raw_prefix_metrics=result.metrics, recorded_lifts=len(result.plan.moves),
            required_loads=len(instance.load_list))
        write_json(output, record)
        if args.phase == 'check':
            from stowage.checker import check
            report = check(instance, result.plan)
            hard = [v.model_dump(mode='json') for v in report.violations if v.severity == 'hard']
            record.update(checker_report=report.model_dump(mode='json'),
                prefix_checker_passed=report.passed, first_hard_violation=hard[0] if hard else None,
                hard_violations=len(hard), status='diagnosed')
        else:
            helper = ROOT / 'experiments/mvp/solve47/slack/run_small_yield_finalizer.py'
            record['mask_helper_sha256'] = sha(helper)
            spec = importlib.util.spec_from_file_location('medium_prefix_mask_helper', helper)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            details = {}
            record['mask'] = details
            try:
                # No end-of-plan tokens: this checks precisely the saved prefix.
                module.token_replay(instance, result.plan, False, details, require_complete=False)
                record['status'] = 'diagnosed'
            finally:
                module.summarize_replay(details)
                record['prefix_mask_passed'] = bool(details.get('prefix_passed'))
        return 0
    except Exception as exc:
        record.update(status='diagnostic_error', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
        traceback.print_exc()
        return 1
    finally:
        after = sources()
        record.update(finished_at=utc(), wall_time_s=time.monotonic()-started, resources=memory_stats(),
            passed=False, departure_approved=False, source_hashes_after=after,
            changed_sources=sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)),
            input_bytes_unchanged=record['instance_sha256'] == sha(instance_path),
            prefix_bytes_unchanged=record['prefix_sha256'] == sha(prefix_path))
        write_json(output, record)


if __name__ == '__main__':
    sys.exit(main())

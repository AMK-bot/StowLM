"""Benchmark final model: real artifact miss, warm lookup and fresh-process hit.

The cold experiment uses a disposable empty cache directory; its child process
then loads the same calibrated artifact. Normal production caching remains in
experiments/cache/linear. All timings preserve the full physics/calibration.
"""
from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time

import stowage.linear.cache as cache
from stowage.linear.model import build_linear_model, evaluate_linear, linear_constraints, solve_ballast_lp

_HERE = Path(__file__).resolve().parent


def timed(function, repetitions):
    start = time.perf_counter()
    for _ in range(repetitions):
        result = function()
    return (time.perf_counter()-start)/repetitions, result


def phase(cache_dir):
    cache._DEFAULT_ROOT = Path(cache_dir)
    helpers = runpy.run_path(str(_HERE.parents[1]/'tests/linear/test_model.py'))
    context_function = helpers['context'].__wrapped__
    build_timing = {}
    def measured_build(instance):
        start = time.perf_counter()
        model = build_linear_model(instance)
        build_timing['seconds'] = time.perf_counter()-start
        return model
    context_function.__globals__['build_linear_model'] = measured_build
    start = time.perf_counter()
    instance, model = context_function()
    context_seconds = time.perf_counter()-start
    ship = helpers['forward_cargo'].__wrapped__((instance, model))
    warm, _ = timed(lambda: build_linear_model(instance), 10)
    evaluation, _ = timed(lambda: evaluate_linear(model, ship, instance), 100)
    constraints, _ = timed(lambda: linear_constraints(model, margin_scale=1.1), 100)
    lp_first, result = timed(lambda: solve_ballast_lp(model, ship, instance, margin_scale=1.1), 1)
    assert result.status == 'optimal', result
    lp, result = timed(lambda: solve_ballast_lp(model, ship, instance, margin_scale=1.1), 3)
    assert result.status == 'optimal', result
    def checksum(value):
        return hashlib.sha256(json.dumps(cache._jsonable(value), sort_keys=True).encode()).hexdigest()
    return {'model_sha256': checksum(model),
            'evaluation_sha256': checksum(evaluate_linear(model, ship, instance)),
            'constraints_sha256': checksum(linear_constraints(model, margin_scale=1.1)),
            'model_version': model.version, 'calibration_samples': model.measurement_diagnostics['samples'],
            'model_build_or_load_seconds': build_timing['seconds'],
            'fixture_and_model_seconds': context_seconds, 'warm_lookup_mean_seconds_10': warm,
            'evaluate_mean_seconds_100': evaluation, 'constraints_mean_seconds_100': constraints,
            'lp_first_seconds': lp_first, 'lp_mean_seconds_3': lp,
            'lp_status': result.status, 'ballast_mass_kg': result.objective}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--child-cache-dir', type=Path)
    parser.add_argument('--output', type=Path, default=_HERE/'narrow-benchmark.json')
    args = parser.parse_args()
    started = time.perf_counter()
    if args.child_cache_dir:
        args.output.write_text(json.dumps(phase(args.child_cache_dir), indent=2)+'\n')
        return
    with tempfile.TemporaryDirectory(prefix='stowlm-linear-benchmark-') as temporary:
        root = Path(temporary)
        cold = phase(root)
        child_result = root/'fresh-process.json'
        subprocess.run([sys.executable, str(Path(__file__).resolve()),
                        '--child-cache-dir', str(root), '--output', str(child_result)], check=True)
        disk = json.loads(child_result.read_text())
        namespaces = {name: len(list((root/name).glob('*.json')))
                      for name in ('model', 'geometry', 'integration')}
        assert namespaces['geometry'] > 0 and namespaces['integration'] > 0, namespaces
        for key in ('model_sha256', 'evaluation_sha256', 'constraints_sha256'):
            assert cold[key] == disk[key], f'Cache numerical mismatch: {key}'
    result = {'cold_artifact_miss': cold, 'fresh_process_artifact_hit': disk,
              'verified_artifact_manifest_counts': namespaces,
              'elapsed_seconds': time.perf_counter()-started,
              'cache_note': 'Isolated empty temporary cache for true cold calibration; child process reuses its artifact. Production default unchanged.'}
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()

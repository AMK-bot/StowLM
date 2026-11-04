"""One-thread timing of the final measured model and reusable solver algebra."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import time

from stowage.linear.model import build_linear_model, evaluate_linear, linear_constraints, solve_ballast_lp


def timed(function, repetitions):
    start = time.perf_counter()
    for _ in range(repetitions):
        result = function()
    return (time.perf_counter()-start)/repetitions, result


def main():
    root = Path(__file__).resolve().parents[2]
    helpers = runpy.run_path(str(root / 'tests/linear/test_model.py'))
    started = time.perf_counter()
    context = helpers['context'].__wrapped__()
    build_seconds = time.perf_counter()-started
    instance, model = context
    ship = helpers['forward_cargo'].__wrapped__(context)
    warm_seconds, cached = timed(lambda: build_linear_model(instance), 1)
    assert cached is model
    first_evaluate, _ = timed(lambda: evaluate_linear(model, ship, instance), 1)
    first_constraints, _ = timed(lambda: linear_constraints(model, margin_scale=1.1), 1)
    first_lp, solution = timed(lambda: solve_ballast_lp(model, ship, instance, margin_scale=1.1), 1)
    assert solution.status == 'optimal', solution
    evaluate_seconds, _ = timed(lambda: evaluate_linear(model, ship, instance), 100)
    constraints_seconds, _ = timed(lambda: linear_constraints(model, margin_scale=1.1), 100)
    lp_seconds, solution = timed(lambda: solve_ballast_lp(model, ship, instance, margin_scale=1.1), 3)
    assert solution.status == 'optimal', solution
    result = {
        'vessel': model.vessel_id, 'calibration_samples': model.measurement_diagnostics['samples'],
        'context_and_cold_build_seconds': build_seconds,
        'warm_build_seconds': warm_seconds,
        'first_evaluate_seconds': first_evaluate, 'first_constraints_seconds': first_constraints,
        'first_lp_seconds': first_lp,
        'evaluate_mean_seconds_100': evaluate_seconds,
        'constraints_mean_seconds_100': constraints_seconds,
        'lp_mean_seconds_3': lp_seconds,
        'lp_status': solution.status, 'lp_total_ballast_kg': solution.objective,
        'elapsed_seconds': time.perf_counter()-started,
        'note': 'Full vessel JSON cache validated by source/input digest. Cold timing includes fixture context creation; no prior linear model cache.',
    }
    output = Path(__file__).with_name('benchmark.json')
    output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()

"""Single-core arithmetic A/B for exact cone/edge intersection reuse."""
import os
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
from itertools import product
import json
from pathlib import Path
from statistics import median
from time import perf_counter
import numpy as np
from stowage.checker import visibility_range as module
from stowage.checker.perf import without_reuse

rng = np.random.default_rng(1028)
inputs = [(np.array(list(product((30., 42.), (-4., 4.), (5., 19.))))
           + rng.uniform((-50., -30., -15.), (100., 30., 15.)),
           float(rng.uniform(15., 40.)), float(rng.uniform(50., 500.))) for _ in range(500)]
module._range_intersections.cache_clear()
for args in inputs:
    actual = module.range_blind_intervals(*args)
    with without_reuse():
        assert actual == module.range_blind_intervals(*args)
samples = {'reference': [], 'optimized': []}
for _ in range(7):
    for mode in samples:
        start = perf_counter()
        if mode == 'reference':
            with without_reuse():
                values = [module.range_blind_intervals(*args) for args in inputs]
        else:
            values = [module.range_blind_intervals(*args) for args in inputs]
        samples[mode].append(perf_counter()-start)
result = dict(boxes=500, samples=samples, medians={key: median(value) for key,value in samples.items()},
              bitwise_equal=True, cache=module._range_intersections.cache_info()._asdict())
Path(__file__).with_suffix('.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result))

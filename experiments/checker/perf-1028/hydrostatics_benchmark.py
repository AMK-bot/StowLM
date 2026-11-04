"""One-core full waterplane integration A/B at identical physical inputs."""
import os
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'

from contextlib import nullcontext
from dataclasses import fields
import json
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
from stowage.checker.perf import without_reuse
from stowage.vessel.fleet import list_fleet, load_ship_params
from stowage.vessel.hydrostatics import HullGeometry, clear_hydrostatic_caches


rows = []
for name in list_fleet():
    solid = HullGeometry.from_params(load_ship_params(name))
    clear_hydrostatic_caches()
    samples = {'reference': [], 'reuse': []}
    expected = None
    for iteration in range(8):
        for mode in samples:
            with without_reuse() if mode == 'reference' else nullcontext():
                started = perf_counter()
                result = solid.submerged(.9*solid.design_draught_m, .91*solid.design_draught_m, .01)
                elapsed = perf_counter()-started
            if expected is None:
                expected = result
            for field in fields(result):
                a, b = np.asarray(getattr(result, field.name)), np.asarray(getattr(expected, field.name))
                assert a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()
            if iteration:
                samples[mode].append(elapsed)
    row = dict(vessel=name, samples=samples, medians={mode:median(values) for mode,values in samples.items()},
               bitwise_equal=True)
    print(json.dumps(row), flush=True)
    rows.append(row)
Path(__file__).with_suffix('.json').write_text(json.dumps(rows, indent=2)+'\n')

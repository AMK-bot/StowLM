"""Why does the code's sampler reject a laden synthetic manifest? (counts reasons)"""
import runpy, sys
from pathlib import Path
import numpy as np
here = Path(__file__).resolve().parent; root = here.parents[2]
import stowage.linear.cache as C; C._DEFAULT_ROOT = here/".cache"
helper = runpy.run_path(str(root/"experiments/linear-model/validate.py"))
import stowage.linear.physics as P
from stowage.linear.bands import sample_manifest_loading, band_for_instance
from stowage.conventions import make_container_id
n20, n40 = int(sys.argv[1]), int(sys.argv[2])
inst = helper["fleet_instance"]("feeder_500"); v = inst.vessel
inst = inst.model_copy(update={'vessel': v.model_copy(update={'fixed_loads': [*v.fixed_loads, (.7*v.hull.lpp_m, 0., 2., 400_000.)]})})
rng = np.random.default_rng(7); base = {c.id: c for c in inst.containers}
boxes = [base['QZXU0000035'].model_copy(update={'id': make_container_id('RVWU', k+1), 'gross_mass_kg': float(rng.uniform(6e3, 24e3))}) for k in range(n20)]
boxes += [base['QZXU0000014'].model_copy(update={'id': make_container_id('RVWU', 5000+k), 'iso_type': '45G1', 'reefer': False, 'reefer_setpoint_c': None, 'gross_mass_kg': float(rng.uniform(8e3, 28e3))}) for k in range(n40)]
inst = inst.model_copy(update={'containers': boxes, 'load_list': [b.id for b in boxes]})
band = band_for_instance(inst); print("band", band)
# build a model skeleton without calibration by monkeypatching measure_errors
orig = P.measure_errors
P.measure_errors = lambda m, i, samples=64, seed=0: (m.errors, {})
m = P.build_model(inst, _samples=1); P.measure_errors = orig
print("stacks", len(inst.vessel.stacks), "cells", len(m.cell_ids))
reasons = {}
for k in range(30):
    tank = m.tank_capacity_kg*rng.random(len(m.tank_ids))*m.tank_adjustable
    r = sample_manifest_loading(m, inst, tank, rng)
    if r is None: reasons['sampler None'] = reasons.get('sampler None', 0)+1; continue
    lin = P._linear_arrays(m, r[0], tank)
    key = 'trim prescreen' if abs(lin['trim_m']) > band.max_abs_trim_m*1.3 else 'ok'
    reasons[key] = reasons.get(key, 0)+1
    if k < 5: print(f" lin trim {lin['trim_m']:.2f} gm {lin['gm_m']:.2f} placed {np.count_nonzero(r[0])}")
print(reasons)

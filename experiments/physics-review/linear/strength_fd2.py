"""Finite-difference SF/BM influence check (v2): per-cell exact response vs linear vs
'parallel-sinkage only' buoyancy (no trim redistribution), which isolates the trim term."""
import runpy
from pathlib import Path
import numpy as np
here = Path(__file__).resolve().parent; root = here.parents[2]
import stowage.linear.cache as C; C._DEFAULT_ROOT = here/".cache"
helper = runpy.run_path(str(root/"experiments/linear-model/validate.py"))
import stowage.linear.physics as P
inst = helper["fleet_instance"]("feeder_500"); v = inst.vessel
inst = inst.model_copy(update={'vessel': v.model_copy(update={'fixed_loads': [*v.fixed_loads, (.7*v.hull.lpp_m, 0., 2., 400_000.)]})})
P.measure_errors, orig = (lambda m, i, samples=64, seed=0: (m.errors, {})), P.measure_errors
m = P.build_model(inst, _samples=1); P.measure_errors = orig
tank = m.tank_capacity_kg*m.tank_adjustable*0.5
cell = np.zeros(len(m.cell_ids))
ex0 = P.exact_condition(m, inst, cell, tank)
print(f"base trim exact {ex0['trim_m']:.4f}  disp {ex0['displacement_kg']/1e3:.1f} t")
dm = 50e3
order = np.argsort(m.cell_lcg_m)
for idx in (order[0], order[len(order)//2], order[-1]):
    c = cell.copy(); c[idx] = dm
    ex = P.exact_condition(m, inst, c, tank)
    dbm = ex['bm_nm']-ex0['bm_nm']; lin = m.bm_per_kg_cell[:, idx]*dm
    dtrim_lin = m.trim_line[1]*m.cell_lcg_m[idx]*dm + m.trim_line[2]*dm
    print(f"cell {m.cell_ids[idx]} x={m.cell_lcg_m[idx]:.1f}: dtrim exact {ex['trim_m']-ex0['trim_m']:+.4f} lin {dtrim_lin:+.4f}; "
          f"dBM peak exact {dbm[np.argmax(abs(dbm))]/1e6:+.3f} MN.m at x={m.station_x_m[np.argmax(abs(dbm))]:.1f}; "
          f"max|lin-exact| {np.abs(lin-dbm).max()/1e6:.4f} ({np.abs(lin-dbm).max()/np.abs(dbm).max()*100:.1f}%)")

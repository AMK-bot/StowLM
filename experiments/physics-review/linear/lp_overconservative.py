"""Concrete example: the ballast LP rejects a condition that the exact checker accepts,
because the GM margin (= max|lin-exact|, dominated by the all-tanks-slack FSM reserve)
is subtracted ON TOP of that same reserve.

Fixture feeder_500 + 400 t at 0.7 Lpp (as tests) + a declared high fixed load (z=24 m) at
midship to bring GM into the realistic 0.5-1.5 m range. Full 192-condition calibration.
"""
import runpy, sys
from pathlib import Path
import numpy as np
here = Path(__file__).resolve().parent; root = here.parents[2]
import stowage.linear.cache as C; C._DEFAULT_ROOT = here/".cache"
helper = runpy.run_path(str(root/"experiments/linear-model/validate.py"))
import stowage.linear.physics as P
from stowage.linear.model import solve_ballast_lp, linear_constraints
w = float(sys.argv[1]) if len(sys.argv) > 1 else 500e3
inst = helper["fleet_instance"]("feeder_500"); v = inst.vessel; L = v.hull.lpp_m
inst = inst.model_copy(update={'vessel': v.model_copy(update={'fixed_loads': [*v.fixed_loads, (.7*L, 0., 2., 400_000.), (.5*L, 0., 24., w)]}), 'load_list': []})
m = P.build_model(inst)            # default: 64 + 128 calibration conditions
print(f"high load {w/1e3:.0f} t at z=24 m; stated GM error {m.errors.gm_m:.3f} m; sum FSM_max/Dmid {m.tank_fsm_max_kgm.sum()/((m.band.displacement_min_kg+m.band.displacement_max_kg)/2):.3f} m; gm_min {m.gm_min_m}")
print("code gm_components:", {k: round(d['max_abs'], 4) for k, d in m.measurement_diagnostics['batches'][1]['gm_components'].items()})
res = solve_ballast_lp(m, inst.onboard, inst, margin_scale=1.0)
print(f"stated list error {m.errors.list_rad:.4f} rad (limit {m.max_list_rad:.4f}), trim error {m.errors.trim_m:.4f} m"); print("LP:", res.status, "binding", res.binding[:6], "min_violation", res.min_violation)
cell = np.zeros(len(m.cell_ids))
best = None
for name, frac in (("all empty", 0.), ("all half", .5), ("all full", 1.)):
    tank = m.tank_capacity_kg*m.tank_adjustable*frac
    ex = P.exact_condition(m, inst, cell, tank); lin = P._linear_arrays(m, cell, tank)
    cs = linear_constraints(m); val = cs.a_cell@cell+cs.a_tank@tank+cs.const
    viol = [cs.names[i] for i in range(len(val)) if val[i] > cs.ub[i]+1e-6 or val[i] < cs.lb[i]-1e-6]
    print(f"  {name:9s}: exact GM {ex['gm_m']:.3f} (FSC {ex['fsc_m']:.3f}) trim {ex['trim_m']:+.3f} list {ex['list_rad']:+.4f} | "
          f"linear GM {lin['gm_m']:.3f}, GM row needs lin GM >= {m.gm_min_m+m.errors.gm_m:.3f} | violated rows: {viol[:5]}")

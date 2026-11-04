"""Physics review 3.2: where do max SF/BM ratios occur (station x/L), and SF/limit at quarter points.

Run: UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/physics-review/strength/probe_ratio_location.py
"""
import numpy as np
from stowage.checker import strength as S
from stowage.model import ShipState
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location('pc', pathlib.Path(__file__).with_name('probe_conditions.py'))
pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)

for name in ('feeder_500', 'feeder_1000'):
    pc.NAME[:] = [name]
    inst, _ = pc.make_instance(name)
    L = inst.vessel.hull.lpp_m; cells = inst.vessel.cells; vid = inst.vessel.id
    conds = [('empty', ShipState(vessel_id=vid))]
    pl = pc.load_boxes(inst, [c.id for c in cells if c.accepts_20], 10_000)
    conds.append(('homog10t', ShipState(vessel_id=vid, placements=pl)))
    for label, st in conds:
        r = S.check_strength(inst, st, call_index=0)
        m = r.metrics
        rows = []
        for s in inst.vessel.hull.stations:
            lab = format(s.x_m, '.12g'); sf, bm = m[f'sf_n@{lab}'], m[f'bm_nm@{lab}']
            rows.append((s.x_m/L, sf, s.max_shear_n, sf/(s.max_shear_n if sf >= 0 else -s.min_shear_n) if True else 0,
                         bm, s.max_bending_nm))
        rows = np.array(rows)
        i = int(np.argmax(np.abs(rows[:, 3])))
        j = int(np.argmax(np.abs(rows[:, 4] / rows[:, 5])))
        print(f'{name} {label}: viol={len(r.violations)} max SF ratio {abs(rows[i,3]):.3f} at u={rows[i,0]:.3f} '
              f'(SF={rows[i,1]/1e6:+.2f} MN, limit={rows[i,2]/1e6:.2f} MN); max|SF| {np.max(np.abs(rows[:,1]))/1e6:.2f} MN at '
              f'u={rows[int(np.argmax(np.abs(rows[:,1]))),0]:.3f}; max BM ratio {abs(rows[j,4]/rows[j,5]):.3f} at u={rows[j,0]:.3f}')
        for u in (.02, .05, .1, .25, .5, .75, .9):
            k = int(np.argmin(np.abs(rows[:, 0]-u)))
            print(f'    u={rows[k,0]:.3f} SF={rows[k,1]/1e6:+7.2f} MN limit={rows[k,2]/1e6:6.2f} ratio={abs(rows[k,3]):.3f}  '
                  f'BM={rows[k,4]/1e6:+8.2f} limit={rows[k,5]/1e6:7.2f}')

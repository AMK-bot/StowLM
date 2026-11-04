"""Reproduce feeder_1700 homogeneous 10 t/cell SF 124 % and locate it.

Run: STOWAGE_VESSEL_CACHE_DIR=/private/tmp/stow-strength-vessels OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
     uv run python experiments/physics-review/design/strength/diag_1700.py [name]
"""
import sys, importlib.util, pathlib, math
import numpy as np
from stowage.checker.strength import check_strength
from stowage.checker import strength_limits as SL

root = pathlib.Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('fl', root/'experiments/strength/full_load.py')
fl = importlib.util.module_from_spec(spec); spec.loader.exec_module(fl)
name = sys.argv[1] if len(sys.argv) > 1 else 'feeder_1700'
inst = fl.make_condition(name)
hull = inst.vessel.hull; L = hull.lpp_m
r = check_strength(inst, inst.onboard, call_index=0)
m = r.metrics
print(name, 'L', L, 'max_sf_ratio', m['max_sf_ratio'], 'max_bm_ratio', m['max_bm_ratio'], 'T', m['draught_aft_m'], m['draught_fwd_m'])
params = fl.load_ship_params(name)
_, geom, _, kw = SL._inputs(params, hull)
Z, A = SL._assumed_scantlings(kw)
print('kw', {k: round(v, 4) for k, v in kw.items()}, 'Z', Z, 'A_peak', A, 'Zmin', SL.minimum_section_modulus_m3(L, kw['beam_m'], kw['block_coefficient']))
xs = sorted({round(c.lcg_m, 2) for c in inst.vessel.cells})
print('cell lcg range', xs[0], xs[-1], 'n bays-ish', len(xs))
print(' x/L    x     SF(MN)  lim+(MN) lim-(MN) ratio  waveSF+ waveSF- cap   BM(MN.m) hog-lim sag-lim')
rows = []
for s in hull.stations:
    lab = format(s.x_m, '.12g'); sf, bm = m[f'sf_n@{lab}'], m[f'bm_nm@{lab}']
    ratio = sf/s.max_shear_n if sf >= 0 else sf/s.min_shear_n
    w = SL.wave_loads(SL._rule_station(s.x_m, L), **kw)
    cap = SL.SHEAR_PERMISSIBLE_PA*A*(.35+.65*math.sin(2*math.pi*SL._rule_station(s.x_m, L)/L)**2)
    rows.append((s.x_m/L, s.x_m, sf/1e6, s.max_shear_n/1e6, s.min_shear_n/1e6, ratio, w['max_shear_n']/1e6, w['min_shear_n']/1e6, cap/1e6, bm/1e6, s.max_bending_nm/1e6, s.min_bending_nm/1e6))
rows = np.array(rows)
last = -1
for row in rows:
    if row[0] - last >= .025 or row[5] > .9:
        print(' '.join(f'{v:8.3f}' for v in row)); last = row[0]
i = int(np.argmax(rows[:, 5])); print('PEAK SF ratio', rows[i])
print('max |SF| MN', np.abs(rows[:, 2]).max(), 'at x/L', rows[int(np.argmax(np.abs(rows[:, 2]))), 0])

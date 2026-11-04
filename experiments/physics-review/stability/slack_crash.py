"""Reproduce the slack-ballast crash (review 3.1): traceback of the swallowed ValueError."""
import sys, traceback, math
sys.path.insert(0, 'experiments/physics-review/stability')
from probes import feeder, P, G
from stowage.checker import stability as S
from stowage.checker.stability_loading import loading_condition, liquid_cg
for frac in (.5, .3, .9):
    half = 130.39488*frac*1025
    i = feeder(kg=6.0, tanks={'WB-2-P': half, 'WB-2-S': half})
    cond = loading_condition(i, i.onboard, geometry=G, ship_params=P)
    print('fill', frac, 'fsc', cond.fsc_m, 'tanks', [t.id for t in cond.tanks], 'actual', [t.id for t in cond.actual_tanks])
    try:
        cg = (cond.lcg_m, cond.tcg_m, cond.kg_m)
        f = G.equilibrium(cond.mass_kg, *cg, 1025.)
        print('  equilibrium heel', f.heel_rad, 'trim', f.trim_m)
        print('  liquid_cg', liquid_cg(cond, f.heel_rad, math.atan(f.trim_m/G.lpp_m)))
    except Exception:
        traceback.print_exc(limit=-3)
    r = S.check_stability(i, i.onboard, call_index=0)
    print('  check ran', r.ran, r.error, r.metrics.get('fsc_m'))

# Re-run the free-trim curve outside the try/except to expose the traceback.
import numpy as np
half = 130.39488*.5*1025
i = feeder(kg=6.0, tanks={'WB-2-P': half, 'WB-2-S': half})
cond = loading_condition(i, i.onboard, geometry=G, ship_params=P)
angles = np.radians(np.sort(np.r_[np.arange(0., 90., 2.), 25., 89.99]))
try:
    G.free_trim_curve(angles, cond.mass_kg, cond.lcg_m, cond.kg_m, 1025.,
                      cg_callback=lambda phi, trim: liquid_cg(cond, phi, trim))
    print('curve OK')
except Exception:
    traceback.print_exc()

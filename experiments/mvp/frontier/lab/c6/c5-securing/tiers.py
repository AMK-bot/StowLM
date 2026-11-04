"""Per-tier weight of mass in the binding rows of one deck stack, for NOTES.md."""
import numpy as np
from rows import stack_rows, HEIGHT
from stowage.checker import lashing
class Hull:
    lpp_m, beam_m, depth_m, scantling_draught_m, bilge_keel = 124.41, 22.5, 11.3, 8.7, False
m = lashing._motions(Hull, 7.5, 1.9, .65)
print('roll period', m.period_roll, 'phi deg', np.degrees(m.phi), 'at by tier', [round(m.at(60, 5, 12.5+(k+.45)*HEIGHT)[1], 2) for k in range(5)])
A, Bt, Bl, names = stack_rows(m, 60., 5., 12.5, 5)
for name in (('tension', 'soft', 0), ('tension', 'soft', 2), ('rod', 'soft')):
    r = names.index(name)
    print(name, 'per 10 t by tier', np.round(A[r]*10000, 3), 'wind floor', round(float(Bt[r]@np.full(5, 31600.)+Bl[r]@np.full(5, 6400.)), 3))
for n in (2, 3, 4, 5, 6):
    A, Bt, Bl, names = stack_rows(m, 60., 5., 12.5, n)
    lo, hi = 0., 40000.
    for _ in range(40):
        mid = (lo+hi)/2
        ok = np.max(A@np.full(n, mid)+Bt@np.full(n, 31600.)+Bl@np.full(n, 6400.)) <= 1.
        lo, hi = (mid, hi) if ok else (lo, mid)
    print(n, 'high: largest uniform box mass t', round(lo/1000, 1), 'stack t', round(n*lo/1000, 1))

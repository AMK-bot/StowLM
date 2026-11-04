"""Independent check of the declared-opening flooding angle (review 3.1).

At the code's phi_f the opening (x=8, y=7, z=10) must lie on the free-trim waterplane
z = Ta + (Tf-Ta) x/L + y tan(phi). Solve free trim directly at that angle (not interpolated)."""
import math, sys, numpy as np
sys.path.insert(0, 'experiments/physics-review/stability')
from probes import feeder, P, G
from stowage.checker import stability as S
from scipy.optimize import brentq
i = feeder(kg=6.0)
c = S.loading_condition(i, i.onboard, geometry=G, ship_params=P)
def clearance(phi):
    _, d = G.free_trim_curve(np.array([phi]), c.mass_kg, c.lcg_m, c.kg_m, 1025.)
    ta, tf = d[0]
    return 10.0 - (ta + (tf-ta)*8/G.lpp_m + 7*math.tan(phi)), ta, tf
for deg in (0, 10, 20, 20.212):
    cl, ta, tf = clearance(math.radians(deg)); print(f'{deg:7.3f} deg: Ta={ta:.4f} Tf={tf:.4f} clearance={cl:.4f}')
root = brentq(lambda p: clearance(p)[0], math.radians(5), math.radians(30), xtol=1e-6)
print('direct phi_f =', math.degrees(root), 'deg (code 20.212 from 2-deg interpolated drafts)')

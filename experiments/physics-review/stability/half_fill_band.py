"""Find fills near 50 % where liquid_centroid's brentq bracket fails (review 3.1)."""
import math, numpy as np
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry
from stowage.checker.stability_loading import liquid_centroid
P = load_ship_params('feeder_500')
for tid in ('WB-2-P', 'WB-1-P', 'FO-P'):
    s = TankGeometry.from_params(next(t for t in P['tanks'] if t['id'] == tid))
    cap = s.capacity_m3
    bad = []
    for rel in (-1e-9, -1e-12, -1e-14, -3e-16, 0., 3e-16, 1e-14, 1e-12, 1e-9):
        for deg in (2., 10., 30., 60.):
            try: liquid_centroid(s, cap*(.5+rel), math.radians(deg), 0.001)
            except ValueError: bad.append((rel, deg))
    # realistic: mass = 0.5 * nominal capacity from the instance tank table * density
    v = (cap*.5*1025)/1025
    print(tid, 'cap', repr(cap), 'failing (rel offset, heel):', bad)

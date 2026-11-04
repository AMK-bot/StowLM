"""Single-core direct tank timings; clear numerical memo before each sample.

Reference centroid agreement uses the original vessel geometry provider, not
an interpolation table. Envelope tolerance remains explicitly recorded.
"""
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry
from stowage.checker import stability_tank_transfer as direct


def run():
    rows=[]
    for name in ('feeder_500','feeder_800','feeder_1700'):
        params=load_ship_params(name)
        for identity in ('DB-A-P','FPT-P','FO-A-P'):
            record=next(t for t in params['tanks'] if t['id']==identity)
            solid=TankGeometry.from_params(record,ship_params=params)
            heel,trim=np.deg2rad(37.3),.013
            direct._CACHE.clear(); direct._POLYNOMIAL_CACHE.clear()
            started=perf_counter()
            envelope=direct.max_transfer(solid,heel,trim,tolerance_m4=.1)
            envelope_s=perf_counter()-started
            started=perf_counter()
            centroid=direct.liquid_centroid(solid,.417*solid.capacity_m3,heel,trim)
            centroid_s=perf_counter()-started
            expected=np.asarray(solid.liquid(.417*solid.capacity_m3,heel,trim).centroid_m)
            rows.append(dict(ship=name,tank=identity,envelope_seconds=envelope_s,
                centroid_seconds=centroid_s,envelope_upper_bound_m4=envelope,
                envelope_tolerance_m4=.1,centroid_error_m=float(np.max(abs(centroid-expected)))))
    return dict(scope='fresh direct numerical kernels; no disk interpolation or memo hits',rows=rows)


if __name__=='__main__':
    result=run()
    Path(__file__).with_name('kernel_timings.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

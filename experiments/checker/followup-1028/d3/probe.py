import os
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'): os.environ[k]='1'
import json,sys,warnings
from time import perf_counter
from pathlib import Path
import numpy as np
from stowage.vessel.fleet import list_fleet,load_ship_params
from stowage.vessel.tanks import TankGeometry
from stowage.checker.stability_tank_transfer import liquid_centroid
rows=[]; failures=[]; start=perf_counter()
with warnings.catch_warnings():
 warnings.simplefilter('ignore')
 for ship in list_fleet():
  params=load_ship_params(ship)
  for tank in params['tanks']:
   solid=TankGeometry.from_params(tank,ship_params=params)
   for volume in (0.,1e-15,1e-14,1e-13,1e-12,1e-11,1e-10,1e-9,1e-8,1e-6,.01*solid.capacity_m3):
    for heel in (0.,.2,.8):
     row=dict(ship=ship,tank=tank['id'],volume=volume,heel=heel)
     try:
      liquid=solid.liquid(volume,heel)
      direct=liquid_centroid(solid,volume,heel)
      bounds=np.array([[getattr(solid,f'{a}_min_m'),getattr(solid,f'{a}_max_m')] for a in 'xyz'])
      centres=np.array([liquid.centroid_m,direct])
      row.update(centroids=centres.tolist(),fsm=liquid.fsm_m4,area=liquid.free_surface_area_m2,liquid_volume=liquid.volume_m3)
      row['valid']=bool(np.isfinite(centres).all() and (centres>=bounds[:,0]-1e-8).all() and (centres<=bounds[:,1]+1e-8).all())
     except Exception as e:
      row.update(valid=False,error=repr(e))
     rows.append(row)
     if not row['valid']: failures.append(row)
record=dict(seconds=perf_counter()-start,checks=len(rows),failures=failures,rows=rows)
Path(sys.argv[1]).write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:v for k,v in record.items() if k!='rows'}),flush=True)

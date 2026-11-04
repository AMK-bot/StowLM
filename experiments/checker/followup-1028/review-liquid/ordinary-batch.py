import os
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
import json,sys
from pathlib import Path
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry
from stowage.checker.stability_loading import CorrectionTank,LoadingCondition
from stowage.checker.stability_direct_liquid_batch import prepare,centroids,evaluate
p=load_ship_params('feeder_500');tanks=[]
for record in p['tanks']:
 solid=TankGeometry.from_params(record,ship_params=p)
 if not solid._prismatic:
  tanks.append(CorrectionTank(record['id'],1025.,solid.capacity_m3*.37,0.,solid))
 if len(tanks)==3:break
condition=LoadingCondition(8e6,50.,0.,6.,0.,0.,actual_tanks=tuple(tanks))
rows=[]
for heel,trim in ((0.,0.),(.2,0.),(.8,0.),(.2,.01),(-.2,-.01)):
 centres=centroids(prepare(condition),heel,trim)
 rows.append(dict(heel=heel,trim=trim,centres=[[float(v).hex() for v in row] for row in centres],cg=[v.hex() for v in evaluate(condition,heel,trim)]))
path=Path(__file__).with_name('ordinary-before.json')
if '--write' in sys.argv:path.write_text(json.dumps(rows,indent=2)+'\n')
else:
 assert rows==json.loads(path.read_text())
 print('15 ordinary tank centroids and5 ship CGs bitwise identical')

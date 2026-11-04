"""Short, single-thread production pilot (no changes to src)."""
import json, math, resource, runpy, time
import numpy as np
from scipy.optimize import brentq
from stowage.vessel.hydrostatics import HullGeometry
start=time.perf_counter()
p=runpy.run_path('experiments/hydrostatics/validate_kcs.py')['reference_params']()
g=HullGeometry.from_params(p,nx=96,nz=128)
rows=[]
for name,vol,kg in [('aqwa2025',51906.,14.38),('real2025',52030.,14.32),('jmse2022',54148.,13.674)]:
    draft=brentq(lambda t:g.submerged(t,t,0).volume_m3-vol,9,12)
    initial=g.submerged(draft,draft,0)
    print(name,'draft',draft,'lcb',initial.lcb_m,'GM',initial.vcb_m+initial.transverse_inertia_m4/vol-kg,flush=True)
    for deg in (0,10,20,30,40,50,60):
        a=math.radians(deg)
        f=g._solve(vol*1025,initial.lcb_m,0,kg,1025,a)
        _,_,across=g._axes(f.t_aft_m,f.t_fwd_m,a)
        gz=float(np.dot(np.array(f.submerged.centroid_m)-[initial.lcb_m,0,kg],across))
        kn=g.kn(a,vol*1025,initial.lcb_m)
        row=dict(case=name,angle_deg=deg,gz_m=gz,kn_m=kn,kn_kg_sin_m=kn-kg*math.sin(a),trim_m=f.t_aft_m-f.t_fwd_m)
        rows.append(row)
        print(row,flush=True)
print('wall_s',time.perf_counter()-start,'maxrss',resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
open('experiments/hydrostatics/large-angle-1028/pilot.json','w').write(json.dumps(rows,indent=2)+'\n')

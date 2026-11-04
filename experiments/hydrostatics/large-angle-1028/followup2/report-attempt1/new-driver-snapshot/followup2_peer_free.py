"""APPROXIMATION: free-trim sensitivity with missing LCG set to own upright LCB."""
import json
import math
import resource
import runpy
import time
import numpy as np
from followup2_common import ROOT, OUT, save, source_hashes
from followup2_peer import clipped
from iges_upper19 import read_patches, upper_triangles
from stowage.vessel.hydrostatics import HullGeometry

start=time.perf_counter();before=source_hashes()
primary=json.loads((OUT/'peer17p2.json').read_text())
native=HullGeometry.from_params(runpy.run_path(str(ROOT/'experiments/hydrostatics/validate_kcs.py'))['reference_params'](),nx=96,nz=128)
g,_=clipped(native,upper_triangles(read_patches(),80))
lcg=primary['upright_at_source_volume']['lcb_m'];kg=13.674;volume=54148.
rows=[]
for row in primary['rows']:
    deg=row['heel_deg'];a=math.radians(deg)
    f=g._solve(volume*1025.,lcg,0.,kg,1025.,a)
    _,_,across=g._axes(f.t_aft_m,f.t_fwd_m,a)
    gz=float(np.dot(np.array(f.submerged.centroid_m)-[lcg,0.,kg],across))
    out=dict(heel_deg=deg,gz_m=gz,trim_aft_minus_fore_m=f.t_aft_m-f.t_fwd_m,
        volume_residual_m3=f.submerged.volume_m3-volume,solver_scaled_residual=f.residual_norm)
    for author in ('abs','csi'):
        ref=row[author]['reference_gz_m'];e=gz-ref
        out[author]=dict(error_m=e,within_l5_style_bar=abs(e)<=row[author]['l5_style_bar_m'])
    rows.append(out);print(deg,gz,out['abs']['error_m'],out['csi']['error_m'],flush=True)
result=dict(assumption='Missing LCG set to own upright LCB at published volume; actual-KG free trim. Not a recovered author loading condition.',
    lcg_m=lcg,rows=rows,source_hashes_before=before,source_hashes_after=source_hashes(),
    elapsed_s=time.perf_counter()-start,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
save(OUT/'peer17p2-free-trim.json',result)
assert before==result['source_hashes_after']

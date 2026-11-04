"""JMSE2022 independent published curves at the published 17.2m envelope.

APPROXIMATION: reuses the explicitly documented IGES tessellation/splice,
clipped at the paper's depth17.2. Fixed-zero-trim is the primary comparison:
paper LCG and heeled trim policy are not available. No ordinate is fitted.
"""
import csv
from datetime import datetime, timezone
import json
import math
import resource
import runpy
import time
import numpy as np
from followup2_common import HERE, ROOT, OUT, save, source_hashes
from extend19 import geometry_from_sections, solve_fixed_zero_trim
from iges_upper19 import read_patches, upper_triangles, append_upper
from stowage.vessel.hydrostatics import HullGeometry


def clipped(native, mesh):
    sections=[]; diagnostics=[]
    for x, section in zip(native.x_m,native.sections):
        poly, diagnostic=append_upper(section[0],mesh,x,clip_top=17.2)
        sections.append([poly,*[p.copy() for p in section[1:]]])
        diagnostics.append(diagnostic)
    return geometry_from_sections(native,sections,depth_m=17.2),diagnostics


def main():
    start=time.perf_counter(); before=source_hashes()
    params=runpy.run_path(str(ROOT/'experiments/hydrostatics/validate_kcs.py'))['reference_params']()
    native=HullGeometry.from_params(params,nx=96,nz=128)
    patches=read_patches()
    fine, diagnostic=clipped(native,upper_triangles(patches,80))
    coarse,_=clipped(native,upper_triangles(patches,40))
    volume,kg=54148.,13.674
    upright=solve_fixed_zero_trim(fine,0.,volume,kg)
    rows=[]
    for ref in csv.DictReader((HERE/'marlantes-fig22-samples.csv').open()):
        if not ref['abs_gz_m'] or not ref['csi_gz_m']: continue
        a=float(ref['heel_deg']); v=solve_fixed_zero_trim(fine,a,volume,kg)
        low=solve_fixed_zero_trim(coarse,a,volume,kg)
        row=dict(heel_deg=a,computed=v,mesh40_minus80_gz_m=low['gz_m']-v['gz_m'])
        for author in ('abs','csi'):
            gz=float(ref[f'{author}_gz_m']); err=v['gz_m']-gz
            row[author]=dict(reference_gz_m=gz,error_gz_m=err,
                l5_style_bar_m=max(.05,.05*abs(gz)),within_l5_style_bar=abs(err)<=max(.05,.05*abs(gz)),
                graphical_reading_allowance_m=.02 if author=='abs' else .01)
        rows.append(row)
        print(a, v['gz_m'],row['abs']['error_gz_m'],row['csi']['error_gz_m'],flush=True)
    result=dict(date_utc=datetime.now(timezone.utc).isoformat(),
        source='Marlantes, Kim and Hurt, JMSE2022,10(1),41,Tables23/24 and Figure22',
        doi='https://doi.org/10.3390/jmse10010041',
        loading=dict(volume_m3=volume,kg_m=kg,source_draught_m=10.8,source_depth_m=17.2,
                     fixed_trim_m=0.,source_lcg_m=None,source_heel_trim_policy=None),
        upright_at_source_volume=upright,
        volume_at_source_draught_m3=fine.submerged(10.8,10.8,0.).volume_m3,
        rows=rows,section_diagnostics=diagnostic,
        summary={author:dict(points=len(rows),passed=sum(r[author]['within_l5_style_bar'] for r in rows),
            passed20to50=sum(r[author]['within_l5_style_bar'] for r in rows if 20<=r['heel_deg']<=50),
            max_abs_error_m=max(abs(r[author]['error_gz_m']) for r in rows),
            max_abs_error20to50_m=max(abs(r[author]['error_gz_m']) for r in rows if 20<=r['heel_deg']<=50))
            for author in ('abs','csi')},
        max_mesh40to80_gz_change_m=max(abs(r['mesh40_minus80_gz_m']) for r in rows),
        source_hashes_before=before,source_hashes_after=source_hashes(),
        elapsed_s=time.perf_counter()-start,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    save(OUT/'peer17p2.json',result)
    assert before==result['source_hashes_after'],'Source drift: inspect result manifest'
    print(json.dumps(result['summary']),result['elapsed_s'],flush=True)


if __name__=='__main__': main()

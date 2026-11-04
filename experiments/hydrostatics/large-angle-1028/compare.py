"""Reproduce read-only KCS comparisons against independently published figures.

APPROXIMATION: use the original 16m-sealed source loft, with no reconstructed
upper hull. Missing published LCG is set to our own upright LCB at the required
volume, giving even keel initially. Actual-KG free trim and zero-trim results
are both recorded because neither reference specifies the heel trim policy.
This is a diagnostic comparison, not class approval or validation of mismatched
envelopes. No parameters are fitted to plotted ordinates.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path
import resource
import runpy
import time
import numpy as np
from scipy.optimize import brentq
from stowage.vessel.hydrostatics import HullGeometry

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
SOURCE_FILES=['src/stowage/vessel/hydrostatics.py','src/stowage/vessel/hull_offsets.py',
    'experiments/hydrostatics/validate_kcs.py','data/raw/kcs/kcs.fix',
    'experiments/hydrostatics/large-angle-1028/compare.py']
def hashes():
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCE_FILES}

def l5_gz(reference):
    return max(.05,.05*abs(reference))

def solve(g,deg,vol,kg,lcg):
    angle=math.radians(deg)
    # Using the public geometry's unchanged fixed-heel solver with physical KG
    # is essential: KN-KG*sin(phi) alone ignores the KG-dependent free trim.
    f=g._solve(vol*1025,lcg,0.,kg,1025.,angle)
    _,_,across=g._axes(f.t_aft_m,f.t_fwd_m,angle)
    gz=float(np.dot(np.array(f.submerged.centroid_m)-[lcg,0.,kg],across))
    bound=100.
    d=brentq(lambda t:g.submerged(t,t,angle).volume_m3-vol,-bound,bound)
    b=g.submerged(d,d,angle)
    fixed_kn=b.tcb_m*math.cos(angle)+b.vcb_m*math.sin(angle)
    fixed_gz=fixed_kn-kg*math.sin(angle)
    kn=g.kn(angle,vol*1025,lcg_m=lcg)
    return dict(heel_deg=float(deg),production_gz_m=gz,production_kn_m=kn,
        zero_trim_gz_m=fixed_gz,zero_trim_kn_m=fixed_kn,
        kn_minus_kg_sin_m=kn-kg*math.sin(angle),
        trim_aft_minus_fore_m=f.t_aft_m-f.t_fwd_m,
        volume_residual_m3=f.submerged.volume_m3-vol,solver_scaled_residual=f.residual_norm)

def main():
    start=time.perf_counter(); before=hashes()
    p=runpy.run_path(str(ROOT/'experiments/hydrostatics/validate_kcs.py'))['reference_params']()
    g=HullGeometry.from_params(p,nx=96,nz=128)
    upright=g.submerged(10.8,10.8,0)
    cases=[]
    definitions=[('marlantes2022',54148.,13.674,OUT/'marlantes-fig22-samples.csv',{'abs_gz_m':.02,'csi_gz_m':.01}),
                 ('trang2025_aqwa',51906.,14.38,OUT/'secondary/jmse2025_fig28_bare_hull.csv',{'gz_m':.01}),
                 ('trang2025_real',52030.,14.32,OUT/'secondary/jmse2025_fig28_bare_hull.csv',{'gz_m':.01})]
    # Keep this mandatory: a missing extraction must not silently omit the
    # strongest explicit KN/GZ comparison from an apparently complete report.
    definitions.append(('keramati2026',51765.,13.,OUT/'keramati-fig8-samples.csv',
                        {'kcs_gz_m':.01,'kcs_kn_m':.04}))
    rows=[]
    for name,vol,kg,file,columns in definitions:
        draft=brentq(lambda t:g.submerged(t,t,0).volume_m3-vol,9.,12.)
        s=g.submerged(draft,draft,0); lcg=s.lcb_m
        source=list(csv.DictReader(file.open()))
        conditions=dict(case=name,volume_m3=vol,kg_m=kg,assumed_lcg_m=lcg,
            own_upright_draft_m=draft,own_gm_m=s.vcb_m+s.transverse_inertia_m4/vol-kg,
            source_csv=str(file.relative_to(ROOT)),source_csv_sha256=hashlib.sha256(file.read_bytes()).hexdigest())
        checks=[]
        for row in source:
            deg=float(row['heel_deg'])
            # Do not extrapolate missing source points at 55/60 degrees.
            if not any(row.get(c) for c in columns): continue
            ours=solve(g,deg,vol,kg,lcg)
            for col,reading in columns.items():
                if not row.get(col): continue
                ref=float(row[col]); kind='kn' if 'kn' in col else 'gz'
                fixed_trim=name=='keramati2026'
                actual=ours[('zero_trim_kn_m' if kind=='kn' else 'zero_trim_gz_m') if fixed_trim
                            else ('production_kn_m' if kind=='kn' else 'production_gz_m')]
                tol=.05 if kind=='kn' else l5_gz(ref)
                record=dict(case=name,source_series=col,quantity=kind,
                    compared_trim_policy='fixed_zero' if fixed_trim else 'free',
                    compared_production_m=actual,**ours,
                    reference_m=ref,error_m=actual-ref,l5_diagnostic_tolerance_m=tol,
                    within_l5_diagnostic_bar=abs(actual-ref)<=tol,
                    graphical_reading_allowance_m=reading,
                    discrepancy_exceeds_bar_plus_reading=abs(actual-ref)>tol+reading)
                rows.append(record); checks.append(record)
        cases.append(dict(**conditions,comparisons=checks))
    after=hashes()
    if before!=after: raise RuntimeError('Source changed during comparison; repeat with stable source')
    result=dict(date='2026-10-28',scope='unscaled KCS offsets; sealed at16m; no coamings',
        source_hashes=before,production_grid=dict(nx=96,nz=128),
        upright_at_published_draft=dict(draft_m=10.8,volume_m3=upright.volume_m3,lcb_m=upright.lcb_m,
            km_m=upright.vcb_m+upright.transverse_inertia_m4/upright.volume_m3),
        assumptions=['Source curves are plotted numerical results, not towing-tank measurements.',
            '2022/2025 heel trim policy and LCG not reported; LCG chosen for own even-keel state.',
            '2026 source says upright trim; fixed zero trim used, not an unspecified free-trim condition.',
            'Compare at reported volume, not force both source draft and mismatched source volume.',
            'IACS L5 bars are diagnostic only: reference envelope and loading must match for approval.'],
        cases=cases,wall_s=time.perf_counter()-start,max_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (OUT/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')
    with (OUT/'comparison.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(json.dumps(dict(cases=[dict(case=c['case'],n=len(c['comparisons']),passed=sum(r['within_l5_diagnostic_bar'] for r in c['comparisons']),draft=c['own_upright_draft_m']) for c in cases],wall_s=result['wall_s'],max_rss_bytes=result['max_rss_bytes']),indent=2))
if __name__=='__main__': main()

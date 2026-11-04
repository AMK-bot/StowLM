"""Extract the published black bare-hull vector curve, not our hydrostatics.
Run: uv run --with pymupdf python experiments/hydrostatics/large-angle-1028/secondary/extract_fig28.py
APPROXIMATION: figure coordinates represent publication graphics, not original tabular data.
We assign conservative reading uncertainty +/-0.01 m and +/-0.3 deg, not statistical errors.
"""
from pathlib import Path
import csv
import json
import math
import pymupdf

ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
PDF=ROOT/'data/raw/kcs-largeangle-secondary/jmse-13-00412.pdf'
p=pymupdf.open(PDF)[17]
ds=p.get_drawings()
# PDF p18 plotting axes and bare-hull path. Assertions make selection fail loudly.
border=ds[5]['rect']; curve=ds[8]
assert curve['type']=='s' and curve['color']==(0,0,0) and len(curve['items'])==15
assert all(it[0]=='c' for it in curve['items'])
x0,x1=border.x0,border.x1; y1,y0=border.y0,border.y1

def physical(point):
    return ((point.x-x0)/(x1-x0)*60, (y0-point.y)/(y0-y1))

def bezier(ps,t):
    u=1-t
    return pymupdf.Point(*(u*u*u*ps[0][k]+3*u*u*t*ps[1][k]+3*u*t*t*ps[2][k]+t*t*t*ps[3][k] for k in (0,1)))

segments=[it[1:] for it in curve['items']]
pa,pb=segments[0][:2]
initial_tangent_m_per_rad=-(pb.y-pa.y)/(pb.x-pa.x)*(x1-x0)/60/(y0-y1)*180/math.pi
print(f'Initial vector tangent: {initial_tangent_m_per_rad:.12f} m/rad')
endpoints=[segments[0][0]]+[s[-1] for s in segments]
with (OUT/'jmse2025_fig28_bare_hull_vertices.csv').open('w') as f:
    w=csv.writer(f); w.writerow(['heel_deg','gz_m','reading_uncertainty_m','reading_uncertainty_deg'])
    for pt in endpoints:
        a,g=physical(pt); w.writerow([f'{a:.6f}',f'{g:.6f}','0.01','0.3'])

def gz_at(a):
    target=x0+a/60*(x1-x0)
    for ps in segments:
        if ps[0].x-1e-6<=target<=ps[-1].x+1e-6:
            lo,hi=0.,1.
            for _ in range(60):
                mid=(lo+hi)/2
                if bezier(ps,mid).x<target:lo=mid
                else:hi=mid
            return physical(bezier(ps,(lo+hi)/2))[1]
    raise ValueError(f'Angle {a} outside curve extent')
angles=[0,5,10,15,20,25,27,28,29,30,33,35,37,38,40,42,44,44.8]
with (OUT/'jmse2025_fig28_bare_hull.csv').open('w') as f:
    w=csv.writer(f); w.writerow(['heel_deg','gz_m','reading_uncertainty_m','reading_uncertainty_deg'])
    for a in angles:
        w.writerow([a,f'{gz_at(a):.6f}','0.01','0.3'])
        print(f'{a:g}: {gz_at(a):.6f}')
(OUT/'jmse2025_fig28_extraction.json').write_text(json.dumps({'source':str(PDF.relative_to(ROOT)),'page':18,'curve_path_index_zero_based':8,'axes_path_index_zero_based':5,'axis_limits':{'heel_deg':[0,60],'gz_m':[0,1]},'plot_bbox_pdf_points':list(border),'method':'Bezier vector path inversion, no our-model values used','initial_tangent_m_per_rad':initial_tangent_m_per_rad,'uncertainty':'Conservative graphical reading allowance +/-0.01 m and +/-0.3 deg; not statistical confidence interval','loading_caveat':'Fig28 design condition. Table5 AQWA KG14.38m volume51906m3; Real Ship KG14.32m volume52030m3. Caption does not repeat KG. Table1 depth19m,draft10.8m.'},indent=2)+'\n')
# Visual check crop contains axes, curve and legend.
p.get_pixmap(matrix=pymupdf.Matrix(4,4),clip=pymupdf.Rect(166,173,421,330)).save(OUT/'jmse2025_fig28_crop.png')

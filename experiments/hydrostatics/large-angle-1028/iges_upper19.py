"""IGES-informed upper-envelope experiment; inert source geometry only.

APPROXIMATION: evaluate public rational B-spline patches as chordal triangles,
recover station intersections above z=16, splice onto the unchanged published
lower loft, clip at z=19 and seal the shell's actual upper boundary. This is a
geometry diagnostic, not a certified CAD solid or a reconstruction of openings.
No upper height is selected by fitting GZ. Numerical scanline and production
integrators are unchanged. Source files are never executed.
"""
from __future__ import annotations
import json
import csv
from datetime import datetime,timezone
import hashlib
import importlib.util
import math
from pathlib import Path
import resource
import runpy
import sys
import time
import numpy as np
from scipy.interpolate import BSpline

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def read_patches(path=ROOT/"data/raw/kcs/KCS_hull_SVA.igs"):
    lines=Path(path).read_text().splitlines()
    directory=[line for line in lines if len(line)>72 and line[72]=="D"]
    parameters=[line for line in lines if len(line)>72 and line[72]=="P"]
    result=[]
    for first,second in zip(directory[::2],directory[1::2]):
        if int(first[:8])!=128: continue
        assert int(first[48:56])==0,"IGES transforms not supported"
        begin,length=int(first[8:16])-1,int(second[24:32])
        text="".join(line[:64] for line in parameters[begin:begin+length])
        values=[float(x.replace("D","E")) for x in text.split(";")[0].split(",")]
        k1,k2,m1,m2=map(int,values[1:5]);n1,n2=k1+1,k2+1;pos=10
        knots1=np.array(values[pos:pos+k1+m1+2]);pos+=k1+m1+2
        knots2=np.array(values[pos:pos+k2+m2+2]);pos+=k2+m2+2
        weights=np.array(values[pos:pos+n1*n2]).reshape(n2,n1);pos+=n1*n2
        controls=np.array(values[pos:pos+3*n1*n2]).reshape(n2,n1,3);pos+=3*n1*n2
        u0,u1,v0,v1=values[pos:pos+4]
        assert len(values)==pos+4 and np.all(weights>0)
        result.append(dict(label=second[56:64].strip(),knots1=knots1,knots2=knots2,
            weights=weights,controls=controls,m1=m1,m2=m2,u0=u0,u1=u1,v0=v0,v1=v1))
    assert len(result)==55
    return result


def evaluate_patch(patch,us,vs):
    """Tensor grid of actual rational B-spline points, not control vertices."""
    n2,n1=patch["weights"].shape
    bu=BSpline(patch["knots1"],np.eye(n1),patch["m1"])(us)
    bv=BSpline(patch["knots2"],np.eye(n2),patch["m2"])(vs)
    denominator=np.einsum("vi,uj,ij->vu",bv,bu,patch["weights"])
    return np.einsum("vi,uj,ij,ijk->vuk",bv,bu,patch["weights"],patch["controls"])/denominator[...,None]


def survey(patches):
    rows=[]
    for p in patches:
        points=evaluate_patch(p,np.linspace(p["u0"],p["u1"],41),np.linspace(p["v0"],p["v1"],41))
        if points[...,2].max()<=16:continue
        rows.append(dict(label=p["label"],xyz_min_m=points.reshape(-1,3).min(axis=0).tolist(),
                         xyz_max_m=points.reshape(-1,3).max(axis=0).tolist()))
    return rows


def upper_triangles(patches, subdivisions=40):
    result=[]
    for p in patches:
        if p["controls"][...,2].max()<16:continue
        us=np.unique(np.r_[np.linspace(p["u0"],p["u1"],subdivisions+1),
                           p["knots1"][(p["knots1"]>=p["u0"])&(p["knots1"]<=p["u1"])]])
        vs=np.unique(np.r_[np.linspace(p["v0"],p["v1"],subdivisions+1),
                           p["knots2"][(p["knots2"]>=p["v0"])&(p["knots2"]<=p["v1"])]])
        points=evaluate_patch(p,us,vs)
        a,b,c,d=points[:-1,:-1],points[:-1,1:],points[1:,1:],points[1:,:-1]
        triangles=np.concatenate((np.stack((a,b,c),axis=2).reshape(-1,3,3),
                                  np.stack((a,c,d),axis=2).reshape(-1,3,3)))
        result.append(triangles[triangles[...,2].max(axis=1)>16-1e-8])
    return np.concatenate(result)


def upper_section(triangles,x,clip_top=19.):
    active=triangles[(triangles[...,0].min(axis=1)<=x)&(triangles[...,0].max(axis=1)>=x)]
    nxt=np.roll(active,-1,axis=1)
    dd,dn=active[...,0]-x,nxt[...,0]-x
    cross=(dd<=0)!=(dn<=0)
    count=cross.sum(axis=1)
    active,nxt,dd,dn,cross=active[count==2],nxt[count==2],dd[count==2],dn[count==2],cross[count==2]
    ratio=np.divide(dd,dd-dn,out=np.zeros_like(dd),where=cross)
    points=(active+ratio[...,None]*(nxt-active))[cross].reshape(-1,2,3)[...,1:]
    points=points[(points[...,1].max(axis=1)>=16)&(points[...,1].min(axis=1)<=clip_top)]
    low=np.argmin(points[...,1],axis=1)
    a=points[np.arange(len(points)),low];b=points[np.arange(len(points)),1-low]
    useful=b[:,1]-a[:,1]>1e-9
    a,b=a[useful],b[useful]
    slope=(b[:,0]-a[:,0])/(b[:,1]-a[:,1])
    za,zb=np.maximum(a[:,1],16.),np.minimum(b[:,1],clip_top)
    levels=np.unique(np.round(np.r_[za,zb],8))
    pieces=[];max_spread=0.;gaps=[]
    for z0,z1 in zip(levels,levels[1:]):
        if z1-z0<1e-7:continue
        mid=(z0+z1)/2
        covering=(za<=mid+1e-7)&(zb>=mid-1e-7)
        if not covering.any():
            gaps.append([float(z0),float(z1)]);continue
        yy=a[covering,0]+slope[covering]*(mid-a[covering,1])
        max_spread=max(max_spread,float(np.ptp(yy)))
        index=np.flatnonzero(covering)[np.argmax(yy)]
        pieces.extend([[a[index,0]+slope[index]*(z0-a[index,1]),z0],
                       [a[index,0]+slope[index]*(z1-a[index,1]),z1]])
    return np.asarray(pieces),dict(x_m=float(x),max_branch_spread_m=max_spread,gaps_z_m=gaps,
                                  triangle_segments=len(a))


def append_upper(polygon,triangles,x,clip_top=19.):
    n=len(polygon)//2
    half=polygon[:n]
    upper,diagnostic=upper_section(triangles,x,clip_top)
    if not len(upper):
        diagnostic.update(join_width_error_m=None,upper_top_m=16.,lower_trace_height_m=0.)
        return polygon.copy(),diagnostic
    diagnostic.update(join_width_error_m=float(upper[0,0]-half[-1,0]) if abs(upper[0,1]-16)<1e-7 else None,
        upper_top_m=float(upper[-1,1]),lower_trace_height_m=float(upper[0,1]-16),
        lower_trace_halfwidth_m=float(half[-1,0]))
    assert diagnostic["max_branch_spread_m"]<.01,"ambiguous multiple upper-shell branches"
    assert all(hi-lo<.01 for lo,hi in diagnostic["gaps_z_m"]),"unresolved upper-shell gap"
    if upper[0,1]>16+1e-6:
        assert abs(upper[0,0])<1e-5,"unresolved lower upper-shell branch"
        # Native offset and IGES bow termination differ slightly. Close the
        # native solid at16 and follow a zero-area centreline trace to the
        # actual IGES bow onset; do not fill the intervening absent shell.
        half=np.vstack((half,[0.,16.],[0.,upper[0,1]]))
    # APPROXIMATION: source differences at z=16 are joined by a zero-height
    # breadth segment; tiny interpatch tessellation gaps are straight chords.
    # Both discrepancies are measured and reported, never tuned to GZ.
    upper[:,0]=np.maximum(upper[:,0],0.) # Remove <=nanometre mesh roundoff at CP.
    extended_half=np.vstack((half,upper))
    result=np.vstack((extended_half,extended_half[::-1]*[-1.,1.]))
    assert np.array_equal(result[:n],polygon[:n])
    return result,diagnostic


def make_sections(original_sections,x_values,triangles):
    sections,diagnostics=[],[]
    for section,x in zip(original_sections,x_values):
        poly,d=append_upper(section[0],triangles,x)
        sections.append([poly]+[p.copy() for p in section[1:]])
        diagnostics.append(d)
    joins=[abs(d["join_width_error_m"]) for d in diagnostics if d["join_width_error_m"] is not None]
    return sections,dict(stations=len(sections),max_join_width_error_m=max(joins),
        max_branch_spread_m=max(d["max_branch_spread_m"] for d in diagnostics),
        max_gap_height_m=max([hi-lo for d in diagnostics for lo,hi in d["gaps_z_m"]] or [0.]),
        min_top_m=min(d["upper_top_m"] for d in diagnostics),
        max_top_m=max(d["upper_top_m"] for d in diagnostics),station_diagnostics=diagnostics)


def horizontal_section_bounds(triangles,z):
    active=triangles[(triangles[...,2].min(axis=1)<=z)&(triangles[...,2].max(axis=1)>=z)]
    nxt=np.roll(active,-1,axis=1)
    dd,dn=active[...,2]-z,nxt[...,2]-z
    cross=(dd<=0)!=(dn<=0)
    ratio=np.divide(dd,dd-dn,out=np.zeros_like(dd),where=cross)
    points=(active+ratio[...,None]*(nxt-active))[cross]
    return dict(z_m=z,xy_min_m=points[:,:2].min(axis=0).tolist(),
                xy_max_m=points[:,:2].max(axis=0).tolist(),intersection_points=len(points))


def main():
    from stowage.vessel.hydrostatics import HullGeometry
    start=time.perf_counter()
    files=["data/raw/kcs/kcs.fix","data/raw/kcs/KCS_hull_SVA.igs","data/raw/kcs/MANIFEST",
        "src/stowage/vessel/hydrostatics.py","src/stowage/vessel/hull_offsets.py",
        "src/stowage/checker/stability_geometry.py","experiments/hydrostatics/validate_kcs.py",
        *[str((HERE/name).relative_to(ROOT)) for name in
          ("iges_upper19.py","extend19.py","independent_geometry.py","keramati-fig8-samples.csv")]]
    hashes=lambda:{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}
    before=hashes()
    manifest=json.loads((ROOT/"data/raw/kcs/MANIFEST").read_text())
    expected=next(r["sha256"] for r in manifest if r["file"]=="KCS_hull_SVA.igs")
    assert before["data/raw/kcs/KCS_hull_SVA.igs"]==expected
    extension=runpy.run_path(str(HERE/"extend19.py"))
    solve,clone=extension["solve_fixed_zero_trim"],extension["geometry_from_sections"]
    params=runpy.run_path(str(ROOT/"experiments/hydrostatics/validate_kcs.py"))["reference_params"]()
    native=HullGeometry.from_params(params,nx=96,nz=128)
    vertical=extension["extended_production_hull"](native)
    patches=read_patches()
    coarse_mesh,fine_mesh=upper_triangles(patches,40),upper_triangles(patches,80)
    coarse_sections,coarse_diagnostic=make_sections(native.sections,native.x_m,coarse_mesh)
    fine_sections,fine_diagnostic=make_sections(native.sections,native.x_m,fine_mesh)
    coarse,fine=clone(native,coarse_sections,depth_m=19.),clone(native,fine_sections,depth_m=19.)
    preservation=[]
    for draft,angle in ((10.8,0.),(10.8,10.),(14.,0.),(15.5,0.)):
        a,b=native.submerged(draft,draft,math.radians(angle)),fine.submerged(draft,draft,math.radians(angle))
        delta=np.array([b.volume_m3,*b.centroid_m])-np.array([a.volume_m3,*a.centroid_m])
        assert np.max(np.abs(delta))<1e-7
        preservation.append(dict(draft_m=draft,heel_deg=angle,volume_change_m3=float(delta[0]),
                                 centroid_change_m=delta[1:].tolist()))
    spec=importlib.util.spec_from_file_location("igesupper_independent",HERE/"independent_geometry.py")
    independent=importlib.util.module_from_spec(spec);sys.modules[spec.name]=independent;spec.loader.exec_module(independent)
    oracle_native=independent.ScanlineHull.from_kcs_fix(quadrature_order=4,max_panel_length_m=4.)
    oracle_sections,oracle_diagnostic=make_sections([[p] for p in oracle_native.sections],oracle_native.x_m,fine_mesh)
    oracle=independent.ScanlineHull(oracle_native.x_m,oracle_native.weights_m,[s[0] for s in oracle_sections])
    source={float(r["heel_deg"]):r for r in csv.DictReader((HERE/"keramati-fig8-samples.csv").open())}
    rows=[]
    for angle in (20.,25.,30.,35.,40.,45.,50.):
        t=time.perf_counter()
        values=solve(fine,angle,51765.,13.)
        baseline=solve(native,angle,51765.,13.)
        vertical_values=solve(vertical,angle,51765.,13.)
        lower_resolution=solve(coarse,angle,51765.,13.)
        reference_kn,reference_gz=float(source[angle]["kcs_kn_m"]),float(source[angle]["kcs_gz_m"])
        kn_tolerance,gz_tolerance=.05,max(.05,.05*abs(reference_gz))
        row=dict(heel_deg=angle,iges_upper=values,native16=baseline,vertical19=vertical_values,
            reference_kn_m=reference_kn,reference_gz_m=reference_gz,
            error_kn_m=values["kn_m"]-reference_kn,error_gz_m=values["gz_m"]-reference_gz,
            l5_diagnostic_kn_tolerance_m=kn_tolerance,l5_diagnostic_gz_tolerance_m=gz_tolerance,
            within_l5_diagnostic_kn=abs(values["kn_m"]-reference_kn)<=kn_tolerance,
            within_l5_diagnostic_gz=abs(values["gz_m"]-reference_gz)<=gz_tolerance,
            reading_allowance_kn_m=.04,reading_allowance_gz_m=.01,
            mesh40_minus80_kn_m=lower_resolution["kn_m"]-values["kn_m"],
            mesh40_minus80_gz_m=lower_resolution["gz_m"]-values["gz_m"])
        if angle in (20.,30.,40.,50.):
            reference=oracle.float_at_heel(angle,51765.,111.63491550836414,kg_m=13.,fixed_trim_m=0.)
            row.update(independent=reference,
                       production_minus_oracle_gz_m=values["gz_m"]-reference["lever_m"])
            assert abs(row["production_minus_oracle_gz_m"])<.002
        row["elapsed_seconds"]=time.perf_counter()-t
        print(json.dumps({k:row[k] for k in ("heel_deg","error_kn_m","error_gz_m","mesh40_minus80_gz_m")}),flush=True)
        rows.append(row)
    after=hashes();assert before==after
    result=dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
        experiment="Actual IGES upper-shell coordinates, clipped at19; original native16 lower loft preserved",
        source_hashes_before=before,source_hashes_after=after,
        source_geometry_url="https://t2015.nmri.go.jp/file/Geometry_IGES_files/kcs/KCS_hull_SVA.zip",
        reference_curve_url="https://arxiv.org/abs/2610.12870v1",
        assumptions=["Fixed zero trim interpreted from source upright trim; volume51765m3 KG13m.",
            "The entire above16IGEShalfshell is counted and its actual upper trace sealed, then clippedat19; no certified watertight CAD solid or actual openings supplied.",
            "No geometry or KG parameter is fitted to KN/GZ. Mainupper17.199991m and bowabove19 come directly from IGEScoordinates."],
        approximations=["APPROXIMATION: rationalB-spline surfaces triangulated at80subdivisions plusallknots, with40subdivisioncomparison.",
            "APPROXIMATION: original offset loft is retained below16; join breadth differences at16 usezeroheightsegments.",
            "APPROXIMATION: tiny interpatch chordgaps are joined linearly; measuredgap/branchdiagnostics recorded.",
            "APPROXIMATION: source hull above16 has singlevalued breadthvsheight at sampledsections; branchspread independentlychecked."],
        mesh=dict(coarse_subdivisions=40,fine_subdivisions=80,coarse_triangles=len(coarse_mesh),fine_triangles=len(fine_mesh)),
        production_grid=dict(nx=96,nz=128,stations=len(native.x_m)),
        independent_grid=dict(order=4,max_panel_length_m=4.,stations=len(oracle.x_m)),
        patch_survey=survey(patches),horizontal_cut_bounds=[horizontal_section_bounds(fine_mesh,z) for z in (18.,19.)],
        fine_section_diagnostics=fine_diagnostic,oracle_section_diagnostics=oracle_diagnostic,
        preservation_checks=preservation,rows=rows,
        summary=dict(points=len(rows),kn_passed=sum(r["within_l5_diagnostic_kn"] for r in rows),
            gz_passed=sum(r["within_l5_diagnostic_gz"] for r in rows),
            max_abs_error_kn_m=max(abs(r["error_kn_m"]) for r in rows),
            max_abs_error_gz_m=max(abs(r["error_gz_m"]) for r in rows),
            max_abs_mesh40to80_gz_change_m=max(abs(r["mesh40_minus80_gz_m"]) for r in rows),
            max_abs_production_oracle_gz_difference_m=max(abs(r["production_minus_oracle_gz_m"]) for r in rows if "independent" in r)),
        elapsed_seconds=time.perf_counter()-start,peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (HERE/"iges-upper19-results.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:result[k] for k in ("summary","elapsed_seconds","peak_process_rss_bytes")},indent=2))


if __name__=="__main__":
    main()

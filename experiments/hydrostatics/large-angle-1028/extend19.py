"""Experimental constant-breadth extension of the native KCS loft to 19 m.

APPROXIMATION: add vertical sides above each already-lofted section's 16 m
top and a flat sealed deck at 19 m. This is not the real KCS upper flare/sheer,
and is a sensitivity experiment, not an authoritative envelope reconstruction.
No point or chord below 16 m is moved, and no original vertical interval is
stretched. Reparameterizing extended *raw* stations would change the lower
loft: this deliberately extends completed native sections instead.

The unchanged production polygon integration is compared with the unchanged
independent scanline integration on the extension. Neither src nor the pinned
independent_geometry.py is edited. Downloaded IGES is only read as inert data
for a lightweight B-spline surface survey; it does not define this extension.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import resource
import runpy
import sys
import time

import numpy as np
from scipy.interpolate import BSpline
from scipy.optimize import brentq

from stowage.vessel.hydrostatics import HullGeometry


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE_FILES = [
    "data/raw/kcs/kcs.fix", "data/raw/kcs/KCS_hull_SVA.igs",
    "src/stowage/vessel/hydrostatics.py", "src/stowage/vessel/hull_offsets.py",
    "src/stowage/checker/stability_geometry.py",
    "experiments/hydrostatics/validate_kcs.py",
    str((HERE/"independent_geometry.py").relative_to(ROOT)),
    str((HERE/"keramati-fig8-samples.csv").relative_to(ROOT)),
    str(Path(__file__).resolve().relative_to(ROOT)),
]


def hashes():
    return {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCE_FILES}


def vertical_extension(polygon, *, old_top_m=16., new_top_m=19.):
    """Replace only the positive-to-negative half-contour's top closing edge."""
    polygon = np.asarray(polygon)
    assert len(polygon) % 2 == 0
    n = len(polygon)//2
    assert np.max(polygon[:, 1]) <= old_top_m+1e-10
    assert np.allclose(polygon[n-1:n+1, 1], old_top_m, atol=1e-10, rtol=0)
    assert new_top_m > old_top_m
    # APPROXIMATION: constant breadth from the native 16 m top to a 19 m
    # sealed deck. No real upper-hull flare, deck sheer or end overhang inferred.
    result = np.vstack((polygon[:n],
                        [polygon[n-1, 0], new_top_m],
                        [polygon[n, 0], new_top_m], polygon[n:]))
    # The original full polygon is retained byte-for-byte as an ordered subset.
    assert np.array_equal(result[np.r_[np.arange(n), np.arange(n+2, len(result))]], polygon)
    return result


def geometry_from_sections(native, sections, *, depth_m=None):
    """Fresh experimental HullGeometry/StabilityGeometry, no stale caches.

    ``sections`` is the usual station -> polygons -> [[y,z],...] collection;
    callers retain coaming polygons by including them unchanged. The quadrature
    x/weights are retained exactly. depth_m defaults to the original depth;
    max_z_m is recovered from all supplied polygons (including coamings).
    The fingerprint is rebuilt, and no source object/array is mutated.
    """
    result = type(native).__new__(type(native))
    result.__dict__.update({k: v for k, v in vars(native).items() if not k.startswith("_")})
    result.x_m = native.x_m.copy()
    result.integration_weights_m = native.integration_weights_m.copy()
    result.sections = [[np.array(polygon,dtype=float,copy=True) for polygon in section]
                       for section in sections]
    assert len(result.sections)==len(result.x_m)==len(result.integration_weights_m)
    assert all(section for section in result.sections)
    assert all(p.ndim==2 and p.shape[1]==2 and len(p)>=3 and np.isfinite(p).all()
               for section in result.sections for p in section)
    result.depth_m = native.depth_m if depth_m is None else float(depth_m)
    result.max_z_m = max(float(p[:,1].max()) for section in result.sections for p in section)
    result._finish_geometry()  # Fresh cache fingerprint for the actual extension.
    assert not hasattr(result,"_solver_arrays")
    if type(native) is not HullGeometry:
        from stowage.checker.stability_geometry import StabilityGeometry, _bilateral_sections
        if type(native) is not StabilityGeometry:
            raise TypeError("only HullGeometry and StabilityGeometry clone preparation supported")
        # Recreate the subclass's exact fast edge representation from the NEW
        # polygons, then use its existing invalidation/preparation boundary.
        # This is geometry plumbing; independent integration does not use it.
        result._stability_table_bilateral = _bilateral_sections(result.sections)
        polygons = []
        for x,w,section in zip(result.x_m,result.integration_weights_m,result.sections):
            for p in section:
                p = p[np.any(p!=np.roll(p,1,axis=0),axis=1)]
                before,after = p-np.roll(p,1,axis=0),np.roll(p,-1,axis=0)-p
                cross = before[:,0]*after[:,1]-before[:,1]*after[:,0]
                polygons.append((x,w,p[cross!=0.]))
        n = max(len(p) for _,_,p in polygons)
        result._p = np.array([np.concatenate((p,np.repeat(p[-1:],n-len(p),axis=0)))
                              for _,_,p in polygons])
        result._q = np.roll(result._p,-1,axis=1)
        result._px = np.array([x for x,_,_ in polygons])
        result._pw = np.array([w for _,w,_ in polygons])
        result._prepare_edges()
    return result


def extended_production_hull(native):
    """New experimental geometry object; leave the native object unchanged."""
    result = geometry_from_sections(native,
        [[vertical_extension(polygon) for polygon in section] for section in native.sections],
        depth_m=19.)
    assert result._geometry_digest != native._geometry_digest
    return result


def solve_fixed_zero_trim(geometry, angle_deg, volume_m3, kg_m):
    angle = math.radians(angle_deg)
    quick = geometry._solver_submerged
    draft = brentq(lambda d: quick(d, d, angle).volume_m3-volume_m3,
                   -100., 100., xtol=2e-10)
    b = geometry.submerged(draft, draft, angle)
    kn = b.tcb_m*math.cos(angle)+b.vcb_m*math.sin(angle)
    return dict(heel_deg=angle_deg, centreline_intercept_m=draft,
                volume_m3=b.volume_m3, volume_residual_m3=b.volume_m3-volume_m3,
                lcb_m=b.lcb_m, tcb_m=b.tcb_m, vcb_m=b.vcb_m,
                kn_m=kn, gz_m=kn-kg_m*math.sin(angle))


def survey_iges(path, sample_count=13):
    """Survey all type-128 patches by tensor-product B-spline evaluation.

    Only a sampled surface survey, not tessellation, water-tightness/normal
    validation, intersection/section recovery or a CAD hydrostatics method.
    Nonzero IGES transformation pointers are rejected rather than ignored.
    IGES data layout: NIST IGES v3 §3.17 and STEP Tools type128 schema.
    """
    lines = Path(path).read_text().splitlines()
    directory = [line for line in lines if len(line)>72 and line[72]=="D"]
    parameters = [line for line in lines if len(line)>72 and line[72]=="P"]
    patches, all_samples, all_controls = [], [], []
    for first, second in zip(directory[::2], directory[1::2]):
        if int(first[:8]) != 128:
            continue
        assert int(first[48:56]) == 0, "unhandled IGES transform"
        begin, length = int(first[8:16])-1, int(second[24:32])
        text = "".join(line[:64] for line in parameters[begin:begin+length])
        values = [float(x.replace("D", "E")) for x in text.split(";")[0].split(",")]
        assert int(values[0]) == 128
        k1, k2, m1, m2 = map(int, values[1:5])
        n1, n2 = k1+1, k2+1
        pos = 10
        knots1 = np.array(values[pos:pos+k1+m1+2]); pos += k1+m1+2
        knots2 = np.array(values[pos:pos+k2+m2+2]); pos += k2+m2+2
        weights = np.array(values[pos:pos+n1*n2]).reshape(n2, n1); pos += n1*n2
        controls = np.array(values[pos:pos+3*n1*n2]).reshape(n2, n1, 3); pos += 3*n1*n2
        u0, u1, v0, v1 = values[pos:pos+4]
        assert len(values)==pos+4 and np.all(weights>0)
        uu, vv = np.linspace(u0,u1,sample_count), np.linspace(v0,v1,sample_count)
        bu = BSpline(knots1,np.eye(n1),m1)(uu)
        bv = BSpline(knots2,np.eye(n2),m2)(vv)
        denominator = np.einsum("vi,uj,ij->vu", bv,bu,weights)
        points = np.einsum("vi,uj,ij,ijk->vuk", bv,bu,weights,controls)/denominator[...,None]
        assert np.isfinite(points).all()
        patches.append(dict(label=second[56:64].strip(), control_shape=[n2,n1],
            z_min_sample_m=float(points[...,2].min()), z_max_sample_m=float(points[...,2].max()),
            sampled_points_above16=int(np.sum(points[...,2]>16+1e-9))))
        all_samples.append(points.reshape(-1,3)); all_controls.append(controls.reshape(-1,3))
    samples, controls = np.concatenate(all_samples), np.concatenate(all_controls)
    assert len(patches)==55
    return dict(patches=patches, patch_count=len(patches), samples_per_patch=sample_count**2,
        sampled_xyz_min_m=samples.min(axis=0).tolist(), sampled_xyz_max_m=samples.max(axis=0).tolist(),
        control_xyz_min_m=controls.min(axis=0).tolist(), control_xyz_max_m=controls.max(axis=0).tolist(),
        patches_with_sample_above16=sum(p["sampled_points_above16"]>0 for p in patches),
        purpose="Inert-data surface sampling only; not used to build the vertical extension",
        limits="No watertightness, patch topology, coordinates, closure or mapping to offsets certified")


def main():
    start = time.perf_counter(); before = hashes()
    params = runpy.run_path(str(ROOT/"experiments/hydrostatics/validate_kcs.py"))["reference_params"]()
    native = HullGeometry.from_params(params,nx=96,nz=128)
    native_hash = native._geometry_digest.hex()
    extended = extended_production_hull(native)
    assert native._geometry_digest.hex()==native_hash
    preservation = []
    # Identical immersion geometry for all these waterplanes, strictly below16.
    for ta, tf, angle in ((10.8,10.8,0.), (10.8,10.8,10.), (14.,14.,0.), (15.5,15.5,0.)):
        a, b = native.submerged(ta,tf,math.radians(angle)), extended.submerged(ta,tf,math.radians(angle))
        delta = np.asarray([b.volume_m3,*b.centroid_m])-np.asarray([a.volume_m3,*a.centroid_m])
        assert np.max(np.abs(delta)) < 1e-7
        preservation.append(dict(t_aft_m=ta,t_fwd_m=tf,heel_deg=angle,
            delta_volume_m3=float(delta[0]),delta_lcb_tcb_vcb_m=delta[1:].tolist()))
    # Independent raw parser/loft and polynomial scanline integration, with the
    # same simple vertical-edge extension applied after its separate lofting.
    spec = importlib.util.spec_from_file_location("extend19_independent_geometry", HERE/"independent_geometry.py")
    oracle = importlib.util.module_from_spec(spec); sys.modules[spec.name]=oracle; spec.loader.exec_module(oracle)
    independent_native = oracle.ScanlineHull.from_kcs_fix(quadrature_order=4,max_panel_length_m=4.)
    independent_extended = oracle.ScanlineHull(independent_native.x_m, independent_native.weights_m,
        [vertical_extension(poly) for poly in independent_native.sections],lpp_m=230.)
    source = {float(row["heel_deg"]):row for row in csv.DictReader((HERE/"keramati-fig8-samples.csv").open())}
    volume, kg, rows = 51765., 13., []
    for angle in (20.,25.,30.,35.,40.,45.,50.):
        t = time.perf_counter()
        a, b = solve_fixed_zero_trim(native,angle,volume,kg), solve_fixed_zero_trim(extended,angle,volume,kg)
        ref = source[angle]
        kn_ref, gz_ref = float(ref["kcs_kn_m"]), float(ref["kcs_gz_m"])
        kn_tol, gz_tol = .05, max(.05,.05*abs(gz_ref))
        row = dict(heel_deg=angle,native16=a,vertical19=b,reference_kn_m=kn_ref,reference_gz_m=gz_ref,
            error_kn_m=b["kn_m"]-kn_ref,error_gz_m=b["gz_m"]-gz_ref,
            l5_diagnostic_kn_tolerance_m=kn_tol,l5_diagnostic_gz_tolerance_m=gz_tol,
            within_l5_diagnostic_kn=abs(b["kn_m"]-kn_ref)<=kn_tol,
            within_l5_diagnostic_gz=abs(b["gz_m"]-gz_ref)<=gz_tol,
            plot_reading_allowance_kn_m=.04,plot_reading_allowance_gz_m=.01,
            within_kn_bar_plus_reading=abs(b["kn_m"]-kn_ref)<=kn_tol+.04,
            within_gz_bar_plus_reading=abs(b["gz_m"]-gz_ref)<=gz_tol+.01,
            added_reserve_kn_m=b["kn_m"]-a["kn_m"],added_reserve_gz_m=b["gz_m"]-a["gz_m"])
        if angle in (20.,30.,40.,50.):
            independent = independent_extended.float_at_heel(angle,volume,111.63491550836414,kg_m=kg,fixed_trim_m=0.)
            row.update(independent=independent,
                production_minus_independent_gz_m=b["gz_m"]-independent["lever_m"],
                independent_kn_m=independent["lever_m"]+kg*math.sin(math.radians(angle)))
            assert abs(row["production_minus_independent_gz_m"])<1e-5
        row["elapsed_seconds"] = time.perf_counter()-t
        rows.append(row)
        print(json.dumps({k:row[k] for k in ("heel_deg","error_kn_m","error_gz_m","within_l5_diagnostic_kn","within_l5_diagnostic_gz")}),flush=True)
    iges = survey_iges(ROOT/"data/raw/kcs/KCS_hull_SVA.igs")
    after = hashes(); assert before==after
    result = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
        experiment="Vertical constant-breadth addition16to19m, preserving every native lower-hull chord",
        approximation="APPROXIMATION: vertical sides and flat sealed19m deck; not actual KCS upper flare/sheer/endshape",
        loading=dict(volume_m3=volume,kg_m=kg,tcg_m=0.,fixed_trim_m=0.),
        source="Keramati etal arXiv2610.12870v1 fig8, table5, section3.3.1; previously archived/digitized",
        native_geometry_hash=native_hash,extended_geometry_hash=extended._geometry_digest.hex(),
        source_hashes_before=before,source_hashes_after=after,
        production_grid=dict(nx=96,nz=128,stations=len(native.x_m)),
        independent_grid=dict(order=4,max_panel_length_m=4.,stations=len(independent_extended.x_m)),
        preservation_checks=preservation,rows=rows,iges_survey=iges,
        summary=dict(kn_passed=sum(r["within_l5_diagnostic_kn"] for r in rows),
            gz_passed=sum(r["within_l5_diagnostic_gz"] for r in rows),points=len(rows),
            max_abs_kn_error_m=max(abs(r["error_kn_m"]) for r in rows),
            max_abs_gz_error_m=max(abs(r["error_gz_m"]) for r in rows),
            max_abs_production_oracle_difference_m=max(abs(r["production_minus_independent_gz_m"]) for r in rows if "independent" in r)),
        elapsed_seconds=time.perf_counter()-start,peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (HERE/"extend19-results.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(dict(summary=result["summary"],elapsed_seconds=result["elapsed_seconds"],
                         peak_process_rss_bytes=result["peak_process_rss_bytes"]),indent=2))


if __name__=="__main__":
    main()

"""Independent KCS public-offset validation; no fitted hydrostatic quantities.

Run on one CPU thread with ``uv run python experiments/hydrostatics/validate_kcs.py``.
Published targets are from data/raw/kcs/kcs_gc.html, full-scale table.
The weather deck is synthetically sealed at the supplied z=16 m offset cutoff;
this does not affect upright validation at the published 10.8 m draught.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

from stowage.params import load_param_file
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.hull_offsets import wetted_surface_area_m2


KCS_OFFSET_SHA256 = "aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190"
REFERENCE = dict(volume_m3=52030.0, cb=0.6505, cm=0.9849,
                 lcb_m=230 * (0.5 - 0.0148), wetted_surface_m2=9424.0,
                 km_model2_m=(0.378 + 0.0158) * 230 / 6.0702,
                 km_model3_m=(0.168 + 0.007) * 230 / 2.7)


def reference_params():
    # APPROXIMATION: close the source upper cutoff, without deckhouse/coamings.
    return dict(hull=dict(lpp_m=230.0, beam_m=32.2, design_draught_m=10.8,
        depth_m=16.0, water_density_kg_m3=1025.0),
        hull_form=dict(family="stowlm_offsets_v1", offsets_path="data/raw/kcs/kcs.fix",
            offsets_sha256=KCS_OFFSET_SHA256, offsets_format="kcs_fix",
            reference_lpp_m=230.0, reference_beam_m=32.2,
            reference_draught_m=10.8, reference_depth_m=16.0),
        envelope=dict(deck_z_m=16.0, hatch_coamings=[], openings=[], count_deckhouse=False))


def hydro_values(geometry, draft):
    r = geometry.submerged(draft, draft, 0)
    h = geometry
    cm = float(max(r.sectional_areas_m2)) / (h.beam_m * draft)
    cb = r.volume_m3 / (h.lpp_m * h.beam_m * draft)
    return dict(volume_m3=r.volume_m3, cb=cb, cm=cm, cp=cb/cm,
        cwp=r.waterplane_area_m2/(h.lpp_m*h.beam_m), lcb_m=r.lcb_m,
        kb_m=r.vcb_m, km_m=r.vcb_m+r.transverse_inertia_m4/r.volume_m3,
        waterplane_area_m2=r.waterplane_area_m2)


def compare(actual, reference, absolute_tolerance):
    error = actual-reference
    return dict(actual=actual, reference=reference, error=error,
        error_percent=100*error/reference, absolute_tolerance=absolute_tolerance,
        passes=bool(abs(error)<=absolute_tolerance))


def generate_report(include_kn=True):
    start = time.perf_counter()
    p = reference_params()
    coarse = HullGeometry.from_params(p, nx=48, nz=64)
    fine = HullGeometry.from_params(p, nx=96, nz=128)
    a,b = hydro_values(coarse,10.8),hydro_values(fine,10.8)
    wetted = wetted_surface_area_m2(fine,10.8)
    checks = dict(volume=compare(b["volume_m3"],REFERENCE["volume_m3"],.02*REFERENCE["volume_m3"]),
        cb=compare(b["cb"],REFERENCE["cb"],.02*REFERENCE["cb"]),
        cm=compare(b["cm"],REFERENCE["cm"],.001*REFERENCE["cm"]),
        lcb=compare(b["lcb_m"],REFERENCE["lcb_m"],.05),
        km_model2=compare(b["km_m"],REFERENCE["km_model2_m"],max(.05,.01*REFERENCE["km_model2_m"])),
        km_model3=compare(b["km_m"],REFERENCE["km_model3_m"],max(.05,.01*REFERENCE["km_model3_m"])),
        wetted=compare(wetted,REFERENCE["wetted_surface_m2"],.02*REFERENCE["wetted_surface_m2"]))
    report = dict(source="data/raw/kcs/kcs_gc.html", geometry="data/raw/kcs/kcs.fix",
        source_sha256=KCS_OFFSET_SHA256, draught_m=10.8,
        calibration="none; published offsets and quantities are independent inputs",
        tolerance_basis="L5 bar: displacement 2%, KM proxy max(1%,0.05m); supplemental Cb2%, Cm0.1%, LCB0.05m, wetted2%.",
        km_reference_derivation="Model2 (KG .378+GM .0158)*230/6.0702; model3 (.168+.007)*230/2.7. Geometric similarity, not fitted.",
        coarse=a, fine=b, checks=checks,
        convergence_absolute={k:abs(a[k]-b[k]) for k in a},
        affine_fleet=[], kn_convergence=[])
    reference_cb=b["cb"]
    for name in ("feeder_500","feeder_800","feeder_1700"):
        ship=load_param_file("fleet",name)
        # Independently exercise the prescribed affine scaling even while
        # another owner is updating fleet TOMLs to this exact source family.
        scaled=reference_params()
        for key in ("lpp_m","beam_m","design_draught_m","depth_m","water_density_kg_m3"):
            scaled["hull"][key]=ship["hull"][key]
        scaled["envelope"]["deck_z_m"]=scaled["hull"]["depth_m"]
        g=HullGeometry.from_params(scaled,nx=96,nz=128)
        values=hydro_values(g,scaled["hull"]["design_draught_m"])
        report["affine_fleet"].append(dict(ship=name,computed=values,
            cb_reference_residual=values["cb"]-reference_cb,
            current_parameter_family=ship["hull_form"]["family"]))
    if include_kn:
        mass=fine.submerged(10.8,10.8,0).displacement_kg
        lcg=b["lcb_m"]
        for deg in (30,60):
            angle=math.radians(deg)
            c=coarse.kn(angle,mass,lcg_m=lcg)
            f=fine.kn(angle,mass,lcg_m=lcg)
            report["kn_convergence"].append(dict(heel_deg=deg,displacement_kg=mass,
                reference_lcg_m=lcg,coarse_kn_m=c,fine_kn_m=f,absolute_change_m=abs(c-f)))
    report["elapsed_seconds"]=time.perf_counter()-start
    return report


if __name__=="__main__":
    report=generate_report()
    Path(__file__).with_name("kcs_results.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))

"""DTC STL sections checked against independent published volume and KM.

Source: el Moctar, Shigunov & Zorn (2012), Tables 1 and 6, local research copy
data/raw/dtc/el-moctar-shigunov-zorn-2012.pdf. Exact published scale is 59.407.
No coefficient, draft, displacement or KM is fitted to a reference value.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time

from stowage.params import repo_root
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.hull_dtc import write_dtc_offsets, dtc_reference_params
from stowage.vessel.hull_offsets import wetted_surface_area_m2


REFERENCES = {12.0: dict(volume_m3=136617.5,km_m=25.95),
              14.0: dict(volume_m3=165868.5,km_m=25.05),
              14.5: dict(volume_m3=173467.0,cb=0.661,wetted_surface_m2=22032.0)}


def values(geometry,draft,with_wetted=True):
    r=geometry.submerged(draft,draft,0)
    row=dict(volume_m3=r.volume_m3,lcb_m=r.lcb_m,kb_m=r.vcb_m,
        km_m=r.vcb_m+r.transverse_inertia_m4/r.volume_m3,
        cb=r.volume_m3/(355*51*draft))
    if with_wetted:
        row["wetted_surface_m2"]=wetted_surface_area_m2(geometry,draft)
    return row


def comparison(actual,ref,quantity):
    tolerance=max(.05,.01*ref) if quantity=="km_m" else .02*ref
    return dict(actual=float(actual),reference=ref,error_percent=100*(actual/ref-1),
        absolute_tolerance=tolerance,passes=bool(abs(actual-ref)<=tolerance))


def generate_report(offsets_path=None,with_convergence=True):
    started=time.perf_counter()
    path=Path(offsets_path or repo_root()/"experiments/hydrostatics/dtc_offsets.json")
    if not path.exists():
        raise FileNotFoundError("Run hull_dtc.write_dtc_offsets with station_count=121 first")
    p=dtc_reference_params(path)
    geometry=HullGeometry.from_params(p,nx=96,nz=128)
    meta=json.loads(path.read_text())
    report=dict(source="data/raw/dtc/el-moctar-shigunov-zorn-2012.pdf",
        mesh="data/raw/dtc/DTC-scaled.stl.gz",source_sha256=meta["source_sha256"],
        model_scale=59.407,source_station_count_requested=meta["station_count_requested"],
        calibration="none",source_tables="Table1 PDFp2: design14.5; Table6 PDFp6: roll-test12/14m",
        coordinate_basis="OpenFOAM hRef=0.244m at model scale; Piehl baseline/AP coordinates; exact scale59.407 from2012 paper",
        approximation="One port half of STL retained and mirrored, source-plane slicing and ruled loft; computational upper cutoff34m, irrelevant to tested drafts.",
        tolerance_basis="Volume/Cb/wetted2%; KM max(1%,0.05m). KM bar adopts L5 KG/GM magnitude tolerance.",
        rows=[],source_slice_convergence=[])
    for draft,reference in REFERENCES.items():
        actual=values(geometry,draft,with_wetted=(draft==14.5))
        report["rows"].append(dict(draught_m=draft,computed=actual,
            checks={key:comparison(actual[key],ref,key) for key,ref in reference.items()}))
    if with_convergence:
        with tempfile.TemporaryDirectory(prefix="stowage-dtc-convergence-") as temporary:
            coarse_path=Path(temporary)/"dtc_61.json"
            write_dtc_offsets(repo_root()/"data/raw/dtc/DTC-scaled.stl.gz",coarse_path,station_count=61)
            coarse=HullGeometry.from_params(dtc_reference_params(coarse_path),nx=48,nz=64)
            for row in report["rows"]:
                draft=row["draught_m"]
                a=values(coarse,draft,with_wetted=False)
                b=row["computed"]
                report["source_slice_convergence"].append(dict(draught_m=draft,
                    coarse_61_planes=a, fine_121_planes={k:b[k] for k in a},
                    absolute_change={k:abs(a[k]-b[k]) for k in a},
                    volume_change_percent=100*abs(a["volume_m3"]/b["volume_m3"]-1)))
    report["elapsed_seconds"]=time.perf_counter()-started
    return report


if __name__=="__main__":
    report=generate_report()
    Path(__file__).with_name("dtc_results.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))

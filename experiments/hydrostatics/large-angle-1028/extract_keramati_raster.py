"""Read published Fig.8 raster; no downloaded code is executed.

APPROXIMATION: black KCS curve centres are digitized from the embedded raster.
No extrapolation is used; the validation samples end at 57.5 degrees.
GZ is also derived from KN - 13*sin(heel), an independent internal-figure check.
These are figure-reading results, not the authors' numerical data.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
import pymupdf

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
PDF = ROOT / "data/raw/kcs-largeangle-public/keramati2026-arxiv2610.12870v1.pdf"


def main():
    doc = pymupdf.open(PDF)
    image = doc.extract_image(559)
    assert image["width"] == 2549 and image["height"] == 1155
    path = OUT / "keramati-fig8.png"
    path.write_bytes(image["image"])
    a = np.array(Image.open(path).convert("RGB"))
    black = a.max(axis=2) < 80
    # Axis centres measured from four-pixel-wide black raster axes and ticks.
    # KN: y=992.5 at0m, y=69.5 at12m. GZ:y=829.5 at0m,945.5at-.25m.
    xkn0, xkn60, ykn0, ykn12 = 162.5, 1185.5, 992.5, 69.5
    xgz0, xgz60, ygz0, ygzneg025 = 1471.5, 2494.5, 829.5, 945.5
    assert black[30:34, 161:1188].all()
    assert black[991:995, 161:1188].all()

    def trace(xstart, xend, initial_y, excluded_rows=()):
        result = []
        last = initial_y
        for x in range(xstart, xend + 1):
            ys = np.flatnonzero(black[40:990, x]) + 40
            ys = np.array([y for y in ys if all(not lo <= y <= hi for lo, hi in excluded_rows)])
            if not len(ys):
                continue
            # Follow connected KCS ink. This rejects labels and axes hundreds of
            # pixels from the previous curve ordinate; small overlap gaps remain.
            near = ys[np.abs(ys - last) < 20]
            if not len(near):
                continue
            groups = np.split(near, np.where(np.diff(near) > 1)[0] + 1)
            group = min(groups, key=lambda g: abs(np.median(g) - last))
            last = float(np.median(group))
            result.append([x, last])
        return np.array(result)

    kn_pixels = trace(175, 1182, 981)
    gz_pixels = trace(1482, 2458, 820, excluded_rows=((827,832),))
    kn_ang = (kn_pixels[:,0] - xkn0) * 60 / (xkn60 - xkn0)
    kn_val = (ykn0 - kn_pixels[:,1]) * 12 / (ykn0 - ykn12)
    gz_ang = (gz_pixels[:,0] - xgz0) * 60 / (xgz60 - xgz0)
    gz_val = (ygz0 - gz_pixels[:,1]) * .25 / (ygzneg025 - ygz0)
    assert len(kn_pixels) > 900 and len(gz_pixels) > 850
    samples = []
    for angle in np.arange(5,57.501,2.5):
        if angle == 0:
            kn, gz = 0., 0.
        else:
            kn = float(np.interp(angle,kn_ang,kn_val))
            gz = float(np.interp(angle,gz_ang,gz_val)) if gz_ang[0] <= angle <= gz_ang[-1] else None
        derived = kn - 13 * math.sin(math.radians(float(angle)))
        samples.append({"heel_deg":float(angle),"kcs_kn_m":kn,"kcs_gz_m":gz,
                        "kcs_gz_from_kn_m":derived,
                        "gz_internal_difference_m":None if gz is None else gz-derived})
    with (OUT / "keramati-fig8-samples.csv").open("w", newline="") as f:
        w = csv.DictWriter(f,fieldnames=list(samples[0])); w.writeheader(); w.writerows(samples)
    details = {"source":"https://arxiv.org/abs/2610.12870v1", "figure":8,"pdf_page":17,
               "pdf_sha256":hashlib.sha256(PDF.read_bytes()).hexdigest(),
               "loading":{"kg_m":13,"depth_m":19,"volume_m3":51765,"draft_m":10.8,
                          "km_m":14.93,"trim":"upright trim per section3.3.1; treated as fixed0 trim"},
               "calibration":{"kn_x0":xkn0,"kn_x60":xkn60,"kn_y0":ykn0,"kn_y12":ykn12,
                              "gz_x0":xgz0,"gz_x60":xgz60,"gz_y0":ygz0,"gz_yminus025":ygzneg025},
               "reading_allowance":{"kn_m":.04,"gz_m":.01,
                                    "qualification":"Conservative half-stroke/axis reading allowance; does not bound author-method or between-knot interpolation error."},
               "validation_exclusion":"Exclude heel below5 degrees: overlap of the black and red ink near the origin makes centre extraction unreliable (internal discrepancy reaches0.041m at2.5deg).",
               "extrapolation":"none; samples stop at57.5deg within both traces",
               "kn_trace_support_deg":[float(kn_ang[0]),float(kn_ang[-1])],
               "gz_trace_support_deg":[float(gz_ang[0]),float(gz_ang[-1])],
               "kn_pixels":kn_pixels.tolist(),"gz_pixels":gz_pixels.tolist(),"samples":samples}
    (OUT / "keramati-fig8-digitization.json").write_text(json.dumps(details,indent=2)+"\n")
    print(json.dumps([s for s in samples if s["heel_deg"]%10==0],indent=2))
    print("max internal GZ difference",max(abs(s["gz_internal_difference_m"]) for s in samples if s["gz_internal_difference_m"] is not None))


if __name__ == "__main__":
    main()

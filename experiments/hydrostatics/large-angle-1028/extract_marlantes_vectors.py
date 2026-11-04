"""Read published Fig.22 PDF vectors; never execute downloaded source files.

Usage: OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --with pymupdf 
       python experiments/hydrostatics/large-angle-1028/extract_marlantes_vectors.py
APPROXIMATION: plotted marker centres and polyline vertices are figure-derived
reference ordinates, not author-supplied numerical hydrostatic results. Linear
interpolation is used only between adjacent published figure points.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
PDF = ROOT / "data/raw/kcs-largeangle-public/marlantes2022-jmse10010041.pdf"


def interpolate(points: list[dict], angle: float) -> float | None:
    for a, b in zip(points, points[1:]):
        if a["heel_deg"] <= angle <= b["heel_deg"]:
            f = (angle - a["heel_deg"]) / (b["heel_deg"] - a["heel_deg"])
            return a["gz_m"] + f * (b["gz_m"] - a["gz_m"])
    return None


def main() -> None:
    doc = pymupdf.open(PDF)
    page = doc[29]
    drawings = page.get_drawings()
    # All limits are vector tick/axis locations, in PDF page coordinates (pt).
    xminus20, x60 = 199.1439971923828, 413.29400634765625
    yzero, yplus08 = 174.405029296875, 98.405029296875

    def point(x: float, y: float) -> dict:
        return {"heel_deg": 80.0 * (x - xminus20) / (x60 - xminus20) - 20.0,
                "gz_m": 0.8 * (yzero - y) / (yzero - yplus08),
                "pdf_x_pt": x, "pdf_y_pt": y}

    # Fig22 ABS square markers indices23..45. Index46 is its legend marker.
    abs_points = []
    for index in range(23, 46):
        d = drawings[index]
        assert d["type"] == "f" and len(d["items"]) == 4
        r = d["rect"]
        assert abs(r.width - 3.15) < 0.01 and abs(r.height - 3.15) < 0.01
        abs_points.append(point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    # CSI drawing47 starts with its horizontal legend; exclude that first item.
    csi_segments = drawings[47]["items"][1:]
    assert all(item[0] == "l" for item in csi_segments)
    csi_points = [point(*csi_segments[0][1])]
    for _, start, end in csi_segments:
        assert abs(start.x - csi_points[-1]["pdf_x_pt"]) < 0.001
        assert abs(start.y - csi_points[-1]["pdf_y_pt"]) < 0.001
        csi_points.append(point(*end))
    assert all(p["heel_deg"] < q["heel_deg"] for p, q in zip(abs_points, abs_points[1:]))
    assert all(p["heel_deg"] < q["heel_deg"] for p, q in zip(csi_points, csi_points[1:]))
    samples = [{"heel_deg": a, "abs_gz_m": interpolate(abs_points, a),
                "csi_gz_m": interpolate(csi_points, a)} for a in [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]]
    result = {
        "source_doi": "10.3390/jmse10010041", "source_figure": 22,
        "pdf_page_1_based": 30, "pdf_sha256": hashlib.sha256(PDF.read_bytes()).hexdigest(),
        "loading": {"length_m": 230, "beam_m": 32.2, "depth_m": 17.2,
                    "draft_aft_m": 10.8, "draft_fore_m": 10.8, "kg_m": 13.674,
                    "reported_volume_m3": 54148, "reported_gm_m": 1.32,
                    "lcg": "not reported", "heel_trim_policy": "not explicit in paper"},
        "calibration": {"pdf_x_at_minus20": xminus20, "pdf_x_at_plus60": x60,
                        "pdf_y_at_gz0": yzero, "pdf_y_at_gz08": yplus08,
                        "csi_line_width_pt": drawings[47]["width"],
                        "abs_marker_width_pt": 3.15},
        "uncertainty": {"csi_plot_gz_m": 0.01, "abs_plot_gz_m": 0.02,
                        "qualification": "Conservative reading allowances based on stroke/marker half width, not author accuracy or interpolation error bounds."},
        "abs_points": abs_points, "csi_points": csi_points, "interpolated_samples": samples,
        "extrapolation": "none; null means angle outside plotted data support"}
    (OUT / "marlantes-fig22-vectors.json").write_text(json.dumps(result, indent=2) + "\n")
    with (OUT / "marlantes-fig22-samples.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(samples[0]))
        writer.writeheader()
        writer.writerows(samples)
    print(json.dumps(samples, indent=2))


if __name__ == "__main__":
    main()

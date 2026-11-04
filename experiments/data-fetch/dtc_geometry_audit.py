"""Inspect inert gzip ASCII STL; no OpenFOAM or downloaded code is executed.

Run with uv run --no-project --python 3.12 python experiments/data-fetch/dtc_geometry_audit.py.
Streams triangles, clips exactly to z=0.244 m (linear STL facets), and integrates
signed tetrahedra with origin on the waterplane, so the closing cap contributes zero.
This is a geometry audit, not a replacement for the project's hydrostatics checker.
"""
from pathlib import Path
import gzip
import json
import math

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/raw/dtc/DTC-scaled.stl.gz"
OUT = ROOT / "data/raw/dtc/geometry_audit.json"
DRAFT = 0.244
SCALE = 59.407


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def clip(poly):
    result = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        inside_a, inside_b = a[2] <= DRAFT, b[2] <= DRAFT
        if inside_a:
            result.append(a)
        if inside_a != inside_b:
            fraction = (DRAFT - a[2]) / (b[2] - a[2])
            result.append(tuple(a[i] + fraction*(b[i]-a[i]) for i in range(3)))
    return result


def main():
    minimum, maximum = [math.inf]*3, [-math.inf]*3
    triangles = vertices = 0
    signed_volume = area = moment_x = 0.0
    pending = []
    with gzip.open(SOURCE, "rt", encoding="ascii") as stream:
        assert stream.readline().startswith("solid "), "ASCII STL required"
        for line in stream:
            parts = line.split()
            if not parts or parts[0] != "vertex":
                continue
            assert len(parts) == 4
            point = tuple(map(float, parts[1:]))
            assert all(math.isfinite(value) for value in point)
            for axis in range(3):
                minimum[axis] = min(minimum[axis], point[axis])
                maximum[axis] = max(maximum[axis], point[axis])
            vertices += 1
            pending.append(point)
            if len(pending) != 3:
                continue
            triangles += 1
            poly = clip(pending)
            pending = []
            for i in range(1, len(poly)-1):
                a, b, c = poly[0], poly[i], poly[i+1]
                ab, ac = tuple(b[j]-a[j] for j in range(3)), tuple(c[j]-a[j] for j in range(3))
                normal = cross(ab, ac)
                area += 0.5*math.sqrt(sum(v*v for v in normal))
                az, bz, cz = ((v[0], v[1], v[2]-DRAFT) for v in (a,b,c))
                volume = sum(az[j]*cross(bz,cz)[j] for j in range(3))/6.0
                signed_volume += volume
                moment_x += volume*(a[0]+b[0]+c[0])/4.0
    assert not pending and triangles == 116062
    assert abs(minimum[2]) < 1e-8
    assert 0.82 < abs(signed_volume) < 0.84
    result = {
        "source": SOURCE.name,
        "format": "gzip compressed ASCII STL, solid ascii, facets with 3 xyz vertices",
        "units": "model-scale metres (inferred from source reference dimensions)",
        "triangles": triangles, "vertex_records": vertices,
        "bounds_m": {"minimum_xyz": minimum, "maximum_xyz": maximum},
        "scale_full_over_model": SCALE,
        "audit_waterplane_z_m": DRAFT,
        "audit_waterplane_fullscale_draft_m": DRAFT*SCALE,
        "submerged_volume_model_m3": abs(signed_volume),
        "submerged_volume_fullscale_m3": abs(signed_volume)*SCALE**3,
        "submerged_surface_area_model_m2": area,
        "submerged_surface_area_fullscale_m2": area*SCALE**2,
        "longitudinal_centroid_model_x_m": moment_x/signed_volume,
        "longitudinal_centroid_fullscale_x_m": moment_x/signed_volume*SCALE,
        "note": "STL facet clipping and signed tetrahedra audit, not the project's checker. Waterplane closure has zero signed volume about the selected origin. No geometry repaired or altered. Published comparisons are in NOTES.md."
    }
    OUT.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

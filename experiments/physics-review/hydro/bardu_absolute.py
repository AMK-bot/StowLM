"""Absolute displacement / Cb implied by the TPC-calibrated Bardu reconstruction.

Bardu lightship is NOT published; this prints the lightship the reconstruction
would imply (Delta(11.6) - 34,000 t) so a reader can judge plausibility.
Run: uv run python experiments/physics-review/hydro/bardu_absolute.py
"""
import runpy
from stowage.params import repo_root
from stowage.vessel.hydrostatics import HullGeometry

m = runpy.run_path(str(repo_root() / "experiments/hydrostatics/validate.py"))
for label, fn in (("illustrative", "bardu_illustrative_params"), ("calibrated", "bardu_calibrated_params")):
    p = m[fn]()
    g = HullGeometry.from_params(p, nx=48, nz=64)
    for T in (10.1, 11.6):
        s = g.submerged(T, T, 0)
        cb = s.volume_m3 / (196.9 * 29.8 * T)
        print(f"{label} T={T}: disp={s.displacement_kg/1e6:.0f} t Cb={cb:.3f} Cwp={s.waterplane_area_m2/(196.9*29.8):.4f}"
              f" KB/T={s.vcb_m/T:.3f}")
    s = g.submerged(11.6, 11.6, 0)
    print(f"  implied lightship = {s.displacement_kg/1e3 - 34000:.0f} t")

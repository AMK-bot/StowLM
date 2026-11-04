"""Independent large-angle integration of the unscaled, published KCS offsets.

The integration and floating solver import no stowage modules. The optional
``--compare-production`` command imports production only as the comparator.
Production clips section polygons and applies
the shoelace formula. Here each original polygon is rotated into the vertical
plane and split into horizontal strips at *all* vertex elevations. A scanline
pairs boundary crossings: strip width is linear, and area/first-moment
integrands are polynomials integrated analytically. Longitudinal quadrature
uses configurable Gauss-Legendre order and independently generated stations.
The floating solver nests two scalar Brent brackets (immersion, then trim).

APPROXIMATION: the same declared normalized-arc-length ruled loft is used so
that this checks numerical integration, not the loft assumption. The published
offsets end at z=16 m and are sealed there by a synthetic weather deck. This is
not a reconstruction of the real KCS depth (19 m), superstructure or openings.
No coamings are added. Source file is inert, SHA-256 pinned and independently
parsed. Thus this method is independent numerically, not independent evidence
of the ship's actual large-angle reserve buoyancy.

Run a small diagnostic (one CPU, no production dependency)::
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python \
        experiments/hydrostatics/large-angle-1028/independent_geometry.py

Public API: ScanlineHull.from_kcs_fix(...), .submerged(ta,tf,heel_deg),
.float_at_heel(heel_deg,volume_m3,lcg_m,kg_m=0,fixed_trim_m=None).
KN is the returned lever for kg_m=0; GZ is the lever for the physical KG.
Free trim is solved against that specified centre of gravity. Holding trim
instead is explicit through fixed_trim_m, useful for a published fixed-trim
reference. Units: metres, cubic metres, degrees; x forward from AP, y port.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.optimize import brentq


KCS_SHA256 = "aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190"
ROOT = Path(__file__).resolve().parents[3]


def read_kcs_fix(path, expected_sha256=KCS_SHA256):
    """Read the raw Fortran layout without production parsing/interpolation."""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("unexpected source checksum")
    tokens = iter(" ".join(raw.decode("utf-8-sig").splitlines()[1:]).split())
    records = []
    for _ in range(int(next(tokens))):
        x, n = float(next(tokens)), int(next(tokens))
        y = [float(next(tokens)) for _ in range(n)]
        z = [float(next(tokens)) for _ in range(n)]
        half = np.column_stack((y, z))
        half = half[np.r_[True, np.any(np.diff(half, axis=0) != 0, axis=1)]]
        if half[0, 0] > 0:
            half = np.vstack(([0., half[0, 1]], half))
        # Match the explicitly declared reference loft: the source design
        # waterline is a mandatory arc-parameter node, before interpolation.
        contour = []
        for i, point in enumerate(half):
            contour.append(point)
            if i + 1 < len(half):
                other = half[i + 1]
                if (point[1] - 10.8) * (other[1] - 10.8) < 0:
                    contour.append(point + (10.8-point[1]) / (other[1]-point[1]) * (other-point))
        records.append((x, np.asarray(contour)))
    if next(tokens, None) is not None:
        raise ValueError("trailing offset tokens")
    records.sort(key=lambda row: row[0])
    return records


def _arc_parameters(points):
    distances = np.r_[0., np.cumsum(np.hypot(*np.diff(points, axis=0).T))]
    return distances / distances[-1]


@dataclass(frozen=True)
class Immersion:
    volume_m3: float
    lcb_m: float
    tcb_m: float
    vcb_m: float


class _Strips:
    """Flattened piecewise-polynomial scanlines for one fixed heel."""
    def __init__(self, sections, heel_rad):
        self.c, self.s = math.cos(heel_rad), math.sin(heel_rad)
        strips = []
        for station, polygon in enumerate(sections):
            u = polygon[:, 0] * self.c + polygon[:, 1] * self.s
            q = -polygon[:, 0] * self.s + polygon[:, 1] * self.c
            un, qn = np.roll(u, -1), np.roll(q, -1)
            levels = np.unique(q)
            for q0, q1 in zip(levels, levels[1:]):
                if q1-q0 < 1e-12:
                    continue
                mid = (q0+q1)/2
                crossing = ((q <= mid) & (mid < qn)) | ((qn <= mid) & (mid < q))
                slope = (un[crossing]-u[crossing]) / (qn[crossing]-q[crossing])
                initial = u[crossing] + slope*(q0-q[crossing])
                order = np.argsort(initial + slope*(mid-q0))
                initial, slope = initial[order], slope[order]
                if len(order) % 2:
                    raise ValueError("odd scanline crossing count: nonclosed section")
                left, right = initial[::2], initial[1::2]
                la, ra = slope[::2], slope[1::2]
                w0, w1 = float(np.sum(right-left)), float(np.sum(ra-la))
                m0 = float(np.sum((right*right-left*left)/2))
                m1 = float(np.sum(right*ra-left*la))
                m2 = float(np.sum((ra*ra-la*la)/2))
                strips.append((station, q0, q1-q0, w0, w1, m0, m1, m2))
        a = np.asarray(strips)
        self.station = a[:, 0].astype(int)
        self.q0, self.dq, self.w0, self.w1, self.m0, self.m1, self.m2 = a[:, 1:].T
        self.count = len(sections)

    def moments(self, water_levels):
        t = np.clip(water_levels[self.station]-self.q0, 0., self.dq)
        area = self.w0*t + self.w1*t*t/2
        moment_u = self.m0*t + self.m1*t*t/2 + self.m2*t*t*t/3
        moment_q = self.q0*self.w0*t + (self.q0*self.w1+self.w0)*t*t/2 + self.w1*t*t*t/3
        aggregate = lambda a: np.bincount(self.station, a, minlength=self.count)
        return aggregate(area), aggregate(moment_u), aggregate(moment_q)


class ScanlineHull:
    def __init__(self, x_m, weights_m, sections, *, lpp_m=230.):
        self.x_m = np.asarray(x_m)
        self.weights_m = np.asarray(weights_m)
        self.sections = sections
        self.lpp_m = lpp_m
        self._strips_cache = {}

    @classmethod
    def from_kcs_fix(cls, path=ROOT / "data/raw/kcs/kcs.fix", *,
                     quadrature_order=4, max_panel_length_m=4.):
        """Construct the unscaled, 16 m sealed KCS loft from its raw offsets."""
        if quadrature_order < 2 or max_panel_length_m <= 0:
            raise ValueError("positive panel length and order >= 2 required")
        records = read_kcs_fix(path)
        nodes, gauss_weights = leggauss(quadrature_order)
        xs, weights, sections = [], [], []
        for (xa, a), (xb, b) in zip(records, records[1:]):
            pa, pb = _arc_parameters(a), _arc_parameters(b)
            parameters = np.unique(np.r_[pa, pb])
            aa = np.column_stack([np.interp(parameters, pa, a[:, k]) for k in (0, 1)])
            bb = np.column_stack([np.interp(parameters, pb, b[:, k]) for k in (0, 1)])
            boundaries = np.linspace(xa, xb, math.ceil((xb-xa)/max_panel_length_m)+1)
            for lo, hi in zip(boundaries, boundaries[1:]):
                for x, w in zip((lo+hi)/2+(hi-lo)/2*nodes, (hi-lo)/2*gauss_weights):
                    half = aa + (x-xa)/(xb-xa)*(bb-aa)
                    sections.append(np.vstack((half, half[::-1]*[-1., 1.])))
                    xs.append(x)
                    weights.append(w)
        return cls(xs, weights, sections)

    def _strips(self, heel_deg):
        if not math.isfinite(heel_deg) or abs(heel_deg) >= 89.99:
            raise ValueError("heel must be finite and inside +/-89.99 degrees")
        if heel_deg not in self._strips_cache:
            # Keep only the current angle: dense curves must not grow memory.
            self._strips_cache.clear()
            self._strips_cache[heel_deg] = _Strips(self.sections, math.radians(heel_deg))
        return self._strips_cache[heel_deg]

    def submerged(self, t_aft_m, t_fwd_m, heel_deg=0.):
        strips = self._strips(heel_deg)
        levels = (t_aft_m + (t_fwd_m-t_aft_m)*self.x_m/self.lpp_m)*strips.c
        area, mu, mq = strips.moments(levels)
        volume = float(area @ self.weights_m)
        if volume <= 1e-12:
            return Immersion(volume, 0., 0., 0.)
        u, q = (mu @ self.weights_m)/volume, (mq @ self.weights_m)/volume
        return Immersion(volume, float((area*self.x_m) @ self.weights_m/volume),
                         float(u*strips.c-q*strips.s), float(u*strips.s+q*strips.c))

    def float_at_heel(self, heel_deg, volume_m3, lcg_m, *, kg_m=0., tcg_m=0., fixed_trim_m=None):
        """Constant-volume scalar brackets; return lever about specified G.

        The lever is the buoyancy-to-G separation along the true transverse
        waterplane direction. For free trim the longitudinal separation is
        zero. Positive trim means aft draught exceeds forward draught.
        """
        if not np.isfinite([volume_m3, lcg_m, kg_m, tcg_m]).all() or volume_m3 <= 0:
            raise ValueError("finite CG and positive finite volume required")
        strips = self._strips(heel_deg)
        # A conservative bracket supports negative centreline immersions.
        max_extent = max(float(np.max(np.abs(p))) for p in self.sections)
        bound = 4*max_extent + self.lpp_m

        def at_trim(trim):
            limit = bound + abs(trim)
            mean = brentq(lambda d: self.submerged(d+trim/2, d-trim/2, heel_deg).volume_m3-volume_m3,
                          -limit, limit, xtol=2e-10)
            return mean, self.submerged(mean+trim/2, mean-trim/2, heel_deg)

        def longitudinal(trim):
            _, b = at_trim(trim)
            slope = -trim/self.lpp_m
            return b.lcb_m-lcg_m + slope*(b.vcb_m-kg_m)

        if fixed_trim_m is None:
            trim = None
            for extent in (.1, .5, 2., 10., 50., self.lpp_m, self.lpp_m*4):
                if longitudinal(-extent)*longitudinal(extent) <= 0:
                    trim = brentq(longitudinal, -extent, extent, xtol=2e-9)
                    break
            if trim is None:
                raise ValueError("cannot bracket longitudinal balance")
        else:
            trim = float(fixed_trim_m)
        mean, b = at_trim(trim)
        slope = -trim/self.lpp_m
        # Construct across = normal cross along, written independently as
        # explicit components; normalization retains correct nonzero trim.
        across = np.array([-strips.s*slope, strips.c*(1+slope*slope), strips.s])
        across /= np.linalg.norm(across)
        lever = float(np.dot(np.array([b.lcb_m-lcg_m, b.tcb_m-tcg_m, b.vcb_m-kg_m]), across))
        return dict(heel_deg=float(heel_deg), t_aft_m=mean+trim/2, t_fwd_m=mean-trim/2,
                    trim_m=trim, volume_m3=b.volume_m3, lcb_m=b.lcb_m,
                    tcb_m=b.tcb_m, vcb_m=b.vcb_m, lever_m=lever,
                    volume_residual_m3=b.volume_m3-volume_m3,
                    longitudinal_residual_m=longitudinal(trim),
                    kg_m=kg_m, lcg_m=lcg_m, fixed_trim_m=fixed_trim_m)


def compare_production():
    """Reproducible same-envelope numerical oracle; not external validation."""
    from datetime import datetime, timezone
    import resource
    import sys
    from stowage.vessel.hydrostatics import HullGeometry

    start = time.perf_counter()
    paths = ["data/raw/kcs/kcs.fix", "src/stowage/vessel/hydrostatics.py",
             "src/stowage/vessel/hull_offsets.py",
             str(Path(__file__).resolve().relative_to(ROOT))]
    hashes = lambda: {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
    before = hashes()
    params = dict(hull=dict(lpp_m=230., beam_m=32.2, design_draught_m=10.8,
                           depth_m=16., water_density_kg_m3=1025.),
        hull_form=dict(family="stowlm_offsets_v1", offsets_path="data/raw/kcs/kcs.fix",
            offsets_sha256=KCS_SHA256, offsets_format="kcs_fix", reference_lpp_m=230.,
            reference_beam_m=32.2, reference_draught_m=10.8, reference_depth_m=16.),
        envelope=dict(deck_z_m=16., hatch_coamings=[], openings=[], count_deckhouse=False))
    fine = ScanlineHull.from_kcs_fix(quadrature_order=4)
    coarse = ScanlineHull.from_kcs_fix(quadrature_order=2)
    production = HullGeometry.from_params(params, nx=96, nz=128)
    upright = fine.submerged(10.8, 10.8)
    lcg, volume, kg, rho = upright.lcb_m, 51906., 14.38, 1025.
    rows, kn_rows = [], []
    for angle in range(0, 61, 10):
        t = time.perf_counter()
        oracle = fine.float_at_heel(angle, volume, lcg, kg_m=kg)
        low_order = coarse.float_at_heel(angle, volume, lcg, kg_m=kg)
        floating = production._solve(volume*rho, lcg, 0., kg, rho, math.radians(angle))
        across = production._axes(floating.t_aft_m, floating.t_fwd_m, math.radians(angle))[2]
        actual_gz = float(np.dot(np.asarray(floating.submerged.centroid_m)-[lcg, 0., kg], across))
        rows.append(dict(heel_deg=angle, independent_order4=oracle,
                         independent_order2=low_order, production_gz_m=actual_gz,
                         production_trim_m=floating.trim_m,
                         production_minus_order4_gz_m=actual_gz-oracle["lever_m"],
                         order2_minus_order4_gz_m=low_order["lever_m"]-oracle["lever_m"],
                         elapsed_seconds=time.perf_counter()-t))
        if angle in (0, 10, 30, 60):
            reference = fine.float_at_heel(angle, volume, lcg, kg_m=0.)
            low = coarse.float_at_heel(angle, volume, lcg, kg_m=0.)
            actual_kn = production.kn(math.radians(angle), volume*rho, lcg_m=lcg)
            kn_rows.append(dict(heel_deg=angle, independent_order4=reference,
                independent_order2=low, production_kn_m=actual_kn,
                production_minus_order4_kn_m=actual_kn-reference["lever_m"],
                order2_minus_order4_kn_m=low["lever_m"]-reference["lever_m"]))
        print(f"finished {angle} degrees", file=sys.stderr, flush=True)
    memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
        purpose="Independent numerical integration of the SAME 16 m sealed envelope; not external KCS validation",
        source_hashes_before=before, source_hashes_after=hashes(),
        loading=dict(volume_m3=volume, density_kg_m3=rho, kg_m=kg, lcg_m=lcg,
            lcg_basis="fixed at independent upright LCB for draft10.8; no published LCG asserted",
            tcg_m=0., free_trim=True),
        geometry=params, independent_quadrature=dict(orders=[2,4], max_panel_length_m=4.,
            order4_stations=len(fine.x_m), order2_stations=len(coarse.x_m)),
        production_quadrature=dict(nx=96,nz=128), independent_upright_draft10_8=upright.__dict__,
        production_upright_draft10_8=production.submerged(10.8,10.8).centroid_m,
        gz_rows=rows, kn_rows=kn_rows,
        max_abs_gz_error_m=max(abs(r["production_minus_order4_gz_m"]) for r in rows),
        max_abs_kn_error_m=max(abs(r["production_minus_order4_kn_m"]) for r in kn_rows),
        max_abs_order_change_gz_m=max(abs(r["order2_minus_order4_gz_m"]) for r in rows),
        max_abs_order_change_kn_m=max(abs(r["order2_minus_order4_kn_m"]) for r in kn_rows),
        peak_process_rss_bytes=memory if sys.platform=="darwin" else memory*1024,
        elapsed_seconds=time.perf_counter()-start)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-production", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.compare_production:
        result = compare_production()
    else:
        start = time.perf_counter()
        hull = ScanlineHull.from_kcs_fix()
        upright = hull.submerged(10.8, 10.8)
        result = dict(upright=upright.__dict__, rows=[
            hull.float_at_heel(a, upright.volume_m3, upright.lcb_m)
            for a in (0., 30., 60.)], elapsed_seconds=time.perf_counter()-start)
    serialized = json.dumps(result, indent=2)+"\n"
    if args.output:
        args.output.write_text(serialized)
    else:
        print(serialized, end="")

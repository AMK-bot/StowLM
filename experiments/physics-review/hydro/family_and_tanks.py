"""Independent checks: rounded bilge + flare at large heel, free-trim KN vs real-KG GZ,
tank free-surface wedges, MCT approximation size, and Bardu data consistency.

Run: uv run python experiments/physics-review/hydro/family_and_tanks.py
"""
from __future__ import annotations

import math

import numpy as np

from box_barge import RHO, params, section_2d
from stowage.params import load_param_file
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.tanks import TankGeometry


def bilge_flare_check():
    print("== A. Prismatic rounded-bilge + flared section, exact circle vs code chords ==")
    L, B, D, TD, r, flare = 100.0, 20.0, 10.0, 6.0, 2.0, 0.08

    def hb(z):
        v = np.ones_like(z)
        low = z < r
        v[low] = 1 - 2 * r / B + 2 / B * np.sqrt(np.maximum(0, r * r - (z[low] - r) ** 2))
        return B / 2 * v * (1 + flare * np.maximum(0, z - TD) / (D - TD))

    for nz in (64, 256):
        g = HullGeometry.from_params(params(r=r, flare=flare, L=L, B=B, D=D, TD=TD), nx=48, nz=nz)
        for T in (1.0, 5.0):
            s = g.submerged(T, T, 0)
            zs = (np.arange(400000) + .5) * T / 400000
            a_exact = (2 * hb(zs)).sum() * T / 400000
            print(f" nz={nz} T={T}: section area code={s.volume_m3/L:.6f} exact={a_exact:.6f}"
                  f" ({(s.volume_m3/L/a_exact-1)*100:+.4f}%)")
            for deg in (30, 50, 70):
                phi = math.radians(deg)
                yb, zb = section_2d(phi, a_exact, hb, D)
                kn_exact = yb * math.cos(phi) + zb * math.sin(phi)
                kn = g.kn(phi, s.displacement_kg, lcg_m=L / 2)
                print(f"   heel {deg}: KN code={kn:.5f} exact={kn_exact:.5f} diff={kn-kn_exact:+.5f} m")


def free_trim_kg_check():
    print("\n== B. Free-trim KN (G at keel) vs direct GZ with actual KG (asymmetric hull) ==")
    p = params(aft=0.30, fore=0.08, r=1.0, flare=0.04, L=120, B=20, D=10, TD=7)
    g = HullGeometry.from_params(p, nx=48, nz=64)
    T = 6.0
    s = g.submerged(T, T, 0)
    mass, lcg = s.displacement_kg, s.lcb_m
    for kg in (6.0, 9.0):
        for deg in (20, 40, 60):
            phi = math.radians(deg)
            kn = g.kn(phi, mass, lcg_m=lcg)
            f = g._solve(mass, lcg, 0., kg, RHO, phi)
            _, _, tr = g._axes(f.t_aft_m, f.t_fwd_m, phi)
            gz_direct = float(np.dot(np.asarray(f.submerged.centroid_m) - [lcg, 0, kg], tr))
            fk = g._solve(mass, lcg, 0., 0., RHO, phi)
            print(f" KG={kg} heel {deg}: KN-KG sin={kn-kg*math.sin(phi):.5f}  direct GZ={gz_direct:.5f}"
                  f"  diff={kn-kg*math.sin(phi)-gz_direct:+.5f} m; trim(KG=0)={fk.trim_m:+.3f} trim(KG)={f.trim_m:+.3f}")


def tank_checks():
    print("\n== C. Tank free-surface wedges ==")
    t = TankGeometry(0, 10, -2, 2, 0, 4)
    # 10% fill, heel 45 deg: liquid is a right isosceles triangle in the port-bottom corner.
    v = 16.0
    liq = t.liquid(v, math.radians(45))
    a = math.sqrt(2 * v / 10)
    w = a * math.sqrt(2)
    print(f" heel45 10%: centroid code={tuple(round(c,6) for c in liq.centroid_m)} hand=(5, {2-a/3:.6f}, {a/3:.6f})")
    print(f"   surface area code={liq.free_surface_area_m2:.6f} hand={10*w:.6f}; i code={liq.fsm_m4:.6f} hand l w^3/12={10*w**3/12:.6f}")
    # Trim-only wedge, bow (x max) down? positive trim = aft down -> liquid goes to x_min.
    th = math.atan(0.5)
    v = 0.5 * 4 * 4 * (4 / math.tan(th)) * 0.5  # triangle in x-z: depth h at aft wall, length h/tan
    # choose liquid triangle with aft depth h, length l=h/tan(th) <10
    h = 3.0; l = h / math.tan(th); v = 0.5 * h * l * 4
    liq = t.liquid(v, 0.0, th)
    print(f" trim atan(.5), wedge h=3,l=6: centroid code={tuple(round(c,6) for c in liq.centroid_m)} hand=({l/3:.6f}, 0, {h/3:.6f})")
    ls = math.hypot(h, l)
    print(f"   i_t code={liq.fsm_m4:.6f} hand l_s b^3/12={ls*4**3/12:.6f} (about longitudinal axis)")
    # Upright sounding units
    s = t.sounding(80)
    print(f" upright half fill: fsm_m4={s.fsm_m4:.6f} hand l b^3/12={10*4**3/12:.6f} (m^4, multiply by rho_liquid)")
    # Off-centre tank: inertia must be about the tank's own axis, not ship CL.
    off = TankGeometry(0, 10, 3, 7, 0, 4)
    print(f" off-centre tank y in [3,7]: fsm_m4={off.sounding(80).fsm_m4:.4f} (own-axis {10*64/12:.4f};"
          f" about ship CL would be {10*(7**3-3**3)/3:.4f})")
    lq = off.liquid(80, math.radians(10), 0.0)
    print(f"   heeled 10deg: i={lq.fsm_m4:.4f} hand 10*(4/cos)^3/12={10*(4/math.cos(math.radians(10)))**3/12:.4f}")


def mct_size():
    print("\n== D. Size of MCT's neglected (KG-KB) term, fleet design draught ==")
    for name in ("feeder_500", "feeder_1000", "feeder_1700"):
        p = load_param_file("fleet", name)
        g = HullGeometry.from_params(p)
        T = p["hull"]["design_draught_m"]
        s = g.submerged(T, T, 0)
        for kg in (0.55 * g.depth_m, 0.8 * g.depth_m):
            print(f" {name}: BML={s.bm_l_m:.1f} KB={s.vcb_m:.2f} KG={kg:.2f} -> MCT overstated by"
                  f" {(s.bm_l_m/(s.bm_l_m+s.vcb_m-kg)-1)*100:+.2f}%")


def bardu_consistency():
    print("\n== E. Bardu: is TPC(11.6)=52.13 consistent with DWT 34,000@11.6 / 26,700@10.1? ==")
    inc = 34000 - 26700
    mean_tpc = inc / 150
    print(f" implied mean TPC 10.1-11.6 m = {mean_tpc:.2f} t/cm vs 52.13 at 11.6 m;"
          f" linear TPC => TPC(10.1) = {2*mean_tpc-52.13:.2f} t/cm, i.e. Awp +{(52.13/(2*mean_tpc-52.13)-1)*100:.1f}% over 1.5 m")
    print(f" with DWT rounding +/-50 t each: mean TPC range {(inc-100)/150:.2f}-{(inc+100)/150:.2f}")
    print(f" waterplane coefficient implied at 11.6 m: {52130*100/RHO/(196.9*29.8):.4f}")


if __name__ == "__main__":
    bilge_flare_check()
    free_trim_kg_check()
    tank_checks()
    mct_size()
    bardu_consistency()

"""Independent box-barge hand checks against stowage.vessel.hydrostatics.

Box L x B x D, tiny end taper (a=f=1e-7) so the declared family degenerates to a
box. Closed forms: Delta=rho L B T, KB=T/2, BM=B^2/12T, BML=L^2/12T, TPC=rho L B/100,
wall-sided KN (G at K) = sin(phi)(KB + BM(1 + tan^2(phi)/2)) before deck-edge
immersion / bilge emergence. Beyond that, an independent brute-force 2D section
integration (midpoint in y, 200k strips) of the rectangle is used.
Run: uv run python experiments/physics-review/hydro/box_barge.py
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from stowage.vessel.hydrostatics import HullGeometry

RHO, G = 1025.0, 9.80665
L, B, D, TD = 100.0, 20.0, 10.0, 5.0


def params(aft=1e-7, fore=1e-7, r=0.0, flare=0.0, L=L, B=B, D=D, TD=TD):
    return {"hull": dict(lpp_m=L, beam_m=B, depth_m=D, design_draught_m=TD,
                         water_density_kg_m3=RHO),
            "hull_form": dict(family="stowlm_separable_rounded_bilge_v1",
                              aft_run_fraction=aft, fore_run_fraction=fore,
                              bilge_radius_m=r, deck_flare_fraction=flare),
            "envelope": dict(hatch_coamings=[], openings=[])}


def rel(a, b):
    return (a / b - 1) * 100 if b else float("nan")


def section_2d(phi, area_target, half_breadth, depth, n=200_000):
    """Brute-force wet area/centroid of a section y in [-hb(z),hb(z)], z in [0,D].

    half_breadth(z) vectorised; water z < c + y tan(phi) (port +y down for phi>0).
    Integrate over z strips: wet y-interval of each strip computed analytically.
    """
    zs = (np.arange(n) + 0.5) * depth / n
    dz = depth / n
    hb = half_breadth(zs)
    t = math.tan(phi)

    def stats(c):
        # wet where y > (z - c)/t  (phi>0)
        if abs(t) < 1e-15:
            lo = np.where(zs < c, -hb, hb)
        else:
            lo = np.clip((zs - c) / t, -hb, hb)
        width = hb - lo
        a = (width * dz).sum()
        ym = ((hb + lo) / 2 * width * dz).sum()
        zm = (zs * width * dz).sum()
        return a, ym, zm

    c = brentq(lambda c: stats(c)[0] - area_target, -50 * depth, 50 * depth, xtol=1e-12)
    a, ym, zm = stats(c)
    return ym / a, zm / a


def main():
    g = HullGeometry.from_params(params(), nx=48, nz=64)
    print("== 1. Upright box barge (code vs hand) ==")
    for T in (0.05, 2.0, 4.0, 7.5):
        s = g.submerged(T, T, 0.0)
        row = g.hydrostatic_row(T)
        hand = dict(disp=RHO * L * B * T, kb=T / 2, bm=B * B / (12 * T), bml=L * L / (12 * T),
                    tpc=RHO * L * B / 100, mct=RHO * G * (B * L ** 3 / 12) / (100 * L))
        code = dict(disp=s.displacement_kg, kb=s.vcb_m, bm=s.bm_t_m, bml=s.bm_l_m,
                    tpc=row.tpc_kg_per_cm, mct=row.mct_nm_per_cm)
        print(f"T={T}: " + ", ".join(f"{k} code={code[k]:.6g} hand={hand[k]:.6g} ({rel(code[k],hand[k]):+.2e}%)" for k in hand))
        print(f"   KM code={row.kmt_m:.6f} hand={T/2+B*B/(12*T):.6f}  LCF={row.lcf_m:.4f} LCB={row.lcb_m:.4f}")

    print("\n== 2. Free-trim KN, box T=4 (deck edge at atan(6/10)=%.2f deg) ==" % math.degrees(math.atan(0.6)))
    T = 4.0
    mass = RHO * L * B * T
    for deg in (5, 10, 20, 30, 31, 40, 50, 60, 75):
        phi = math.radians(deg)
        kn_code = g.kn(phi, mass, lcg_m=L / 2)
        ws = math.sin(phi) * (T / 2 + B * B / (12 * T) * (1 + math.tan(phi) ** 2 / 2))
        yb, zb = section_2d(phi, B * T, lambda z: np.full_like(z, B / 2), D)
        kn_bf = yb * math.cos(phi) + zb * math.sin(phi)
        print(f"heel {deg:2d}: code {kn_code:.5f}  brute-force {kn_bf:.5f} (diff {kn_code-kn_bf:+.2e})"
              f"  wall-sided {ws:.5f}")

    print("\n== 3. Draught edge cases ==")
    for Tx in (1e-4, 9.999, 10.0, 11.0):
        s = g.submerged(Tx, Tx, 0.0)
        print(f"T={Tx}: vol={s.volume_m3:.4f} (hand {L*B*min(Tx,D):.4f}) Awp={s.waterplane_area_m2:.3f} KB={s.vcb_m:.5f}")
    try:
        g.hydrostatic_row(10.0)
    except ValueError as e:
        print("hydrostatic_row(T=D) ->", e)

    print("\n== 4. Trim by the head: equilibrium vs exact wall-sided longitudinal relation ==")
    # Box at mean T=4, G at (xG, 0, zG). Exact (no deck immersion): with
    # slope s=tan(theta)=(tf-ta)/L, B_x=L/2 + L^2/(12T) s, B_z=T/2 + L^2/(24T) s^2,
    # equilibrium (B-G).along=0: (B_x-xG) + (B_z-zG) s = 0.
    for xG, zG in ((55.0, 6.0), (45.0, 6.0), (52.0, 0.0)):
        f = lambda s: (L / 2 + L * L / (12 * T) * s - xG) + (T / 2 + L * L / (24 * T) * s * s - zG) * s
        s_hand = brentq(f, -0.2, 0.2)
        eq = g.equilibrium(mass, xG, 0.0, zG)
        s_code = (eq.t_fwd_m - eq.t_aft_m) / L
        ta_h, tf_h = T - L / 2 * s_hand, T + L / 2 * s_hand
        print(f"xG={xG} zG={zG}: code ta={eq.t_aft_m:.5f} tf={eq.t_fwd_m:.5f} trim(ta-tf)={eq.trim_m:+.5f};"
              f" hand ta={ta_h:.5f} tf={tf_h:.5f} trim={ta_h-tf_h:+.5f}  heel={eq.heel_rad:.2e}")
        # MCT prediction (labelled approx, KG-KB neglected) for comparison
        row = g.hydrostatic_row(T)
        trim_mct = mass * G * (xG - row.lcb_m) / row.mct_nm_per_cm / 100  # m, + = bow down
        gml = T / 2 + L * L / (12 * T) - zG
        trim_exact_small = mass * G * (xG - L / 2) / (mass * G * gml / (100 * L)) / 100
        print(f"     MCT-table trim {trim_mct:.5f} m vs small-angle GM_L trim {trim_exact_small:.5f} m"
              f" ({rel(trim_mct, trim_exact_small):+.2f}%) vs exact {tf_h-ta_h:.5f} m")


if __name__ == "__main__":
    main()

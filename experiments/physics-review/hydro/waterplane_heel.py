"""Heeled/trimmed box waterplane area and inertias vs closed form.

Box L=100,B=20,D=10 at T=4, heel phi (no deck immersion below 31 deg) and slope s.
In-plane waterplane is a parallelogram-free rectangle of sides L*sqrt(1+s^2) and
B/cos(phi) only when trim=0; with both, area = L*B*|n|/n_z-ish. Hand formula:
area = L*B / (cos(phi_eff)) where the plane normal n=(−s cos, −sin, cos)/N:
projected area onto xy = L*B, so true area = L*B / n_z.
Run: uv run python waterplane_heel.py
"""
import math
from box_barge import params
from stowage.vessel.hydrostatics import HullGeometry

g = HullGeometry.from_params(params(), nx=48, nz=64)
L, B, T = 100., 20., 4.
for deg, trim in ((10, 0.0), (20, 0.0), (10, 1.0), (0, 2.0)):
    phi = math.radians(deg)
    s_ = g.submerged(T + trim / 2, T - trim / 2, phi)
    n = g.waterplane_normal(T + trim / 2, T - trim / 2, phi)
    area_hand = L * B / n[2]
    it_hand = L * (B / math.cos(phi)) ** 3 / 12 if trim == 0 else float('nan')
    print(f"heel {deg} trim {trim}: area code={s_.waterplane_area_m2:.4f} hand={area_hand:.4f};"
          f" I_T code={s_.transverse_inertia_m4:.1f} hand(trim=0)={it_hand:.1f}; tcb={s_.tcb_m:.4f}")

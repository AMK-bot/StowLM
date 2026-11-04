"""Independent box-hull check of GZ, areas and A2.2 criteria (physics review 3.1).

Hand model: 80 x 16 x 12 m prism, T = 5 m, KG = 4 m, seawater 1025.
Wall-sided formula (Biran & Lopez-Pulido 2014): GZ = sin(phi)[GM + BM tan^2(phi)/2],
valid while neither deck edge (tan phi < 2(D-T)/B = 0.875, 41.2 deg) nor bilge
(tan phi < 2T/B = 0.625, 32.0 deg) emerges/immerses. Beyond that we use our OWN
independent 2D clip (written here, not the project's) of the rectangle section:
for a prism with even keel the free-trim solve is trivial (no trim coupling).
Areas are integrated with a 0.01-degree Simpson rule on the independent curve
and compared with the code's 2-degree grid + evaluate_criteria.
"""
import math
import numpy as np
from scipy.optimize import brentq
from scipy.integrate import simpson

from stowage.vessel.hydrostatics import HullGeometry
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_criteria import evaluate_criteria
from types import SimpleNamespace

L, B, D, T, KG, RHO = 80., 16., 12., 5., 4., 1025.


def box():
    g = object.__new__(StabilityGeometry)
    g.lpp_m, g.beam_m, g.depth_m = L, B, D
    g.design_draught_m, g.density_kg_m3 = T, RHO
    g.max_z_m, g.openings = D, []
    g.x_m = np.array([L/2 - L/2/math.sqrt(3), L/2 + L/2/math.sqrt(3)])
    g.integration_weights_m = np.array([L/2, L/2])
    sec = np.array([[B/2, 0.], [B/2, D], [-B/2, D], [-B/2, 0.]])
    g.sections = [[sec.copy()], [sec.copy()]]
    g._p = np.array([sec, sec]); g._q = np.roll(g._p, -1, axis=1)
    g._px = g.x_m; g._pw = g.integration_weights_m
    return g


def my_clip(poly, phi, c):
    """Keep y*(-sin)+z*cos <= c (wet side). Own implementation."""
    n = np.array([-math.sin(phi), math.cos(phi)])
    out = []
    for i in range(len(poly)):
        p, q = poly[i], poly[(i+1) % len(poly)]
        dp, dq = p @ n - c, q @ n - c
        if dp <= 0: out.append(p)
        if (dp <= 0) != (dq <= 0):
            out.append(p + dp/(dp-dq)*(q-p))
    return np.array(out)


def area_centroid(p):
    x, y = p[:, 0], p[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    c = x*y1 - x1*y
    a = c.sum()/2
    return a, ((x+x1)*c).sum()/(6*a), ((y+y1)*c).sum()/(6*a)


def gz_independent(phi):
    sec = np.array([[B/2, 0.], [B/2, D], [-B/2, D], [-B/2, 0.]])
    target = B*T
    f = lambda c: (area_centroid(my_clip(sec, phi, c))[0] if len(my_clip(sec, phi, c)) >= 3 else 0.) - target
    c = brentq(f, -30, 30, xtol=1e-13)
    _, yb, zb = area_centroid(my_clip(sec, phi, c))
    # GZ = (B - G) . (cos phi, sin phi)
    return (yb - 0.)*math.cos(phi) + (zb - KG)*math.sin(phi)


def main():
    bm = B**2/(12*T); gm = T/2 + bm - KG
    print(f'hand: KB={T/2}, BM={bm:.6f}, GM={gm:.6f}')
    g = box()
    grid = np.radians(np.sort(np.r_[np.arange(0., 90., 2.), 25., 89.99]))
    mass = L*B*T*RHO
    gz, drafts = g.free_trim_curve(grid, mass, L/2, KG, RHO)
    indep = np.array([gz_independent(p) for p in grid])
    ws = np.sin(grid)*(gm + .5*bm*np.tan(grid)**2)
    print('deg   code_GZ    indep_GZ   wallsided  (wall-sided valid <32.0 deg)')
    for d, a, b, w in zip(np.degrees(grid), gz, indep, ws):
        if int(round(d)) % 10 == 0 or d in (25., 32., 42.):
            print(f'{d:5.1f} {a:10.6f} {b:10.6f} {w:10.6f}')
    print('max |code-indep| =', float(np.max(np.abs(gz-indep))))
    # Fine independent areas
    fine = np.radians(np.arange(0, 90.0001, .01))
    fine = fine[fine < math.radians(89.999)]
    gf = np.array([gz_independent(p) for p in fine])
    def A(lo, hi):
        m = (fine >= math.radians(lo)-1e-12) & (fine <= math.radians(hi)+1e-12)
        return simpson(gf[m], x=fine[m])
    hand = dict(a030=A(0, 30), a040=A(0, 40), a3040=A(30, 40),
                gzmax=gf.max(), phimax=math.degrees(fine[np.argmax(gf)]))
    # code criteria, sealed (phi_f = 90)
    hull = SimpleNamespace(lpp_m=L, beam_m=B, depth_m=D, bilge_keel=False,
                           windage_area_m2=400., windage_lever_m=6.)
    angles = np.r_[grid, math.pi/2]; gzc = np.r_[gz, gz[-1]]
    m, mg, fails = evaluate_criteria(angles, gzc, gm_m=gm, hull=hull, geometry=g,
        displacement_kg=mass, kg_m=KG, draught_m=T, phi_f_rad=math.pi/2,
        hull_params={'hull_form': {'bilge_radius_m': 0.}, 'envelope': {'hatch_coamings': []}})
    print('\nquantity        code        hand(0.01deg Simpson)  diff')
    for k, h in [('area_0_30_mrad', 'a030'), ('area_0_40_mrad', 'a040'),
                 ('area_30_40_mrad', 'a3040'), ('gz_max_m', 'gzmax')]:
        print(f'{k:16s}{m[k]:12.6f}{hand[h]:14.6f}{m[k]-hand[h]:14.2e}')
    print(f'angle_gz_max     {math.degrees(m["angle_gz_max_rad"]):.3f} deg  hand {hand["phimax"]:.3f} deg')
    print('weather metrics:', {k: round(v, 5) for k, v in m.items() if k.startswith('weather')})
    print('failures:', [f[0] for f in fails])
    # weather by hand for this box
    X1 = np.interp(B/T, [2.4,2.5,2.6,2.7,2.8,2.9,3.,3.1,3.2,3.4,3.5],[1.,.98,.96,.95,.93,.91,.90,.88,.86,.82,.80])
    C = .373+.023*B/T-.043*L/100; Tr = 2*C*B/math.sqrt(gm)
    s = np.interp(Tr, [6,7,8,12,14,16,18,20],[.1,.098,.093,.065,.053,.044,.038,.035])
    r = .73+.6*(KG-T)/T
    k = .7  # sharp bilge (box): A2.3.4 k=0.7
    phi1 = 109*k*X1*1.0*math.sqrt(r*s)
    lw1 = 504*400*6/(1000*9.81*(mass/1000))
    print(f'hand weather: X1={X1:.4f} X2=1 k={k} C={C:.5f} T={Tr:.4f}s s={s:.5f} r={r:.4f} phi1={phi1:.4f}deg lw1={lw1:.6f}')
    # hand a/b with fine curve (positive side, mirrored negative)
    fx = np.r_[-fine[:0:-1], fine]; fy = np.r_[-gf[:0:-1], gf]
    lw2 = 1.5*lw1
    phi0 = brentq(lambda p: gz_independent(p)-lw1, 0, .3)
    pg = brentq(lambda p: gz_independent(p)-lw2, 0, .3)
    # second intercept of lw2
    idx = np.where((gf[1:]-lw2)*(gf[:-1]-lw2) < 0)[0]
    phic = fine[idx[1]] if len(idx) > 1 else math.pi/2
    phi2 = min(math.radians(50), phic, math.pi/2)
    st = phi0 - math.radians(phi1)
    ma = (fx >= st) & (fx <= pg); mb = (fx >= pg) & (fx <= phi2)
    a = lw2*(pg-st) - simpson(fy[ma], x=fx[ma]); b = simpson(fy[mb], x=fx[mb]) - lw2*(phi2-pg)
    print(f'hand weather: phi0={math.degrees(phi0):.4f} phi2={math.degrees(phi2):.3f} a={a:.6f} b={b:.6f} ratio={b/a:.5f}')
    print(f'code weather: phi0={math.degrees(m["weather_phi0_rad"]):.4f} phi1={math.degrees(m["weather_phi1_rad"]):.4f} '
          f'phi2={math.degrees(m["weather_phi2_rad"]):.3f} a={m["weather_area_a_mrad"]:.6f} b={m["weather_area_b_mrad"]:.6f} ratio={m["weather_ratio"]:.5f}')
    print(f'code deck edge {math.degrees(m["weather_deck_edge_rad"]):.4f} deg; hand atan(2(D-T)/B) = {math.degrees(math.atan(2*(D-T)/B)):.4f}')


if __name__ == '__main__':
    main()

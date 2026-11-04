"""Weather-criterion probes (review 3.1).

(1) windward side truncated at phi_f; (2) hand recomputation of departure_metrics.json
weather numbers; (3) max-GZ-angle sampling bias near 25 deg.
"""
import json, math, sys
import numpy as np
sys.path.insert(0, 'experiments/physics-review/stability')
from probes import feeder, P, G
from stowage.checker import stability as S
from stowage.checker.stability_criteria import evaluate_criteria, _integral

# (1) base KG6 curve, then evaluate with phi_f = 20.2 deg (opening) vs 90
i = feeder(kg=6.0)
cond = S.loading_condition(i, i.onboard, geometry=G, ship_params=P)
f = G.equilibrium(cond.mass_kg, cond.lcg_m, cond.tcg_m, cond.kg_m, 1025.)
angles = np.radians(np.sort(np.r_[np.arange(0., 90., 2.), 25., 89.99]))
gz, dr = G.free_trim_curve(angles, cond.mass_kg, cond.lcg_m, cond.kg_m, 1025.)
A, Z = S._windage(i, i.onboard, P, f.t_aft_m, f.t_fwd_m)
ang = np.r_[angles, math.pi/2]; g = np.r_[gz, gz[-1]]
common = dict(gm_m=float(G.full_submerged(*dr[0]).vcb_m + G.full_submerged(*dr[0]).bm_t_m - cond.kg_m),
              hull=i.vessel.hull, geometry=G, displacement_kg=cond.mass_kg, kg_m=cond.kg_m,
              draught_m=f.mean_draught_m, windage_area_m2=A, windage_lever_m=Z, hull_params=P,
              draught_aft_m=f.t_aft_m, draught_fwd_m=f.t_fwd_m)
for pf in (90., 30., 20.2):
    m, _, fl = evaluate_criteria(ang, g, phi_f_rad=math.radians(pf), **common)
    phi0, phi1, phi2 = (math.degrees(m[k]) for k in ('weather_phi0_rad', 'weather_phi1_rad', 'weather_phi2_rad'))
    print(f'phi_f={pf:5.1f}: phi0={phi0:.3f} phi1={phi1:.3f} start={phi0-phi1:.3f} phi2={phi2:.3f} '
          f'a={m["weather_area_a_mrad"]:.5f} b={m["weather_area_b_mrad"]:.5f} ratio={m["weather_ratio"]:.4f} '
          f'weather_fail={"iscode.a2_3.weather" in {x[0] for x in fl}}')
# hand: windward side uses the full (untruncated) symmetric curve, phi2 = min(20.2, 50, phic)
lw2 = m['weather_lw2_m']; phi0 = m['weather_phi0_rad']; phi1 = m['weather_phi1_rad']
sx = np.r_[-ang[:0:-1], ang]; sy = np.r_[-g[:0:-1], g]
xs = np.linspace(0, .5, 50001); ys = np.interp(xs, ang, g)
pg = xs[np.argmax(ys >= lw2)]
start, phi2 = phi0-phi1, math.radians(20.2)
a = lw2*(pg-start) - _integral(sx, sy, start, pg); b = _integral(sx, sy, pg, phi2) - lw2*(phi2-pg)
print(f'hand (windward curve not truncated): a={a:.5f} b={b:.5f} ratio={b/a:.4f}')

# (2) departure_metrics.json by hand
d = json.load(open('experiments/stability/departure_metrics.json'))
mm = d['checker_metrics']; B, Lpp = 19.2, 107.8; T = mm['draught_mean_m']; KG = mm['kg_m']; GM = mm['gm_m']
X1 = np.interp(B/T, [2.4,2.5,2.6,2.7,2.8,2.9,3.,3.1,3.2,3.4,3.5],[1.,.98,.96,.95,.93,.91,.90,.88,.86,.82,.80])
C = .373 + .023*B/T - .043*Lpp/100; Tr = 2*C*B/math.sqrt(GM)
s = np.interp(Tr, [6,7,8,12,14,16,18,20], [.1,.098,.093,.065,.053,.044,.038,.035])
r = .73 + .6*(KG-T)/T; phi1 = 109*1.0*X1*mm['weather_x2']*math.sqrt(r*s)
lw1 = 504*mm['windage_area_m2']*mm['windage_lever_m']/(1000*9.81*mm['displacement_kg']/1000)
print(f'departure hand: X1={X1:.5f} (code {mm["weather_x1"]:.5f}) T={Tr:.4f} (code {mm["weather_roll_period_s"]:.4f}) '
      f's={s:.5f} (code {mm["weather_s"]:.5f}) r={r:.5f} (code {mm["weather_r"]:.5f}) '
      f'phi1={phi1:.4f} deg (code {math.degrees(mm["weather_phi1_rad"]):.4f}) lw1={lw1:.6f} (code {mm["weather_lw1_m"]:.6f})')

# (3) max-GZ-angle sampling bias: smooth curve peaking at 24.6 deg
grid = np.radians(np.sort(np.r_[np.arange(0., 90., 2.), 25., 89.99, 90.]))
peak = math.radians(24.6)
curve = lambda p: 0.6*np.sin(math.pi/2*p/peak)*(p <= 2*peak) - 0.3*(p > 2*peak)*np.sin(p-2*peak)
y = curve(grid)
print('sampled GZ 22..28 deg:', {int(round(math.degrees(a))): round(float(v), 5) for a, v in zip(grid, y) if 21 < math.degrees(a) < 29})
m, _, fl = evaluate_criteria(grid, y, phi_f_rad=math.pi/2, **common)
print(f'true peak 24.6 deg; code angle_gz_max={math.degrees(m["angle_gz_max_rad"]):.2f} deg; '
      f'max_gz_angle failure reported: {"iscode.a2_2.max_gz_angle" in {x[0] for x in fl}}')

# (4) synthetic flip: stiff curve GZ = 1.2 sin(2 phi) (GM 2.4 m), opening at 18 deg
y = 1.2*np.sin(2*grid)
for pf in (18., 25.):
    m, _, fl = evaluate_criteria(grid, y, phi_f_rad=math.radians(pf), **{**common, 'gm_m': 2.4})
    lw2, phi0, phi1 = m['weather_lw2_m'], m['weather_phi0_rad'], m['weather_phi1_rad']
    sx = np.r_[-grid[:0:-1], grid]; sy = np.r_[-y[:0:-1], y]
    xs = np.linspace(0, .5, 200001); pg = xs[np.argmax(1.2*np.sin(2*xs) >= lw2)]
    st, p2 = phi0-phi1, math.radians(pf)
    a = lw2*(pg-st) - _integral(sx, sy, st, pg); b = _integral(sx, sy, pg, p2) - lw2*(p2-pg)
    print(f'synthetic phi_f={pf}: phi1={math.degrees(phi1):.2f} start={math.degrees(st):.2f}; code ratio={m["weather_ratio"]:.3f} '
          f'fail={"iscode.a2_3.weather" in {x[0] for x in fl}}; hand untruncated ratio={b/a:.3f}')

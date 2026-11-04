"""Quick look: band, tanks, FSM reserve, KM curve convexity for feeder_500 linear model."""
import runpy, sys, time
from pathlib import Path
import numpy as np
from scipy.optimize import brentq
root = Path(__file__).resolve().parents[3]
helper = runpy.run_path(str(root/"experiments/linear-model/validate.py"))
from stowage.linear.physics import build_model, geometry_for
name = sys.argv[1] if len(sys.argv) > 1 else "feeder_500"
inst = helper["fleet_instance"](name)
t0=time.time(); m = build_model(inst, _samples=2); print("build s", time.time()-t0)
print("band", m.band); print("const mass", m.const_mass_kg, "KG0", m.const_moment_z_kgm/m.const_mass_kg)
print("gm_min", m.gm_min_m, "km_line", m.km_line)
for tid, cap, fsm, adj, z in zip(m.tank_ids, m.tank_capacity_kg, m.tank_fsm_max_kgm, m.tank_adjustable, m.tank_vcg_m):
    print(f"  tank {tid} cap {cap/1e3:.0f} t fsm_max {fsm/1e3:.1f} t.m adj {adj} vcg {z:.2f}")
kinds = {t.id: t.kind for t in inst.vessel.tanks}; print("kinds", kinds)
print("sum FSM_max t.m", m.tank_fsm_max_kgm.sum()/1e3, "-> GM reserve at band ends (m):",
      m.tank_fsm_max_kgm.sum()/m.band.displacement_min_kg, m.tank_fsm_max_kgm.sum()/m.band.displacement_max_kg)
g = geometry_for(inst); rho = m.water_density_kg_m3; a, b = m.km_line
print(" Delta(t)  T(m)  KM(m)  KMline(m)  gap(m)")
for D in np.linspace(m.band.displacement_min_kg, m.band.displacement_max_kg, 11):
    T = brentq(lambda t: g.submerged(t,t,0.,rho).displacement_kg-D, .01, g.max_z_m-.01)
    r = g.hydrostatic_row(T, rho)
    print(f" {D/1e3:8.0f} {T:5.3f} {r.kmt_m:7.3f} {(a+b*D)/D:7.3f} {r.kmt_m-(a+b*D)/D:7.4f}")
print("cells", len(m.cell_ids), "cell vcg range", m.cell_vcg_m.min(), m.cell_vcg_m.max())

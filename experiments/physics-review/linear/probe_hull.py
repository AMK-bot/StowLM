"""Hull facts relevant to the linear model: Delta at max draught, cargo in band, waterplane/LCF constancy."""
import runpy, sys
from pathlib import Path
import numpy as np
root = Path(__file__).resolve().parents[3]
helper = runpy.run_path(str(root/"experiments/linear-model/validate.py"))
from stowage.linear.physics import geometry_for
name = sys.argv[1]
inst = helper["fleet_instance"](name); h = inst.vessel.hull
g = geometry_for(inst); rho = inst.port.water_density_kg_m3
print(name, "Lpp", h.lpp_m, "B", h.beam_m, "max draught", h.max_draught_m, "lightship t", h.lightship_mass_kg/1e3)
print("load list n", len(inst.load_list), "mass t", sum(c.gross_mass_kg for c in inst.containers if c.id in set(inst.load_list))/1e3)
for T in np.linspace(1.5, h.max_draught_m, 8):
    r = g.hydrostatic_row(T, rho)
    print(f" T {T:5.2f} Disp {r.displacement_kg/1e3:8.0f} t  KM {r.kmt_m:6.2f}  LCF {r.lcf_m:7.2f} (Lpp/2 {h.lpp_m/2:.2f}) TPC {r.tpc_kg_per_cm/1e3:6.2f}")
tiers = sorted({c.id[-2:] for c in inst.vessel.cells}); print("tiers", tiers)

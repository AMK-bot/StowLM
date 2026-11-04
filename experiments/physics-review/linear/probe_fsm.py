"""Sum of per-tank max FSM / displacement at the reported validity-band ends, per fleet ship.
Compares with the GM 'max error' in experiments/linear-model/calibrated-errors.md."""
import runpy, json
from pathlib import Path
root = Path(__file__).resolve().parents[3]
helper = runpy.run_path(str(root/"experiments/linear-model/validate.py"))
res = json.loads((root/"experiments/linear-model/results.json").read_text())
for name in ("feeder_500", "feeder_1000", "feeder_1700"):
    v = helper["fleet_instance"](name).vessel
    fsm = sum(max(p.fsm_m4 for p in t.table)*t.content_density_kg_m3 for t in v.tanks)
    b = res[name]["band"]; err = res[name]["model_errors"]["gm_m"]
    print(f"{name}: sum FSM_max {fsm/1e3:.0f} t.m; reserve at band min {fsm/b['displacement_min_kg']:.3f} m, at max {fsm/b['displacement_max_kg']:.3f} m; reported GM error {err:.3f} m")

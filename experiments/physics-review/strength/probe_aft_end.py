"""Physics review 3.2: does an ordinary aft-bay load trip the near-AP SF limit (sin(pi u) -> 0)?

Run: UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/physics-review/strength/probe_aft_end.py
"""
import numpy as np
from stowage.checker import strength as S
from stowage.model import ShipState
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location('pc', pathlib.Path(__file__).with_name('probe_conditions.py'))
pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)

name = 'feeder_1000'; pc.NAME[:] = [name]
inst, _ = pc.make_instance(name)
L = inst.vessel.hull.lpp_m; vid = inst.vessel.id
cells = [c for c in inst.vessel.cells if c.accepts_20]
xmin = min(c.lcg_m for c in cells)
print('aftmost cell lcg', xmin, 'tanks aft of 0.15L:', [(t.id) for t in inst.vessel.tanks][:0])
for mass in (10_000, 20_000, 30_000):
    aft = [c.id for c in cells if c.lcg_m < xmin + 1]
    rest = [c.id for c in cells if c.lcg_m >= xmin + 1]
    pl = pc.load_boxes(inst, aft + rest, mass)
    # aft bay at `mass`, everything else at 10 t
    for b, p in zip(inst.containers, pl):
        if p.slot not in aft:
            object.__setattr__(b, 'gross_mass_kg', 10_000) if False else None
    inst.containers = [b.model_copy(update={'gross_mass_kg': mass if p.slot in aft else 10_000})
                       for b, p in zip(inst.containers, pl)]
    r = S.check_strength(inst, ShipState(vessel_id=vid, placements=pl), call_index=0)
    m = r.metrics
    worst = max(((s.x_m, m[f'sf_n@{format(s.x_m, ".12g")}'] / s.max_shear_n) for s in inst.vessel.hull.stations),
                key=lambda t: abs(t[1]))
    print(f'aft bay {mass/1e3:.0f} t/cell ({len(aft)} cells at x~{xmin:.1f}): trim={m["trim_m"]:+.2f} '
          f'max_sf_ratio={m["max_sf_ratio"]:.3f} worst at x={worst[0]:.2f} m (u={worst[0]/L:.3f}); '
          f'viol={[v.message[:60] for v in r.violations]}')

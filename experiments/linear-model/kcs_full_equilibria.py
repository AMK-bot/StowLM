"""Exact full-manifest corner diagnostics, without claiming feasibility proof."""
from pathlib import Path
import json
import runpy
import time
import numpy as np
import stowage.linear.physics as physics
import stowage.linear.bands as bands
from stowage.linear.cache import source_fingerprint
HERE=Path(__file__).resolve().parent
helpers=runpy.run_path(str(HERE/'narrow.py'))
class Captured(Exception): pass
results={}
for name in ('feeder_500','feeder_1000','feeder_1700'):
    start=time.perf_counter(); source=source_fingerprint(); captured=[]
    instance,manifest=helpers['cargo_instance'](name,'full')
    band=helpers['case_band'](instance,name,'full')
    original=bands.sample_manifest_loading
    def intercept(model,instance,tank,rng):
        captured.append(model)
        raise Captured()
    bands.sample_manifest_loading=intercept
    try:
        physics.build_model(instance,band,_samples=1)
    except Captured: pass
    finally: bands.sample_manifest_loading=original
    if not captured: raise RuntimeError('Expected uncalibrated model capture')
    model=captured[0]
    cell=np.zeros(len(model.cell_ids)); heights=model.cell_vcg_m.copy()
    index={cid:i for i,cid in enumerate(model.cell_ids)}
    for stack in instance.vessel.stacks:
        remaining=stack.max_mass_kg
        for depth,cid in enumerate(stack.cell_ids):
            mass=min(30_480.,remaining)
            if mass<=1e-7: break
            cell[index[cid]]=mass; heights[index[cid]]=stack.base_z_m+(depth+.5)*2.591
            remaining-=mass
        assert remaining<1e-6
    from collections import Counter
    from stowage.linear.bands import departure_manifest
    boxes=departure_manifest(instance)
    assert all(box.length_ft==20 and box.height_m==2.591 for box in boxes)
    assert Counter(round(box.gross_mass_kg,5) for box in boxes)==Counter(round(float(m),5) for m in cell if m)
    rows=[]
    for fraction in (0.,.5,1.):
        tank=np.where(model.tank_adjustable,model.tank_capacity_kg*fraction,0.)
        try:
            exact=physics.exact_condition(model,instance,cell,tank,cell_vcg_m=heights)
            rows.append({'ballast_fraction':fraction,**{key:exact[key] for key in ('displacement_kg','gm_m','trim_m','list_rad','draught_m')}})
        except Exception as error: rows.append({'ballast_fraction':fraction,'error':str(error)})
    results[name]={'manifest':manifest,'conditions':rows,'source_start':source,'source_end':source_fingerprint(),
                   'elapsed_seconds':time.perf_counter()-start,'qualification':'Three symmetric ballast corners are observations, not global feasibility or infeasibility proof.'}
    (HERE/'kcs'/'full-equilibria-standard-bottom.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps({name:results[name]}),flush=True)

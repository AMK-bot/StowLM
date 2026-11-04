"""Read current production qualification status without building any cache."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from stowage import cache_warm
from stowage.params import load_param_file
from stowage.checker import stability_tables, stability_liquid_tables
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.vessel.tanks import TankGeometry

rows=[]
for name in cache_warm.PRODUCTION_SHIPS:
    params=load_param_file('fleet',name)
    geometry=StabilityGeometry(params)
    kn={str(density):stability_tables.cached_geometry(geometry,params,density) is not None
        for density in (1025.,1000.)}
    solids={raw['id']:TankGeometry.from_params(raw,ship_params=params) for raw in params['tanks']}
    liquid={}
    for raw in params['tanks']:
        solid=solids[raw['id']]
        table=stability_liquid_tables.lookup(solid)
        liquid[raw['id']]=dict(qualified=table is not None,
            covers_required_domain=cache_warm._liquid_covers_domain(
                table,cache_warm._liquid_required_grid(raw,stability_liquid_tables)))
    manifest=cache_warm._manifest_valid(params,solids)
    row=dict(ship=name,kn_qualified=kn,liquid_tables=liquid,vessel_liquid_manifest_valid=manifest,
             complete=all(kn.values()) and manifest and all(x['qualified'] and x['covers_required_domain'] for x in liquid.values()))
    rows.append(row)
    print(name,'KN',all(kn.values()),'liquid_tables',sum(x['qualified'] for x in liquid.values()),'manifest',manifest,flush=True)
result=dict(complete=all(row['complete'] for row in rows),ships=rows,
    source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('src/stowage').rglob('*.py')})
Path('experiments/fleet-params/convergence-current-cache-status.json').write_text(json.dumps(result,indent=2)+'\n')
raise SystemExit(0 if result['complete'] else 2)

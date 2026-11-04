"""One normal-builder benchmark; no certification or manifest edits."""
import json
from pathlib import Path
import resource
import time
from stowage.vessel import load_ship_params
from stowage.vessel.tanks import TankGeometry
from stowage.cache_warm import _liquid_required_grid, _liquid_covers_domain
from stowage.checker import stability_liquid_tables as liquid

out = Path(__file__).parent
inventory = []
chosen = None
for name in ('feeder_500', 'feeder_800', 'feeder_1700'):
    params = load_ship_params(name)
    rows = []
    for raw in params.get('tanks', []):
        solid = TankGeometry.from_params(raw, ship_params=params)
        if getattr(solid, 'shape', None) != 'hull_sections':
            continue
        required = _liquid_required_grid(raw, liquid)
        table = liquid.lookup(solid)
        grid = liquid._grid_spec(**required)
        row = dict(tank=raw['id'], capacity_m3=solid.capacity_m3,
                   sections=len(solid.x_m), polygons=sum(len(s) for s in solid.sections_yz_m),
                   qualified_domain=_liquid_covers_domain(table, required),
                   grid_exists=liquid._path(solid, grid).exists(), path=str(liquid._path(solid, grid)))
        rows.append(row)
        if name == 'feeder_500' and raw['id'] == 'HEEL-P':
            chosen = solid, required, row
    inventory.append(dict(ship=name, tanks=rows))
(out / 'pre-shard-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
solid, required, row = chosen
if row['grid_exists']:
    raise RuntimeError('HEEL-P grid already exists; refuse misleading benchmark')
start = time.perf_counter()
table = liquid.build(solid, **required)
wall = time.perf_counter() - start
result = dict(ship='feeder_500', tank='HEEL-P', build_wall_s=wall,
              process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              path=row['path'], dimensions={k:len(v) for k,v in required.items()},
              builder_only=True, qualified=False)
(out / 'shard-measurement.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2), flush=True)

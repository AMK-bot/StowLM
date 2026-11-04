"""Combined documented sensitivity; no closure or acceptance-policy changes.

APPROXIMATION: count entire0.6m cover cuboids plus hypothetical2m forecastle
in front10%L. Retain all mass, liquid transfers, windage and vent apertures.
Not a proposed replacement geometry or full-checker acceptance witness.
"""
import hashlib,json,resource,time
from datetime import datetime,timezone
from pathlib import Path
from feeder_envelope import (ROOT,OUT,Instance,load_ship_params,StabilityGeometry,
 geometry_variant,evaluate,derive_arrival_liquid_state,source_hashes)
from historical_fixture import read_historical_fixture,require_fixture_hash
start=time.perf_counter()
i,identity=read_historical_fixture(ROOT,Instance)
extra=[identity['source_path'],str(Path(__file__).resolve().relative_to(ROOT)),
       str((Path(__file__).resolve().parent/'historical_fixture.py').relative_to(ROOT))]
before=source_hashes('feeder_500',extra_paths=extra)
require_fixture_hash(before,identity)
p=load_ship_params('feeder_500')
native=StabilityGeometry(p)
g=geometry_variant(geometry_variant(native,p,'sealed_cover_0p6'),p,'forecastle_2m')
liquid=derive_arrival_liquid_state(i.onboard,i.voyage,vessel=i.vessel)
arrival=i.onboard.model_copy(update={'tank_masses_kg':dict(liquid.tank_masses_kg)})
rows=[]
for phase,ship in [('departure',i.onboard),('arrival',arrival)]:
 base,c,wind=evaluate(i,ship,p,native)
 row,_,_=evaluate(i,ship,p,g,c,wind)
 row.update(phase=phase,variant='sealed_cover_0p6_and_forecastle_2m',
  delta_total_area_mrad=row['metrics']['area_0_flood_mrad']-base['metrics']['area_0_flood_mrad'])
 rows.append(row);print(phase,row['b_total_area_ratio'],row['flooding_deg'],flush=True)
after=source_hashes('feeder_500',extra_paths=extra)
require_fixture_hash(after,identity)
if before!=after:raise RuntimeError('Source changed during historical combined solve')
r=dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),rows=rows,**identity,
 source_hashes_before=before,source_hashes_after=after,source_stable=True,
 own_script_sha256=before[str(Path(__file__).resolve().relative_to(ROOT))],
 elapsed_s=time.perf_counter()-start,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
(OUT/'feeder_500-historical-combined.json').write_text(json.dumps(r,indent=2,default=float)+'\n')
print('elapsed',r['elapsed_s'],'rss',r['peak_rss_bytes'])

"""Profile only a changed D29 background after unprofiled family warm-up."""
import cProfile
import json
from pathlib import Path
import resource
import time
from stowage.linear import build_linear_model
from stowage.linear.bands import band_for_instance, departure_manifest
from stowage.linear import physics, family_calibration
from stowage.linear.cache import source_fingerprint
from stowage.model.fixtures import build_instance
from stowage.model import ShipState
from stowage.vessel import load_fleet_preset, recondition_slice, departure_tank_masses
out=Path(__file__).parent
before=source_fingerprint()
base=build_instance();vessel=load_fleet_preset('feeder_500','tiny',with_hull=True)
base=base.model_copy(update={'vessel':vessel,'onboard':ShipState(vessel_id=vessel.id),
 'port':base.port.model_copy(update={'max_draught_m':vessel.hull.max_draught_m})})
cargo=sum(box.gross_mass_kg for box in departure_manifest(base))
def variant(index):
 v=recondition_slice(vessel,expected_load_mass_kg=cargo*(.7+.06*index),seed=3700+index)
 return base.model_copy(update={'id':f'warm-profile-{index}','vessel':v,
  'onboard':ShipState(vessel_id=v.id,tank_masses_kg=departure_tank_masses(v))})
first,current=variant(0),variant(1)
start=time.perf_counter();warm=build_linear_model(first,band=band_for_instance(first));warm_seconds=time.perf_counter()-start
physics._CACHE.clear()
def forbidden(*args,**kwargs):raise AssertionError('attempted instance recalibration')
family_calibration.calibrate_family=forbidden
profile=cProfile.Profile();cpu=time.process_time();start=time.perf_counter()
profile.enable();model=build_linear_model(current,band=band_for_instance(current));profile.disable()
wall=time.perf_counter()-start;cpu=time.process_time()-cpu
profile.dump_stats(str(out/'d29-warm-only.pstats'))
assert model.measurement_diagnostics['family_calibration_cache_key']==warm.measurement_diagnostics['family_calibration_cache_key']
result=dict(warm_seconds=warm_seconds,wall_seconds=wall,cpu_seconds=cpu,source_clean=source_fingerprint()==before,
 peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2)
(out/'d29-warm-only.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

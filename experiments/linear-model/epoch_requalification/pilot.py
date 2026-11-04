"""Two-state timing pilot only; never production accuracy evidence."""
import argparse
import json
from pathlib import Path
import resource
import time

from stowage.linear.warm import _empty_context
from stowage.linear.physics import _prepared_params_for
from stowage.linear.reusable import coefficient_family
from stowage.linear.family_calibration import calibrated_family

p=argparse.ArgumentParser();p.add_argument('--ship',default='feeder_500');a=p.parse_args()
start=time.perf_counter();instance=_empty_context(a.ship);loaded=time.perf_counter()
_,params=_prepared_params_for(instance)
family=coefficient_family(instance,params);coeff=time.perf_counter()
errors,diagnostics=calibrated_family(family,instance,samples=2,force_rebuild=True)
end=time.perf_counter()
result=dict(ship=a.ship,samples=2,accuracy_qualification=False,
    vessel_seconds=loaded-start,coefficient_seconds=coeff-loaded,
    calibration_seconds=end-coeff,total_seconds=end-start,
    attempts=diagnostics['attempts'],peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
    reference_qualified=diagnostics['reference_inputs'].get('qualified'),
    response=diagnostics['response_calibration_identity'])
Path(__file__).with_name('pilot-'+a.ship+'.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)

"""Bounded feasibility sampling for full manifests; failure is not a proof."""
from __future__ import annotations
import json
import argparse
from pathlib import Path
import resource
import runpy
import time
import stowage.linear.bands as bands
import stowage.linear.physics as physics
from stowage.linear.cache import source_fingerprint
HERE=Path(__file__).resolve().parent
helpers=runpy.run_path(str(HERE/'narrow.py'))
parser=argparse.ArgumentParser()
parser.add_argument('--ship',choices=['feeder_500','feeder_1000','feeder_1700'])
parser.add_argument('--output',type=Path,default=HERE/'kcs'/'full-probes-standard-bottom.json')
args=parser.parse_args()
output=args.output
results={}
for name in ('feeder_500','feeder_1000','feeder_1700'):
    if args.ship and args.ship!=name: continue
    begin=time.perf_counter(); source=source_fingerprint(); count=[0]
    original=bands.sample_manifest_loading
    def bounded(*args,**kwargs):
        count[0]+=1
        if count[0]>48: raise RuntimeError('experiment cap48physical-layout attempts reached; not an infeasibility proof')
        return original(*args,**kwargs)
    bands.sample_manifest_loading=bounded
    record={'source_start':source}
    try:
        instance,manifest=helpers['cargo_instance'](name,'full')
        band=helpers['case_band'](instance,name,'full')
        model=physics.build_model(instance,band,_samples=1)
        errors,diagnostics=physics.measure_errors(model,instance,samples=4,seed=3712)
        record.update(status='accepted',manifest=manifest,diagnostics=diagnostics)
    except Exception as error:
        record.update(status='limited_probe_failed',error=f'{type(error).__name__}: {error}')
    finally:
        bands.sample_manifest_loading=original
    record.update(attempts=count[0],elapsed_seconds=time.perf_counter()-begin,
                  peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
                  source_end=source_fingerprint())
    record['source_clean']=record['source_start']==record['source_end']
    results[name]=record
    output.parent.mkdir(exist_ok=True,parents=True)
    output.write_text(json.dumps(helpers['_HELPERS']['serializable'](results),indent=2)+'\n')
    print(json.dumps({k:v for k,v in record.items() if k!='diagnostics'}),flush=True)

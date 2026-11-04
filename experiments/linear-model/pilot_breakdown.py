"""Separate upright KM interpolation error from free-trim KM changes."""
from __future__ import annotations
import argparse
from dataclasses import replace
import json
from pathlib import Path
import runpy
import time

from scipy.optimize import brentq
import stowage.linear.physics as physics
from stowage.linear.cache import source_fingerprint

HERE=Path(__file__).resolve().parent
helpers=runpy.run_path(str(HERE/'narrow.py'))
p=argparse.ArgumentParser()
p.add_argument('--ship',default='feeder_1700')
p.add_argument('--level',default='medium')
p.add_argument('--trim',type=float)
p.add_argument('--samples',type=int,default=4)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
start=time.perf_counter(); source=source_fingerprint()
instance,manifest=helpers['cargo_instance'](a.ship,a.level)
band=helpers['case_band'](instance,a.ship,a.level)
if a.trim is not None:
    original=band
    band=replace(band,max_abs_trim_m=a.trim)
    for key,value in vars(original).items():
        if key.startswith('_'): object.__setattr__(band,key,value)
model=physics.build_model(instance,band,_samples=1)
geometry=physics.geometry_for(instance)
rho=instance.port.water_density_kg_m3
records=[]; original=physics.exact_condition

def wrapped(*args,**kwargs):
    exact=original(*args,**kwargs)
    if (band.displacement_min_kg<=exact['displacement_kg']<=band.displacement_max_kg
        and band.draught_min_m<=exact['draught_m']<=band.draught_max_m
        and abs(exact['trim_m'])<=band.max_abs_trim_m and abs(exact['list_rad'])<=band.max_abs_heel_rad):
        mass=exact['displacement_kg']
        draught=brentq(lambda d:geometry.submerged(d,d,0.,rho).displacement_kg-mass,.001,geometry.max_z_m-.001)
        wet=geometry.submerged(draught,draught,0.,rho)
        even=wet.vcb_m+wet.bm_t_m
        linear=(model.km_line[0]+model.km_line[1]*mass)/mass
        approx=physics._linear_arrays(model,args[2],args[3],instance=instance)
        records.append({'displacement_kg':mass,'trim_m':exact['trim_m'],'list_rad':exact['list_rad'],
            'even_keel_km_m':even,'free_trim_km_m':exact['km_m'],'linear_km_m':linear,
            'linear_minus_even_keel_m':linear-even,'even_keel_minus_free_trim_m':even-exact['km_m'],
            'point_gm_error_m':approx['gm_m']-exact['gm_m']})
    return exact
physics.exact_condition=wrapped
try: errors,diagnostics=physics.measure_errors(model,instance,samples=a.samples,seed=3712)
finally: physics.exact_condition=original
result={'ship':a.ship,'level':a.level,'trim_band_m':band.max_abs_trim_m,'manifest':manifest,
        'samples':records,'diagnostics':diagnostics,'source_start':source,'source_end':source_fingerprint(),
        'elapsed_seconds':time.perf_counter()-start}
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps(helpers['_HELPERS']['serializable'](result),indent=2)+'\n')
print(json.dumps({'samples':records,'elapsed_seconds':result['elapsed_seconds'],'source_clean':result['source_start']==result['source_end']}),flush=True)

"""Small exact-geometry contrast of deep-mass curvature and a 98% tank branch.

Actual fill geometry and coupled equilibrium remain unchanged. The experiment
rebalances a synthetic dry point to hold exact trim/list fixed while changing
mass or a single tank fill, using the qualified edge sampler's original roots.
"""
import importlib.util
import json
from pathlib import Path
import resource
import time

import numpy as np
from stowage.model import Instance
from stowage.linear.reusable import coefficient_family
from stowage.checker.stability_geometry import StabilityGeometry

HERE=Path(__file__).resolve().parent
OUT=HERE/'claude-edge-cause.json'
if OUT.exists(): raise ValueError('preserve existing diagnosis')
fixture=HERE/'claude-review-v2/feeder_500'
review=json.loads((fixture/'necessary-review.json').read_text())
observed=review['states'][0]
instance=Instance.model_validate_json((fixture/'frozen-instance.json').read_text())
params=json.loads((fixture/'frozen-parameters.json').read_text())
family=coefficient_family(instance,params)
spec=importlib.util.spec_from_file_location('claude_edge_cause_sampler',HERE.parent/'edge_grid.py')
edge=importlib.util.module_from_spec(spec);spec.loader.exec_module(edge)
driver=edge.load_driver()
before=driver.snapshot('feeder_500',params)
assert before==review['physical_inputs_start']
domain=review['domain'];band=family.template.band
geometry=StabilityGeometry(params)
fills=np.asarray(observed['all_tank_masses_kg'])/family.template.tank_capacity_kg
base=dict(mass_fraction=(observed['mass_kg']-band.displacement_min_kg)/(band.displacement_max_kg-band.displacement_min_kg),
    trim_fraction=observed['exact_trim_m']/band.max_abs_trim_m,
    heel_fraction=observed['exact_list_rad']/band.max_abs_heel_rad,
    dry_kg_fraction=(observed['fixed_point']['z_m']-domain['kg_min_m'])/(domain['kg_max_m']-domain['kg_min_m']),
    explicit_fills=fills.tolist(),fill_pattern='explicit-frozen-physical-fills')
# Isolated sampler hook chooses only requested physical tank fractions. It
# does not change any checker/root/model or any acceptance/domain condition.
edge.tank_fractions=lambda request,count:np.asarray(request['explicit_fills'])
requests=[]
for fraction in (.90,.95,base['mass_fraction'],1.):
    requests.append(dict(base,state_id=f'mass-{fraction:.12g}',contrast='mass',mass_fraction=fraction))
selected=int(np.argmax(family.template.tank_capacity_kg*family.template.tank_adjustable))
for fraction in (.98-1e-6,.98,.98+1e-6):
    variant=fills.copy();variant[selected]=fraction
    requests.append(dict(base,state_id=f'fill-{fraction:.12g}',contrast='fill',explicit_fills=variant.tolist()))
started=time.monotonic();states=[]
for request in requests:
    row=edge.evaluate(request,family,instance,geometry,domain)
    if row['admitted']:
        row['mean_residual_m']=row['affine_draught_m']-row['exact_draught_m']
        row['aft_residual_m']=row['mean_residual_m']+.5*(row['affine_trim_m']-row['exact_trim_m'])
    states.append(row)
    print(json.dumps({'state':row['state_id'],'admitted':row['admitted'],
        'mean_residual_m':row.get('mean_residual_m'),'aft_residual_m':row.get('aft_residual_m')}),flush=True)
after=driver.snapshot('feeder_500',params)
assert before==after
result=dict(states=states,physical_inputs_start=before,physical_inputs_end=after,
    selected_tank=family.template.tank_ids[selected],
    sampler_source=edge.pin(edge.__file__),diagnostic_source=edge.pin(__file__),
    source_review=edge.pin(fixture/'necessary-review.json'),
    seconds=time.monotonic()-started,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    warning='Seven controlled synthetic physical-domain contrasts; no universal bound or legal-cargo feasibility claim')
driver.write(OUT,result)
print(json.dumps({'seconds':result['seconds'],'selected_tank':result['selected_tank']}),flush=True)

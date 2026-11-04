"""Freshly re-evaluate one saved reviewer probe point as training regression.

This recomputes the saved physical fixed load and tank vectors on the current
full template. The original observation remains pinned and untouched; measured
quantities are neither copied into a new epoch nor labelled independent holdout.
"""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-state', type=Path, required=True)
parser.add_argument('--prepared', type=Path, default=HERE/'ordinary')
parser.add_argument('--output', type=Path, default=HERE/'claude-review-v2')
parser.add_argument('--frozen-instance', type=Path, required=True,
                    help='immutable full instance from prior exact replay; reuse fixture, never numerical observations')
args = parser.parse_args()
output = args.output.resolve()
if output == HERE or not output.is_relative_to(HERE) or not output.name.startswith('claude-'):
    parser.error('dedicated claude-prefixed output required')
path = output/'feeder_500/necessary-review.json'
if path.exists(): parser.error('never overwrite frozen review evidence')

def pin(path):
    path = Path(path).resolve()
    return {'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()

source = pin(args.source_state)
original = json.loads(args.source_state.read_text())
spec = importlib.util.spec_from_file_location('claude_review_driver', HERE.parent/'driver.py')
driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
from stowage.linear.physics import exact_condition, _linear_arrays, _prepared_params_for
from stowage.linear.reusable import assemble_instance, coefficient_family
from stowage.linear.family_calibration import _validated_errors, _validate_calibration_certificate, domain_for
from stowage.linear.cache import family_calibration_key, response_calibration_identity
from stowage.model import FixedLoad, ShipState, Instance
started = time.monotonic()
fixture_pin = pin(args.frozen_instance)
fixture_data = json.loads(args.frozen_instance.read_text())
# The synthetic reference removed all decision containers. Clear their inert
# yard inventory too so the self-contained fixture passes the Instance schema;
# no ship load, tank, hull or numerical quantity changes.
fixture_data['yard']['stacks'] = []
fixture_data['yard']['quay_buffer'] = []
instance = Instance.model_validate(fixture_data)
_, parameters = _prepared_params_for(instance)
family = coefficient_family(instance,parameters)
before = driver.snapshot('feeder_500',parameters)
raw = json.loads((args.prepared/'feeder_500/raw.json').read_text())
if before != raw['physical_inputs']:
    raise ValueError('frozen fixture numerical epoch differs from fresh raw calibration')
diagnostics = raw['diagnostics']
if diagnostics['coefficient_family_cache_key'] != family.cache_key:
    raise ValueError('frozen fixture changed full coefficient family')
if diagnostics['response_calibration_identity'] != response_calibration_identity(family):
    raise ValueError('frozen fixture response identity differs from raw calibration')
key = family_calibration_key(family,before['reference'],domain_for(family,instance),samples=192)
if key != raw['key']:
    raise ValueError('frozen fixture calibration domain differs from fresh raw')
errors = _validated_errors(raw['errors'],len(family.template.station_x_m))
_validate_calibration_certificate(diagnostics['calibration_certificate'],key,errors,diagnostics)
tanks = sorted(instance.vessel.tanks,key=lambda tank:tank.id)
if [tank.id for tank in tanks] != original['tank_ids']:
    raise ValueError('saved tank order differs from current full template')
values = np.asarray(original['all_tank_masses_kg'])
point = {key: original['point_load'][key] for key in ('x_m','y_m','z_m','mass_kg')}
onboard = ShipState(vessel_id=instance.vessel.id,tank_masses_kg={tank.id:float(value)
    for tank,value in zip(tanks,values,strict=True) if not tank.adjustable})
state = instance.model_copy(update={'containers':[],'load_list':[],'discharge_list':[],
    'onboard':onboard,'vessel':instance.vessel.model_copy(update={'fixed_loads':[FixedLoad(**point)]})})
model = assemble_instance(family,state,family.template.band,errors=errors,
    diagnostics={'calibration_synthetic':True,'family_domain':diagnostics['family_domain']})
cells = np.zeros(len(model.cell_ids)); ballast = values*model.tank_adjustable
exact = exact_condition(model,state,cells,ballast,cell_vcg_m=model.cell_vcg_m)
affine = _linear_arrays(model,cells,ballast,instance=state,solver=True)
differences = {}
for name in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm'):
    delta = float(np.max(np.abs(np.asarray(exact[name])-original['exact'][name])))
    differences[name] = delta
    if not np.allclose(exact[name],original['exact'][name],rtol=1e-9,atol=1e-5):
        raise ValueError('full-template physical replay changed '+name)
domain,band = diagnostics['family_domain'],model.band
admitted = (domain['fixed_x_min_m']<=point['x_m']<=domain['fixed_x_max_m']
    and abs(point['y_m'])<=domain['fixed_y_abs_max_m']
    and domain['kg_min_m']<=point['z_m']<=domain['kg_max_m']
    and band.displacement_min_kg<=exact['displacement_kg']<=band.displacement_max_kg
    and band.draught_min_m<=exact['draught_m']<=band.draught_max_m
    and abs(exact['trim_m'])<=band.max_abs_trim_m and abs(exact['list_rad'])<=band.max_abs_heel_rad)
if not admitted: raise ValueError('review point is outside admitted full family domain')
row = dict(state_id='claude-review-'+digest(point)[:16],admitted=True,qualified_domain=True,
    fixed_point=point,mass_kg=float(exact['displacement_kg']),
    affine_restoring_kgm=float(exact['displacement_kg']*affine['gm_m']),
    transverse_moment_kgm=float(model.const_moment_y_kgm+model.tank_tcg_m@ballast),
    cell_masses_kg=cells.tolist(),tank_masses_kg=ballast.tolist(),
    all_tank_masses_kg=values.tolist(),cell_vcg_m=model.cell_vcg_m.tolist())
for name in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm'):
    row['exact_'+name]=np.asarray(exact[name]).tolist();row['affine_'+name]=np.asarray(affine[name]).tolist()
source_script = pin(__file__)
config = dict(ship='feeder_500',level='family',phase='train',seed=54939,samples=1,
    construction='fresh exact replay of immutable reviewer synthetic point/tank vectors',
    source_observation=source,affine_feasibility_filter=False,
    required_strata=['review-regression'],frozen_full_instance=fixture_pin,
    fixture_transformation='clear orphaned yard inventory after synthetic reference removes all decision containers; physics unchanged')
identity = dict(physical_inputs=before,config=config,builder_version='1.0',source_sha256=source_script['sha256'])
after = driver.snapshot('feeder_500',family.parameters)
if after != before or pin(args.source_state)!=source or pin(args.frozen_instance)!=fixture_pin:
    raise ValueError('physical epoch or source observation changed during replay')
record = dict(format=1,kind='necessary-domain-edges-v1',ship='feeder_500',level='family',
    phase='train',role='review-regression',seed=54939,complete=True,samples=1,attempts=1,
    states=[row],list_row_samples=[row],physical_inputs_start=before,physical_inputs_end=after,
    physical_inputs_unchanged=True,band=asdict(band),domain=domain,
    station_x_m=model.station_x_m.tolist(),calibration=diagnostics,
    sampler_builder_version='1.0',sampler_source_sha256=source_script['sha256'],
    sampler_source=source_script,sampler_config=config,sampler_inputs_hash=digest(identity),
    reference_transfer_source=pin(args.prepared/'feeder_500/train-light.json'),
    coverage=dict(required_strata=['review-regression'],strata={'review-regression':
        dict(requested=1,evaluated=1,retained=1)}),
    original_exact_replay_max_differences=differences,
    old_reserves=original['old_reserves'],original_probe_pin=source,
    seconds=time.monotonic()-started,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    warning='Training regression only; not independent holdout or full-checker/legal-cargo approval')
driver.write(path,record)
driver.write(output/'feeder_500/frozen-instance.json',state.model_dump(mode='json'))
driver.write(output/'feeder_500/frozen-parameters.json',family.parameters)
driver.write(output/'feeder_500/frozen-numeric-model.json',asdict(model))
print(json.dumps({'review':str(path),'seconds':record['seconds'],'admitted':True,
    'mean_residual_m':affine['draught_m']-exact['draught_m'],
    'old_mean_reserve_m':original['old_reserves']['upper_unsafe']['draught_m']}),flush=True)

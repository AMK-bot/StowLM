"""Benchmark D29 background changes with a shared calibrated coefficient family.

Sample1 mode is a PRIVATE timing pilot, never production accuracy evidence.
Sample192 mode uses the unmodified public builder. After warming, any attempted
calibration is an error. D29 geometry/instance construction is timed separately;
per-instance latency includes band selection and assembly of all constants.
"""
from __future__ import annotations
import argparse
from dataclasses import fields,is_dataclass
import hashlib
import json
import os
from pathlib import Path
import resource
import runpy
import time

def _before_import_source():
    package=Path(__file__).resolve().parents[2]/'src/stowage'
    paths=set()
    for area in ('linear','checker','vessel','model','rules'):paths.update((package/area).rglob('*.py'))
    paths.update((package/'rules').rglob('*.toml'))
    paths.update(package/name for name in ('versions.py','params.py','conventions.py'))
    digest=hashlib.sha256()
    for path in sorted(paths):digest.update(str(path.relative_to(package)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()

_IMPORT_SOURCE=_before_import_source()
import numpy as np
from stowage.linear.cache import source_fingerprint
from stowage.linear.model import build_linear_model,linear_constraints
import stowage.linear.physics as physics
import stowage.linear.family_calibration as calibration
from stowage.linear.bands import band_for_instance,departure_manifest
from stowage.linear.reusable import coefficient_family,assemble_instance
from stowage.model.fixtures import build_instance
from stowage.model import ShipState
from stowage.vessel import load_fleet_preset,recondition_slice,departure_tank_masses
HERE=Path(__file__).resolve().parent

def digest(value):
    hashed=hashlib.sha256()
    def visit(item):
        if isinstance(item,np.ndarray):
            a=np.ascontiguousarray(item);hashed.update(str((a.dtype.str,a.shape)).encode());hashed.update(a.tobytes())
        elif is_dataclass(item):
            for f in fields(item):hashed.update(f.name.encode());visit(getattr(item,f.name))
        elif isinstance(item,dict):
            for key in sorted(item):hashed.update(str(key).encode());visit(item[key])
        elif isinstance(item,(tuple,list)):
            for part in item:visit(part)
        else:hashed.update(json.dumps(item,sort_keys=True,default=str).encode())
    visit(value);return hashed.hexdigest()

def main():
 p=argparse.ArgumentParser();p.add_argument('--samples',type=int,choices=(1,192),default=1);p.add_argument('--variants',type=int,default=10);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 helper=runpy.run_path(str(HERE/'narrow.py'));source=source_fingerprint();assert source==_IMPORT_SOURCE,'source changed during imports; retry fresh process'
 files_start=helper['source_file_hashes']();inputs_start=helper['input_snapshot']('feeder_500');started=time.perf_counter()
 base=build_instance();vessel=load_fleet_preset('feeder_500','tiny',with_hull=True)
 base=base.model_copy(update={'vessel':vessel,'onboard':ShipState(vessel_id=vessel.id),
       'port':base.port.model_copy(update={'max_draught_m':vessel.hull.max_draught_m})})
 cargo=sum(box.gross_mass_kg for box in departure_manifest(base));variants=[]
 for index in range(a.variants):
  expected=cargo*(.7+.06*index)
  v=recondition_slice(vessel,expected_load_mass_kg=expected,seed=3700+index)
  instance=base.model_copy(update={'id':f'linear-d29-bench-{index}', 'vessel':v,
       'onboard':ShipState(vessel_id=v.id,tank_masses_kg=departure_tank_masses(v))})
  variants.append((instance,expected))
 context_seconds=time.perf_counter()-started
 def build(instance,band):
  return physics.build_model(instance,band,_samples=1) if a.samples==1 else build_linear_model(instance,band=band)
 first=variants[0][0];warm_started=time.perf_counter();warm=build(first,band_for_instance(first));warm_seconds=time.perf_counter()-warm_started
 assert warm.measurement_diagnostics['family_calibration_samples']==a.samples
 calkey=warm.measurement_diagnostics['family_calibration_cache_key'];coeffkey=warm.measurement_diagnostics['coefficient_family_cache_key']
 original_calibrate=calibration.calibrate_family
 def forbidden(*args,**kwargs):raise AssertionError('background variant attempted family recalibration')
 calibration.calibrate_family=forbidden
 rows=[];models=[]
 try:
  for index,(instance,expected) in enumerate(variants):
   physics._CACHE.clear();begin=time.perf_counter();band=band_for_instance(instance);band_finished=time.perf_counter();model=build(instance,band);elapsed=time.perf_counter()-begin
   assert model.measurement_diagnostics['family_calibration_cache_key']==calkey
   assert model.measurement_diagnostics['coefficient_family_cache_key']==coeffkey
   loads=np.asarray([tuple(load) for load in instance.vessel.fixed_loads]);mass=float(loads[:,3].sum()) if len(loads) else 0.
   moments=(loads[:,:3]*loads[:,3,None]).sum(axis=0).tolist() if len(loads) else [0.,0.,0.]
   rows.append({'index':index,'expected_load_mass_kg':expected,'fixed_mass_kg':mass,'fixed_moments_kgm':moments,
       'band_seconds':band_finished-begin,'assembly_seconds':elapsed-(band_finished-begin),
       'band_and_assembly_seconds':elapsed,'model_public_fields_sha256':digest(model)})
   models.append((instance,band,model))
  assert len({(r['fixed_mass_kg'],*r['fixed_moments_kgm']) for r in rows})==a.variants,'background variants were not distinct'
  instance,band,model=models[-1];begin=time.perf_counter();fresh=coefficient_family(instance,force_rebuild=True)
  errors,diagnostics=calibration.calibrated_family(fresh,instance,samples=a.samples)
  assembled=assemble_instance(fresh,instance,band,errors=errors,diagnostics=diagnostics)
  assert digest(model)==digest(assembled),'fresh coefficient model fields differ'
  assert np.array_equal(model._constraint_cell_vcg_m,assembled._constraint_cell_vcg_m)
  assert digest(linear_constraints(model,margin_scale=2.))==digest(linear_constraints(assembled,margin_scale=2.))
  comparison_seconds=time.perf_counter()-begin
 finally:calibration.calibrate_family=original_calibrate
 end_source=source_fingerprint();result={'status':'passed','api':'private_samples1_pilot' if a.samples==1 else 'unmodified_public192',
  'calibration_samples':a.samples,'production_calibration_sample_count':a.samples==192,
  'independent_accuracy_validation':False,'reference_free_surface_method':os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1'),
  'explicit_direct_reference':os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT')=='1','reference_inputs':warm.measurement_diagnostics.get('reference_inputs'),
  'source_start':source,'source_end':end_source,'source_clean':source==end_source and helper['source_file_hashes']()==files_start and helper['input_snapshot']('feeder_500')==inputs_start,
  'source_file_hashes_start':files_start,'input_hashes_start':inputs_start,'context_and_variant_construction_seconds':context_seconds,
  'initial_family_build_seconds':warm_seconds,'family_calibration_cache_key':calkey,'coefficient_family_cache_key':coeffkey,
  'family_domain':warm.measurement_diagnostics.get('family_domain'),'family_calibration_band':warm.measurement_diagnostics.get('family_calibration_band'),
  'family_calibration_attempts':warm.measurement_diagnostics.get('attempts'),'family_calibration_liquid_usage':warm.measurement_diagnostics.get('liquid_usage'),
  'signed_errors':warm.measurement_diagnostics.get('signed_errors'),'variants':rows,'all_variant_latency_below_one_second':all(r['band_and_assembly_seconds']<=1 for r in rows),
  'forced_coefficient_equality_seconds':comparison_seconds,'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
  'elapsed_seconds':time.perf_counter()-started}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:result[k] for k in ('status','api','source_clean','context_and_variant_construction_seconds','initial_family_build_seconds','all_variant_latency_below_one_second','forced_coefficient_equality_seconds','peak_rss_mib','elapsed_seconds')}),flush=True)
 print(json.dumps({'variant_seconds':[r['band_and_assembly_seconds'] for r in rows]}),flush=True)
 assert result['source_clean'],'source/input drift retained; not current benchmark'

if __name__=='__main__':main()

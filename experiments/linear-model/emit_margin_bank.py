"""Transfer pinned measurements only after coefficient/grid numerical equality.

This is an offline qualification tool, never a normal model-build fallback.
It writes only linear-owned evidence/cache/policy data. It does not modify
stability assets or turn empirical budgets into a uniform safety proof.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,runpy
from dataclasses import fields
from pathlib import Path
import numpy as np
from stowage.linear.cache import _canonical
HERE=Path(__file__).resolve().parent
METRICS=('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm','draught_aft_m','draught_forward_m')
SIDES=('lower_unsafe','upper_unsafe')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
def numeric(path):
 with np.load(path,allow_pickle=False) as saved:
  return {key:np.asarray(saved[key]) for key in saved.files if key not in ('metadata','provenance_json')}
def equal(first,second):
 assert set(first)==set(second),'numeric field inventory differs'
 for key in first:
  assert np.array_equal(np.asarray(first[key]),np.asarray(second[key])),f'numeric field differs: {key}'
def gap_arrays(record):
 result={side:{} for side in SIDES};c=record.get('public_checker_comparison')
 for metric in METRICS:
  zero=np.zeros(len(record['station_x_m'])) if metric in ('sf_n','bm_nm') else np.asarray(0.)
  if not c:core=public=zero
  elif metric in ('sf_n','bm_nm'):
   core=np.asarray(c['exact_condition'][metric]);public=np.asarray([c['strength_metrics'][f'{metric}@{float(x):.12g}'] for x in record['station_x_m']])
  elif metric in ('draught_aft_m','draught_forward_m'):
   sign=1 if metric=='draught_aft_m' else -1
   core=np.asarray(c['exact_condition']['draught_m']+sign*.5*c['exact_condition']['trim_m'])
   public=np.asarray(c['stability_metrics']['draught_aft_m' if sign==1 else 'draught_fwd_m'])
  else:
   core=np.asarray(c['exact_condition'][metric]);public=np.asarray(c['stability_metrics']['draught_mean_m' if metric=='draught_m' else metric])
  result['lower_unsafe'][metric]=np.maximum(core-public,0)
  result['upper_unsafe'][metric]=np.maximum(public-core,0)
 return result

def historical_model(key):
 """Read checksummed inert legacy data without retrofitting its old cache entry."""
 from stowage.linear.cache import load_model,load_payload
 model=load_model(key)
 if model is not None:return model
 payload=load_payload(key,namespace='model')
 if payload is None:
  # Only this offline verifier can rewrap the old inert envelope. The
  # production reader still performs archive/dtype/size/checksum checks.
  import re,shutil,tempfile
  from stowage.linear import cache
  assert re.fullmatch(r'[0-9a-f]{64}',key)
  folder=cache._DEFAULT_ROOT/'model';manifestpath=folder/(key+'.json')
  assert manifestpath.stat().st_size<=16*1024*1024
  manifest=json.loads(manifestpath.read_text());checksum=manifest['sha256']
  assert manifest['format']==1 and manifest['key']==key and re.fullmatch(r'[0-9a-f]{64}',checksum)
  assert manifest['file']==key+'.'+checksum+'.npz'
  assert hashlib.sha256(_canonical(manifest['payload'])).hexdigest()==manifest['payload_sha256']
  archived=folder/manifest['file'];assert archived.stat().st_size<=cache._MAX_BYTES and sha(archived)==checksum
  with tempfile.TemporaryDirectory(prefix='linear-inert-legacy-') as temporary:
   target=Path(temporary)/'model';target.mkdir()
   shutil.copyfile(archived,target/manifest['file'])
   manifest['format']=cache._FORMAT
   (target/(key+'.json')).write_text(json.dumps(manifest))
   payload=load_payload(key,namespace='model',root=temporary)
 assert payload is not None,'historical inert payload failed guarded format rewrap'
 from stowage.linear.model import LinearModel,LinearErrors,ValidityBand
 data=payload['fields'];data['band']=ValidityBand(**data['band']);data['errors']=LinearErrors(**data['errors'])
 for name in ('cell_ids','tank_ids','km_line','trim_line','draught_line'):data[name]=tuple(data[name])
 model=LinearModel(**data)
 for name,value in payload.get('metadata',{}).items():object.__setattr__(model,name,value)
 return model


def emit_ship(ship,records,directory,*,migrate=False):
 from stowage.linear.reusable import coefficient_family,_ARRAYS
 from stowage.linear.family_calibration import reference_inputs,reference_cache_identity,domain_for,_calibration_certificate,_validate_calibration_certificate
 from stowage.linear.cache import load_model,response_calibration_identity,family_calibration_key,store_payload,source_fingerprint
 helper=runpy.run_path(str(HERE/'narrow.py'))
 original=records[ship+'/full'];record=json.loads(Path(original['original_record_path']).read_text())
 assert helper['input_snapshot'](ship)==record['input_hashes_start'],'raw production inputs changed'
 for key in ('light','medium','full'):
  item=records[ship+'/'+key];assert sha(item['original_record_path'])==item['original_record_sha256']
  prior=json.loads(Path(item['original_record_path']).read_text())
  assert helper['input_snapshot'](ship)==prior['input_hashes_start']
  for path,digest in prior['liquid_table_inputs_start']['files'].items():assert sha(path)==digest,'original reference asset changed'
 instance,_=helper['cargo_instance'](ship,'full')
 fresh=coefficient_family(instance,force_rebuild=True)
 old=historical_model(record['calibration']['coefficient_family_cache_key']);assert old is not None
 equal({field.name:getattr(old,field.name) for field in fields(old) if field.name not in ('version','errors','band')},
       {field.name:getattr(fresh.template,field.name) for field in fields(fresh.template) if field.name not in ('version','errors','band')})
 assert _canonical(old.band)==_canonical(fresh.template.band)
 for name in _ARRAYS:assert np.array_equal(getattr(old,'_family_'+name),getattr(fresh,name)),name
 reference=reference_inputs(fresh.parameters);assert reference.get('qualified'),'warm qualified stability assets first'
 oldfiles=record['liquid_table_inputs_start']['files']
 old_manifest=next(Path(path) for path in oldfiles if Path(path).name.startswith('liquid-vessel-'))
 oldkeys=json.loads(old_manifest.read_text())['tank_table_keys']
 from stowage.vessel.tanks import TankGeometry
 from stowage.checker.stability_liquid_tables import _path
 grids={}
 for tank in fresh.parameters['tanks']:
  solid=TankGeometry.from_params(tank,ship_params=fresh.parameters)
  if getattr(solid,'shape',None)!='hull_sections':continue
  before=next(Path(path) for path in oldfiles if Path(path).name=='liquid-'+oldkeys[tank['id']]+'.npz')
  after=_path(solid);equal(numeric(before),numeric(after))
  grids[tank['id']]={'old_path':str(before),'old_sha256':sha(before),'current_path':str(after),'current_sha256':sha(after),'numeric_arrays_equal':True}
 identity=response_calibration_identity(fresh);domain=domain_for(fresh,instance)
 assert domain==record['calibration']['family_domain']
 evidence=[{'path':records[ship+'/'+level]['original_record_path'],'sha256':records[ship+'/'+level]['original_record_sha256']} for level in ('light','medium','full')]
 proof={'ship':ship,'response_identity':identity,'source_fingerprint':source_fingerprint(),'coefficient_fields_equal':True,
        'response_columns_equal':True,'raw_input_hashes':helper['input_snapshot'](ship),'domain_equal':True,
        'current_reference_inputs':reference,'grids':grids,'original_evidence':evidence,
        'scope':'exact coefficient/grid equality; empirical measurements remain sampled evidence, exact checker mandatory'}
 proofpath=directory/('equivalence-'+ship+'.json');write(proofpath,proof)
 evidence.append({'path':str(proofpath),'sha256':sha(proofpath)})
 physical={side:{} for side in SIDES};transfer={side:{} for side in SIDES}
 data=[json.loads(Path(records[ship+'/'+level]['original_record_path']).read_text()) for level in ('light','medium','full')]
 gaps=[gap_arrays(item) for item in data]
 for side in SIDES:
  for metric in METRICS:
   physical[side][metric]=np.maximum.reduce([np.asarray(item['signed_heldout']['signed_errors'][side][metric]) for item in data]).tolist()
   transfer[side][metric]=np.maximum.reduce([item[side][metric] for item in gaps]).tolist()
 entry={'station_x_m':fresh.template.station_x_m.tolist(),'physical_unsafe':physical,'reference_transfer':transfer,
        'domain':domain,'reference_identity':reference_cache_identity(reference),'evidence':evidence,
        'equivalence':{'path':str(proofpath),'sha256':sha(proofpath),'numeric_coefficients_and_grids_equal':True}}
 if migrate:
  measured=historical_model(record['calibration']['family_calibration_cache_key']);assert measured is not None
  raw=copy.deepcopy(measured.measurement_diagnostics)
  raw['original_measurement_reference_inputs']=raw['reference_inputs']
  raw['original_family_calibration_cache_key']=record['calibration']['family_calibration_cache_key']
  key=family_calibration_key(fresh,reference_cache_identity(reference),domain,samples=192)
  raw.update(family_calibration_cache_key=key,coefficient_family_cache_key=fresh.cache_key,
      response_calibration_identity=identity,reference_inputs=reference,reference_identity=reference_cache_identity(reference),
      equivalence_evidence=entry['equivalence'],measurement_transfer='verified identical coefficients and qualified numerical grids')
  cert=_calibration_certificate(key,measured.errors,raw);_validate_calibration_certificate(cert,key,measured.errors,raw)
  raw['calibration_certificate']=cert
  store_payload(key,{'errors':{name:getattr(measured.errors,name) for name in METRICS[:6]},'diagnostics':raw,'certificate':cert},namespace='payload')
  entry['migrated_calibration_key']=key
 return identity,entry

def main():
 p=argparse.ArgumentParser();p.add_argument('--ship',choices=('feeder_500','feeder_1000','feeder_1700'),action='append');p.add_argument('--migrate-calibration',action='store_true');p.add_argument('--output',type=Path,default=HERE.parents[1]/'src/stowage/linear/margin_policy_data.json');p.add_argument('--directory',type=Path,default=HERE/'signed-family/d30');a=p.parse_args()
 source=json.loads((HERE/'signed-family/production-method1/provisional-nine.json').read_text());bank={'format':1,'entries':{}}
 if a.output.exists():bank=json.loads(a.output.read_text())
 for ship in a.ship or ('feeder_500','feeder_1000','feeder_1700'):
  identity,entry=emit_ship(ship,source['cases'],a.directory,migrate=a.migrate_calibration)
  bank['entries'][identity]=entry;write(a.output,bank);print(json.dumps({'ship':ship,'response_identity':identity,'bank':str(a.output),'migrated':a.migrate_calibration}),flush=True)
if __name__=='__main__':main()

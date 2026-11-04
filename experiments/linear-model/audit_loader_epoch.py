"""Qualify a study across an explicitly reviewed, loader-only source epoch.

Original results remain untouched and dirty. A hit at the OLD source key using
CURRENT complete inputs proves those inputs equal the measured cache inputs.
Only four named loader files may differ; every numerical file and input pin must
be unchanged. The audit is separate from the original observation.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import runpy
import numpy as np
from stowage.linear.cache import _canonical,cache_key,model_cache_key,load_model,store_model,source_fingerprint
from stowage.linear.physics import _params_for
HERE=Path(__file__).resolve().parent
ALLOWED={'vessel/__init__.py','vessel/fleet_cache.py','vessel/fleet_conditions.py','vessel/fleet_slices.py'}

def record_digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def qualified(path,data,current_source):
    """Read a previously proved audit without weakening direct clean acceptance."""
    audit_path=path.with_suffix('.epoch-audit.json')
    if not audit_path.exists(): return False
    proof=json.loads(audit_path.read_text())
    return (proof.get('status')=='qualified_loader_only_epoch'
        and proof.get('current_source_fingerprint')==current_source
        and proof.get('original_record_sha256')==record_digest(path)
        and set(proof.get('changed_files',{}))<=ALLOWED
        and proof.get('model_fields_sha256')==proof.get('promoted_model_fields_sha256'))

def main():
    p=argparse.ArgumentParser();p.add_argument('--case',type=Path,required=True);a=p.parse_args()
    record=json.loads(a.case.read_text())
    helper=runpy.run_path(str(HERE/'narrow.py'))
    current_source=source_fingerprint();current_files=helper['source_file_hashes']()
    start_files=record['source_file_hashes_start']; end_files=record['source_file_hashes_end']
    assert set(current_files)-ALLOWED==set(start_files)-ALLOWED==set(end_files)-ALLOWED,'numerical source inventory changed'
    inventory=set(current_files)|set(start_files)|set(end_files)
    changed={name:{'start':start_files.get(name),'end':end_files.get(name),'current':current_files.get(name)}
             for name in inventory if len({start_files.get(name),end_files.get(name),current_files.get(name)})>1}
    assert changed and set(changed)<=ALLOWED,changed
    ship,level=record['ship'],record['level']
    assert record['input_hashes_start']==record['input_hashes_end']==helper['input_snapshot'](ship)
    import stowage.versions as versions
    current_versions={**versions.component_versions(),**{k:v for k,v in vars(versions).items() if k.endswith('_VERSION')}}
    assert record['component_versions_start']==record['component_versions_end']==current_versions
    assert record['calibration']['samples']==192 and record['diagnostics']['samples']==128
    assert record['stability_free_surface_method'] in ('1','2')
    assert record['stability_free_surface_method']==os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')
    comparison=record.get('public_checker_comparison')
    assert comparison is None or comparison['passed']
    instance,_=helper['cargo_instance'](ship,level)
    assert helper['manifest_identity'](instance)==record['departure_manifest_identity']
    band=helper['case_band'](instance,ship,level)
    assert asdict(band)==record['band']
    assert _canonical(getattr(band,'_tank_mass_bounds_kg',{}))==_canonical(record['tank_windows_kg'])
    _,params=_params_for(instance)
    old_key=cache_key('model',vessel_id=instance.vessel.id,params=params,
        inputs={'instance':instance,'samples':64,
                'reference_free_surface_method':int(record['stability_free_surface_method'])},band=band,density=instance.port.water_density_kg_m3,
        source_hash=record['source_fingerprint_start'],versions=record['component_versions_start'])
    measured=load_model(old_key)
    assert measured is not None,'old-source key with CURRENT full inputs missed; cannot qualify'
    assert asdict(measured.band)==record['band']
    assert getattr(measured,'_manifest_fingerprint')==record['departure_manifest_identity']['sha256']
    assert measured.measurement_diagnostics['samples']==192
    for metric,value in record['model_errors'].items():
        assert np.array_equal(getattr(measured.errors,metric),value),metric
    digest=hashlib.sha256(_canonical(measured)).hexdigest()
    assert source_fingerprint()==current_source and helper['source_file_hashes']()==current_files
    assert helper['input_snapshot'](ship)==record['input_hashes_end']
    new_key=model_cache_key(instance,band,64,params)
    assert store_model(new_key,measured),'cache promotion failed'
    promoted=load_model(new_key)
    assert promoted is not None
    promoted_digest=hashlib.sha256(_canonical(promoted)).hexdigest()
    assert digest==promoted_digest
    result={'status':'qualified_loader_only_epoch','original_record_sha256':record_digest(a.case),
        'original_source_fingerprint':record['source_fingerprint_start'],
        'original_end_source_fingerprint':record['source_fingerprint_end'],
        'current_source_fingerprint':current_source,'changed_files':changed,
        'original_source_key_with_current_full_inputs':old_key,'promoted_current_key':new_key,
        'model_fields_sha256':digest,'promoted_model_fields_sha256':promoted_digest,
        'departure_manifest_identity':record['departure_manifest_identity'],
        'note':'Original record and source_clean preserved. Numerical files, versions, raw inputs unchanged; complete current inputs hit old key and calibration vectors match exactly.'}
    a.case.with_suffix('.epoch-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()

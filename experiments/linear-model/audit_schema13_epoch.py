"""Qualify a study across an explicitly reviewed, reviewed schema metadata source epoch.

Original results remain untouched and dirty. A hit at the OLD source key using
CURRENT complete inputs proves those inputs equal the measured cache inputs.
Only explicitly reviewed schema, loader and unrelated visibility/lashing files may differ; every numerical file and input pin must
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
ALLOWED={'model/vessel.py','model/__init__.py','model/fixtures.py','versions.py','checker/visibility.py','checker/lashing.py','vessel/__init__.py','vessel/fleet_cache.py','vessel/fleet_conditions.py','vessel/fleet_slices.py'}
REVIEWED_PAIRS={('6b834730e9c0bf6f9c224a15d50e39b0628ab8c0363e4665eced6e1b193fe0e7',
                 '0b0c7dc5ac43481c0cb1d73ce972e9d4d1eacd650fb86245e56d95430eb42c99')}

def record_digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def qualified(path,data,current_source):
    """Read a previously proved audit without weakening direct clean acceptance."""
    audit_path=path.with_suffix('.schema-audit.json')
    if not audit_path.exists(): return False
    proof=json.loads(audit_path.read_text())
    return (proof.get('status')=='qualified_schema13_metadata_epoch'
        and (data.get('source_fingerprint_start'),current_source) in REVIEWED_PAIRS
        and data.get('stability_free_surface_method')==os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')
        and proof.get('original_source_fingerprint')==data.get('source_fingerprint_start')
        and proof.get('current_source_fingerprint')==current_source
        and proof.get('original_record_sha256')==record_digest(path)
        and set(proof.get('changed_files',{}))<=ALLOWED
        and proof.get('model_fields_sha256')==proof.get('promoted_model_fields_sha256'))

def main():
    p=argparse.ArgumentParser();p.add_argument('--case',type=Path,required=True);a=p.parse_args()
    record=json.loads(a.case.read_text())
    helper=runpy.run_path(str(HERE/'narrow.py'))
    current_source=source_fingerprint();current_files=helper['source_file_hashes']()
    assert (record['source_fingerprint_start'],current_source) in REVIEWED_PAIRS,'epoch transition has not been reviewed'
    assert record['source_fingerprint_end'] in (record['source_fingerprint_start'],current_source)
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
    old_versions=record['component_versions_start']
    for snapshot in (record['component_versions_start'],record['component_versions_end'],current_versions):
        assert {k:v for k,v in snapshot.items() if k not in ('schema','SCHEMA_VERSION')}=={k:v for k,v in current_versions.items() if k not in ('schema','SCHEMA_VERSION')}
        assert snapshot.get('schema') in ('1.2','1.3') and snapshot.get('SCHEMA_VERSION') in ('1.2','1.3')
    assert record['calibration']['samples']==192 and record['diagnostics']['samples']==128
    assert record['stability_free_surface_method'] in ('1','2')
    assert record['stability_free_surface_method']==os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1'), 'reference method migration is not metadata equivalence'
    comparison=record.get('public_checker_comparison')
    assert comparison is None or comparison['passed']
    instance,_=helper['cargo_instance'](ship,level)
    assert helper['manifest_identity'](instance)==record['departure_manifest_identity']
    band=helper['case_band'](instance,ship,level)
    assert asdict(band)==record['band']
    assert _canonical(getattr(band,'_tank_mass_bounds_kg',{}))==_canonical(record['tank_windows_kg'])
    _,params=_params_for(instance)
    def original_key(candidate):
        return cache_key('model',vessel_id=instance.vessel.id,params=params,
            inputs={'instance':candidate,'samples':64,
                    'reference_free_surface_method':int(record['stability_free_surface_method'])},
            band=band,density=instance.port.water_density_kg_m3,
            source_hash=record['source_fingerprint_start'],versions=old_versions)
    old_key=original_key(instance)
    measured=load_model(old_key)
    normalization=[]
    if measured is None:
        # Only the reviewed schema1.2 ->1.3 representation is reversible.
        assert old_versions['schema']=='1.2' and current_versions['schema']=='1.3'
        legacy=instance.model_dump(mode='json')
        assert legacy['schema_version']=='1.3'
        legacy['schema_version']='1.2';normalization.append('instance.schema_version:1.3->1.2')
        assert legacy['provenance']['versions']['schema']=='1.3'
        legacy['provenance']['versions']['schema']='1.2';normalization.append('provenance.versions.schema:1.3->1.2')
        for index,load in enumerate(legacy['vessel']['fixed_loads']):
            assert load['kind']=='other' and load['silhouette'] is None
            assert set(load)=={'x_m','y_m','z_m','mass_kg','kind','silhouette'}
            legacy['vessel']['fixed_loads'][index]=[load[k] for k in ('x_m','y_m','z_m','mass_kg')]
            normalization.append(f'vessel.fixed_loads[{index}]:default-other/None->legacy-pointmass')
        old_key=original_key(legacy);measured=load_model(old_key)
    assert measured is not None,'original key with exact current or reversible schema1.2 inputs missed'
    assert asdict(measured.band)==record['band']
    assert getattr(measured,'_manifest_fingerprint')==record['departure_manifest_identity']['sha256']
    assert measured.measurement_diagnostics['samples']==192
    for metric,value in record['model_errors'].items():
        assert np.array_equal(getattr(measured.errors,metric),value),metric
    public_proof=None
    if level=='light':
        import stowage.linear.physics as physics
        original_exact=physics.exact_condition;captured=[]
        def capture(*positional,**keywords):
            exact=original_exact(*positional,**keywords)
            if not captured and (band.displacement_min_kg<=exact['displacement_kg']<=band.displacement_max_kg
                and band.draught_min_m<=exact['draught_m']<=band.draught_max_m
                and abs(exact['trim_m'])<=band.max_abs_trim_m and abs(exact['list_rad'])<=band.max_abs_heel_rad):
                captured.append((positional[2].copy(),positional[3].copy(),keywords['cell_vcg_m'].copy(),exact))
            return exact
        physics.exact_condition=capture
        try:physics.measure_errors(measured,instance,samples=1,seed=3712)
        finally:physics.exact_condition=original_exact
        assert captured
        public_proof=helper['public_crosscheck'](measured,instance,captured[0]);assert public_proof['passed']
        previous=record.get('public_checker_comparison')
        assert previous and previous['passed']
        for metric,tolerance in (('gm_m',1e-7),('trim_m',1e-7),('list_rad',1e-7),('draught_m',1e-7),('sf_n',1.),('bm_nm',10.)):
            assert np.allclose(captured[0][3][metric],previous['exact_condition'][metric],rtol=0,atol=tolerance),metric
    digest=hashlib.sha256(_canonical(measured)).hexdigest()
    assert source_fingerprint()==current_source and helper['source_file_hashes']()==current_files
    assert helper['input_snapshot'](ship)==record['input_hashes_end']
    new_key=model_cache_key(instance,band,64,params)
    assert store_model(new_key,measured),'cache promotion failed'
    promoted=load_model(new_key)
    assert promoted is not None
    promoted_digest=hashlib.sha256(_canonical(promoted)).hexdigest()
    assert digest==promoted_digest
    result={'status':'qualified_schema13_metadata_epoch','original_record_sha256':record_digest(a.case),
        'original_source_fingerprint':record['source_fingerprint_start'],
        'original_end_source_fingerprint':record['source_fingerprint_end'],
        'current_source_fingerprint':current_source,'changed_files':changed,'schema_normalization':normalization,'current_public_checker_proof':public_proof,
        'original_source_key_with_current_full_inputs':old_key,'promoted_current_key':new_key,
        'model_fields_sha256':digest,'promoted_model_fields_sha256':promoted_digest,
        'departure_manifest_identity':record['departure_manifest_identity'],
        'note':'Original record preserved. Reviewed schema1.2->1.3 default-other/None FixedLoad representation only; exact current or reversibly normalized complete inputs hit original key. All numerical linear/hydro/stability/strength files and non-schema versions unchanged. Current public reference replay required for each light case.'}
    result=helper['_HELPERS']['serializable'](result)
    a.case.with_suffix('.schema-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()

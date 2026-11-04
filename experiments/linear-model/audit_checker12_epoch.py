"""Explicit, fail-closed bridge for one metadata-only component-version epoch.

Never writes stability assets or edits an original observation. Owners must
first publish current, qualified liquid assets. We compare every tank's numeric
NPZ arrays, physical provenance and SHA-bound validation evidence. Only our
linear coefficient/calibration caches may then be promoted, and only after a
separate current public-checker replay covers all three ships. Historical failed
public equality comparisons remain failures; an unchanged measured gap is not a
new strict pass. No numerical source changes are accepted by this bridge.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict,replace,fields,is_dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import runpy

import numpy as np

from stowage.linear.cache import (_canonical,cache_key,coefficient_family_key,
    load_model,store_model,source_fingerprint)

HERE=Path(__file__).resolve().parent
OLD='103cc58bc0477c146f3790395bc10a916a3e8e7b3d97732336b7b36bc0b69538'
CURRENT='f2a1b24ebbaea06acca38e5ceb088f503e983dae283ad65294465c82ec3a2756'
OLD_STABILITY_DOC="""    Stability follows the selected public 3.1 policy: method2 (production
    default) uses the Code's virtual rise; method1 verifies finite liquid
    transfer. Strength always follows 3.2's physical finite-liquid equilibrium,"""
CURRENT_STABILITY_DOC="""    Stability follows the selected public 3.1 policy: method1 (production
    default) uses finite liquid transfer; method2 retains the Code's virtual-rise
    compatibility path. Strength always follows 3.2's physical finite-liquid equilibrium,"""
SHIPS={'feeder_500','feeder_1000','feeder_1700'}
TRANSITIONS={'checker':('1.1','1.2'),'CHECKER_VERSION':('1.1','1.2'),
             'ballast':('2.0','2.1'),'BALLAST_VERSION':('2.0','2.1')}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized_pins(pins):return {str(Path(path).resolve()):value for path,value in pins.items()}


def model_digest(model):
    """Bind public fields and reusable response arrays, including array dtypes."""
    digest=hashlib.sha256()
    def visit(value):
        if isinstance(value,np.ndarray):
            digest.update(str((value.dtype.str,value.shape)).encode());digest.update(value.tobytes())
        elif is_dataclass(value):
            for field in fields(value):digest.update(field.name.encode());visit(getattr(value,field.name))
        elif isinstance(value,(tuple,list)):
            for item in value:visit(item)
        else:digest.update(_canonical(value))
    visit(model)
    for name in sorted(name for name in vars(model) if name.startswith('_family_')):
        digest.update(name.encode());visit(getattr(model,name))
    return digest.hexdigest()


def _versions():
    import stowage.versions as versions
    return {**versions.component_versions(),**{k:v for k,v in vars(versions).items() if k.endswith('_VERSION')}}


def _unchanged_versions(old,new):
    assert old.keys()==new.keys(),'version inventory changed'
    for key in old:
        assert (old[key],new[key])==TRANSITIONS[key] if key in TRANSITIONS else old[key]==new[key],key


def static_proof(path,record,helper):
    assert source_fingerprint()==CURRENT,'unreviewed current source epoch'
    assert record['source_fingerprint_start']==OLD,'unreviewed original source epoch'
    assert record['stability_free_surface_method']==os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')=='1'
    assert os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT')!='1'
    current=helper['source_file_hashes']();start=record['source_file_hashes_start'];end=record['source_file_hashes_end']
    assert start.keys()==end.keys()==current.keys(),'numerical source inventory changed'
    changed={name for name in start if len({start[name],end[name],current[name]})>1}
    assert changed=={'versions.py','linear/physics.py'},changed
    # Reconstruct the old bytes, proving exactly the two reviewed constant
    # substitutions, rather than granting an unrestricted versions.py exception.
    version_file=HERE.parents[1]/'src/stowage/versions.py'
    original=version_file.read_text()
    for name,(before,after) in (('CHECKER_VERSION',('1.1','1.2')),('BALLAST_VERSION',('2.0','2.1'))):
        original,count=re.subn(r'(?m)^('+name+r': Final = ")'+re.escape(after)+r'(")$',
            lambda match:match[1]+before+match[2],original)
        if count!=1:
            # Owner comments follow the literal in the production file.
            original,count=re.subn(r'(?m)^('+name+r': Final = ")'+re.escape(after)+r'(")',
                lambda match:match[1]+before+match[2],original)
        assert count==1,name
    assert hashlib.sha256(original.encode()).hexdigest()==start['versions.py'],'version changes are not the reviewed literal substitutions'
    physics_file=HERE.parents[1]/'src/stowage/linear/physics.py'
    physics_bytes=physics_file.read_bytes()
    assert physics_bytes.count(CURRENT_STABILITY_DOC.encode())==1,'current doc correction differs from the reviewed literal'
    original_physics=physics_bytes.replace(CURRENT_STABILITY_DOC.encode(),OLD_STABILITY_DOC.encode())
    assert hashlib.sha256(original_physics).hexdigest()==start['linear/physics.py'],'physics changed beyond the exact documented default correction'
    assert end['linear/physics.py'] in (start['linear/physics.py'],current['linear/physics.py'])
    for name in set(current)-{'versions.py','linear/physics.py'}:assert start[name]==end[name]==current[name],name
    versions=_versions();_unchanged_versions(record['component_versions_start'],versions)
    old=record['component_versions_start']
    for key,value in record['component_versions_end'].items():
        assert value in TRANSITIONS[key] if key in TRANSITIONS else value==versions[key],key
    assert record['input_hashes_start']==record['input_hashes_end']==helper['input_snapshot'](record['ship'])
    assert record['reference_table_inputs_clean']
    assert record['liquid_table_inputs_start']==record['liquid_table_inputs_end']
    assert record['calibration']['samples']==192 and record['diagnostics']['samples']==128
    return {'original_record_sha256':sha(path),'original_source':OLD,'current_source':CURRENT,
            'original_versions':old,'current_versions':versions,
            'input_hashes':record['input_hashes_start'],'changed_files':['versions.py','linear/physics.py'],
            'physics_default_docstring_correction_only':True,
            'original_observation_epoch':'explicit_virtual_prior_metadata' if record.get('virtual_prior_metadata_epoch') else 'native_prior_metadata'}


def asset_proof(record,parameters):
    from stowage.checker import stability_liquid as liquid,stability_liquid_tables as tables
    from stowage.vessel.tanks import TankGeometry
    old_snapshot=record['liquid_table_inputs_start']
    old_pins=old_snapshot['files']
    assert old_pins and all(value is not None and sha(file)==value for file,value in old_pins.items())
    old_path=Path(old_snapshot['manifest_path']);old=json.loads(old_path.read_text())
    new_path=liquid.manifest_path(parameters)
    assert new_path.exists(),f'owner current-version qualification pending: {new_path}'
    new=json.loads(new_path.read_text())
    assert new['metadata']==liquid.metadata(parameters),'current vessel qualification metadata mismatch'
    _unchanged_versions(old['metadata']['component_versions'],new['metadata']['component_versions'])
    assert {k:v for k,v in old['metadata'].items() if k!='component_versions'}=={
        k:v for k,v in new['metadata'].items() if k!='component_versions'}
    assert old['validation']==new['validation'] and liquid._valid_statistics(new['validation'])
    assert old['tank_table_keys'].keys()==new['tank_table_keys'].keys()
    current_pins={str(new_path):sha(new_path)};rows=[];expected_old={str(old_path.resolve())}
    raw={tank['id']:tank for tank in parameters['tanks']}
    for tid,old_key in old['tank_table_keys'].items():
        solid=TankGeometry.from_params(raw[tid],ship_params=parameters)
        assert new['tank_table_keys'][tid]==tables._identity(solid)
        a=old_path.parent/f'liquid-{old_key}.npz';b=tables._path(solid)
        ca=a.with_suffix('.qualified.json');cb=b.with_suffix('.qualified.json')
        expected_old.update((str(a.resolve()),str(ca.resolve())))
        assert b.exists() and cb.exists(),f'owner current tank qualification pending: {tid}'
        left=json.loads(ca.read_text());right=json.loads(cb.read_text())
        assert left['sha256']==sha(a) and right['sha256']==sha(b)
        assert {k:v for k,v in left.items() if k!='sha256'}=={k:v for k,v in right.items() if k!='sha256'}
        assert right['samples']>=4 and all(np.isfinite(right[k]) and 0<=right[k]<=.2 for k in ('gz','area','gm'))
        numeric={}
        with np.load(a,allow_pickle=False) as aa,np.load(b,allow_pickle=False) as bb:
            assert set(aa.files)==set(bb.files)
            for key in aa.files:
                if key=='provenance_json':continue
                x,y=aa[key],bb[key]
                assert x.dtype.kind in 'biuf' and x.dtype==y.dtype and x.shape==y.shape
                assert np.isfinite(x).all() and np.array_equal(x,y),(tid,key)
                numeric[key]=hashlib.sha256(x.tobytes()).hexdigest()
            pa=json.loads(str(aa['provenance_json']));pb=json.loads(str(bb['provenance_json']))
        _unchanged_versions(pa['component_versions'],pb['component_versions'])
        assert {k:v for k,v in pa.items() if k!='component_versions'}=={k:v for k,v in pb.items() if k!='component_versions'}
        assert pb==tables._provenance(solid)
        tables._read(b,solid,version_strict=True)  # Reads only; never publishes qualification.
        current_pins.update({str(b):sha(b),str(cb):sha(cb)})
        rows.append({'tank':tid,'old_path':str(a),'current_path':str(b),'numeric_arrays_sha256':numeric,
                     'qualification_statistics_unchanged':True})
    assert len(rows)==sum(TankGeometry.from_params(t,ship_params=parameters).shape=='hull_sections' for t in raw.values())
    assert set(normalized_pins(old_pins))==expected_old,'original reference file inventory incomplete'
    return {'old_files':old_pins,'current_files':current_pins,'tanks':rows,
            'validation':new['validation'],'status':'qualified_assets_numerically_identical'}


def require_public_proof(path):
    """Protocol for validation's independent three-vessel replay evidence.

    Samples contain complete public_crosscheck dictionaries under ``current``
    and, where a historical observation exists, ``original``. No historical
    comparison may be fabricated for a previously unmeasured vessel condition.
    """
    evidence=json.loads(path.read_text())
    assert evidence['original_source_fingerprint']==OLD and evidence['source_fingerprint']==CURRENT
    assert evidence['method']=='1' and set(evidence['samples'])==SHIPS
    from stowage.vessel.fleet import load_ship_params
    from stowage.linear.family_calibration import reference_inputs
    for ship,sample in evidence['samples'].items():
        current=sample['current'];assert current.get('stability_metrics') and current.get('strength_metrics')
        assert sample['input_hashes'] and all(sha(file)==value for file,value in sample['input_hashes'].items())
        assert sample['current_reference_files'] and all(sha(file)==value for file,value in sample['current_reference_files'].items())
        required=normalized_pins(reference_inputs(load_ship_params(ship))['files'])
        provided=normalized_pins(sample['current_reference_files'])
        assert required and required.items()<=provided.items(),'public replay did not pin all current qualified liquid inputs'
        original=sample.get('original')
        if original is not None:
            prior=Path(sample['original_case_path']);record=json.loads(prior.read_text())
            assert sha(prior)==sample['original_case_sha256']
            assert record['ship']==ship and record['public_checker_comparison']==original
            for key,tolerance in (('gm_m',1e-5),('trim_m',1e-5),('list_rad',1e-5),('sf_n',1.),('bm_nm',10.)):
                assert np.allclose(current['exact_condition'][key],original['exact_condition'][key],rtol=0,atol=tolerance),(ship,key)
                assert abs(current['absolute_differences'][key]-original['absolute_differences'][key])<=tolerance,(ship,key)
            assert current['passed']==original['passed'],'preserve the original strict equality result'
        else:
            assert sample['historical_comparison_available'] is False
        assert sample['directional_coverage'] and all(row['covered'] for row in sample['directional_coverage'])
    return {'path':str(path),'sha256':sha(path),'ships':sorted(SHIPS)}


def promote(record,helper,instance,parameters,assets,public_proof):
    """Rekey only inert linear payloads; preserve every public model field."""
    from stowage.linear.family_calibration import reference_inputs,domain_for
    from stowage.linear.reusable import CoefficientFamily,_ARRAYS
    old_versions=record['component_versions_start'];diag=record['calibration']
    old_coeff_key=diag['coefficient_family_cache_key'];old_cal_key=diag['family_calibration_cache_key']
    template=load_model(old_coeff_key);calibrated=load_model(old_cal_key)
    assert template is not None and calibrated is not None,'original linear payload missing'
    vessel=instance.vessel.model_dump(mode='json')
    for name in ('fixed_loads','sources','name'):vessel.pop(name,None)
    expected=cache_key('coefficient_family',vessel_id=instance.vessel.id,params=parameters,
        inputs={'vessel_without_fixed_loads':vessel},band=template.band,density=instance.port.water_density_kg_m3,
        source_hash=OLD,versions=old_versions)
    assert expected==old_coeff_key,'complete current numerical vessel differs from original coefficient inputs'
    for key,value in diag['family_measured_absolute_errors'].items():
        assert np.array_equal(getattr(calibrated.errors,key),value),key
    assert calibrated.measurement_diagnostics['signed_errors']==diag['signed_errors']
    arrays=[getattr(template,'_family_'+name) for name in _ARRAYS]
    family=CoefficientFamily(template,*arrays,parameters,old_coeff_key)
    expected_cal=cache_key('family_calibration',vessel_id=instance.vessel.id,params=parameters,
        inputs={'coefficient_family':old_coeff_key,'samples':192,'reference':diag['reference_inputs'],
                'domain':domain_for(family,instance)},band=template.band,density=instance.port.water_density_kg_m3,
        source_hash=OLD,versions=old_versions)
    assert expected_cal==old_cal_key,'complete calibration inputs differ from the measurement'
    new_coeff_key=coefficient_family_key(instance,parameters,template.band)
    current_reference=reference_inputs(parameters)
    new_cal_key=cache_key('family_calibration',vessel_id=instance.vessel.id,params=parameters,
        inputs={'coefficient_family':new_coeff_key,'samples':192,'reference':current_reference,
                'domain':domain_for(family,instance)},band=template.band,density=instance.port.water_density_kg_m3)
    updated=replace(calibrated)
    metadata=dict(calibrated.measurement_diagnostics,
        family_calibration_cache_key=new_cal_key,coefficient_family_cache_key=new_coeff_key,
        reference_inputs=current_reference,
        metadata_epoch_bridge={'source_pair':[OLD,CURRENT],'old_coefficient_key':old_coeff_key,
            'old_calibration_key':old_cal_key,'public_replay_proof':public_proof})
    object.__setattr__(updated,'measurement_diagnostics',metadata)
    proofs=[]
    for before,new_key in ((template,new_coeff_key),(updated,new_cal_key)):
        digest=model_digest(before)
        existing=load_model(new_key)
        if existing is not None:
            assert model_digest(existing)==digest,'current numerical payload conflicts; never overwrite'
        assert store_model(new_key,before)
        after=load_model(new_key);assert after is not None
        assert model_digest(after)==digest
        proofs.append({'new_key':new_key,'public_model_fields_sha256':digest})
    return {'old_coefficient_key':old_coeff_key,'old_calibration_key':old_cal_key,'promoted':proofs}


def qualified(path,record,current_source):
    proof_path=path.with_suffix('.checker12-audit.json')
    if not proof_path.exists():return False
    proof=json.loads(proof_path.read_text())
    return (current_source==CURRENT and record['source_fingerprint_start']==OLD
        and record['stability_free_surface_method']==os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')=='1'
        and proof.get('status')=='qualified_checker12_metadata_epoch'
        and proof['static']['original_record_sha256']==sha(path)
        and sha(proof['public_replay']['path'])==proof['public_replay']['sha256']
        and all(sha(file)==value for file,value in {**proof['assets']['old_files'],**proof['assets']['current_files']}.items()))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',type=Path,required=True)
    parser.add_argument('--public-proof',type=Path,help='Mandatory independent three-vessel evidence for promotion.')
    args=parser.parse_args();record=json.loads(args.case.read_text())
    helper=runpy.run_path(str(HERE/'narrow.py'))
    static=static_proof(args.case,record,helper)
    from stowage.vessel.fleet import load_ship_params
    parameters=load_ship_params(record['ship'])
    assets=asset_proof(record,parameters)  # Fail before constructing a vessel if assets are pending.
    result={'status':'assets_equivalent_pending_public_replay','static':static,'assets':assets}
    if args.public_proof:
        public=require_public_proof(args.public_proof)
        instance,_=helper['cargo_instance'](record['ship'],record['level'])
        assert helper['manifest_identity'](instance)==record['departure_manifest_identity']
        assert asdict(helper['case_band'](instance,record['ship'],record['level']))==record['band']
        assert source_fingerprint()==CURRENT and sha(args.case)==static['original_record_sha256']
        result['linear_cache_promotion']=promote(record,helper,instance,parameters,assets,public)
        result.update(status='qualified_checker12_metadata_epoch',public_replay=public)
    assert source_fingerprint()==CURRENT and sha(args.case)==static['original_record_sha256']
    assert all(sha(file)==value for file,value in {**assets['old_files'],**assets['current_files']}.items())
    output=args.case.with_suffix('.checker12-audit.json')
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'case':str(args.case),'status':result['status'],'proof':str(output)}))


if __name__=='__main__':main()

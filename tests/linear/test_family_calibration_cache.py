"""Shared error payloads and semantic reference identities need no fleet build."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import numpy as np
import pytest
from stowage.linear.model import LinearErrors


def setup_calibration(monkeypatch):
    import stowage.linear.family_calibration as cal
    import stowage.linear.cache as cache
    import stowage.linear.margin_policy as policy
    monkeypatch.setattr(cal,'_CALIBRATION_MEMORY',{})
    domain={'kg_min_m':0.,'kg_max_m':20.};data={};calls=[];policies=[]
    monkeypatch.setattr(cal,'domain_for',lambda *a:dict(domain))
    monkeypatch.setattr(cal,'reference_inputs',lambda *a:{'method':1,'qualified':True,'files':{'certificate':'date1'}})
    monkeypatch.setattr(cal,'reference_inputs_cached',lambda *a:cal.reference_inputs(*a))
    monkeypatch.setattr(cal,'synchronize_reference',lambda *a:None)
    monkeypatch.setattr(cache,'response_calibration_identity',lambda *a:'same-response')
    def key(family,reference,current,**kw):
        assert 'files' not in reference and current==domain and kw=={'samples':192}
        return 'shared-errors'
    monkeypatch.setattr(cache,'family_calibration_key',key)
    monkeypatch.setattr(cache,'load_payload',lambda key,**kw:deepcopy(data.get(key)))
    monkeypatch.setattr(cache,'store_payload',lambda key,value,**kw:data.update({key:deepcopy(value)}))
    def calibrate(*args,**kw):
        calls.append(kw)
        errors=LinearErrors(.1,.2,.03,.04,np.ones(2),np.ones(2)*2)
        return errors,{'samples':192,'family_domain':domain,'signed_errors':{'test':1},'p99':{name:getattr(errors,name) for name in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')}}
    monkeypatch.setattr(cal,'calibrate_family',calibrate)
    def qualify(family,instance,diagnostics):
        policies.append((family.cache_key,diagnostics['reference_identity']))
        return {'sha256':str(len(policies))}
    monkeypatch.setattr(policy,'qualify_policy',qualify)
    family=lambda name:NS(cache_key=name,parameters={},template=NS(station_x_m=np.array([0.,1.])))
    return cal,family,data,calls,policies


def test_layout_free_errors_reused_and_policy_requalified(monkeypatch):
    cal,family,data,calls,policies=setup_calibration(monkeypatch)
    first,original=cal.calibrated_family(family('full-layout'),NS())
    cal._CALIBRATION_MEMORY.clear()
    second,current=cal.calibrated_family(family('tiny-layout'),NS())
    assert len(calls)==1 and len(policies)==2
    np.testing.assert_array_equal(first.sf_n,second.sf_n)
    assert current['coefficient_family_cache_key']=='tiny-layout'
    assert current['calibration_origin_coefficient_family_cache_key']=='full-layout'
    assert current['margin_policy']['sha256']=='2' and original['margin_policy']['sha256']=='1'
    assert set(data['shared-errors'])=={'certificate','errors','diagnostics'}
    assert not second.sf_n.flags.writeable


def test_certificate_audit_bytes_do_not_recalibrate_unchanged_dispatch(monkeypatch):
    cal,family,data,calls,policies=setup_calibration(monkeypatch)
    cal.calibrated_family(family('full'),NS())
    monkeypatch.setattr(cal,'reference_inputs',lambda *a:{'method':1,'qualified':True,'files':{'certificate':'date2'}})
    _,diag=cal.calibrated_family(family('full'),NS())
    assert len(calls)==1
    assert diag['reference_inputs']['files']['certificate']=='date2'
    assert diag['calibration_reference_inputs']['files']['certificate']=='date1'


def test_malformed_disk_error_payload_rebuilds(monkeypatch):
    cal,family,data,calls,policies=setup_calibration(monkeypatch)
    cal.calibrated_family(family('full'),NS())
    cal._CALIBRATION_MEMORY.clear();data['shared-errors']['errors']['sf_n']=[float('nan')]*2
    cal.calibrated_family(family('tiny'),NS())
    assert len(calls)==2


def test_reference_identity_ignores_certificate_dates_but_tracks_actual_dispatch(monkeypatch,tmp_path):
    import stowage.linear.family_calibration as cal
    import stowage.linear.dependencies as dependencies
    from stowage.checker import stability_liquid as liquid,stability_liquid_tables as tables
    from stowage.vessel.tanks import TankGeometry
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')
    monkeypatch.delenv('STOWAGE_STABILITY_LIQUID_DIRECT',raising=False)
    monkeypatch.setattr(dependencies,'dependency_fingerprint',lambda *a:'reference-deps')
    manifest=tmp_path/'manifest.json';path=tmp_path/'tank.npz';cert=path.with_suffix('.qualified.json')
    manifest.write_text(json.dumps({'metadata':{'physical':1},'validation':{'samples':4,'gm':.1,'gz':.1,'area':.1},'tank_table_keys':{'T':'grid'}}))
    path.write_bytes(b'numerical grid');cert.write_text('{"date":"first"}')
    monkeypatch.setattr(liquid,'manifest_path',lambda *a:manifest)
    monkeypatch.setattr(liquid,'metadata',lambda *a:{'physical':1})
    monkeypatch.setattr(liquid,'_table_identity',lambda *a:'grid')
    monkeypatch.setattr(tables,'_path',lambda *a:path)
    monkeypatch.setattr(TankGeometry,'from_params',lambda *a,**kw:NS(shape='hull_sections'))
    table=NS(fills=np.arange(4.),heels=np.arange(4.),trims=np.arange(4.),moments=np.ones((4,4,4,3)),capacity=10.,empty_centroid=np.zeros(3),prismatic=False)
    monkeypatch.setattr(tables,'lookup',lambda *a:table)
    cal._reference_paths.cache_clear();params={'tanks':[{'id':'T'}]}
    first=cal.reference_inputs(params);cert.write_text('{"date":"later"}')
    later=cal.reference_inputs(params)
    assert first['qualified'] and first['files']!=later['files']
    assert cal.reference_cache_identity(first)==cal.reference_cache_identity(later)
    table.moments[0,0,0,0]+=1
    changed=cal.reference_inputs(params)
    assert cal.reference_cache_identity(changed)!=cal.reference_cache_identity(later)
    manifest.write_text('{}')
    unavailable=cal.reference_inputs(params)
    assert not unavailable['qualified'] and not unavailable['dispatch'][0]['qualified']
    cal._reference_paths.cache_clear()


@pytest.mark.parametrize('field,value',[('inputs_hash','wrong'),('builder_version','obsolete'),('error_table_sha256','tampered')])
def test_calibration_certificate_tampering_rebuilds(monkeypatch,field,value):
    cal,family,data,calls,policies=setup_calibration(monkeypatch)
    cal.calibrated_family(family('full'),NS())
    cal._CALIBRATION_MEMORY.clear();data['shared-errors']['certificate'][field]=value
    cal.calibrated_family(family('tiny'),NS())
    assert len(calls)==2


def test_existing_d30_measurements_receive_missing_certificate_without_recalibration(monkeypatch):
    cal,family,data,calls,policies=setup_calibration(monkeypatch)
    cal.calibrated_family(family('full'),NS())
    cal._CALIBRATION_MEMORY.clear();data['shared-errors'].pop('certificate')
    _,diag=cal.calibrated_family(family('tiny'),NS())
    assert len(calls)==1
    assert diag['calibration_certificate']['qualification_wrapper']=='verified existing D30 measurement payload'
    assert 'certificate' in data['shared-errors']

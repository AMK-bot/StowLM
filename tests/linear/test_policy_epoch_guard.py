"""Known production profiles cannot silently revert to generic calibration."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from stowage.linear import margin_policy as policy
from stowage.linear.model import LinearErrors


@pytest.fixture
def context(monkeypatch, tmp_path):
    from stowage.linear import cache, family_calibration as cal
    path = tmp_path/'bank.json'
    path.write_text(json.dumps({'format':1,'known_vessel_ids':['known'],'entries':{}}))
    monkeypatch.setattr(policy,'_BANK_PATH',path)
    monkeypatch.setattr(cache,'response_calibration_identity',lambda family:'new-response')
    model = NS(station_x_m=np.array([0.,1.]),water_density_kg_m3=1025.)
    family = NS(parameters={'id':'known'},template=model,cache_key='coeff')
    domain = {'kg_min_m':0.,'kg_max_m':20.}
    reference = {'method':1,'direct':False}
    signed = {side:{metric:[.1,.2] if metric in ('sf_n','bm_nm') else .01
                    for metric in policy._METRICS} for side in policy._SIDES}
    errors = LinearErrors(.1,.2,.03,.04,np.ones(2),np.ones(2))
    raw = {'samples':192,'family_domain':domain,'signed_errors':signed,
           'p99':{name:getattr(errors,name) for name in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')}}
    calls = []
    monkeypatch.setattr(cal,'_CALIBRATION_MEMORY',{})
    monkeypatch.setattr(cal,'reference_inputs_cached',lambda *args:deepcopy(reference))
    monkeypatch.setattr(cal,'reference_inputs',lambda *args:deepcopy(reference))
    monkeypatch.setattr(cal,'synchronize_reference',lambda *args:None)
    monkeypatch.setattr(cal,'domain_for',lambda *args:deepcopy(domain))
    monkeypatch.setattr(cache,'family_calibration_key',lambda *args,**kwargs:'calibration')
    monkeypatch.setattr(cache,'load_payload',lambda *args,**kwargs:None)
    monkeypatch.setattr(cache,'store_payload',lambda *args,**kwargs:True)
    def calibrate(*args,**kwargs):
        calls.append(kwargs)
        return errors,deepcopy(raw)
    monkeypatch.setattr(cal,'calibrate_family',calibrate)
    diagnostics = dict(raw,family_calibration_cache_key='calibration',
                       response_calibration_identity='new-response',reference_identity=reference)
    return NS(family=family,model=model,cal=cal,path=path,calls=calls,diagnostics=diagnostics)


def test_missing_known_epoch_stops_before_any_cache_or_calibration(context, monkeypatch):
    from stowage.linear import cache
    def forbidden(*args,**kwargs):
        pytest.fail('stale production epoch reached domain/cache/calibration work')
    monkeypatch.setattr(context.cal,'domain_for',forbidden)
    monkeypatch.setattr(cache,'load_payload',forbidden)
    monkeypatch.setattr(context.cal,'calibrate_family',forbidden)
    with pytest.raises(ValueError,match='production linear policy epoch is stale; requalify'):
        context.cal.calibrated_family(context.family,None)
    assert not context.calls


def test_known_missing_default_qualification_fails_closed(context):
    with pytest.raises(ValueError,match='production linear policy epoch is stale'):
        policy.qualify_policy(context.family,None,context.diagnostics)


def test_explicit_qualification_certificate_is_not_solver_usable(context):
    _,diagnostics = context.cal.calibrated_family(context.family,None,force_rebuild=True)
    assert len(context.calls) == 1
    assert diagnostics['margin_policy']['production_usable'] is False
    context.model.measurement_diagnostics = diagnostics
    with pytest.raises(ValueError,match='invalid default margin-policy'):
        policy.default_reserves(context.model, direction="conservative")


def test_matching_registered_profile_remains_valid(context, tmp_path):
    evidence = tmp_path/'observed.json'; evidence.write_text('measured')
    bank = json.loads(context.path.read_text())
    bank['entries']['new-response'] = dict(domain=context.diagnostics['family_domain'],
        policy_application_identity=policy.policy_application_identity(),
        station_x_m=context.model.station_x_m.tolist(),
        reference_identity=context.diagnostics['reference_identity'],
        physical_unsafe=policy._zeros(2),reference_transfer=policy._zeros(2),
        equivalence={'numeric_equal':True},evidence=[dict(path=str(evidence),
            sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())])
    from stowage.linear.qualification import qualify_holdout
    bank['entries']['new-response']['holdout_qualification'] = qualify_holdout(
        context.diagnostics['signed_errors'], policy._zeros(2), policy._zeros(2),
        station_x_m=[0.,1.], calibration_seed=1, holdout_seed=2,
        calibration_samples=192, holdout_samples=64)
    context.path.write_text(json.dumps(bank))
    policy.assert_policy_epoch(context.family,'new-response')
    context.diagnostics['margin_policy'] = policy.qualify_policy(context.family,None,context.diagnostics)
    context.model.measurement_diagnostics = context.diagnostics
    assert policy.default_reserves(context.model, direction="conservative")['lower_unsafe']['gm_m'] == pytest.approx(.011)


def test_registered_calibration_only_bank_fails_before_cache_work(context):
    bank=json.loads(context.path.read_text())
    bank['entries']['new-response']={}
    context.path.write_text(json.dumps(bank))
    with pytest.raises(ValueError,match='requires independent holdout'):
        policy.assert_policy_epoch(context.family,'new-response')
    with pytest.raises(ValueError,match='requires independent holdout'):
        policy.qualify_policy(context.family,None,context.diagnostics)
    certificate=policy.qualify_policy(context.family,None,context.diagnostics,
        allow_reference_qualification=True,bank={'format':1,'known_vessel_ids':['known'],
                                                'entries':{}})
    # Explicit inline research banks keep their documented offline behavior.
    assert certificate['bank_source']=='inline'


def test_unknown_geometry_retains_family_calibration_behavior(context):
    context.family.parameters['id'] = 'research-geometry'
    _,diagnostics = context.cal.calibrated_family(context.family,None)
    assert len(context.calls) == 1
    assert diagnostics['margin_policy']['production_usable'] is True
    context.model.measurement_diagnostics = diagnostics
    assert policy.default_reserves(context.model, direction="conservative")['lower_unsafe']['gm_m'] == pytest.approx(.011)


def test_inline_bank_is_an_explicit_offline_hook(context):
    certificate = policy.qualify_policy(context.family,None,context.diagnostics,
        bank={'format':1,'known_vessel_ids':['known'],'entries':{}})
    assert certificate['production_usable'] is True
    assert certificate['bank_source'] == 'inline'

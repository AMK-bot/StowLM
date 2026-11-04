"""Applying a measured reserve binds executable policy source, not prose."""
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from stowage.linear import dependencies, margin_policy as policy

APPLICATION_FILES=("linear/margin_policy.py", "linear/boundary_calibration.py",
                   "linear/qualification.py", "linear/operations.py")


@pytest.fixture
def source_tree(monkeypatch,tmp_path):
    original=dependencies._ROOT
    names={name for profile in dependencies._PROFILES.values() for name in profile}
    for name in names|set(APPLICATION_FILES):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((original/name).read_bytes())
    monkeypatch.setattr(dependencies,"_ROOT",tmp_path)
    monkeypatch.setattr(policy,"_BANK_PATH",tmp_path/"bank.json")
    return tmp_path


@pytest.fixture
def certified_model(source_tree,monkeypatch):
    from stowage.linear import cache
    monkeypatch.setattr(cache,"response_calibration_identity",lambda family:"response")
    model=NS(station_x_m=np.array([0.,5.]),water_density_kg_m3=1025.)
    signed={side:{metric:[.01,.02] if metric in ("sf_n","bm_nm") else .01
                  for metric in policy._METRICS} for side in policy._SIDES}
    diagnostics=dict(signed_errors=signed,family_domain={"kg_min_m":0.,"kg_max_m":20.},
        reference_identity={"method":1,"qualified":True},
        family_calibration_cache_key="calibration",response_calibration_identity="response")
    family=NS(template=model)
    # An explicit offline bank also requires a fresh application certificate;
    # skipping only the default-bank checksum must not skip executable policy.
    diagnostics["margin_policy"]=policy.qualify_policy(family,None,diagnostics,
                                                       bank={"format":1,"entries":{}})
    model.measurement_diagnostics=diagnostics
    assert policy.default_reserves(model,direction="conservative") is not None
    return model


@pytest.mark.parametrize("name",APPLICATION_FILES)
def test_executable_policy_change_revokes_application_identity_only(source_tree,name):
    before=policy.policy_inputs()
    numerical={kind:dependencies.dependency_fingerprint(kind)
               for kind in ("coefficient","calibration","instance")}
    path=source_tree/name
    path.write_text(path.read_text()+"\nAPPLICATION_EXECUTABLE_REGRESSION = 17\n")
    assert policy.policy_inputs()!=before
    for kind,value in numerical.items():
        # operations already supplies numerical row/eligibility code. The new
        # policy-only files must not evict otherwise reusable residual arrays.
        changed=name in dependencies._PROFILES[kind]
        assert (dependencies.dependency_fingerprint(kind)!=value)==changed


@pytest.mark.parametrize("name",APPLICATION_FILES)
def test_old_model_rejects_changed_policy_without_manual_version_bump(source_tree,certified_model,name):
    path=source_tree/name
    path.write_text(path.read_text()+"\nAPPLICATION_EXECUTABLE_REGRESSION = 19\n")
    with pytest.raises(ValueError,match="application|policy.*changed"):
        policy.default_reserves(certified_model,direction="conservative")


@pytest.mark.parametrize("name",APPLICATION_FILES)
def test_policy_comments_and_docstrings_preserve_identity(source_tree,certified_model,name):
    before=policy.policy_inputs();path=source_tree/name
    text=path.read_text();assert text.startswith('"""')
    path.write_text(text.replace('"""','"""Documentation only. ',1)+"\n# comment only\n")
    assert policy.policy_inputs()==before
    assert policy.default_reserves(certified_model,direction="conservative") is not None


def test_registered_bank_policy_epoch_rejects_source_change_before_recalibration(source_tree):
    family=NS(parameters={"id":"registered"})
    identity=policy.policy_inputs().get("application_identity","old-unbound-application")
    bank=dict(format=1,known_vessel_ids=["registered"],entries={"response":dict(
        holdout_qualification={},policy_application_identity=identity)})
    policy._BANK_PATH.write_text(json.dumps(bank))
    policy.assert_policy_epoch(family,"response")
    path=source_tree/"linear/qualification.py"
    path.write_text(path.read_text()+"\nAPPLICATION_EXECUTABLE_REGRESSION = 23\n")
    with pytest.raises(ValueError,match="application|policy epoch.*stale"):
        policy.assert_policy_epoch(family,"response")
    # Explicit forced research may collect a replacement, never use it to solve.
    policy.assert_policy_epoch(family,"response",allow_unqualified=True)


def test_registered_bank_without_application_identity_requires_requalification(source_tree):
    family=NS(parameters={"id":"registered"})
    policy._BANK_PATH.write_text(json.dumps(dict(format=1,known_vessel_ids=["registered"],
        entries={"response":{"holdout_qualification":{}}})))
    with pytest.raises(ValueError,match="application|policy epoch.*stale"):
        policy.assert_policy_epoch(family,"response")


@pytest.mark.parametrize('inline',[False,True])
def test_new_certificate_cannot_relabel_old_registered_bank(source_tree,certified_model,inline):
    family=NS(template=certified_model,parameters={'id':'registered'})
    diagnostics=certified_model.measurement_diagnostics
    bank=dict(format=1,known_vessel_ids=['registered'],entries={'response':dict(
        holdout_qualification={},policy_application_identity=policy.policy_application_identity())})
    policy._BANK_PATH.write_text(json.dumps(bank))
    path=source_tree/'linear/qualification.py'
    path.write_text(path.read_text()+'\nAPPLICATION_EXECUTABLE_REGRESSION = 29\n')
    kwargs={'bank':bank} if inline else {}
    with pytest.raises(ValueError,match='application epoch is stale'):
        policy.qualify_policy(family,None,diagnostics,**kwargs)
    replacement=policy.qualify_policy(family,None,diagnostics,
        allow_reference_qualification=True,**kwargs)
    assert replacement['production_usable'] is False
    diagnostics['margin_policy']=replacement
    with pytest.raises(ValueError,match='invalid default margin-policy'):
        policy.default_reserves(certified_model,direction='conservative')


def test_application_version_changes_policy_without_discarding_numeric_measurements(source_tree,monkeypatch):
    before=policy.policy_application_identity()
    numerical={kind:dependencies.dependency_fingerprint(kind)
               for kind in ('coefficient','calibration','instance')}
    monkeypatch.setattr(dependencies,'LINEAR_APPLICATION_VERSION','next-application')
    assert policy.policy_application_identity()!=before
    assert all(dependencies.dependency_fingerprint(kind)==value for kind,value in numerical.items())

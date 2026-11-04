"""Reproduced migration-review highs before changing admission."""
import importlib.util,json
from pathlib import Path
from types import SimpleNamespace
import pytest
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]

def driver(name):
 s=importlib.util.spec_from_file_location('review_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

@pytest.mark.parametrize('damage',['mode','epoch'])
def test_completed_diagnostic_freeze_cannot_be_relabelled(tmp_path,monkeypatch,damage):
 f=driver('freeze');pin=dict(schema_version=1,sources={},files={},sha256='fixture-cache')
 monkeypatch.setattr(f,'snapshot_numerical_caches',lambda root:pin)
 monkeypatch.setattr(f,'verify_numerical_caches',lambda value:value)
 monkeypatch.setattr(f,'migration_gate',lambda *a,**k:dict(passed=True,component_epoch={},issues=[],canaries={'numerical_cache_sha256':pin['sha256']}))
 value=f.create_manifest(ROOT,paths=[],diagnostic=True)
 if damage=='mode':value.update(diagnostic=False,evidence_eligible=True)
 else:value['epoch_id']='diagnostic-relabelled'
 path=tmp_path/'freeze.json';f.atomic_json(path,value)
 with pytest.raises(ValueError,match='creation|epoch|mode'):
  f.check_manifest(path,diagnostic=damage!='mode')


def test_resolved_override_cannot_relax_frozen_weight_tolerance():
 policy=driver('rule_policy')
 from stowage.rules.catalogue import rule_freeze_v1
 with rule_freeze_v1(enabled=True) as snapshot:
  frozen={'component_epoch':{'catalogue_loaded_sha256':snapshot.content_sha256}}
  instance=SimpleNamespace(rule_overrides={'expert.e2.weight_order':{'weight_order_tolerance_kg':100}})
  with pytest.raises(ValueError,match='resolved.*weight'):
   policy.enforce_instance_weight_policy(instance,frozen)


@pytest.mark.parametrize('overrides',[
 {},{'expert.e2.weight_order':{'weight_order_tolerance_kg':0}},
 {'expert.e1.heavy_bottom':{'weight_order_tolerance_kg':0},'expert.e2.weight_order':{'weight_order_tolerance_kg':0}}])
def test_exact_frozen_weight_policy_resolves_allowed_zero_overrides(overrides):
 policy=driver('rule_policy')
 from stowage.rules.catalogue import rule_freeze_v1
 with rule_freeze_v1(enabled=True) as snapshot:
  frozen={'component_epoch':{'catalogue_loaded_sha256':snapshot.content_sha256}}
  assert policy.enforce_instance_weight_policy(SimpleNamespace(rule_overrides=overrides),frozen)['weight_order_tolerance_kg']==0


@pytest.mark.parametrize('overrides',[
 {'expert.e2.weight_order':{'weight_order_mode':'weight_class'}},
 {'expert.e2.weight_order':{'weight_order_tolerance_kg':True}},
 {'expert.e1.heavy_bottom':{'weight_order_tolerance_kg':0}},
 {'expert.e2.weight_order':{'weight_order_tolerance_kg':float('nan')}}])
def test_invalid_or_legacy_resolved_weight_policy_refuses(overrides):
 policy=driver('rule_policy')
 from stowage.rules.catalogue import rule_freeze_v1
 with rule_freeze_v1(enabled=True) as snapshot:
  frozen={'component_epoch':{'catalogue_loaded_sha256':snapshot.content_sha256}}
  with pytest.raises(ValueError,match='resolved.*weight'):
   policy.enforce_instance_weight_policy(SimpleNamespace(rule_overrides=overrides),frozen)

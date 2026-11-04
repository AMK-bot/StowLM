"""Portable verification tests use a temporary authorized signer, not real physics."""
import importlib.util
from pathlib import Path
import pytest

HERE=Path(__file__).resolve().parent

def module(name):
 s=importlib.util.spec_from_file_location('signed_unit_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

@pytest.fixture
def signed(tmp_path,monkeypatch):
 policy=module('rule_policy');receipts=module('trusted_receipts')
 monkeypatch.setattr(receipts,'PRIVATE_KEY',tmp_path/'private/key');monkeypatch.setattr(receipts,'TRUST_STORE',tmp_path/'trust')
 receipts.initialize_signer();original=policy._module
 monkeypatch.setattr(policy,'_module',lambda name:receipts if name=='trusted_receipts' else original(name))
 summary=dict(epoch_id='e',g1_met=True,min_yield={'tiny':.2},solve_yield={'tiny':1},d62_crossing={'tiny':{'observed':1}})
 payload=dict(complete=True,epoch_id='e',approval_body_sha256=policy._approval_basis(summary),
  component_epoch=original('migration_evidence').current_component_epoch(),
  required_rules=policy.enforce_rule_policy(HERE.parents[1]),admission_implementation=policy._admission_implementation(),
  counts={'live_rechecked_solver_plans':1,'returned_solver_plans':1},d62_crossing=summary['d62_crossing'])
 def sign(value):
  r=receipts.sign_execution('migration-admission-v2',value,{},tmp_path/'receipt',
   context={'epoch_id':'e','diagnostic':False,'evidence_eligible':True})
  return {**summary,'migration_admission':receipts.portable_execution(r)}
 return policy,summary,payload,sign


def test_unsigned_approval_cannot_use_consistent_raw_reports(signed):
 policy,summary,_,_=signed
 summary['migration_evidence']={'checker_ran':True,'mask_ran':True,'complete':True}
 with pytest.raises(ValueError,match='signed admission'):policy.validate_migration_approval(summary,'e')


def test_signed_admission_is_portable_and_bound_to_approval(signed):
 policy,summary,payload,sign=signed
 approved=sign(payload)
 assert policy.validate_migration_approval(approved,'e')['complete']
 approved['min_yield']={'tiny':.1}
 with pytest.raises(ValueError,match='approved summary'):policy.validate_migration_approval(approved,'e')


def test_approval_survives_gold_and_sft_admission_without_reserializing_numbers(signed):
 policy,_,payload,sign=signed
 approved=sign(payload)
 admitted=policy.validate_g1_approval(approved,'e',['tiny'])
 # A value such as solve_yield=1 must not become 1.0 in a signed approval.
 assert policy._approval_basis(admitted)==policy._approval_basis(approved)
 assert policy.validate_g1_approval(admitted,'e',['tiny'])==approved


@pytest.mark.parametrize('fault',['population','components','gate_revision','empty'])
def test_authenticated_but_incomplete_or_stale_admission_refuses(signed,fault):
 policy,_,payload,sign=signed
 if fault=='population':payload['counts']['returned_solver_plans']=999
 if fault=='components':payload['component_epoch']['checker_version']='stale'
 if fault=='gate_revision':payload['admission_implementation']['rule_policy.py']='0'*64
 if fault=='empty':payload['counts']={'live_rechecked_solver_plans':0,'returned_solver_plans':0}
 approved=sign(payload)
 with pytest.raises(ValueError):policy.validate_migration_approval(approved,'e')

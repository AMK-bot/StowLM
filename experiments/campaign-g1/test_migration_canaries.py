"""Canary reports require gate-owned execution, exact attribution and clean twins."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

HERE=Path(__file__).resolve().parent


def module():
    spec=importlib.util.spec_from_file_location('tested_migration_canaries',HERE/'migration_canaries.py')
    result=importlib.util.module_from_spec(spec);sys.modules[spec.name]=result;spec.loader.exec_module(result)
    return result


@pytest.fixture
def root(tmp_path):
    (tmp_path/'src').mkdir();(tmp_path/'src/current.py').write_text('# synthetic source epoch\n')
    return tmp_path


def journal(m,root,cases):
    payload=dict(schema_version=1,diagnostic=False,evidence_eligible=True,
        source=m.source_identity(root),driver_sha256=m.driver_identity(),versions=m.component_versions(),cases=cases)
    path=root/'journal.json';path.write_text(json.dumps(payload));return path,payload


def test_missing_journal_refuses_every_required_rule_and_accept_boundary(root):
    m=module();report=m.canary_report(root,root/'absent.json')
    assert report['evidence_eligible'] is False
    assert report['missing_cases']==list(m.CANARIES)
    assert {'e6_soft_heavy_on_deck','kg_equal_mass','yard_crossing_at_h','oog_empty_killed_slots'}<=set(m.CANARIES)
    assert all(row['missing']==['missing_proof'] for row in report['cases'].values())


def test_internally_consistent_raw_reports_have_no_execution_authority(root):
    m=module()
    forged={'status':'observed','passed':True,'eligible':True,'checker_passed':False,
        'mask_denied':True,'artifacts':{},'variants':{'clean':{'eligible':True},'defect':{'eligible':True}}}
    result=m._validate_case('kg_inversion_within_class',forged,m.component_versions())
    assert not result['eligible']
    assert result['missing']==['gate_execution_receipt_missing']


def test_raw_json_proof_cannot_overrule_actual_mask_admission(root,monkeypatch):
    # This regression failed against the old raw-JSON admission implementation.
    m=module();calls=[]
    def live(name,entry,versions):
        calls.append(name)
        return {'eligible':False,'missing':['production_mask_denial'],
            'expected_rule':m.CANARIES[name],'observed_mask_status':'admitted'}
    monkeypatch.setattr(m,'_fresh_case_result',live)
    result=m._validate_case('kg_inversion_within_class',{'status':'observed','artifacts':{},'passed':True},m.component_versions())
    assert calls==['kg_inversion_within_class']
    assert not result['eligible'] and result['missing']==['production_mask_denial']


@pytest.mark.parametrize('changed',['source','driver','versions','diagnostic','evidence_eligible'])
def test_obsolete_or_diagnostic_journal_refuses_every_case(root,changed):
    m=module();path,payload=journal(m,root,{})
    if changed=='source':payload['source']['sha256']='0'*64
    elif changed=='driver':payload['driver_sha256']='0'*64
    elif changed=='versions':payload['versions']={'checker':'old'}
    else:payload[changed]=changed=='diagnostic'
    path.write_text(json.dumps(payload));report=m.canary_report(root,path)
    assert report['missing_cases']==list(m.CANARIES)
    assert all(row['missing']==['journal_identity'] for row in report['cases'].values())


@pytest.mark.parametrize('cases',[None,[],'all passed',{'unknown':{'passed':True}}])
def test_malformed_inventory_refuses(root,cases):
    m=module();path,_=journal(m,root,cases);report=m.canary_report(root,path)
    assert report['missing_cases']==list(m.CANARIES)
    assert all(row['missing']==['journal_identity'] for row in report['cases'].values())


def test_physical_list_canary_has_loadable_owner_contract_and_no_waiver(root):
    m=module();case=m.build_case('inport_list_failure')
    assert case['status']=='missing' and case['missing']==['external_pair_missing']
    assert case['contract']=='inport-list-v1'
    assert case['expected_rule']=='port.stability.cargo_operations'


def test_declared_receipt_requires_a_pinned_numerical_snapshot(root):
    m=module();path,_=journal(m,root,{next(iter(m.CANARIES)):{'status':'observed',
        'execution_receipt':{'path':'invented','sha256':'0'*64}}})
    result=m.canary_report(root,path)
    assert all(row['missing']==['journal_identity'] for row in result['cases'].values())
    assert all('numerical' in row['reason'] for row in result['cases'].values())


@pytest.mark.parametrize('drift',['journal','cache_pin','source'])
def test_metadata_drift_during_verification_never_qualifies(root,monkeypatch,drift):
    from hashlib import sha256
    m=module();pin=root/'pin.json';pin.write_text(json.dumps({'sha256':'a'*64}))
    path,payload=journal(m,root,{next(iter(m.CANARIES)):{'status':'observed','execution_receipt':{}}})
    payload['numerical_cache_pin']={'path':str(pin),'sha256':sha256(pin.read_bytes()).hexdigest()}
    path.write_text(json.dumps(payload));calls=[]
    monkeypatch.setattr(m,'_load',lambda name:SimpleNamespace(verify_numerical_caches=lambda pin:None))
    def metadata_only(name,entry,versions):
        if not calls:
            changed=path if drift=='journal' else pin if drift=='cache_pin' else root/'src/current.py'
            changed.write_text(changed.read_text()+'\n# replacement\n')
        calls.append(name);return {'eligible':True,'missing':[],'expected_rule':m.CANARIES[name]}
    monkeypatch.setattr(m,'_validate_case',metadata_only)
    report=m.canary_report(root,path)
    assert report['missing_cases']==list(m.CANARIES)
    assert all(row['missing']==['journal_identity'] for row in report['cases'].values())


def report_with(ids=(),*,passed=False,complete=True):
    from stowage.model import CheckReport,PartReport,Violation
    return CheckReport(passed=passed,violations=[Violation(rule_id=i,severity='hard',call_index=0,message='unit observation') for i in ids],
        parts={name:PartReport(name=name,ran=complete) for name in
            ('moves','rules','stability','strength','visibility','lashing','constraints','voyage')})


def test_defect_checker_requires_exact_rule_and_complete_parts():
    m=module();rule='port.dg.refused_class'
    assert m._checker_observation(report_with([rule]),rule,'deny')['eligible']
    assert not m._checker_observation(report_with([rule,'imdg.stowage.3_2']),rule,'deny')['eligible']
    assert not m._checker_observation(report_with([rule],complete=False),rule,'deny')['eligible']


def test_clean_twin_and_accept_canaries_must_fully_pass():
    m=module();rule='expert.e6.heavy_under_deck'
    assert m._checker_observation(report_with(passed=True),rule,'accept')['eligible']
    assert not m._checker_observation(report_with([rule]),rule,'accept')['eligible']
    assert not m._checker_observation(report_with(passed=True,complete=False),rule,'accept')['eligible']


def test_kg_alias_family_is_exact_and_excludes_unrelated_violations():
    m=module();ids=['expert.e1.heavy_bottom','expert.e2.weight_order']
    assert m._checker_observation(report_with(ids),ids[1],'deny',expected_rules=ids)['eligible']
    assert not m._checker_observation(report_with(ids+['expert.moves.hatch_clear']),ids[1],'deny',expected_rules=ids)['eligible']


@pytest.mark.parametrize('rules',[[],['imdg.stowage.3_2'],['port.dg.refused_class','imdg.stowage.3_2']])
def test_generic_or_wrong_mask_denial_never_counts_as_rule_proof(rules):
    m=module();observed=dict(status='rejected',rule_ids=rules,production_c2_active=True)
    assert not m._mask_matches(observed,'port.dg.refused_class','deny')


def test_mask_denial_uses_observed_reason_and_accept_requires_production_c2():
    m=module();rule='port.dg.refused_class'
    assert m._mask_matches(dict(status='rejected',rule_ids=[rule],production_c2_active=True),rule,'deny')
    assert not m._mask_matches(dict(status='admitted',production_c2_active=False),rule,'accept')
    assert m._mask_matches(dict(status='admitted',production_c2_active=True),rule,'accept')


def test_canary_rule_ids_exist_in_current_catalogue():
    import tomllib
    present={row['id'] for row in tomllib.loads((HERE.parents[1]/'src/stowage/rules/catalogue.toml').read_text())['rules']}
    assert set(module().CANARIES.values())<=present


def test_review_r1_forged_checker_and_mask_with_matching_hashes_are_refused(root,monkeypatch):
    """Hash-bound reports are still caller-written assertions, not execution."""
    from dataclasses import asdict
    from hashlib import sha256
    from stowage.model.fixtures import build_instance
    from stowage.model import MovePlan,CheckReport,PartReport,Violation
    from stowage.model.moves import Load
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    from stowage.feasibility import FeasibilityConfig
    from llm.stowlm.mask import MaskConfig,MASK_VERSION
    from llm.stowlm.format import ActionCodec,load_tokenizer
    m=module();instance=build_instance();box=instance.containers[0]
    plan=MovePlan(id='unit-forged',instance_id=instance.id,call_index=0,
        moves=[Load(call_index=0,container_id=box.id,src=instance.yard.stacks[0].key+'-01',dst='0010182')])
    action=get_action_space().encode(Place(box.id,'0010182'))
    built={'status':'ready','input':instance.model_dump(mode='json'),'plan':plan.model_dump(mode='json'),'action':action}
    monkeypatch.setattr(m,'build_case',lambda name:built)
    parents=['checker.'+kind+'.sha256:'+sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        for kind,value in [('instance',built['input']),('plan',built['plan'])]]
    checker=CheckReport(passed=False,violations=[Violation(rule_id='expert.e2.weight_order',severity='hard',call_index=0,message='FORGED unit metadata; nothing executed')],
        parts={name:PartReport(name=name) for name in ('moves','rules','stability','strength','visibility','lashing','constraints','voyage')},
        provenance={'versions':m.component_versions(),'parents':parents})
    artifacts={}
    def save(kind,value):
        path=root/(kind+'.json');path.write_text(json.dumps(value));artifacts[kind]={'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest()}
    save('input',built['input']);save('plan',built['plan']);save('checker',checker.model_dump(mode='json'))
    tokens=list(ActionCodec(load_tokenizer(),[b.id for b in instance.containers]).encode_action(get_action_space().decode(action)))
    save('mask',dict(mask_version=MASK_VERSION,feasibility_config=asdict(FeasibilityConfig()),mask_config=asdict(MaskConfig()),
        status='rejected',error_type='MaskError',versions=m.component_versions(),production_c2_active=True,global_screen='on',default_configuration=True,
        input_sha256=artifacts['input']['sha256'],plan_sha256=artifacts['plan']['sha256'],expected_rule='expert.e2.weight_order',
        action_tokens=tokens,prefix_tokens=[],denied_token=tokens[0],allowed_tokens=[]))
    result=m._validate_case('kg_inversion_within_class',{'status':'observed','artifacts':artifacts},m.component_versions())
    assert not result['eligible'] and result['missing']==['gate_execution_receipt_missing']


def test_clean_e6_soft_feature_is_a_metric_not_a_soft_violation():
    from stowage.model import Violation
    m=module();checker=report_with(passed=True)
    checker.violations=[Violation(rule_id='expert.e6.heavy_under_deck',severity='soft',call_index=0,message='unit soft violation')]
    assert not m._checker_observation(checker,'expert.e6.heavy_under_deck','accept')['eligible']


def test_worker_pins_the_exact_input_and_plan_snapshots_checked(root,monkeypatch):
    """No validate-then-re-read-for-hash in the new controlled writer either."""
    from stowage.model.fixtures import build_instance
    from stowage.model import MovePlan
    import stowage.checker
    m=module();instance=build_instance();plan=MovePlan(id='unit',instance_id=instance.id,call_index=0,moves=[])
    checker=report_with(passed=True);checker.provenance.versions=m.component_versions()
    monkeypatch.setattr(stowage.checker,'check',lambda *a:checker)
    monkeypatch.setattr(stowage.checker,'report_matches_inputs',lambda *a:True)
    monkeypatch.setattr(m,'_mask_observation',lambda *a:dict(status='admitted',production_c2_active=True,rule_ids=[]))
    original=m.read_snapshot;reads={}
    def observed(path,*a,**kw):
        name=Path(path).name;reads[name]=reads.get(name,0)+1
        return original(path,*a,**kw)
    monkeypatch.setattr(m,'read_snapshot',observed)
    result=m._variant(root/'case',dict(input=instance.model_dump(mode='json'),plan=plan.model_dump(mode='json')), 'expert.e6.heavy_under_deck','accept')
    assert result['eligible']
    assert reads['input.json']==reads['plan.json']==1


def test_alternate_cache_snapshot_is_impossible_in_production_cli(root):
    with pytest.raises(SystemExit) as exc:
        module().main(['--execute','--diagnostic-cache-root',str(root)])
    assert exc.value.code==2


def test_owned_diagnostic_cache_never_adopts_mutable_attempt(root,monkeypatch):
    m=module();base=m.HERE/'unit/cache-attempts/not-reusable'
    pin={'sources':{name:str(base/name) for name in ('linear','vessels','stability')}}
    adapter=SimpleNamespace()
    modules={'cache_adapter':adapter,'pin_transport':SimpleNamespace(read_pin=lambda:pin),
             'freeze':SimpleNamespace(verify_numerical_caches=lambda *a,**k:None)}
    monkeypatch.setenv('CAMPAIGN_DIAGNOSTIC_OWNED_BASE','1')
    monkeypatch.setattr(m,'_load',lambda name:modules[name])
    with pytest.raises(ValueError,match='never a mutable attempt'):
        m._prepare_worker_cache(root,diagnostic=True)


def test_explicit_baseline_worker_uses_diagnostic_argument(root,monkeypatch):
    m=module();calls=[]
    monkeypatch.setattr(m,'_prepare_worker_cache',lambda output,*,diagnostic:calls.append(diagnostic))
    monkeypatch.setattr(m,'_cases_module',lambda:SimpleNamespace(generate_baseline=lambda path:SimpleNamespace(id='unit-only')))
    assert m.main(['--generate-baseline',str(root/'baseline.json'),'--artifact-dir',str(m.HERE/'unit-baseline'),'--diagnostic'])==0
    assert calls==[True]


def test_standalone_baseline_worker_refuses_non_diagnostic_mode(root):
    with pytest.raises(SystemExit) as exc:
        module().main(['--generate-baseline',str(root/'baseline.json'),'--artifact-dir',str(module().HERE/'unit-baseline')])
    assert exc.value.code==2


def test_existing_stale_baseline_is_refreshed_before_production_suite(root,monkeypatch):
    m=module();baseline=root/'stale.json';baseline.write_text('{}');generated=[]
    provenance=SimpleNamespace(versions={'rule_catalogue':'old'},stubs={},input_versions={})
    def generate_current():
        generated.append('current');provenance.versions['rule_catalogue']='new'
    fake=SimpleNamespace(DEFAULT_BASELINE=baseline,
        acquire_baseline=lambda:(SimpleNamespace(provenance=provenance),{}),
        generate_baseline=generate_current)
    monkeypatch.setattr(m,'_prepare_worker_cache',lambda *a,**k:None)
    monkeypatch.setattr(m,'_cases_module',lambda:fake)
    monkeypatch.setattr(m,'component_versions',lambda:{'rule_catalogue':'new'})
    monkeypatch.setattr(m,'CANARIES',{})
    monkeypatch.setattr(m,'_load',lambda name:SimpleNamespace(read_pin=lambda:{},verify_numerical_caches=lambda *a:None))
    monkeypatch.setenv('CAMPAIGN_CANARY_SOURCE_SHA256',m.source_identity(m.ROOT)['sha256'])
    monkeypatch.setenv('CAMPAIGN_CANARY_DRIVER_SHA256',m.driver_identity())
    m._worker_suite(root/'out',diagnostic=False,baseline_if_missing=True)
    assert generated==['current']


def test_worker_requires_source_and_driver_pins_from_before_process_birth(monkeypatch):
    m=module();context={'source':{'sha256':'a'*64},'driver_sha256':'b'*64}
    monkeypatch.setenv('CAMPAIGN_CANARY_SOURCE_SHA256','c'*64)
    monkeypatch.setenv('CAMPAIGN_CANARY_DRIVER_SHA256','b'*64)
    with pytest.raises(ValueError,match='controlled parent'):
        m._parent_execution_guard(context,diagnostic=False)

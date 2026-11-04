"""Standalone producers must preserve the same acceptance contract as batch."""
from copy import deepcopy
import importlib
import json
import sys

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from .store import canonical_rule_set
from .test_sequence import example


@pytest.fixture(params=['sequence','sequence_blocks','recover_candidate'])
def cli(request,monkeypatch,tmp_path):
    import stowage.io
    module=importlib.import_module(__package__+'.'+request.param)
    batch=importlib.import_module(__package__+'.batch')
    instance,plan=example()
    rules=canonical_rule_set([dict(type='max_tiers',bay=1,zone='hold',max_tiers=1)])
    source=dict(instance_path='synthetic.json',instance=instance.model_dump(mode='json'),
                instance_sha256=sha256_json(instance.model_dump(mode='json')),plan=plan.model_dump(mode='json'),
                canonical_rules=rules,rules=rules,base_plan=plan.model_dump(mode='json'),profile={'name':'standard'})
    files={name:tmp_path/(name+'.json') for name in ('source','frames','proof','target')}
    files['frames'].write_text(json.dumps(dict(physics_epoch='test-epoch')))
    files['proof'].write_text('{}')
    monkeypatch.setattr(stowage.io,'load_instance',lambda _:instance)
    monkeypatch.setattr(batch,'admit',lambda *a,**kw:dict(load1=20))
    monkeypatch.setattr(batch,'binding_fingerprint',lambda:'fixed-source')
    calls=[]
    behavior={'bad_gate':None}
    repaired=plan.model_copy(update={'id':plan.id+'.repaired'})
    def repair(*args,**kwargs):
        calls.append((args,kwargs))
        gate=dict(passed=True,instance_sha256=source['instance_sha256'],
            plan_sha256=sha256_json(repaired.model_dump(mode='json')),checker=dict(passed=True),base_passed=True,
            rules=[dict(envelope=rule,passed=True) for rule in rules],
            gauges=dict(passed=True,all_frames=True,n_frames=len(repaired.moves)+1,
                        outside_limits_steps=[],unavailable_steps=[]),
            sequence_repair=dict(final_native_frame_checkpoint=str(tmp_path/'native.frames.json')))
        if behavior['bad_gate']=='missing_rules':gate['rules']=[]
        elif behavior['bad_gate']=='wrong_envelope':
            gate['rules'][0]['envelope']=canonical_rule_set([dict(type='close_bay',bay=3)])[0]
        elif behavior['bad_gate']=='wrong_plan':gate['plan_sha256']='f'*64
        elif behavior['bad_gate']=='wrong_instance':gate['instance_sha256']='f'*64
        elif behavior['bad_gate']=='failed_gate':gate['passed']=False
        elif behavior['bad_gate']=='failed_base':gate['base_passed']=False
        return repaired,gate
    monkeypatch.setattr(module,'recover_candidate' if request.param=='recover_candidate' else 'repair_candidate',repair)
    def run():
        files['source'].write_text(json.dumps(source))
        args=[str(files['source']),str(files['proof']),str(files['frames']),str(files['target'])] if request.param=='recover_candidate' else \
             [str(files['source']),str(files['frames']),str(files['target'])]
        monkeypatch.setattr(sys,'argv',[request.param,*args])
        module.main()
        return json.loads(files['target'].read_text())
    return source,plan,rules,calls,behavior,run


def test_cli_forwards_all_confirmed_rules_and_baseline(cli):
    source,plan,rules,calls,_,run=cli
    row=run()
    assert row['status']=='checked_plan'
    assert calls[0][1].get('rules')==rules
    assert calls[0][1].get('base_plan')==plan
    assert [audit['envelope'] for audit in row['gate']['rules']]==row['canonical_rules']


@pytest.mark.parametrize('problem',['instance_hash','instance_body','plan_instance','base_instance'])
def test_cli_rejects_source_identity_mismatch_before_repair(cli,problem):
    source,_,_,calls,_,run=cli
    if problem=='instance_hash':source['instance_sha256']='f'*64
    elif problem=='instance_body':source['instance']=dict(source['instance'],id='another-instance')
    elif problem=='plan_instance':source['plan']=dict(source['plan'],instance_id='another-instance')
    else:source['base_plan']=dict(source['base_plan'],instance_id='another-instance')
    row=run()
    assert row['status']=='sequence_repair_failed' and row['passed'] is False
    assert calls==[]


@pytest.mark.parametrize('problem',['missing_rules','wrong_envelope','wrong_plan','wrong_instance','failed_gate','failed_base'])
def test_cli_rejects_unbound_or_failed_returned_gate(cli,problem):
    _,_,_,_,behavior,run=cli
    behavior['bad_gate']=problem
    row=run()
    assert row['status']=='sequence_repair_failed' and row['passed'] is False
    assert row['plan'] is None

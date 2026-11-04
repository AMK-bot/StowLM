"""D137 fully-checked preference proof remains strict across training epochs."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[6]

@pytest.fixture
def subject(monkeypatch,tmp_path):
    # Reuse the frozen CPU fixture contract, substituting only the staged module.
    path=ROOT/'tests/llm/exit/test_d129_reverify.py'
    spec=importlib.util.spec_from_file_location('_d137_reverify_fixture',path)
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    old=fixture.subject.__wrapped__(monkeypatch,tmp_path)
    spec=importlib.util.spec_from_file_location('_d137_reverify',Path(__file__).with_name('reverify.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'_services',lambda:old.services)
    monkeypatch.setattr(module,'_epoch',lambda:old.epoch)
    state={'pass':True,'available':True,'calls':[]}
    base_verify=old.services['verify_candidate']
    def verify(instance,candidate,**kw):
        if candidate.plan.value['id']=='winner':
            return base_verify(instance,candidate,**kw)
        state['calls'].append(kw)
        return SimpleNamespace(passed=state['pass'],plan=candidate.plan,weighted_moves=7,
            details={'report':{'passed':state['pass']},'mask':{'passed':state['pass']},
                     'checker_available':state['available']})
    old.services['verify_candidate']=verify
    pair=old.add_pair('strictly_more_moves')
    pair['evidence']['fully_checked']=True
    pair['rejected_witness'].pop('token_ids')
    def run():
        return module.reverify_training_rows([old.row],output_dir=tmp_path/'fresh',
            training_epoch=old.epoch,model_identity={'parent':'exact-adapter'})
    return SimpleNamespace(state=state,run=run)


def test_costlier_pair_is_rechecked_and_fresh_proof_retained(subject):
    result=subject.run()
    assert result['counts']['pairs_kept']==1
    evidence=result['pairs'][0]['evidence']
    assert evidence['fully_checked'] is True
    assert evidence['verification']['report']['passed'] is True
    assert evidence['rejected_moves']==7
    assert len(subject.state['calls'])==1
    assert subject.state['calls'][0]['check_timeout_s']==300


def test_former_valid_costlier_now_invalid_is_dropped(subject):
    subject.state['pass']=False
    result=subject.run()
    assert result['counts']['pairs_dropped']==1
    assert result['pairs']==[]
    assert 'no longer fully valid' in result['events'][-1]['reason']


def test_costlier_unavailable_never_negative_example(subject):
    subject.state['available']=False
    result=subject.run()
    assert result['counts']['unavailable']==1
    assert result['counts']['pairs_dropped']==1
    assert result['pairs']==[]

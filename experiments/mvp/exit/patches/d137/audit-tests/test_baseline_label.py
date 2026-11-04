"""D137 labels the measured 8.2 baseline without changing numerical gates."""
import importlib.util
import json
from pathlib import Path

import pytest

PATH=Path(__file__).resolve().parents[1]/'audit.py'
spec=importlib.util.spec_from_file_location('_d137_label_audit',PATH)
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)


def rows(passes,cost=10,n=6):
    return [dict(instance_id=str(i),passed=i<passes,weighted_moves=cost if i<passes else None)
            for i in range(n)]


@pytest.mark.parametrize('passes,cost,accepted',[(4,10,True),(4,10.6,False),
    (3,8,False),(2,9.5,True),(2,9.51,False),(1,1,False)])
def test_label_changes_only_name_not_decision(passes,cost,accepted):
    old=A.audit_decision(rows(2),rows(passes,cost))
    new=A.audit_decision(rows(2),rows(passes,cost),baseline_label='8.2')
    assert new['accepted'] is old['accepted'] is accepted
    assert new['rule']['comparison']=='generation-0 8.2 baseline'
    assert new['rule']['baseline_label']=='8.2'
    assert new['action']==('continue' if accepted else 'keep the 8.2 adapter')
    assert old['rule']==A.RULE
    assert old['rule']['comparison']=='generation-0 8.1 baseline'


def test_failed_82_gate_persists_82_adapter_and_method_label(tmp_path):
    decision=A.audit_decision(rows(2),rows(2),baseline_label='8.2')
    terminal=A.persist_stop(tmp_path,generation=1,adapter='failed-child',audit=decision,
        fallback_adapter=tmp_path/'sft82')
    survivor=json.loads((tmp_path/'survivor.json').read_text())
    assert terminal['action']=='keep the 8.2 adapter'
    assert terminal['fallback']==survivor['fallback']=='keep_sft82'
    assert terminal['adapter']==survivor['adapter']==str(tmp_path/'sft82')
    assert terminal['evaluated_adapter']=='failed-child'


def test_reconcile_82_gate_derives_version_from_durable_audit(tmp_path):
    (tmp_path/'first-gate.json').write_text(json.dumps(dict(generation=1,adapter='failed-child',
        fallback_adapter=str(tmp_path/'sft82'),audit=A.audit_decision(rows(2),rows(2),baseline_label='8.2'))))
    A.reconcile_failed_gate(tmp_path)
    terminal=json.loads((tmp_path/'terminal.json').read_text())
    assert terminal['fallback']=='keep_sft82' and terminal['action']=='keep the 8.2 adapter'


def test_label_conflict_fails_before_publishing_terminal(tmp_path):
    (tmp_path/'audit-rule.json').write_text(json.dumps({'rule':A.rule_for_baseline('8.2')}))
    with pytest.raises(RuntimeError,match='baseline label differs'):
        A.persist_stop(tmp_path,generation=1,adapter='child',audit={},
            fallback_adapter=tmp_path/'sft82',baseline_label='8.1')
    assert not (tmp_path/'terminal.json').exists()


def test_pre_registration_names_82_and_cannot_relabel_after_measurement(tmp_path,monkeypatch):
    from llm.exit import identity
    from stowage import io
    class Guard:
        def require_allowed(self,*args,**kwargs):pass
    monkeypatch.setattr(identity,'identity_guard',lambda root:Guard())
    monkeypatch.setattr(io,'load_instance',lambda path:object())
    corpus=tmp_path/'corpus'; out=tmp_path/'out';out.mkdir()
    paths=[]
    for i in range(30):
        path=corpus/'val/small'/f'{i}.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'id':i}));paths.append(path)
    manifest=tmp_path/'audit.txt'
    manifest.write_text(''.join(str(path.relative_to(tmp_path))+'\n' for path in paths))
    A.frozen_audit_paths(tmp_path,corpus,manifest,out,baseline_label='8.2')
    before=(out/'audit-rule.json').read_bytes()
    rule=json.loads(before)['rule']
    assert rule['comparison']=='generation-0 8.2 baseline'
    assert rule['failure_action']=='keep the 8.2 adapter'
    assert A.frozen_audit_paths(tmp_path,corpus,manifest,out,baseline_label='8.2')==paths
    with pytest.raises(RuntimeError,match='pre-registered'):
        A.frozen_audit_paths(tmp_path,corpus,manifest,out,baseline_label='8.1')
    assert (out/'audit-rule.json').read_bytes()==before


@pytest.mark.parametrize('bad',['',None,'8/2','eight','8.2\n'])
def test_invalid_labels_refused(bad):
    with pytest.raises(ValueError,match='dotted numeric'):
        A.rule_for_baseline(bad)

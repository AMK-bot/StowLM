import json
import sys

import pytest

from llm.exit import audit,runner


def rows(passes,cost=10,n=6):
    return [dict(instance_id=f'small-{i}',passed=i<passes,
                 weighted_moves=cost if i<passes else None) for i in range(n)]


@pytest.mark.parametrize('passes,cost,accepted',[(4,20,False),(3,1,False),(2,9.5,True),
    (2,9.51,False),(2,10,False),(1,1,False)])
def test_preregistered_gate_needs_two_passes_or_equal_passes_five_percent(passes,cost,accepted):
    verdict=audit.audit_decision(rows(2),rows(passes,cost))
    assert verdict['accepted'] is accepted
    assert verdict['action']==('continue' if accepted else 'keep the 8.1 adapter')
    assert verdict['rule']['comparison']=='generation-0 8.1 baseline'


def test_terminal_is_honored_on_resume_before_review_or_gpu(tmp_path,monkeypatch):
    audit.persist_stop(tmp_path,generation=1,adapter='child',audit={'accepted':False,'trained_child_audited':True},
                       fallback_adapter=tmp_path/'sft81')
    from llm.exit import resources
    monkeypatch.setattr(resources,'reservation',lambda *a,**kw:pytest.fail('GPU reservation on terminal resume'))
    monkeypatch.setattr(sys,'argv',['runner','--out',str(tmp_path),'--parent','missing'])
    runner.main()
    terminal=json.loads((tmp_path/'terminal.json').read_text())
    assert terminal['fallback']=='keep_sft81'
    assert terminal['fallback_adapter']==str(tmp_path/'sft81')
    assert terminal['adapter']==str(tmp_path/'sft81')
    assert terminal['evaluated_adapter']=='child'
    assert terminal['action']=='keep the 8.1 adapter'


def test_clock_checkpoint_stops_against_initial_baseline(tmp_path,monkeypatch):
    args=runner.parser().parse_args(['--parent','pilot2','--out',str(tmp_path)])
    monkeypatch.setattr(audit,'frozen_audit_paths',lambda *a,**kw:['small'])
    monkeypatch.setattr(runner,'ensure_baseline',lambda *a,**kw:rows(2))
    monkeypatch.setattr(runner,'evaluate',lambda *a,**kw:rows(3))
    monkeypatch.setattr(runner,'adapter_identity',lambda *a,**kw:{'adapters.safetensors':'weights'})
    runner.clock_audit(args,[dict(adapter='trained-child',elite=False)])
    assert (tmp_path/'first-gate.json').exists()
    terminal=json.loads((tmp_path/'terminal.json').read_text())
    assert terminal['stop_reason']=='clock_audit_no_detectable_gain'
    assert terminal['fallback']=='keep_sft81'
    assert terminal['fallback_adapter']==str(args.parent.resolve())


def test_final_children_all_audited_before_child_freeze(tmp_path,monkeypatch):
    args=runner.parser().parse_args(['--parent','pilot2','--out',str(tmp_path)])
    monkeypatch.setattr(audit,'frozen_audit_paths',lambda *a,**kw:['small'])
    monkeypatch.setattr(runner,'ensure_baseline',lambda *a,**kw:rows(2))
    monkeypatch.setattr(runner,'adapter_identity',lambda *a,**kw:{'adapters.safetensors':'weights'})
    calls=[]
    def evaluate(adapter,*a):
        calls.append(str(adapter))
        return rows(4 if adapter=='child-B' else 2)
    from llm.exit import final_recipe
    monkeypatch.setattr(final_recipe,'evaluate_recipe',evaluate)
    runner.final_population_audit([dict(adapter=name) for name in ['elite','child-A','child-B']],args)
    assert calls==['pilot2','elite','child-A','child-B']
    assert json.loads((tmp_path/'survivor.json').read_text())['adapter']=='child-B'
    assert json.loads((tmp_path/'final-audit.json').read_text())['all_children_audited']


def test_final_failed_child_keeps_original_sft81_parent(tmp_path,monkeypatch):
    args=runner.parser().parse_args(['--parent',str(tmp_path/'sft81'),'--out',str(tmp_path)])
    monkeypatch.setattr(audit,'frozen_audit_paths',lambda *a,**kw:['small'])
    monkeypatch.setattr(runner,'adapter_identity',lambda *a,**kw:{'adapters.safetensors':'weights'})
    from llm.exit import final_recipe
    monkeypatch.setattr(final_recipe,'evaluate_recipe',lambda adapter,*a:rows(3 if adapter=='child' else 2))
    runner.final_population_audit([dict(adapter='child')],args)
    terminal=json.loads((tmp_path/'terminal.json').read_text())
    assert terminal['adapter']==str(tmp_path/'sft81')
    assert terminal['evaluated_adapter']=='child'
    assert terminal['fallback']=='keep_sft81'
    assert terminal['fallback_adapter']==str(tmp_path/'sft81')
    assert terminal['action']=='keep the 8.1 adapter'
    survivor=json.loads((tmp_path/'survivor.json').read_text())
    assert survivor['adapter']==str(tmp_path/'sft81')
    assert survivor['frozen'] and survivor['fallback']=='keep_sft81'


def test_map_sizing_uses_measured_variable_phase_and_fixed_training_cost(tmp_path):
    args=runner.parser().parse_args(['--out',str(tmp_path),'--maps','48','--k','8'])
    summary={'timings':{'decode_verify_pipeline_s':6000.,'dataset_s':600.,
        'child_0_train_s':600.,'child_1_train_s':600.}}
    result=runner.resize_after_pilot(summary,args)
    assert 6<=result['maps']<=48 and args.tiny+args.small==result['maps']
    assert result['projection_only'] and not result['fully_measured_inputs']
    assert result['baseline_included_in_recurring_generation'] is False


def test_clock_audit_is_skipped_after_first_gate(tmp_path):
    assert audit.clock_due('2000-01-01T00:00:00+00:00',tmp_path)
    (tmp_path/'first-gate.json').write_text('{}')
    assert not audit.clock_due('2000-01-01T00:00:00+00:00',tmp_path)


def test_crash_after_failed_gate_cannot_bypass_stop_on_resume(tmp_path):
    (tmp_path/'first-gate.json').write_text(json.dumps(dict(
        generation=1,adapter='child',audit={'accepted':False,'trained_child_audited':True})))
    audit.reconcile_failed_gate(tmp_path,fallback_adapter=tmp_path/'sft81')
    terminal=json.loads((tmp_path/'terminal.json').read_text())
    assert terminal['fallback']=='keep_sft81'
    assert terminal['fallback_adapter']==str(tmp_path/'sft81')
    assert terminal['adapter']==str(tmp_path/'sft81')
    assert terminal['evaluated_adapter']=='child'


def test_fallback_never_infers_original_adapter_from_failed_child(tmp_path):
    with pytest.raises(ValueError,match='original 8.1 fallback adapter is required'):
        audit.persist_stop(tmp_path,generation=1,adapter='failed-child',audit={'accepted':False,'trained_child_audited':True})
    assert not (tmp_path/'terminal.json').exists()


def test_reconciliation_can_recover_original_parent_from_registered_audit(tmp_path):
    (tmp_path/'audit-rule.json').write_text(json.dumps(dict(baseline_adapter=str(tmp_path/'sft81'))))
    (tmp_path/'first-gate.json').write_text(json.dumps(dict(
        generation=1,adapter='failed-child',audit={'accepted':False,'trained_child_audited':True})))
    audit.reconcile_failed_gate(tmp_path)
    terminal=json.loads((tmp_path/'terminal.json').read_text())
    assert terminal['fallback_adapter']==str(tmp_path/'sft81')
    assert terminal['action']=='keep the 8.1 adapter'
    assert terminal['stopped'] and terminal['stop_reason']=='resumed_failed_audit_gate'


def test_explicit_fallback_cannot_replace_registered_original_parent(tmp_path):
    (tmp_path/'audit-rule.json').write_text(json.dumps(dict(baseline_adapter=str(tmp_path/'sft81'))))
    with pytest.raises(RuntimeError,match='differs from registered baseline'):
        audit.persist_stop(tmp_path,generation=1,adapter='failed-child',audit={'accepted':False,'trained_child_audited':True},
                           fallback_adapter=tmp_path/'failed-child')


def test_fallback_resolves_original_parent_from_baseline_binding(tmp_path):
    binding=tmp_path/'audit-baseline'/'baseline-binding.json'
    binding.parent.mkdir()
    binding.write_text(json.dumps(dict(baseline_adapter=str(tmp_path/'sft81'))))
    terminal=audit.persist_stop(tmp_path,generation=1,adapter='child',audit={'accepted':False,'trained_child_audited':True})
    assert terminal['fallback_adapter']==str(tmp_path/'sft81')


def test_changed_resume_parent_cannot_replace_baseline_binding(tmp_path):
    binding=tmp_path/'audit-baseline'/'baseline-binding.json'
    binding.parent.mkdir()
    binding.write_text(json.dumps(dict(baseline_adapter=str(tmp_path/'sft81'))))
    (tmp_path/'first-gate.json').write_text(json.dumps(dict(
        generation=1,adapter='failed-child',audit={'accepted':False,'trained_child_audited':True})))
    prior_survivor=dict(adapter='old-child')
    (tmp_path/'survivor.json').write_text(json.dumps(prior_survivor))
    with pytest.raises(RuntimeError,match='differs from registered baseline'):
        audit.reconcile_failed_gate(tmp_path,fallback_adapter=tmp_path/'changed-resume-parent')
    assert not (tmp_path/'terminal.json').exists()
    assert json.loads((tmp_path/'survivor.json').read_text())==prior_survivor


def test_no_gain_replaces_preexisting_child_survivor_with_original_parent(tmp_path):
    (tmp_path/'survivor.json').write_text(json.dumps(dict(adapter='unaudited-child')))
    terminal=audit.persist_stop(tmp_path,generation=3,adapter='failed-child',audit={'accepted':False,'trained_child_audited':True},
                                fallback_adapter=tmp_path/'sft81')
    survivor=json.loads((tmp_path/'survivor.json').read_text())
    assert survivor['adapter']==terminal['adapter']==str(tmp_path/'sft81')
    assert survivor['evaluated_adapter']==terminal['evaluated_adapter']=='failed-child'
    assert survivor['generation']==-1 and survivor['stopped_after_generation']==3
    assert terminal['stopped']


def test_fallback_survivor_is_written_before_terminal_and_crash_can_reconcile(tmp_path,monkeypatch):
    (tmp_path/'first-gate.json').write_text(json.dumps(dict(
        generation=1,adapter='failed-child',audit={'accepted':False,'trained_child_audited':True})))
    (tmp_path/'survivor.json').write_text(json.dumps(dict(adapter='unaudited-child')))
    original=runner.write_json
    def interrupted_write(path,value):
        if path.name=='terminal.json':
            assert json.loads((tmp_path/'survivor.json').read_text())['adapter']==str(tmp_path/'sft81')
            raise OSError('interrupted terminal write')
        original(path,value)
    with monkeypatch.context() as patch:
        patch.setattr(runner,'write_json',interrupted_write)
        with pytest.raises(OSError,match='interrupted terminal write'):
            audit.persist_stop(tmp_path,generation=1,adapter='failed-child',audit={'accepted':False,'trained_child_audited':True},
                               fallback_adapter=tmp_path/'sft81')
    assert not (tmp_path/'terminal.json').exists()
    audit.reconcile_failed_gate(tmp_path,fallback_adapter=tmp_path/'sft81')
    terminal=json.loads((tmp_path/'terminal.json').read_text())
    assert terminal['adapter']==str(tmp_path/'sft81')
    assert terminal['evaluated_adapter']=='failed-child'
    assert terminal['stopped']


def test_conflicting_original_parent_registrations_fail_closed(tmp_path):
    (tmp_path/'audit-rule.json').write_text(json.dumps(dict(baseline_adapter=str(tmp_path/'sft81'))))
    binding=tmp_path/'audit-baseline'/'baseline-binding.json'
    binding.parent.mkdir()
    binding.write_text(json.dumps(dict(baseline_adapter=str(tmp_path/'other-parent'))))
    with pytest.raises(RuntimeError,match='registrations disagree'):
        audit.persist_stop(tmp_path,generation=1,adapter='child',audit={'accepted':False,'trained_child_audited':True})


def test_audit_cache_cannot_score_another_adapter(tmp_path,monkeypatch):
    args=runner.parser().parse_args(['--out',str(tmp_path)])
    path=tmp_path/'small.json';path.write_text('{}')
    import hashlib
    epoch=runner.epoch_id({'decoder':'same'})
    monkeypatch.setattr(runner,'_LOADED_AUDIT_EPOCH',{'decoder':'same'})
    monkeypatch.setattr(runner,'assert_audit_epoch',lambda *a:None)
    directory=tmp_path/'audit'
    cached=directory/f'epoch-{epoch}';cached.mkdir(parents=True)
    (cached/'000.json').write_text(json.dumps(rows(1,n=1)[0]))
    state={'weights':'one'}
    monkeypatch.setattr(runner,'adapter_identity',lambda *a,**kw:state.copy())
    monkeypatch.setattr(runner,'source_pins',lambda:{'decoder':'same'})
    assert runner.evaluate('adapter',[path],directory,args)[0]['passed']
    state['weights']='two'
    with pytest.raises(RuntimeError,match='audit cache adapter'):
        runner.evaluate('adapter',[path],directory,args)


def test_initial_baseline_remains_bound_to_pilot_adapter_maps_and_checker(tmp_path,monkeypatch):
    from llm.exit import replay
    args=runner.parser().parse_args(['--out',str(tmp_path),'--parent','pilot'])
    path=tmp_path/'small.json';path.write_text('{}')
    state={'adapter':'one'};calls=[]
    monkeypatch.setattr(runner,'adapter_identity',lambda *a,**kw:state.copy())
    monkeypatch.setattr(replay,'checker_epoch',lambda:'unchanged-checker')
    monkeypatch.setattr(runner,'evaluate',lambda *a,**kw:calls.append('decoded') or rows(2))
    assert runner.ensure_baseline(args,[path])==rows(2)
    assert runner.ensure_baseline(args,[path])==rows(2) and calls==['decoded']
    state['adapter']='other-pilot'
    with pytest.raises(RuntimeError,match='baseline parent'):
        runner.ensure_baseline(args,[path])

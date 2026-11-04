"""D129 CPU admission regression tests; never mutate active generation sources."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def subject(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[3]
    source = root/'experiments/mvp/exit/patches/d129/reverify.py'
    if (root/'src/llm/exit/reverify.py').exists():
        source = root/'src/llm/exit/reverify.py'
    spec = importlib.util.spec_from_file_location('_d129_reverify_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    epoch = {'checker.py':'new-code', 'mask.py':'new-layer'}
    monkeypatch.setattr(module, '_epoch', lambda:dict(epoch))

    class Plan:
        def __init__(self, value):self.value=value
        @classmethod
        def model_validate(cls, value):return cls(value)
        def model_dump(self, **kw):return self.value

    class Guard:
        def require_allowed(self, value, **kw):
            if value.get('heldout'):
                raise ValueError('held-out identity')
            return module._digest(value)

    state = dict(winner_pass=True, winner_unavailable=False, reject_pass=False,
                 reject_available=True, dead_end=True, calls=[], masks=[])
    def verify(instance, candidate, **kw):
        state['calls'].append(('winner', kw))
        return SimpleNamespace(passed=state['winner_pass'], plan=candidate.plan,
            weighted_moves=2, details={'report':{'passed':state['winner_pass']},
                'mask':{'passed':state['winner_pass']},
                'checker_available':not state['winner_unavailable']})
    def trace(instance, *, plan=None, tokens=None, require_complete=False):
        state['masks'].append(plan.value['id'] if plan else 'dead')
        values = tokens or ([1] if plan.value['id']=='winner' else [2])
        return dict(prompt=[9], token_ids=values, allowed_sets=[[1,2] for _ in values],
                    action_mask=[True for _ in values],
                    status='dead_end' if tokens is not None and state['dead_end'] else 'complete')
    def check(instance, plan, **kw):
        state['calls'].append(('rejected', kw))
        return SimpleNamespace(available=state['reject_available'],
            report=Plan({'passed':state['reject_pass'], 'violation':'completed'}),
            details=lambda:{'available':state['reject_available']})
    services = dict(Candidate=lambda plan,source:SimpleNamespace(plan=plan,source=source),
        verify_candidate=verify, score_candidate=lambda i,c:c.plan.value.get('cost',7),
        run_full_check=check, prepare_masked_sequence=trace,
        definitive_checker_failure=lambda report:not report['passed'] and report.get('violation')=='completed',
        layer_identity=lambda:{'new':'layer'}, checker_unavailable=lambda details:details.get('checker_available') is False,
        checker_epoch=lambda:'new-checker', validate_pair=lambda pair:None,
        pair_identity=module._digest, identity_guard=lambda root:Guard(),
        DEFAULT_CORPUS=tmp_path, load_instance=lambda path:json.loads(Path(path).read_text()),
        MovePlan=Plan, require_clean=lambda *args,**kw:None)
    monkeypatch.setattr(module, '_services', lambda:services)
    instance=tmp_path/'map.json';instance.write_text(json.dumps({'id':'train', 'seed':1}))
    record=tmp_path/'winner.json';record.write_text(json.dumps(dict(instance_path=str(instance),
        plan={'id':'winner'}, report={'passed':True})))
    row=dict(instance_path=str(instance),passed=True,winner_record=str(record),
             weighted_moves=2,source='model',candidates=[],preferences={'pairs':[]})
    def add_pair(kind='checker_failure', **kw):
        rejected=dict(candidate_id='rejected',plan={'id':'rejected','cost':7},token_ids=[2])
        if kind=='dead_end':rejected.pop('plan')
        pair=dict(instance_path=str(instance),evidence={'kind':kind},rejected_witness=rejected,
                  rejected={'token_ids':[2]}, **kw)
        row['preferences']['pairs'].append(pair)
        return pair
    def run(**kw):
        return module.reverify_training_rows([row],output_dir=tmp_path/'fresh',
            training_epoch=epoch,model_identity={'parent':'exact-adapter'},**kw)
    return SimpleNamespace(module=module,epoch=epoch,state=state,row=row,run=run,
                           add_pair=add_pair,tmp=tmp_path,services=services)


def test_current_winners_and_failure_proofs_rebuilt_and_originals_untouched(subject):
    subject.add_pair()
    original=Path(subject.row['winner_record']).read_bytes()
    result=subject.run()
    assert result['status']=='ready'
    assert result['counts']['winners_kept']==result['counts']['pairs_kept']==1
    assert result['pairs'][0]['layers']=={'new':'layer'}
    assert result['pairs'][0]['chosen_witness']['report']=={'passed':True}
    assert Path(subject.row['winner_record']).read_bytes()==original
    assert [call[0] for call in subject.state['calls']]==['winner','rejected']
    assert subject.state['calls'][0][1]['check_timeout_s']==300
    assert subject.state['calls'][1][1]['timeout_s']==300


def test_old_failure_that_now_passes_is_logged_and_dropped(subject):
    subject.add_pair();subject.state['reject_pass']=True
    result=subject.run()
    assert result['pairs']==[]
    assert result['counts']['rejection_now_passed']==1
    assert any(e.get('outcome_change')=='old_failure_now_passes' for e in result['events'])
    assert 'rejected' in subject.state['masks']


def test_failed_old_winner_still_checks_and_replays_rejected_plan(subject):
    subject.add_pair();subject.state['winner_pass']=False
    result=subject.run()
    assert result['status']=='blocked_no_current_winners'
    assert result['winner_records']==result['pairs']==[]
    assert result['counts']['winner_now_failed']==1
    assert result['counts']['rejection_still_failed']==1
    assert 'rejected' in subject.state['masks']


def test_unavailable_rejection_never_becomes_failed_pair(subject):
    subject.add_pair();subject.state['reject_available']=False
    result=subject.run()
    assert result['counts']['unavailable']==1
    assert result['counts']['rejection_still_failed']==0
    assert result['pairs']==[]


def test_contradictory_pass_with_unavailable_checker_never_target(subject):
    subject.state['winner_unavailable']=True
    result=subject.run()
    assert result['winner_records']==[]
    assert result['counts']['unavailable']==1
    assert result['status']=='blocked_no_current_winners'


def test_rejected_checker_launch_error_is_unavailable_not_physics_failure(subject):
    subject.add_pair()
    def failed(*a,**kw):raise OSError('launch refused')
    subject.services['run_full_check']=failed
    result=subject.run()
    assert result['pairs']==[]
    assert result['counts']['unavailable']==1
    assert result['counts']['rejection_still_failed']==0


def test_dead_end_is_replayed_with_current_layers(subject):
    subject.add_pair('dead_end');subject.state['dead_end']=False
    result=subject.run()
    assert result['pairs']==[]
    assert any(e.get('outcome_change')=='old_dead_end_now_supported' for e in result['events'])


def test_move_order_recomputed_relative_to_fresh_winner(subject):
    pair=subject.add_pair('strictly_more_moves');pair['rejected_witness']['plan']['cost']=1
    assert subject.run()['pairs']==[]


@pytest.mark.parametrize('location', ['selection','pair','record'])
def test_inline_heldout_identity_refused(subject,location):
    if location=='selection':subject.row['instance']={'heldout':True}
    elif location=='pair':subject.add_pair(instance={'heldout':True})
    else:
        path=Path(subject.row['winner_record']);row=json.loads(path.read_text())
        row['instance']={'heldout':True};path.write_text(json.dumps(row))
    if location=='pair':
        result=subject.run();assert result['pairs']==[]
        assert any('held-out' in e.get('reason','') for e in result['events'])
    else:
        with pytest.raises(ValueError,match='held-out'):subject.run()


def test_receipt_cache_avoids_checks_only_for_identical_epoch_model_and_bytes(subject):
    subject.add_pair();first=subject.run();n=len(subject.state['calls'])
    assert subject.run()['cached']
    assert len(subject.state['calls'])==n
    receipt=subject.tmp/'fresh/receipt.json'
    subject.module.assert_training_receipt(receipt,selections=[subject.row],
        training_epoch=subject.epoch,model_identity={'parent':'exact-adapter'})
    Path(first['winner_records'][0]).write_text('{}')
    with pytest.raises(ValueError,match='output identity'):subject.run()


def test_receipt_refuses_new_source_epoch(subject):
    subject.run();subject.epoch['checker.py']='another-fix'
    with pytest.raises(ValueError,match='epoch mismatch'):subject.run()


def test_receipt_refuses_changed_source_records(subject):
    subject.run();Path(subject.row['winner_record']).write_text('{}')
    with pytest.raises(ValueError,match='input identity'):subject.run()


def test_drift_during_verification_refuses_publication(subject):
    original=subject.services['verify_candidate']
    def drift(*a,**kw):
        result=original(*a,**kw);subject.epoch['mask.py']='changed';return result
    subject.services['verify_candidate']=drift
    # entry epoch is copied so an in-place caller dictionary cannot disguise drift
    epoch=dict(subject.epoch)
    with pytest.raises(RuntimeError,match='changed during'):
        subject.module.reverify_training_rows([subject.row],output_dir=subject.tmp/'fresh',
            training_epoch=epoch,model_identity={'parent':'adapter'})
    assert not (subject.tmp/'fresh/receipt.json').exists()


def test_audit_reverification_preserves_original_and_checks_exact_frozen30(subject,monkeypatch):
    rows=[];paths=[]
    for index in range(30):
        path=subject.tmp/f'audit-{index}.json'
        path.write_text(json.dumps({'id':str(index),'seed':index}))
        paths.append(path)
        rows.append(dict(instance_path=str(path),passed=index>0,weighted_moves=34.05,
                         generation_timings={'status':'complete','tokens':[1]}))
    original=json.dumps(rows,sort_keys=True)
    monkeypatch.setattr(subject.module,'_plan_from_tokens',lambda instance,tokens:
                        subject.services['MovePlan']({'id':'winner'}))
    result=subject.module.reverify_audit_rows(rows,frozen_paths=paths,
        output_dir=subject.tmp/'audit-fresh',training_epoch=subject.epoch,
        model_identity={'parent':'8.1'})
    assert result['changed_to_pass']==1 and result['changed_to_fail']==0
    assert json.dumps(rows,sort_keys=True)==original
    assert len(result['rows'])==30
    rows[1]['generation_timings']['status']='dead_end'
    second=subject.module.reverify_audit_rows(rows,frozen_paths=paths,
        output_dir=subject.tmp/'audit-fresh-second',training_epoch=subject.epoch,
        model_identity={'parent':'8.1'})
    assert second['rows'][1]['passed'] is False
    assert 'never promote' in second['rows'][1]['verification']['error']
    with pytest.raises(ValueError,match='frozen30'):
        subject.module.reverify_audit_rows(rows[:-1],frozen_paths=paths,
            output_dir=subject.tmp/'audit-fresh',training_epoch=subject.epoch,
            model_identity={'parent':'8.1'})


def test_parallel_map_jobs_use_spawn_and_aggregate_original_order_and_receipts(subject,monkeypatch):
    # Load staged code explicitly until the parent activates its fresh epoch.
    root=Path(__file__).resolve().parents[3]
    spec=importlib.util.spec_from_file_location('_d131_parallel_reverify',
        root/'experiments/mvp/exit/patches/d129/reverify.py')
    staged=importlib.util.module_from_spec(spec);spec.loader.exec_module(staged)
    monkeypatch.setattr(staged,'_epoch',lambda:dict(subject.epoch))
    monkeypatch.setattr(staged,'_services',lambda:subject.services)
    rows=[]
    for index in range(3):
        path=subject.tmp/f'parallel-map-{index}.json'
        path.write_text(json.dumps({'id':f'map-{index}','seed':100+index}))
        record=subject.tmp/f'parallel-winner-{index}.json'
        record.write_text(json.dumps(dict(instance_path=str(path),plan={'id':'winner'})))
        rows.append(dict(instance_path=str(path),passed=index!=1,winner_record=str(record),
                         source='model',weighted_moves=2,preferences={'pairs':[]}))
    calls=[]
    class Pool:
        def __init__(self,**kw):
            calls.append(kw)
            assert kw['max_workers']==4
            assert kw['mp_context'].get_start_method()=='spawn'
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def map(self,fn,payloads,**kw):
            assert kw['chunksize']==1
            return reversed([fn(payload) for payload in payloads])
    monkeypatch.setattr(staged,'ProcessPoolExecutor',Pool)
    result=staged.reverify_training_rows(rows,output_dir=subject.tmp/'parallel',
        training_epoch=subject.epoch,model_identity={'adapter':'exact'},workers=4)
    assert result['counts']['winners_seen']==result['counts']['winners_kept']==2
    assert [row['instance_path'] for row in result['selections']]==[row['instance_path'] for row in rows]
    assert len(result['receipt']['outputs'])>=9
    staged.assert_training_receipt(result['receipt'],selections=rows,
        training_epoch=subject.epoch,model_identity={'adapter':'exact'})
    # A blocked single-map result is checkpointable, but never an admitted target.
    blocked=subject.tmp/'parallel/maps/0001/receipt.json'
    with pytest.raises(ValueError,match='no verified winners'):
        staged.assert_training_receipt(blocked,selections=[rows[1]],
            training_epoch=subject.epoch,model_identity={'adapter':'exact'})
    assert staged.reverify_training_rows([rows[1]],output_dir=blocked.parent,
        training_epoch=subject.epoch,model_identity={'adapter':'exact'})['cached']


@pytest.mark.parametrize('workers',[0,5,True,1.5])
def test_worker_budget_is_bounded(subject,workers):
    root=Path(__file__).resolve().parents[3]
    spec=importlib.util.spec_from_file_location('_d131_parallel_budget',
        root/'experiments/mvp/exit/patches/d129/reverify.py')
    staged=importlib.util.module_from_spec(spec);spec.loader.exec_module(staged)
    staged._epoch=lambda:dict(subject.epoch)
    with pytest.raises(ValueError,match='workers'):
        staged.reverify_training_rows([subject.row],output_dir=subject.tmp/'parallel',
            training_epoch=subject.epoch,model_identity={'adapter':'exact'},workers=workers)

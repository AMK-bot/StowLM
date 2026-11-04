"""CPU-only recovery contracts; synthetic gates never claim checker results."""
from types import SimpleNamespace

import pytest

from llm.exit import recovery as r
from llm.exit.identity import IdentityGuard
from stowage.actions.space import Place
from stowage.model.fixtures import build_instance


@pytest.fixture
def setup_case(monkeypatch):
    instance = build_instance()
    prefix = (Place(instance.load_list[0], '0010102'),)
    tail = Place('synthetic-second-box', '0030102')
    full = SimpleNamespace(actions=[*prefix, tail])
    monkeypatch.setattr(r, '_actions', lambda i, p: p.actions)
    monkeypatch.setattr(r, 'last_good_prefix', lambda i, a: tuple(a))
    return instance, prefix, full


def test_recovery_preserves_exact_prefix_before_and_after_gates(setup_case):
    instance, prefix, plan = setup_case
    seen = []
    def complete(i, p, **kwargs):
        seen.append(tuple(p))
        return SimpleNamespace(status='ok', plan=plan)
    result = r._recover(instance, prefix, time_limit_s=10, completer=complete,
        verifier=lambda i, c: r.Verification(True, plan, 3, {'report': {'synthetic': True}}))
    assert result.passed and result.plan is plan
    assert seen == [prefix] and result.prefix_actions == prefix
    assert result.details['weighted_moves'] == 3


@pytest.mark.parametrize('change_at', ['complete', 'finalize'])
def test_suffix_repair_that_changes_prefix_is_never_a_target(setup_case, change_at):
    instance, prefix, full = setup_case
    changed = SimpleNamespace(actions=[full.actions[1]])
    result = r._recover(instance, prefix, time_limit_s=10,
        completer=lambda *a, **k: SimpleNamespace(status='ok',plan=changed if change_at=='complete' else full),
        verifier=lambda *a: r.Verification(True, changed, 0))
    assert not result.passed and result.plan is None
    assert 'prefix' in result.details['error']


def test_failed_gate_never_becomes_target(setup_case):
    instance,prefix,full=setup_case
    result=r._recover(instance,prefix,time_limit_s=10,
        completer=lambda *a,**k:SimpleNamespace(status='ok',plan=full),
        verifier=lambda *a:r.Verification(False,details={'error':'checker violation'}))
    assert not result.passed and result.plan is None
    assert 'gates' in result.details['error']


def test_backoff_is_recorded_and_exact_reduced_prefix_preserved(setup_case,monkeypatch):
    instance,prefix,full=setup_case
    bad=Place('bad-dead-end','0050102')
    monkeypatch.setattr(r,'last_good_prefix',lambda i,a:tuple(a[:-1]))
    result=r._recover(instance,[*prefix,bad],time_limit_s=10,
        completer=lambda *a,**k:SimpleNamespace(status='ok',plan=full),
        verifier=lambda *a:r.Verification(True,full,0))
    assert result.passed and result.prefix_actions==prefix
    assert result.details['prefix_backoff_actions']==1


def test_no_recovery_without_measured_cpu_credit_or_beyond_case_cap(setup_case,tmp_path):
    instance,prefix,_=setup_case
    guard=IdentityGuard(tmp_path)
    budget=r.RecoveryBudget(0)
    result=r.recover_dead_end(instance,prefix,budget=budget,guard=guard)
    assert not result.passed and 'budget' in result.details['skipped']
    assert budget.cases==0
    budget=r.RecoveryBudget(100,max_cases=1,cases=1)
    assert budget.allowance(15)==0
    assert r.RecoveryBudget(100).allowance(15)==pytest.approx(13.75)
    budget=r.RecoveryBudget(100,spent_s=10)
    assert budget.allowance(15)==pytest.approx(3.75)


@pytest.mark.parametrize('kwargs',[{'base_cpu_s':-1},{'base_cpu_s':float('nan')},
    {'base_cpu_s':10,'fraction':.15},{'base_cpu_s':10,'max_cases':-1}])
def test_budget_rejects_unmeasured_or_excessive_credit(kwargs):
    with pytest.raises(ValueError):
        r.RecoveryBudget(**kwargs)


def test_heldout_guard_runs_before_any_worker(setup_case,tmp_path):
    instance,prefix,_=setup_case
    guard=IdentityGuard(tmp_path)
    guard.identities['val'] |= guard.tokens(instance)
    with pytest.raises(ValueError,match='held-out val'):
        r.recover_dead_end(instance,prefix,budget=r.RecoveryBudget(100),guard=guard)
    with pytest.raises(ValueError,match='held-out val'):
        r.build_recovery_example(instance,r.RecoveryResult(),guard=guard)


def test_unchecked_row_refused_before_serialization(setup_case,tmp_path):
    instance,_,_=setup_case
    with pytest.raises(ValueError,match='unchecked recovery'):
        r.build_recovery_example(instance,r.RecoveryResult(),guard=IdentityGuard(tmp_path))


def test_recovery_row_keeps_stock_prompt_hash_and_masks_only_expert_tail(setup_case,tmp_path,monkeypatch):
    from llm.stowlm import format as fmt, sft_data
    from llm.exit import data
    instance,prefix,plan=setup_case
    row=SimpleNamespace(input_ids=[99,98,10,11,12,13],prompt_len=2,
                        meta={'gold':{'example_sha256':'original'},'replay':{'example_sha256':'original'}})
    monkeypatch.setattr(sft_data,'build_mvp_example',lambda *a,**k:row)
    monkeypatch.setattr(sft_data,'mvp_row_failure',lambda e:None)
    monkeypatch.setattr(data,'layered_replay',lambda *a:{'ok':True,'tokens':4})
    monkeypatch.setattr(fmt,'ActionCodec',lambda *a:SimpleNamespace(encode_action=lambda a:[10,11]))
    result=r.RecoveryResult(True,plan,prefix,{'verification':{'report':{'fixture':True}}})
    example=r.build_recovery_example(instance,result,tokenizer=object(),guard=IdentityGuard(tmp_path))
    assert example.prompt_len==2
    assert example.meta['gold']['example_sha256']=='original'
    assert example.meta['replay']['example_sha256']=='original'
    assert example.meta['exit_recovery']['loss_start']==4
    assert example.meta['exit_recovery']['prefix_tokens']==[10,11]
    assert example.input_ids[example.meta['exit_recovery']['loss_start']:]==[12,13]


def test_dead_end_prefix_helper_keeps_last_boundary_with_successors(monkeypatch):
    from llm.exit import selection
    from llm.stowlm import format as fmt
    from stowage import feasibility
    instance=build_instance()
    actions=[Place('a','0010102'),Place('b','0030102'),Place('c','0050102')]
    class Decoder:
        status='running'
        count=0
        def allowed_token_ids(self):
            return {1} if self.count < 3 else set()
        def advance(self,token):
            self.count+=1
            if self.count==3:
                self.status='dead_end'
    decoder=Decoder()
    monkeypatch.setattr(feasibility,'make_feasibility_engine',lambda *a,**k:object())
    monkeypatch.setattr(fmt,'load_tokenizer',lambda:object())
    monkeypatch.setattr(fmt,'ActionCodec',lambda *a:SimpleNamespace(encode_action=lambda a:[1]))
    monkeypatch.setattr(selection,'decoder_factory',lambda:lambda *a:[decoder])
    assert r.last_good_prefix(instance,actions)==tuple(actions[:2])


def test_timeout_terminates_only_owned_process_and_never_admits_target(setup_case,tmp_path,monkeypatch):
    instance,prefix,_=setup_case
    events=[]
    class Connection:
        def close(self): pass
        def poll(self,timeout): return False
        def recv(self): raise EOFError('owned worker stopped')
    class Process:
        pid=None
        exitcode=None
        alive=True
        def start(self):
            self.pid=12345
            events.append(('start',self.pid))
        def join(self,timeout=None): pass
        def is_alive(self): return self.alive
        def terminate(self):
            events.append(('terminate',self.pid))
            self.alive=False
            self.exitcode=-15
        def kill(self): pytest.fail('terminated worker should not be killed again')
    process=Process()
    context=SimpleNamespace(Pipe=lambda **k:(Connection(),Connection()),
                            Process=lambda **k:process)
    monkeypatch.setattr(r.mp,'get_context',lambda mode:context)
    budget=r.RecoveryBudget(100)
    result=r.recover_dead_end(instance,prefix,budget=budget,guard=IdentityGuard(tmp_path))
    assert not result.passed and result.plan is None
    assert events==[('start',12345),('terminate',12345)]
    assert budget.cases==1 and budget.spent_s>=0
    assert result.details['charged_cpu_upper_s']==budget.spent_s


def test_worker_inlines_inner_ballast_deadline_and_restores_no_parent_state(setup_case,monkeypatch):
    from stowage.solver import ballast_deadline, exact_deadline
    instance,prefix,_=setup_case
    sent=[]
    connection=SimpleNamespace(send=sent.append,close=lambda:None)
    original=ballast_deadline.run_with_deadline
    monkeypatch.setenv('STOWAGE_IN_PORT_WORKERS','4')
    monkeypatch.setattr(ballast_deadline,'run_with_deadline',original)
    monkeypatch.setattr(r,'_recover',lambda *a,**k:r.RecoveryResult(details={'fixture':True}))
    r._worker(connection,instance,prefix,1)
    assert ballast_deadline.run_with_deadline is exact_deadline._inline
    assert sent[0].details['worker_cpu_s']>=0
    import os
    assert os.environ['STOWAGE_IN_PORT_WORKERS']=='1'

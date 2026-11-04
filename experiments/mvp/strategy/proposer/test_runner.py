"""Checked budgets, snapshot binding and replay tests; no solver/model calls."""
from __future__ import annotations

from concurrent.futures import Future
import hashlib
import json

import pytest

from experiments.mvp.strategy.proposer.providers import DryRunProvider, JsonlProvider, Proposal
from experiments.mvp.strategy.proposer.run import (SearchProposer, aggregate_prefixes,
    checked_moves, checked_prefix, execute_map, prefix_summary)
from experiments.mvp.strategy.proposer.registration import collection_requirements


def config(arm='spark', k=4, max_proposals=8, **extra):
    return dict(arm=arm, k=k, max_proposals=max_proposals, provider='dry-run', seed=179,
                evaluation={}, **extra)


def checked(moves=10, passed=True):
    return dict(valid_layout=True, evaluations_used=1, checked_evaluations=1,
                terminal_certificate_check_calls=1, terminal_certificate_check_completed=1,
                check={'passed': passed, 'moves': moves} if passed else {'passed': False})


def test_invalid_and_precheck_failure_do_not_spend_checked_budget(tmp_path):
    texts = ['bad', 'pass12', 'unchecked', 'reject', 'pass10', 'pass8']
    calls = []
    class Provider:
        def propose(self, **kwargs):
            return Proposal(texts[kwargs['attempt'] - 1])
    def evaluate(instance, text, folder, **kwargs):
        calls.append(text)
        if text in {'bad', 'unchecked'}:
            return dict(valid_layout=text != 'bad', checked_evaluations=0,
                        status='invalid_layout' if text == 'bad' else 'timeout')
        return checked(passed=False) if text == 'reject' else checked(int(text[4:]))
    row = execute_map(object(), {'map_id': 'train-a'}, tmp_path, config(), provider=Provider(),
                      evaluator=evaluate, prompt_builder=lambda _: 'fixed prompt')
    assert calls == texts
    assert row['complete'] and row['stop_reason'] == 'checked_budget_reached'
    assert row['proposal_tries'] == 6 and row['checked_evaluations'] == row['evaluations_used'] == 4
    assert row['wasted_proposals'] == 2 and row['invalid_proposals'] == 1
    assert row['prefixes']['1']['proposal_tries'] == 2
    assert row['prefixes']['1']['best_moves'] == 12
    assert row['prefixes']['4']['proposal_tries'] == 6
    assert row['prefixes']['4']['checked_evaluations'] == 4
    assert row['prefixes']['4']['best_moves'] == 8


def test_finite_proposal_cap_leaves_budget_incomplete_and_canned_labelled(tmp_path):
    result = execute_map(object(), {'map_id':'a'}, tmp_path, config(k=1, max_proposals=3),
        provider=DryRunProvider('invalid'), prompt_builder=lambda _: 'p',
        evaluator=lambda *a, **kw: dict(valid_layout=False, checked_evaluations=0, status='invalid_layout'))
    assert not result['complete'] and not result['prefixes']
    assert result['proposal_tries'] == result['invalid_proposals'] == result['wasted_proposals'] == 3
    assert result['evaluations_used'] == 0 and result['stop_reason'] == 'proposal_limit_reached'
    assert result['result_kind'] == 'canned_smoke' and result['comparative_eligible'] is False


def test_resume_replays_without_additional_provider_or_checker_work(tmp_path):
    cfg = config(k=1)
    first = execute_map(object(), {'map_id':'a'}, tmp_path, cfg, provider=DryRunProvider('fixed'),
                        prompt_builder=lambda _: 'p', evaluator=lambda *a, **kw: checked())
    class ForbiddenProvider:
        def propose(self, **kwargs):
            pytest.fail('cached proposal should be reused')
    def forbidden(*args, **kwargs):
        pytest.fail('cached result should be reused')
    resumed = execute_map(object(), {'map_id':'a'}, tmp_path, cfg, provider=ForbiddenProvider(),
                          evaluator=forbidden, prompt_builder=lambda _: 'p')
    assert resumed['prefixes'] == first['prefixes']
    assert resumed['proposal_tries'] == resumed['checked_evaluations'] == 1


def test_resume_rejects_changed_evaluation_prompt(tmp_path):
    cfg = config(k=1)
    execute_map(object(), {'map_id':'a'}, tmp_path, cfg, provider=DryRunProvider('fixed'),
                prompt_builder=lambda _: 'p', evaluator=lambda *a, **kw: checked())
    with pytest.raises(ValueError, match='prompt changed'):
        execute_map(object(), {'map_id':'a'}, tmp_path, cfg, provider=DryRunProvider('fixed'),
                    prompt_builder=lambda _: 'different')


def registered_row(attempt=1):
    return dict(map_id='a', arm='glimmer', attempt=attempt, text='fixed',
                collection_provenance=dict(collection_requirements('glimmer', attempt),
                prompt_sha256=hashlib.sha256(b'p').hexdigest(), input_sha256='a'*64))


def test_missing_jsonl_response_is_wasted_then_stops_incomplete(tmp_path):
    source = tmp_path/'responses.jsonl'
    source.write_text(json.dumps(registered_row())+'\n')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    cfg = config('glimmer')
    cfg.update(provider='jsonl', provider_source_sha256=sha)
    row = execute_map(object(), {'map_id':'a', 'input_sha256':'a'*64}, tmp_path/'run', cfg,
        provider=JsonlProvider(source, expected_sha256=sha), evaluator=lambda *a, **kw: checked(7),
        prompt_builder=lambda _: 'p')
    assert row['checked_evaluations'] == 1 and row['proposal_tries'] == 2
    assert row['provider_failures'] == row['wasted_proposals'] == 1
    assert not row['complete'] and row['stop_reason'] == 'proposal_source_exhausted'
    assert set(row['prefixes']) == {'1'}


def test_bad_collection_provenance_never_reaches_evaluator(tmp_path):
    source = tmp_path/'responses.jsonl'
    data = registered_row()
    data['collection_provenance']['feedback'] = 'checker-assisted'
    source.write_text(json.dumps(data)+'\n')
    cfg = config('glimmer', k=1)
    cfg.update(provider='jsonl', provider_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='feedback'):
        execute_map(object(), {'map_id':'a','input_sha256':'a'*64}, tmp_path/'run', cfg,
            provider=JsonlProvider(source), prompt_builder=lambda _: 'p',
            evaluator=lambda *a, **kw: pytest.fail('unverifiable text was evaluated'))


def test_jsonl_resume_rejects_cached_text_changed_under_valid_provenance(tmp_path):
    source = tmp_path/'responses.jsonl'
    source.write_text(json.dumps(registered_row())+'\n')
    cfg = config('glimmer', k=1)
    cfg.update(provider='jsonl', provider_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    folder = tmp_path/'run'
    job = {'map_id':'a','input_sha256':'a'*64}
    execute_map(object(), job, folder, cfg, provider=JsonlProvider(source),
                prompt_builder=lambda _: 'p', evaluator=lambda *a, **kw: checked(7))
    proposal_path = folder/'proposals/proposal-001.json'
    saved = json.loads(proposal_path.read_text())
    saved['text'] = 'tampered text'
    proposal_path.write_text(json.dumps(saved))
    (folder/'attempts/proposal-001.json').unlink()
    with pytest.raises(ValueError, match='immutable response snapshot'):
        execute_map(object(), job, folder, cfg, provider=JsonlProvider(source),
            prompt_builder=lambda _: 'p', evaluator=lambda *a, **kw: pytest.fail('tampered text evaluated'))


def test_search_observes_only_after_recording_each_try(tmp_path):
    observed = []
    class Search:
        def __init__(self, instance, seed):
            self.seen = 0
        def propose(self, attempt):
            assert self.seen == attempt-1
            return Proposal(str(attempt))
        def observe(self, proposal, result):
            assert result['attempt'] == self.seen+1
            self.seen += 1
            observed.append(result['attempt'])
    row = execute_map(object(), {'map_id':'a'}, tmp_path, config('search', 16, 20),
        evaluator=lambda *a, **kw: checked(12), prompt_builder=lambda _: 'p', search_factory=Search)
    assert observed == list(range(1,17))
    assert set(row['prefixes']) == {'1','4','16'} and row['complete']


def test_rule_arm_uses_same_checked_budget_without_free_fallback(tmp_path):
    calls=[]
    class Rule:
        def __init__(self,instance,seed):
            self.registration={'pinned':'rule'}
        def propose(self,attempt):
            return Proposal('rule' if attempt%2 else 'default',{'provider':'rule'})
        def observe(self,*args):
            pass
    def evaluate(instance,text,folder,**kwargs):
        calls.append(text)
        return dict(valid_layout=True,checked_evaluations=0) if text=='rule' else checked(12)
    row=execute_map(object(),{'map_id':'a'},tmp_path,config('rule',k=1,max_proposals=4),
                    evaluator=evaluate,prompt_builder=lambda _:'p',rule_factory=Rule)
    assert calls==['rule','default']
    assert row['proposal_tries']==2 and row['checked_evaluations']==1
    assert row['result_kind']=='rule' and row['comparative_eligible']


def test_rule_source_drift_before_worker_refuses_evaluation(tmp_path):
    class Rule:
        def __init__(self,*args):
            self.registration={'source':'changed'}
    cfg=config('rule',k=1)
    cfg['rule_policy']={'source':'registered'}
    with pytest.raises(ValueError,match='rule policy changed'):
        execute_map(object(),{'map_id':'a'},tmp_path,cfg,rule_factory=Rule,
                    prompt_builder=lambda _:'p',evaluator=lambda *a,**kw:pytest.fail('drift was evaluated'))


def test_rule_resume_rejects_cached_text_corruption(tmp_path):
    class Rule:
        def __init__(self,*args):
            self.registration={}
        def propose(self,attempt):
            return Proposal('registered rule',{'provider':'rule'})
        def observe(self,*args):
            pass
    cfg=config('rule',k=1)
    execute_map(object(),{'map_id':'a'},tmp_path,cfg,rule_factory=Rule,
                prompt_builder=lambda _:'p',evaluator=lambda *a,**kw:checked(10))
    path=tmp_path/'proposals/proposal-001.json'
    saved=json.loads(path.read_text())
    saved['text']='corrupted rule'
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError,match='registered policy'):
        execute_map(object(),{'map_id':'a'},tmp_path,cfg,rule_factory=Rule,
                    prompt_builder=lambda _:'p',evaluator=lambda *a,**kw:pytest.fail('corruption evaluated'))


def test_checked_move_filter_requires_completed_certificate():
    assert checked_moves({'raw_moves':1}) is None
    assert checked_moves({'check':{'passed':True,'moves':1}}) is None
    assert checked_moves(checked(1,False)) is None
    assert checked_moves(checked(float('nan'))) is None
    assert checked_moves(checked(True)) is None
    assert checked_moves(checked(0)) == 0


def test_search_incumbent_only_uses_checked_improvement():
    from experiments.mvp.strategy.headroom.layout import BlockLayout
    search = SearchProposer.__new__(SearchProposer)
    search.used, search.incumbent, search.best = set(), None, None
    layout = BlockLayout({('P2','L'): frozenset({'h/hold'})})
    proposal = Proposal('fixed', {'layout':layout.to_dict()})
    search.observe(proposal, {'raw_moves':1,'solver_complete':True})
    assert search.incumbent is None
    search.observe(proposal, checked(10))
    search.observe(proposal, checked(0, False))
    search.observe(proposal, checked(12))
    assert search.incumbent.allowed == layout.allowed and search.best == 10


def test_prefix_ends_at_kth_check_not_kth_try():
    rows = [dict(attempt=1,valid_layout=False,checked_evaluations=0), dict(checked(12),attempt=2),
            dict(attempt=3,checked_evaluations=0),dict(checked(10),attempt=4)]
    assert checked_prefix(rows,1) == rows[:2]
    assert checked_prefix(rows,4) is None
    prefix = prefix_summary(rows,4)
    assert set(prefix) == {'1'} and prefix['1']['best_moves'] == 12
    assert prefix['1']['wasted_proposals'] == 1
    assert aggregate_prefixes([{'prefixes':prefix}],4)['1']['evaluations_used'] == 1


@pytest.mark.parametrize('count',[True,-1,2,1.0])
def test_invalid_completed_counts_fail_closed(count):
    with pytest.raises(ValueError,match='zero or one'):
        checked_prefix([{'checked_evaluations':count}],1)


def test_worker_scopes_all_steps_to_headroom_rule_epoch(tmp_path, monkeypatch):
    from experiments.mvp.strategy.proposer import run
    from experiments.mvp.strategy.headroom import run as headroom
    from experiments.mvp.strategy.proposer import cohort
    from stowage import io
    from stowage.rules.catalogue import rule_freeze_v1, rule_freeze_v1_enabled
    from stowage.solver import ballast_deadline
    monkeypatch.setattr(headroom,'allowed_input',lambda path:path)
    monkeypatch.setattr(cohort,'_verify_row',lambda row:row)
    monkeypatch.setattr(run,'digest',lambda path:'sha')
    monkeypatch.setattr(io,'load_instance',lambda path:object())
    monkeypatch.setattr(ballast_deadline,'shutdown_worker',lambda:None)
    def execute(*args):
        assert rule_freeze_v1_enabled() is False
        return {'complete':True}
    monkeypatch.setattr(run,'execute_map',execute)
    with rule_freeze_v1(enabled=True):
        run.run_map(dict(input='ignored.json',input_sha256='sha',map_id='a'),tmp_path,config())
        assert rule_freeze_v1_enabled() is True


def test_controller_distributes_immutable_snapshot_even_if_live_file_changes(tmp_path, monkeypatch):
    from experiments.mvp.strategy.proposer import run
    from experiments.mvp.strategy.headroom import run as headroom
    from experiments.mvp.strategy.proposer import cohort
    from experiments.mvp.strategy.proposer.providers import make_provider
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    monkeypatch.setattr(run,'HERE',workspace)
    source = tmp_path/'canned.txt'
    source.write_text('version A')
    manifest = tmp_path/'maps.json'
    job = dict(map_id='a',input='train/a.json',input_sha256='a'*64)
    manifest.write_text(json.dumps({'maps':[job], 'format':cohort.FORMAT,
        'frozen_before_proposer_runs':True,'medium_source':{'input':'frozen','sha256':'f'*64}}))
    monkeypatch.setattr(cohort,'manifest_jobs',lambda path:[job])
    monkeypatch.setattr(headroom,'source_snapshot',lambda:{})
    consumed = []
    class Pool:
        def __init__(self,**kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
        def submit(self, fn, job, output, cfg):
            source.write_text('version B')
            provider = make_provider('dry-run',canned_text=cfg['provider_snapshot'],
                                     expected_sha256=cfg['provider_source_sha256'])
            consumed.append(provider.propose(instance=None,map_id='a',arm='spark',attempt=1,prompt='p').text)
            future = Future()
            future.set_result(dict(map_id='a',complete=True,prefixes={},proposal_tries=1,wasted_proposals=0))
            return future
    monkeypatch.setattr(run,'ProcessPoolExecutor',Pool)
    args = ['--arm','spark','--k','1','--maps',str(manifest),'--canned-text',str(source),
            '--output',str(workspace/'output')]
    assert run.main(args) == 0
    assert consumed == ['version A']
    assert (workspace/'output/provider-source.snapshot').read_text() == 'version A'
    with pytest.raises(RuntimeError,match='changed'):
        run.main(args)


def test_spark_total_at_two_dollars_refuses_before_workers(tmp_path,monkeypatch):
    from experiments.mvp.strategy.proposer import run,cohort
    from experiments.mvp.strategy.headroom import run as headroom
    workspace=tmp_path/'workspace'
    workspace.mkdir()
    monkeypatch.setattr(run,'HERE',workspace)
    job=dict(map_id='a',input='train/a.json',input_sha256='a'*64)
    manifest=tmp_path/'maps.json'
    manifest.write_text(json.dumps({'maps':[job],'format':cohort.FORMAT,
        'frozen_before_proposer_runs':True,'medium_source':{'input':'frozen','sha256':'f'*64}}))
    monkeypatch.setattr(cohort,'manifest_jobs',lambda path:[job])
    monkeypatch.setattr(headroom,'source_snapshot',lambda:{})
    monkeypatch.setattr(run,'ProcessPoolExecutor',lambda **kw:pytest.fail('over-cap source reached worker'))
    source=tmp_path/'spark.jsonl'
    source.write_text(json.dumps(dict(arm='spark',text='unused invalid text',
        usage={'billed_cost_usd':2.0,'billed_cost_includes_all_tokens':True}))+'\n')
    with pytest.raises(ValueError,match='strictly under'):
        run.main(['--arm','spark','--k','1','--provider','jsonl','--proposals',str(source),
                  '--maps',str(manifest),'--output',str(workspace/'output')])
    assert not (workspace/'output').exists()

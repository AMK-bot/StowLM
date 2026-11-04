from concurrent.futures import Future
from pathlib import Path

import pytest

from llm.exit import runner as r
from llm.exit import runtime


def test_samples_are_a_shared_budget_even_with_three_children():
    assert r.sample_allocation(6, 2) == [3, 3]
    assert r.sample_allocation(8, 3) == [3, 3, 2]
    assert sum(r.sample_allocation(7, 3)) == 7
    with pytest.raises(ValueError):
        r.sample_allocation(2, 3)


def test_model_plus_polish_never_counts_as_model_alone_beating_gold():
    from llm.exit.selection import Candidate
    own=r.gold_win_labels(Candidate(None,'model'),4,[5])
    polished=r.gold_win_labels(Candidate(None,'polished',metadata={'parent_source':'model'}),4,[5])
    assert own['model_own_beat_gold'] and not own['model_plus_lns_beat_gold']
    assert polished['model_plus_lns_beat_gold'] and not polished['model_beat_gold']
    assert not r.gold_win_labels(Candidate(None,'model'),5,[5])['model_beat_gold']


def test_elite_has_own_greedy_sample_and_survives_children_without_strict_gain():
    from llm.exit.fitness import population_fitness
    population=[dict(adapter='parent',elite=True),dict(adapter='A'),dict(adapter='B')]
    assert r.population_allocation(6,population)==[1,3,3]
    assert r.population_allocation(6,[population[0]])==[6]
    rows=lambda moves:[dict(instance_id='map',passed=True,weighted_moves=moves)]
    assert population_fitness({'parent':rows(5),'A':rows(5),'B':rows(6)})['best_adapter']=='parent'


def test_generation_pair_cap_keeps_move_comparisons_ahead_of_deadends():
    pairs=[{'evidence':{'kind':kind}} for kind in ['dead_end','checker_failure','strictly_more_moves']]
    assert [p['evidence']['kind'] for p in r.rank_preference_pairs(pairs,7,2)]==[
        'strictly_more_moves','checker_failure']


def test_hyperparameters_inherit_from_fittest_and_are_bounded():
    children = r.mutate_children(dict(adapter='best', learning_rate=8e-5,
                                     replay_ratio=.5, temperature=.9), 3)
    assert [c['learning_rate'] for c in children] == [4e-5, 1e-4, 8e-5]
    assert children[2]['replay_ratio'] == .6
    assert all(c['temperature'] == .9 for c in children)


class InlinePool:
    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def submit(self, fn, *args):
        future = Future()
        future.set_result(fn(*args))
        return future


@pytest.mark.parametrize('generation,rollback,dpo_mode,dpo_failure,population_count', [
    (0,False,False,False,2),(3,True,False,False,2),(0,False,True,False,2),(0,False,True,True,2),(0,False,True,False,3)])
def test_population_shares_maps_trains_checked_winners_and_audit_rolls_back(
        tmp_path, monkeypatch, generation, rollback,dpo_mode,dpo_failure,population_count):
    args = r.parser().parse_args(['--parent', str(tmp_path/'baseline'), '--out', str(tmp_path),
                                  '--tiny', '1', '--small', '1'])
    args.dpo=dpo_mode
    args.population=population_count
    args.early_pruning=False  # Gold verification has separate real CPU fixtures.
    args.audit_clock=None
    (tmp_path/'val.json').write_text('{}')
    from llm.exit import resources
    monkeypatch.setattr(resources,'background_cores',lambda:0)
    from llm.exit import audit
    monkeypatch.setattr(audit,'frozen_audit_paths',lambda *a,**kw:[tmp_path/'val.json'])
    maps = [tmp_path/'tiny.json', tmp_path/'small.json']
    class Schedule:
        root = tmp_path
        state = {'generations': {}}
        def next_train(self, tiny, small, generation, **kwargs): return maps
        def fixed_validation(self, *args): return [tmp_path/'val.json']
    population = [dict(adapter=str(tmp_path/name), learning_rate=5e-5, replay_ratio=.5, temperature=.8)
                  for name in ('child-a', 'child-b')]
    if population_count==3:
        for config in population:config['method']='sft_dpo'
    monkeypatch.setattr(r, 'source_pins', lambda: {'same': 'source'})
    monkeypatch.setattr(r, 'adapter_identity', lambda _: {'same': 'adapter'})
    monkeypatch.setattr(r, 'ProcessPoolExecutor', InlinePool)
    monkeypatch.setattr(r,'teacher_job',lambda *a,**kw:'teacher-manifest')
    monkeypatch.setattr(r,'assert_audit_epoch',lambda *a:None)
    decode_calls, trained, datasets = [], [], []
    def generate(path, parent, args, seed, directory, **kwargs):
        decode_calls.append((path, parent, kwargs['k']))
        return {'timings': {'wall_s': .1}, 'candidates': [dict(plan='opaque-plan', sample=0)]}
    monkeypatch.setattr(r, '_raw_generation', generate)
    def selection(path, candidates, corpus, gold, directory, options, adapters,*extra):
        assert len(candidates) == 2
        # The second child wins fitness; only one map produces an admitted target.
        return dict(instance_id=path,teachers=[dict(source='gold',passed=True,attempted=True)],passed=path.endswith('tiny.json'), winner_record=path+'.checked.record.json',
            source='model', gold_moves=10, model_beat_gold=True,
            timings={'verify_s': .1, 'polish_s': .1, 'greedy_verify_s': .1},
            preferences={'pairs':[{'chosen':{'prompt':[1],'tokens':[2]},
                                   'rejected':{'prompt':[1],'tokens':[3]}}] if dpo_mode else [],'wall_s':.01},
            greedy_fitness={a: dict(instance_id=path, passed=True, weighted_moves=9-i)
                            for i,a in enumerate(adapters)})
    monkeypatch.setattr(r, 'select_job', selection)
    (tmp_path/'loss-or-replay.record.json').write_text('{}')
    monkeypatch.setattr(r, 'saved_gold_records', lambda *a, **k: [tmp_path/'loss-or-replay.record.json'])
    from llm.exit import replay
    monkeypatch.setattr(replay,'choose_replay',lambda pool,*a:pool)
    from llm.exit import reverify
    def readmit(selections, **kwargs):
        return dict(status='ready',selections=selections,
            winner_records=[s['winner_record'] for s in selections if s['passed']],
            receipt=dict(training_epoch={'same':'source'}),counts={},wall_s=.01)
    monkeypatch.setattr(reverify,'reverify_training_rows',readmit)
    monkeypatch.setattr(reverify,'assert_training_receipt',lambda *a,**kw:None)
    def dataset(winners, replay, out, **kwargs):
        datasets.append(winners)
        assert len(winners) == 1 and winners[0].endswith('tiny.json.checked.record.json')
        return {'current_rows': 1}
    monkeypatch.setattr(r, 'build_training_dataset', dataset)
    monkeypatch.setattr(runtime, 'train', lambda parent, *a, **k: trained.append(parent))
    from llm.exit import dpo
    preference_calls=[]
    def preference_train(parent,child,pairs,out,**kwargs):
        preference_calls.append((parent,child,out,kwargs['reference_cache_path']))
        if dpo_failure:raise ValueError('synthetic preference failure')
        return {'method':'sft_dpo','steps':kwargs['steps']}
    monkeypatch.setattr(dpo,'train_preference',preference_train)
    def evaluate(parent, *a):
        return [dict(instance_id='fixed-val', passed=True,
                     weighted_moves=5 if str(parent).endswith('baseline') else 20)]
    monkeypatch.setattr(r, 'evaluate', evaluate)
    result = r.run_generation(generation, population, args, Schedule())
    assert [(p.name,k) for p,_,k in decode_calls] == [('tiny.json',4),('small.json',4)]*2
    assert trained == ([] if rollback else [str(tmp_path/'child-b')]*population_count)
    assert len(result) == (1 if rollback else population_count+1)
    if dpo_mode:
        assert len(preference_calls)==(2 if population_count==3 else 1)
        assert len({str(call[3]) for call in preference_calls})==len(preference_calls)
        assert result[1]['method']=='sft'
        if dpo_failure:
            assert result[2]['method']=='sft'
            assert result[2]['adapter'].endswith('child-1')
            assert 'synthetic preference failure' in result[2]['preference_error']
        else:
            assert result[2]['method']=='sft_dpo'
            assert result[2]['adapter'].endswith('child-1/preference')
    else:
        assert not preference_calls
    summary = r.read_json(tmp_path/f'gen-{generation:03d}'/'summary.json')
    assert summary['fitness']['best_adapter'] == str(tmp_path/'child-b')
    assert bool(summary['audit'] and summary['audit']['rolled_back']) == rollback
    if rollback:
        assert summary['stopped'] and (tmp_path/'terminal.json').exists()
        assert summary['audit']['audited_adapter'].endswith('child-b')
    else:
        assert result[0]['elite'] is True


def test_ledger_unknown_reservations_fail_closed():
    from llm.exit.resources import ledger_totals
    assert ledger_totals('| a | t | 4 | 8 | no | x | y |') == (4.,8.,False)
    assert ledger_totals('| a | t | 2 max | 3 | no | x | y |') == (2.,3.,False)
    with pytest.raises(RuntimeError):
        ledger_totals('| a | t | unknown | 8 | no | x | y |')


def test_d109_flags_and_checked_incumbent_never_wait_or_use_failed_future():
    args=r.parser().parse_args(['--B','4','--k','8','--population','3','--cached-activations'])
    assert args.batch_size==4 and args.workers==6 and args.cached_activations
    future=Future()
    provider=r.incumbent_provider(future)
    assert provider() is None
    future.set_result({'passed':False,'weighted_moves':0,'source':'gold'})
    assert provider() is None
    checked=Future();checked.set_result({'passed':True,'weighted_moves':5,'source':'gold'})
    assert r.incumbent_provider(checked)().weighted_moves==5


def test_cached_activation_switch_refuses_before_gpu_admission(tmp_path,monkeypatch):
    import sys
    from llm.exit import resources
    monkeypatch.setattr(sys,'argv',['runner','--parent','missing','--out',str(tmp_path),
                                    '--cached-activations'])
    monkeypatch.setattr(resources,'reservation',lambda *a,**k:pytest.fail('GPU admission reached'))
    with pytest.raises(SystemExit,match='prototype integration'):
        r.main()


def test_open_review_refuses_direct_generation_before_gpu_admission(tmp_path,monkeypatch):
    import sys
    from llm.exit import resources,review_gate
    monkeypatch.setattr(sys,'argv',['runner','--parent','missing','--out',str(tmp_path)])
    monkeypatch.setattr(resources,'reservation',lambda *a,**k:pytest.fail('GPU admission reached'))
    def blocked():raise RuntimeError('unresolved P1 fixture')
    monkeypatch.setattr(review_gate,'require_review_gate',blocked)
    with pytest.raises(RuntimeError,match='unresolved P1'):
        r.main()


@pytest.mark.parametrize('crash',[False,True])
def test_recovery_restart_never_reuses_spent_or_reserved_credit(tmp_path,monkeypatch,crash):
    from types import SimpleNamespace
    from llm.exit import recovery
    from stowage.model.fixtures import build_instance
    ipath=tmp_path/'instance.json';ipath.write_text(build_instance().model_dump_json())
    args=SimpleNamespace(corpus=tmp_path/'corpus',recovery_fraction=.14,
                         recovery_max_cases=4,recovery_time_limit=30.)
    calls=[]
    def attempt(instance,prefix,*,budget,**kwargs):
        calls.append(budget.allowance(30.))
        if crash:raise RuntimeError('interrupted recovery')
        budget.spent_s+=10.;budget.cases+=1
        return recovery.RecoveryResult(details={'fixture':'charged'})
    monkeypatch.setattr(recovery,'recover_dead_end',attempt)
    samples=[[dict(status='dead_end',prefix_actions=[{}],candidate_id='dead')]]
    if crash:
        with pytest.raises(RuntimeError,match='interrupted'):
            r.recover_generation([ipath],samples,[{'cpu_s':100.}],args,tmp_path)
    else:
        r.recover_generation([ipath],samples,[{'cpu_s':100.}],args,tmp_path)
    r.recover_generation([ipath],samples,[{'cpu_s':100.}],args,tmp_path)
    assert calls==pytest.approx([13.75])
    state=r.read_json(tmp_path/'recovery-progress.json')
    assert state['charged_cpu_upper_s']==pytest.approx(13.75 if crash else 10.)


def test_mask_speedup_requests_fresh_process_only_after_completed_generation(tmp_path,monkeypatch):
    from llm.exit import audit,resources,review_gate,code_epoch
    args=r.parser().parse_args(['--parent','pilot','--out',str(tmp_path),'--gens','3'])
    args.audit_clock=None;args.adaptive_workers=False
    pins={'src/llm/stowlm/mask.py':'old'}
    monkeypatch.setattr(r,'source_pins',lambda:pins.copy())
    monkeypatch.setattr(audit,'frozen_audit_paths',lambda *a,**kw:[])
    monkeypatch.setattr(r,'ensure_baseline',lambda *a,**kw:[])
    monkeypatch.setattr(review_gate,'require_review_gate',lambda:None)
    monkeypatch.setattr(r,'CorpusSchedule',lambda *a,**kw:None)
    monkeypatch.setattr(r,'final_population_audit',lambda *a,**kw:pytest.fail('freeze before new smoke'))
    events=[]
    def generation(number,population,args,schedule):
        events.append('generation_completed')
        assert args._loaded_source_pins['src/llm/stowlm/mask.py']=='old'
        pins['src/llm/stowlm/mask.py']='new'
        return [dict(adapter='child',elite=True)]
    monkeypatch.setattr(r,'run_generation',generation)
    monkeypatch.setattr(r,'read_json',lambda path:{'timings':{}})
    monkeypatch.setattr(r,'resize_after_pilot',lambda *a,**kw:events.append('sized'))
    with pytest.raises(SystemExit) as stopped:r.run_loop(args)
    assert stopped.value.code==code_epoch.RESTART_EXIT_CODE
    assert events==['generation_completed','sized']
    import json
    boundary=json.loads((tmp_path/'epoch-boundary.json').read_text())
    assert boundary['next_generation']==1 and boundary['population'][0]['adapter']=='child'

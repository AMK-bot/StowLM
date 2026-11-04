"""Gold-only ancestry-matched control, all model/build/check calls mocked."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def state(tmp_path, monkeypatch):
    from llm.exit import control
    from llm.exit import replay
    from llm.stowlm.train import TRAINER_VERSION
    monkeypatch.setattr(replay, 'checker_epoch', lambda:'checker-epoch')
    baseline = tmp_path/'original81'; baseline.mkdir()
    (baseline/'adapters.safetensors').write_bytes(b'8.1')
    gold = tmp_path/'gold'
    current = tmp_path/'current.json'
    current.write_text(json.dumps(dict(id='current-map', seed=999)))
    records = []
    for index, rung, ship in ((0,'tiny','shipa'),(1,'small','shipa'),(2,'small','shipb'),(3,'tiny','shipb')):
        folder = gold/'train'/rung/ship/str(index); folder.mkdir(parents=True)
        (folder/'case.lock').touch()
        record = folder/'result.json'
        record.write_text(json.dumps(dict(passed=True, instance=dict(id=f'map-{index}', seed=index,
            size_class=rung, family={'vessel':ship}))))
        records.append(record)
    excluded = gold/'train/tiny/shipa/current'; excluded.mkdir()
    (excluded/'case.lock').touch()
    (excluded/'result.json').write_text(json.dumps(dict(passed=True,
        instance=dict(id='different-map-same-seed', seed=999, size_class='tiny', family={'vessel':'shipa'}))))
    val = gold/'val/tiny/shipa/0'; val.mkdir(parents=True)
    (val/'case.lock').touch(); (val/'result.json').write_text(json.dumps(dict(passed=True,
        instance=dict(id='validation',seed=33))))
    calls = dict(build=[], train=[], audit=[])
    def builder(records, directory, **kwargs):
        calls['build'].append((list(records), Path(directory), kwargs))
        return dict(control_gold_only=True, rows=len(records))
    def trainer(parent, dataset, output, **kwargs):
        calls['train'].append((str(parent), Path(dataset), Path(output), kwargs))
        output.mkdir(parents=True, exist_ok=True)
        (output/'adapters.safetensors').write_bytes(f'control-{len(calls["train"])}'.encode())
        config = dict(kwargs); config['iters'] = config.pop('steps'); config['learning_rate'] = config.pop('lr')
        (output/'adapter_config.json').write_text(json.dumps(dict(stowlm_train_config=config,
                                                               stowlm_trainer=TRAINER_VERSION)))
    def phase(adapter, inherited, generation, *, lr=1e-4, steps=120, method='sft', examples=4):
        adapter = tmp_path/adapter; adapter.mkdir(parents=True, exist_ok=True)
        (adapter/'adapters.safetensors').write_bytes(str(adapter).encode())
        data = adapter/'training-data'; data.mkdir()
        (data/'train.jsonl').write_text(''.join(json.dumps(dict(meta={'gold':{
            'input_sha256':f'{index:064x}'}}))+'\n' for index in range(examples)))
        config = dict(data_dir=str(data), iters=steps, learning_rate=lr, seed=generation, lr_schedule='cosine',
            warmup_frac=.1, min_lr_frac=.2, grad_accumulation_steps=4, batch_size=1,
            max_seq_length=10240, attn_block=0, grad_checkpoint=True,
            extra={'exit_parent':str(inherited)})
        (adapter/'adapter_config.json').write_text(json.dumps(dict(stowlm_train_config=config,
                                                               stowlm_trainer=TRAINER_VERSION)))
        published = adapter
        if method == 'sft_dpo':
            published = adapter/'preference'; published.mkdir()
            (published/'adapters.safetensors').write_bytes(b'dpo')
        row = dict(adapter=str(published), inherited_from=str(inherited), learning_rate=lr, method=method)
        return dict(generation=generation, children=[row]), published
    args = SimpleNamespace(parent=baseline, gold=gold, steps=999, learning_rate=999)
    return SimpleNamespace(c=control, baseline=baseline, args=args, phase=phase, records=records,
        current=current, calls=calls, builder=builder, trainer=trainer, directory=tmp_path/'control')


def run(s, history, parent, **kwargs):
    return s.c.ensure_gold_control(history, parent, s.args, s.directory,
        builder=s.builder, trainer=s.trainer, excluded=[s.current], **kwargs)


def test_two_inherited_sft_phases_use_actual_lr_steps_seed_and_all_train_gold(state):
    s = state
    first, a = s.phase('loop0', s.baseline, 0, lr=5e-5)
    second, b = s.phase('loop1', a, 1, lr=1e-4)
    report = run(s, [first,second], b)
    assert report['phase_count'] == 2 and report['total_sft_microsteps'] == 240
    assert report['schedule_matched'] and report['gold_only']
    assert not report['dpo_updates_unmatched']
    assert [call[3]['lr'] for call in s.calls['train']] == [5e-5,1e-4]
    assert [call[3]['seed'] for call in s.calls['train']] == [0,1]
    assert [call[3]['steps'] for call in s.calls['train']] == [120,120]
    assert s.calls['train'][0][0] == str(s.baseline)
    assert s.calls['train'][1][0] == str(s.directory/'phase-00/adapter')
    for records, _, kw in s.calls['build']:
        assert set(records) == set(s.records)
        assert kw['validation_records'] == ()
    for _, _, _, config in s.calls['train']:
        assert config['lr_schedule'] == 'cosine' and config['grad_accumulation_steps'] == 4
        assert config['warmup_frac'] == .1 and config['min_lr_frac'] == .2
    assert all(phase['optimizer_reset'] for phase in report['phases'])
    assert all(phase['binding']['phase']['optimizer_updates'] == 30 for phase in report['phases'])
    assert 'fresh Adam' in report['optimizer']


def test_elapsed_generations_do_not_create_noninherited_updates(state):
    s = state
    ignored, _ = s.phase('unselected0', s.baseline, 0)
    selected, child = s.phase('selected1', s.baseline, 1, steps=80)
    report = run(s, [ignored, selected], child)
    assert report['phase_count'] == 1 and report['total_sft_microsteps'] == 80
    assert len(s.calls['train']) == 1 and s.calls['train'][0][3]['seed'] == 1


def test_unchanged_elite_still_gets_a_real_trained_gold_control(state):
    s = state
    first, a = s.phase('child0', s.baseline, 0, lr=5e-5, steps=120)
    second, b = s.phase('child1', s.baseline, 0, lr=2e-4, steps=80)
    history = [dict(generation=0, children=first['children']+second['children'])]
    fitness = dict(pass_counts={str(s.baseline):9,str(a):9,str(b):9},
                   mean_moves={str(s.baseline):12.7,str(a):13.5,str(b):13.})
    report = run(s, history, s.baseline, fitness=fitness)
    assert report['phase_count'] == 1 and report['total_sft_microsteps'] == 80
    assert report['schedule_reference_adapter'] == str(b)
    assert report['compared_adapter'] == str(s.baseline)
    assert report['compared_adapter_schedule_matched'] is False
    assert report['schedule_matched'] and report['trained_control']
    assert report['adapter'] != str(s.baseline)
    assert len(s.calls['train']) == len(s.calls['build']) == 1
    assert s.calls['train'][0][3]['lr'] == 2e-4


def test_elite_requires_fitness_and_actual_training_schedule(state):
    s = state
    first, a = s.phase('child0', s.baseline, 0)
    with pytest.raises(ValueError, match='paired fitness'):
        run(s, [first], s.baseline)
    with pytest.raises(ValueError, match='at least one actual'):
        run(s, [first], s.baseline, schedule_adapter=s.baseline)
    with pytest.raises(ValueError, match='actually trained competing'):
        run(s, [], s.baseline, fitness=dict(pass_counts={str(s.baseline):9},
                                          mean_moves={str(s.baseline):12.7}))
    assert not s.calls['train']


def test_elite_control_reference_uses_pass_count_before_moves(state):
    s = state
    first, a = s.phase('child0', s.baseline, 0)
    second, b = s.phase('child1', s.baseline, 0)
    history = [dict(generation=0, children=first['children']+second['children'])]
    fitness = dict(pass_counts={str(s.baseline):10,str(a):9,str(b):8},
                   mean_moves={str(s.baseline):12.,str(a):30.,str(b):1.})
    assert s.c.choose_control_schedule_adapter(history,s.baseline,s.baseline,fitness) == str(a)
    with pytest.raises(ValueError, match='fittest trained competing child'):
        run(s, history, s.baseline, fitness=fitness, schedule_adapter=b)
    assert not s.calls['train']


def test_dpo_ancestry_matches_sft_receipt_and_discloses_extra_updates(state):
    s = state
    generation, adapter = s.phase('sft-before-dpo', s.baseline, 0, method='sft_dpo')
    report = run(s, [generation], adapter)
    assert report['dpo_updates_unmatched'] is True
    assert report['total_sft_microsteps'] == 120
    assert report['phases'][0]['binding']['phase']['sft_adapter'].endswith('sft-before-dpo')


def test_control_audit_uses_caller_identical_frozen_maps_and_recipe(state):
    s = state; generation, adapter = s.phase('loop0', s.baseline, 0)
    paths = [f'fixed-small-{index}' for index in range(30)]
    def evaluator(parent, maps, directory, args):
        s.calls['audit'].append((parent,maps,directory,args)); return [{'passed':True}]*30
    report = run(s, [generation], adapter, evaluator=evaluator, audit_paths=paths)
    assert len(report['audit_rows']) == 30
    assert s.calls['audit'][0][1] == paths and s.calls['audit'][0][3] is s.args


def test_control_phase_resume_is_idempotent_and_rejects_output_tamper(state):
    s = state; generation, adapter = s.phase('loop0', s.baseline, 0)
    first = run(s, [generation], adapter)
    assert run(s, [generation], adapter)['adapter'] == first['adapter']
    assert len(s.calls['train']) == len(s.calls['build']) == 1
    (Path(first['adapter'])/'adapters.safetensors').write_bytes(b'tampered')
    with pytest.raises(RuntimeError, match='committed control phase changed'):
        run(s, [generation], adapter)


def test_changed_inventory_or_schedule_cannot_reuse_control(state):
    s = state; generation, adapter = s.phase('loop0', s.baseline, 0)
    run(s, [generation], adapter)
    s.records[0].write_text(s.records[0].read_text()+'\n')
    with pytest.raises(RuntimeError, match='schedule, gold inventory or checker epoch changed'):
        run(s, [generation], adapter)


@pytest.mark.parametrize('change', ['missing', 'cycle', 'receipt_parent', 'version'])
def test_incomplete_or_mismatched_schedule_lineage_fails_before_training(state, change):
    s = state; generation, adapter = s.phase('loop0', s.baseline, 0)
    if change == 'missing': generation['children'] = []
    elif change == 'cycle': generation['children'][0]['inherited_from'] = str(adapter)
    else:
        path = adapter/'adapter_config.json'; raw = json.loads(path.read_text())
        if change == 'receipt_parent': raw['stowlm_train_config']['extra']['exit_parent'] = str(adapter)
        else: raw['stowlm_trainer'] = 'old-trainer-with-different-schedule'
        path.write_text(json.dumps(raw))
    with pytest.raises(ValueError): run(s, [generation], adapter)
    assert s.calls['train'] == []


def test_current_map_exclusions_required_before_any_control_training(state):
    s = state; generation, adapter = s.phase('loop0', s.baseline, 0)
    with pytest.raises(ValueError, match='explicit current-generation map exclusions'):
        s.c.ensure_gold_control([generation], adapter, s.args, s.directory,
            builder=s.builder, trainer=s.trainer)
    assert s.calls['train'] == []


def test_actual_published_control_schedule_is_checked(state):
    s = state; generation, adapter = s.phase('loop0', s.baseline, 0)
    def wrong(*args, **kwargs):
        kwargs['steps'] = 4; s.trainer(*args, **kwargs)
    with pytest.raises(RuntimeError, match='different update schedule'):
        s.c.ensure_gold_control([generation], adapter, s.args, s.directory,
            builder=s.builder, trainer=wrong, excluded=[s.current])


def test_seeded_stratified_subset_is_drawn_from_whole_gold_pool(state):
    s = state; generation, adapter = s.phase('loop0', s.baseline, 0, examples=2)
    report = run(s, [generation], adapter)
    selected = s.calls['build'][0][0]
    assert len(selected) == 2 and set(selected).issubset(s.records)
    assert report['gold_pool_records'] == 4 and report['example_count_matched']
    assert report['phases'][0]['binding']['gold_target_examples'] == 2


def test_subset_cap_is_labelled_when_unique_count_does_not_match(state):
    s = state; generation, adapter = s.phase('loop0', s.baseline, 0, examples=4)
    s.args.control_max_gold = 2
    report = run(s, [generation], adapter)
    assert len(s.calls['build'][0][0]) == 2
    assert report['example_count_matched'] is False
    assert report['schedule_matched'] is True


def test_default_builder_readmits_selected_gold_before_any_training_target(state, monkeypatch, tmp_path):
    import importlib.abc
    import importlib.machinery
    import importlib.util
    from llm.exit import data, identity, replay
    from stowage.model import MovePlan
    from stowage.model.fixtures import build_instance
    s = state; instance = build_instance()
    plan = MovePlan(id='fresh-checked-plan', instance_id=instance.id, call_index=instance.call_index)
    calls = dict(check=[], guard=[], build=[])
    class Loader(importlib.abc.Loader):
        def create_module(self, spec): return None
        def exec_module(self, module):
            module.load_triple = lambda path:(instance, plan, {'passed':True}, 'a'*64, None)
    original = importlib.util.spec_from_file_location
    def spec(name, path):
        if name == '_control_mvp_builder':
            return importlib.machinery.ModuleSpec(name, Loader())
        return original(name, path)
    monkeypatch.setattr(importlib.util, 'spec_from_file_location', spec)
    monkeypatch.setattr(identity, 'identity_guard', lambda *_:SimpleNamespace(
        require_allowed=lambda *args, **kwargs:calls['guard'].append(kwargs)))
    def current(record, instance, plan, report, cache):
        calls['check'].append((record,cache))
        if record == s.records[0]:
            raise ValueError('fresh checker cannot admit stale gold')
        return plan, dict(passed=True, marker='fresh-current-epoch'), True
    monkeypatch.setattr(replay, 'ensure_current_replay', current)
    def build(records, previous, directory, **kwargs):
        calls['build'].append((records,previous,kwargs))
        for record in records:
            row = json.loads(record.read_text())
            assert row['report']['marker'] == 'fresh-current-epoch'
            assert row['exit_checker_epoch'] == 'checker-epoch'
        return dict(current_rows=len(records))
    monkeypatch.setattr(data, 'build_training_dataset', build)
    out = tmp_path/'control/phase-00/data'
    report = s.c._build_gold_dataset(s.records, out)
    assert len(calls['check']) == len(calls['guard']) == 4
    assert report['gold_admitted'] == 3 and len(report['gold_rejected']) == 1
    assert calls['build'][0][1] == [] and calls['build'][0][2]['replay_ratio'] == 0.
    assert all(str(cache).endswith('control/gold-current-checker-cache') for _,cache in calls['check'])


def test_control_forwards_admitted_pool_and_persists_capacity_before_build(state,monkeypatch):
    from llm.exit import dataset_pool
    s=state; generation,adapter=s.phase('loop-pooled',s.baseline,0)
    s.args.dataset_workers=12;s.args.workers=4
    admitted=dict(workers=2,requested=12,native_threads=1,
                  worker_caps=dict(requested=12,lease_cores=2,lease_ram=4,fleet_cores=3))
    calls=[]
    def chooser(args):
        assert args is s.args;calls.append(args)
        return admitted
    monkeypatch.setattr(dataset_pool,'choose_workers',chooser)
    def builder(records,directory,**kwargs):
        assert kwargs['workers']==2
        receipt=json.loads((s.directory/'phase-00/dataset-capacity.json').read_text())
        assert receipt==admitted
        return s.builder(records,directory,**kwargs)
    report=s.c.ensure_gold_control([generation],adapter,s.args,s.directory,
        trainer=s.trainer,builder=builder,excluded=[s.current])
    assert len(calls)==1
    assert report['phases'][0]['dataset_capacity']==admitted
    assert s.calls['build'][0][2]['workers']==2


def test_default_gold_builder_uses_ordered_pool_before_dataset_replay(state,monkeypatch,tmp_path):
    from contextlib import contextmanager
    from llm.exit import dataset_pool,data
    s=state; calls=[]
    @contextmanager
    def pool(workers):
        calls.append(('pool',workers))
        yield lambda function,jobs:[function(job) for job in jobs]
    def admit(job):
        calls.append(('admit',job['index']))
        return dict(admitted=str(tmp_path/f"row-{job['index']}.json"))
    def build(records,previous,directory,**kwargs):
        calls.append(('replay',kwargs['workers']))
        assert previous==[] and records==[tmp_path/'row-0.json',tmp_path/'row-1.json']
        return dict()
    monkeypatch.setattr(dataset_pool,'ordered_pool',pool)
    monkeypatch.setattr(s.c,'_admit_gold_record',admit)
    monkeypatch.setattr(data,'build_training_dataset',build)
    result=s.c._build_gold_dataset(s.records[:2],tmp_path/'pool-out',workers=3)
    assert calls==[('pool',3),('admit',0),('admit',1),('replay',3)]
    assert result['admission_workers']==3 and result['native_threads']==1

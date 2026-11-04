"""CPU contracts: batching, layer composition and parent initialization."""
import importlib
import sys
from types import SimpleNamespace

import pytest

runtime = importlib.import_module('llm.exit.runtime')


def test_import_runtime_does_not_import_mlx():
    # Test an isolated interpreter so collection order cannot hide eager imports.
    import subprocess
    result = subprocess.run([sys.executable, '-c',
        "import importlib,sys; importlib.import_module('llm.exit.runtime'); "
        "assert 'mlx.core' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_sampling_is_greedy_plus_stochastic_in_one_batch():
    calls = []
    def sample(model, prompt, decoders, **kwargs):
        calls.append((list(decoders), kwargs))
        return list(decoders), SimpleNamespace(summary=lambda: {'wall_s': 1.0})
    results, stats = runtime._sample_batches(sample, 'model', [1], list(range(5)),
        temperature=.8, max_tokens=20, seed=7)
    assert results == list(range(5))
    assert [len(x[0]) for x in calls] == [5]
    assert [x[1]['temperature'] for x in calls] == [[0.0,.8,.8,.8,.8]]
    assert [x[1]['seed'] for x in calls] == [7]
    assert [x['batch'] for x in stats] == [5]


def test_single_greedy_sample_has_no_empty_sampler_call():
    calls = []
    def sample(model, prompt, decoders, **kwargs):
        calls.append(kwargs)
        return decoders, SimpleNamespace(summary=lambda: {})
    runtime._sample_batches(sample, None, [1], ['decoder'],
        temperature=.8, max_tokens=20, seed=7)
    assert len(calls) == 1
    assert calls[0]['temperature'] == [0.0]


def test_decode_batch_flag_chunks_all_rows_without_extra_greedy():
    calls=[]
    def sample(model,prompt,decoders,**kwargs):
        calls.append((list(decoders),kwargs['temperature']))
        return decoders,SimpleNamespace(summary=lambda:{})
    results,_=runtime._sample_batches(sample,None,[1],list(range(8)),temperature=.8,
        max_tokens=20,seed=0,batch_size=3)
    assert results==list(range(8))
    assert calls==[([0,1,2],[0.,.8,.8]),([3,4,5],[.8,.8,.8]),([6,7],[.8,.8])]
    with pytest.raises(ValueError,match='batch size'):
        runtime._sample_batches(sample,None,[],[1],temperature=.8,max_tokens=20,seed=0,batch_size=0)


def test_parent_hook_restores_on_error_and_inherits_provenance(tmp_path, monkeypatch):
    events = []
    def layers(model, *args, **kwargs):
        events.append('lora')
    def provenance(cfg, data, base, inherited):
        return inherited
    trainer = SimpleNamespace(training_provenance=provenance)
    utils = SimpleNamespace(linear_to_lora_layers=layers)
    model = object()
    monkeypatch.setattr(runtime, '_load_parent_weights', lambda model, parent:
                        events.append(str(parent / 'adapters.safetensors')))
    with pytest.raises(RuntimeError):
        with runtime._parent_initialization(trainer, utils, tmp_path, 'parent-lineage'):
            utils.linear_to_lora_layers(model, 16, {'rank': 8})
            assert trainer.training_provenance(None, None, None, ['resume']) == ['parent-lineage', 'resume']
            raise RuntimeError('interrupted')
    assert events == ['lora', str(tmp_path / 'adapters.safetensors')]
    assert utils.linear_to_lora_layers is layers
    assert trainer.training_provenance is provenance


def test_invalid_generation_parameters_fail_before_gpu_import():
    with pytest.raises(ValueError):
        runtime.generate('missing', 'missing', k=0)
    with pytest.raises(ValueError):
        runtime.train('parent', 'data', 'child', steps=0)
    with pytest.raises(ValueError):
        runtime.train('parent', 'data', 'parent')


def test_decoder_factory_is_shared_with_verification(monkeypatch):
    selection = importlib.import_module('llm.exit.selection')
    marker = object()
    monkeypatch.setattr(selection, 'decoder_factory', lambda: marker)
    assert runtime.decoder_factory() is marker


def test_recovery_uses_prefix_only_as_context_and_binds_resume_identity():
    from llm.stowlm.train import TokenDataset,AdmittedData
    from llm.stowlm.sft_data import SFTExample,example_sha256
    example=SFTExample('recovery-map','train',[10,11,12,13,14],2,2,2,'plan',meta={
        'exit_recovery':{'loss_start':3,'prefix_tokens':[12]}})
    before=example_sha256(example)
    data=AdmittedData([example],[],{'entries_sha256':'original'},{})
    trainer=SimpleNamespace(TokenDataset=TokenDataset,admit_dataset=lambda cfg:data)
    original=trainer.TokenDataset
    with runtime._recovery_dataset(trainer):
        admitted=trainer.admit_dataset(None)
        assert trainer.TokenDataset([example],100)[0]==([10,11,12,13,14],3)
        assert admitted.identity['entries_sha256']!='original'
        first_identity=admitted.identity['entries_sha256']
        example.meta['exit_recovery']={'loss_start':4,'prefix_tokens':[12,13]}
        assert trainer.admit_dataset(None).identity['entries_sha256']!=first_identity
    assert trainer.TokenDataset is original
    assert example.prompt_len==2 and example_sha256(example)==before


def test_recovery_refuses_unbound_loss_offset():
    from llm.stowlm.sft_data import SFTExample
    example=SFTExample('map','train',[10,11,12],1,1,1,'p',meta={
        'exit_recovery':{'loss_start':2,'prefix_tokens':[999]}})
    with pytest.raises(ValueError,match='exact prefix'):
        runtime.recovery_loss_offset(example)


def test_training_rejects_parent_architecture_mutation_without_mlx(tmp_path):
    import json
    parent = tmp_path / 'parent'
    parent.mkdir()
    (parent / 'adapter_config.json').write_text(json.dumps({
        'stowlm_train_config': {'rank': 8, 'num_layers': 16, 'scale': 20.0,
                              'dropout': 0.0, 'keys': None, 'model': 'model'}}))
    with pytest.raises(ValueError, match='architecture: rank'):
        runtime.train(parent, tmp_path / 'data', tmp_path / 'child', rank=16)


def test_training_refuses_unrecorded_parent_architecture(tmp_path):
    (tmp_path / 'adapter_config.json').write_text('{}')
    with pytest.raises(ValueError, match='declare stowlm_train_config'):
        runtime.train(tmp_path, 'data', tmp_path / 'child')


@pytest.mark.parametrize('weights,error', [
    ({}, 'missing'),
    ({'a': (2, 3), 'extra': (1,)}, 'extra'),
    ({'a': (3, 2)}, 'shapes'),
])
def test_parent_weight_schema_is_exact_before_load(tmp_path, weights, error):
    assigned = []
    tensor = lambda shape: SimpleNamespace(shape=shape)
    model = SimpleNamespace(trainable_parameters=lambda: {'a': tensor((2, 3))},
                            load_weights=lambda *args, **kwargs: assigned.append(args))
    with pytest.raises(ValueError, match=error):
        runtime._load_parent_weights(model, tmp_path,
            load_arrays=lambda path: {k: tensor(v) for k, v in weights.items()},
            flatten=lambda value: value.items())
    assert not assigned


def test_parent_weight_schema_loads_validated_arrays(tmp_path):
    tensor = SimpleNamespace(shape=(2, 3))
    weights = {'layer.lora_a': tensor}
    assigned = []
    model = SimpleNamespace(trainable_parameters=lambda: weights,
        load_weights=lambda value, strict: assigned.append((value, strict)))
    runtime._load_parent_weights(model, tmp_path, load_arrays=lambda path: weights,
                                  flatten=lambda value: value.items())
    assert assigned == [(list(weights.items()), False)]


def test_tainted_parent_is_refused_without_gpu():
    from stowage.model import Provenance
    planner = SimpleNamespace(_adapter_provenance=lambda:
                              Provenance(stubs={'test': 'diagnostic'}))
    with pytest.raises(Exception, match='stub-tainted'):
        runtime._clean_parent(planner)


def test_accumulation_is_checked_before_provenance_and_gpu(tmp_path):
    import json
    (tmp_path / 'adapter_config.json').write_text(json.dumps({
        'stowlm_train_config': {'rank': 8, 'grad_accumulation_steps': 3}}))
    with pytest.raises(ValueError, match='grad_accumulation_steps'):
        runtime.train(tmp_path, 'data', tmp_path / 'child', steps=121)


def test_resident_training_hook_skips_duplicate_lora_and_restores_loader(tmp_path, monkeypatch):
    events = []
    model = object()
    original_load = lambda path: 'original'
    original_lora = lambda *args, **kwargs: pytest.fail('must not duplicate LoRA wrappers')
    original_prov = lambda cfg, data, base, inherited: inherited
    trainer = SimpleNamespace(training_provenance=original_prov)
    utils = SimpleNamespace(linear_to_lora_layers=original_lora)
    model_utils = SimpleNamespace(load_model=original_load)
    resident = dict(model=model, model_path=str(tmp_path.resolve()), config={'test': True})
    monkeypatch.setattr(runtime, '_unfreeze_lora', lambda m: events.append(('unfreeze', m)))
    monkeypatch.setattr(runtime, '_load_parent_weights', lambda m, p: events.append(('weights', p)))
    with runtime._parent_initialization(trainer, utils, tmp_path, 'clean', resident, model_utils):
        assert model_utils.load_model(tmp_path) == (model, {'test': True})
        with pytest.raises(ValueError, match='different base'):
            model_utils.load_model(tmp_path / 'other')
        utils.linear_to_lora_layers(model, 16, {'rank': 8})
    assert events == [('unfreeze', model), ('weights', tmp_path)]
    assert utils.linear_to_lora_layers is original_lora
    assert model_utils.load_model is original_load


def test_unfreeze_lora_only_enables_adapter_leaves():
    events = []
    module = SimpleNamespace(lora_a='a', lora_b='b',
        unfreeze=lambda **kwargs: events.append(kwargs))
    model = SimpleNamespace(freeze=lambda: events.append('freeze'),
                            named_modules=lambda: [('adapter', module), ('base', object())])
    runtime._unfreeze_lora(model)
    assert events == ['freeze', {'recurse': False, 'keys': ['lora_a', 'lora_b'], 'strict': True}]


def test_parent_requires_base_model_binding():
    from stowage.model import Provenance
    planner = SimpleNamespace(_adapter_provenance=lambda: Provenance(),
                              _adapter_base_model_sha256=lambda: None)
    with pytest.raises(ValueError, match='base-model content'):
        runtime._clean_parent(planner)


def test_resident_swaps_adapters_without_reloading_base(tmp_path, monkeypatch):
    from stowage.model import Provenance
    events = []
    model = SimpleNamespace(eval=lambda: events.append('eval'))
    architecture = {'num_layers': 16, 'lora_parameters': {'rank': 8}}
    resident = dict(model=model, model_path=str(tmp_path), base_sha='base',
                    architecture=architecture, identity={'old': True}, config={})
    provenance = Provenance(parents=['adapter-weights-sha256:new'])
    planner = SimpleNamespace(model_path=str(tmp_path), adapter_path='child',
        _load=lambda: pytest.fail('base must not reload'),
        _check_adapter_base=lambda sha: events.append(('base-check', sha)))
    monkeypatch.setattr(runtime, '_RESIDENT', resident)
    monkeypatch.setattr(runtime, '_PLANNER', None)
    monkeypatch.setattr(runtime, '_clean_parent', lambda p: provenance)
    monkeypatch.setattr(runtime, '_architecture', lambda p: architecture)
    monkeypatch.setattr(runtime, '_unfreeze_lora', lambda m: events.append('unfreeze'))
    monkeypatch.setattr(runtime, '_load_parent_weights', lambda m, p: events.append(('adapter', p)))
    assert runtime._resident_for(planner) is model
    assert events == [('base-check', 'base'), 'unfreeze', ('adapter', 'child'), 'eval']
    events.clear()
    assert runtime._resident_for(planner) is model
    assert events == [('base-check', 'base'), 'eval']


def test_resident_swap_rejects_incompatible_architecture_before_assignment(tmp_path, monkeypatch):
    from stowage.model import Provenance
    resident = dict(model=object(), model_path=str(tmp_path), base_sha='base',
                    architecture={'rank': 8}, identity={}, config={})
    planner = SimpleNamespace(model_path=str(tmp_path), adapter_path='child',
                              _check_adapter_base=lambda sha: None)
    monkeypatch.setattr(runtime, '_RESIDENT', resident)
    monkeypatch.setattr(runtime, '_clean_parent', lambda p: Provenance())
    monkeypatch.setattr(runtime, '_architecture', lambda p: {'rank': 16})
    monkeypatch.setattr(runtime, '_load_parent_weights', lambda *args:
                        pytest.fail('incompatible adapter cannot be assigned'))
    with pytest.raises(ValueError, match='architecture differs'):
        runtime._resident_for(planner)


def test_parent_declared_training_architecture_must_match_adapter_config(tmp_path):
    import json
    (tmp_path / 'adapter_config.json').write_text(json.dumps({
        'num_layers': 16,
        'lora_parameters': {'rank': 8, 'scale': 20.0, 'dropout': 0.0},
        'stowlm_train_config': {'rank': 16, 'num_layers': 16, 'scale': 20.0, 'dropout': 0.0}}))
    with pytest.raises(ValueError, match='architecture disagree'):
        runtime.train(tmp_path, 'data', tmp_path / 'child')



@pytest.mark.parametrize('width',[1,2,3,8,16,None])
def test_mixed_decode_microbatches_keep_one_absolute_greedy_and_every_row(width):
    calls=[]
    def sample(model,prompt,group,**kwargs):
        calls.append((list(group),kwargs))
        return list(group),SimpleNamespace(summary=lambda:{'prefill_s':1.})
    result,timings=runtime._sample_batches(sample,None,[100],list(range(8)),temperature=.7,
        max_tokens=99,seed=5,batch_size=width)
    assert result==list(range(8))
    assert [temp for _,kw in calls for temp in kw['temperature']]==[0.]+[.7]*7
    assert [kw['seed'] for _,kw in calls]==[5+group[0] for group,_ in calls]
    if width is not None:assert all(len(group)<=width for group,_ in calls)
    assert len(timings)==((8+(width or 8)-1)//(width or 8))
    assert all(kw['max_tokens']==99 for _,kw in calls)


def test_empty_mixed_decode_group_never_prefills():
    assert runtime._sample_batches(lambda *a,**k:pytest.fail('empty prefill'),None,[1],[],
        temperature=.8,max_tokens=2,seed=0)==([],[])

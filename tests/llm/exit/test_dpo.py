"""CPU preference mathematics, reference immutability and checkpoint contracts."""
import copy
import importlib
import json
import math
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from llm.exit import dpo


def trace(tokens=(1, 2)):
    return dict(prompt=[5, 6], tokens=list(tokens), allowed_sets=[[1, 2, 3] for _ in tokens])


def pair():
    return dict(chosen_verified=True, chosen=trace(), rejected=trace((2, 3)))


def test_import_is_metal_free():
    run = subprocess.run([sys.executable, '-c',
        "import sys; from llm.exit import dpo; assert 'mlx.core' not in sys.modules"], capture_output=True)
    assert run.returncode == 0, run.stderr


def test_formula_equal_to_reference_and_improving_margin():
    assert dpo.scalar_loss(-2, -3, -2, -3, chosen_tokens=2, sft_weight=0) == pytest.approx(math.log(2))
    baseline = dpo.scalar_loss(-2, -3, -2, -3, chosen_tokens=2)
    assert dpo.scalar_loss(-1, -4, -2, -3, chosen_tokens=2) < baseline
    assert dpo.scalar_loss(-4, -2, -2, -3, chosen_tokens=2) > baseline
    assert baseline == pytest.approx(math.log(2) + .1)
    assert math.isfinite(dpo.scalar_loss(-1e6, -1, -1, -1, chosen_tokens=1))


@pytest.mark.parametrize('change', [
    lambda p: p.update(chosen_verified=False),
    lambda p: p['chosen'].update(tokens=[]),
    lambda p: p['chosen'].update(allowed_sets=[[1], []]),
    lambda p: p['chosen'].update(prompt=[]),
    lambda p: p['chosen'].update(allowed_sets=[[1, 1], [2]]),
    lambda p: p['rejected'].update(prompt=[4]),
    lambda p: p.update(rejected=copy.deepcopy(p['chosen'])),
    lambda p: p['chosen'].update(action_mask=[False, False]),
    lambda p: p['chosen'].update(tokens=[True, 2]),
])
def test_unverified_or_unscorable_trace_refused(change):
    value = pair()
    change(value)
    with pytest.raises(ValueError):
        dpo.validate_pair(value)


def test_reference_cache_is_frozen_persistent_and_bound_to_masks(tmp_path):
    calls = []
    scorer = lambda t: calls.append(t) or -float(sum(t['tokens']))
    path = tmp_path/'reference.json'
    cache = dpo.ReferenceCache(path, parent_identity='parent-bytes', mask_identity='mask-source')
    cache.precompute([pair()], scorer)
    expected = cache.get(pair())
    assert expected == (-3., -5.)
    cache.precompute([pair()], lambda _: pytest.fail('must reuse cached reference'))
    reloaded = dpo.ReferenceCache(path, parent_identity='parent-bytes', mask_identity='mask-source')
    assert reloaded.get(pair()) == expected
    assert len(calls) == 2
    with pytest.raises(ValueError, match='mismatch'):
        dpo.ReferenceCache(path, parent_identity='changed-parent', mask_identity='mask-source')
    changed = pair()
    changed['chosen']['allowed_sets'][0].append(9)
    with pytest.raises(ValueError, match='prepass'):
        cache.get(changed)


def test_nonfinite_prepass_never_commits_partial_reference(tmp_path):
    path = tmp_path/'reference.json'
    cache = dpo.ReferenceCache(path, parent_identity='p', mask_identity='m')
    with pytest.raises(ValueError, match='nonfinite'):
        cache.precompute([pair()], lambda t: -math.inf)
    assert not path.exists() and cache.scores == {}


def test_identity_hashes_content_not_path(tmp_path):
    (tmp_path/'adapter_config.json').write_text('{}')
    weights = tmp_path/'adapters.safetensors'
    weights.write_bytes(b'parent1')
    before = dpo.adapter_identity(tmp_path)
    weights.write_bytes(b'parent2')
    assert dpo.adapter_identity(tmp_path) != before


def fake_mlx(monkeypatch):
    """NumPy execution oracle; never imports MLX or initializes Metal."""
    def index(sets):
        width = max(map(len, sets))
        idx = np.array([s+[s[0]]*(width-len(s)) for s in sets])
        return idx, np.array([[i<len(s) for i in range(width)] for s in sets])
    def logprobs(logits, temperature):
        maximum = np.max(logits, axis=-1, keepdims=True)
        scaled = logits-maximum
        return scaled-np.log(np.exp(scaled).sum(axis=-1, keepdims=True))
    mx = SimpleNamespace(array=np.array, float32=np.float32, where=np.where,
        take_along_axis=np.take_along_axis, argmax=np.argmax, sum=np.sum, logaddexp=np.logaddexp)
    monkeypatch.setitem(sys.modules, 'mlx', SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, 'mlx.core', mx)
    monkeypatch.setitem(sys.modules, 'llm.stowlm.mask_mlx', SimpleNamespace(
        _index_matrix=index, _behavior_logprobs=logprobs))


def test_action_loss_matches_oracle_excludes_prompt_terminal_and_masked_logits(monkeypatch):
    fake_mlx(monkeypatch)
    value = pair()
    for side in ('chosen', 'rejected'):
        value[side]['action_mask'] = [True, False]
    def model(ids):
        # Huge masked-out logit must have no influence on normalization.
        return np.broadcast_to(np.array([10000., 1., 2., 3., 0., 0., 0.]), (*ids.shape, 7))
    chosen = -2.-math.log(1+math.exp(-1)+math.exp(-2))
    rejected = chosen+1
    actual = dpo.mlx_pair_loss(model, value, (chosen, rejected))
    assert actual == pytest.approx(dpo.scalar_loss(chosen, rejected, chosen, rejected, chosen_tokens=1), abs=1e-6)


def test_numerical_preference_gradient_pushes_chosen_up(monkeypatch):
    fake_mlx(monkeypatch)
    value = pair()
    def loss(weight):
        def model(ids):
            return np.broadcast_to(np.array([0., weight, 0., 0., 0., 0., 0.]), (*ids.shape, 7))
        return dpo.mlx_pair_loss(model, value, (-2., -2.))
    derivative = (loss(.001)-loss(-.001))/.002
    assert derivative < 0  # Gradient descent increases chosen's token logit.


def test_reference_scorer_calls_production_at_unit_temperature_and_excludes_terminal(monkeypatch):
    calls = []
    def production(model, prompt, tokens, sets, temperature):
        calls.append(temperature)
        return np.array([-2., -100.])
    monkeypatch.setitem(sys.modules, 'llm.stowlm.mask_mlx', SimpleNamespace(masked_logprobs=production))
    value = trace()
    value['action_mask'] = [True, False]
    assert dpo.reference_scorer(None)(value) == -2.
    assert calls == [1.]


def test_checkpoint_falls_back_if_newer_commit_incomplete(tmp_path):
    def write(step, weight=b'weights'):
        ad=tmp_path/f'{step:07d}_adapters.safetensors'
        op=tmp_path/f'{step:07d}_optimizer.safetensors'
        ad.write_bytes(weight)
        op.write_bytes(b'optimizer')
        record=dict(schema=dpo.SCHEMA, iteration=step,
            adapters_sha256=dpo._file_sha(ad), optimizer_sha256=dpo._file_sha(op))
        (tmp_path/f'{step:07d}_state.json').write_text(json.dumps(record))
    write(1)
    write(2)
    (tmp_path/'0000002_optimizer.safetensors').write_bytes(b'corrupted')
    assert dpo.read_complete_checkpoint(tmp_path)['iteration'] == 1


def test_caps_and_reference_admission_happen_before_mlx():
    cache = dpo.ReferenceCache(None, parent_identity='p', mask_identity='m')
    with pytest.raises(ValueError, match='prepass'):
        dpo.train_steps(None, [pair()], cache, steps=1)
    with pytest.raises(ValueError, match='sequence'):
        dpo.train_steps(None, [pair()], cache, steps=1, max_sequence_length=1)
    with pytest.raises(ValueError):
        dpo.train_preference('p', 'c', [], 'o')


def test_preference_phase_restores_dpo_checkpoint_after_sft_republishes(tmp_path, monkeypatch):
    """CPU fake backend exercises checkpoint/resume/publication control flow."""
    from llm.exit import runtime
    from llm.stowlm import train as trainer
    from llm.stowlm import planner
    from stowage.model import Provenance
    parent, child = tmp_path/'parent', tmp_path/'child'
    cfg = dict(model='base', fine_tune_type='lora', num_layers=1, lora_parameters={'rank':1})
    for folder in (parent, child):
        folder.mkdir()
        (folder/'adapter_config.json').write_text(json.dumps(cfg))
        (folder/'adapters.safetensors').write_bytes(b'SFT')
        (folder/'adapters.safetensors.provenance.json').write_text(json.dumps({
            'training':{'base_model_content_sha256':'base-sha'}, 'provenance':Provenance().model_dump(mode='json')}))
    (parent/'adapters.safetensors').write_bytes(b'generation-parent')
    sft_child_identity = dpo.adapter_identity(child, include_lineage=False)
    monkeypatch.setattr(dpo, '_admit_pair_instances', lambda *a, **kw: ['instance'])
    monkeypatch.setattr(dpo, 'reference_scorer', lambda model: lambda trace: -1.)
    monkeypatch.setattr(dpo, 'mlx_pair_loss', lambda *a, **kw: np.array(.5))
    monkeypatch.setattr(planner, 'StowLMPlanner', lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(runtime, '_clean_parent', lambda p: Provenance())
    restored = []
    model = SimpleNamespace(train=lambda: None, eval=lambda: None,
        load_weights=lambda path, strict: restored.append(path),
        trainable_parameters=lambda: {'w':np.array([2.])}, parameters=lambda: {})
    installed = []
    def resident(p):
        installed.append(p.adapter_path)
        return model
    monkeypatch.setattr(runtime, '_resident_for', resident)
    monkeypatch.setattr(runtime, '_RESIDENT', {'model':model, 'identity':None})
    def save_arrays(path, weights):
        open(path, 'wb').write(b'DPO-weights')
    mx = SimpleNamespace(eval=lambda *a: None, random=SimpleNamespace(seed=lambda s:None), save_safetensors=save_arrays)
    nn = SimpleNamespace(value_and_grad=lambda model, fn: lambda m: (fn(m), {}))
    class Adam:
        def __init__(self, learning_rate): self.state = {}
        def update(self, model, gradients): self.state['step'] = self.state.get('step',0)+1
    optim = SimpleNamespace(Adam=Adam)
    package = SimpleNamespace(core=mx, nn=nn, optimizers=optim)
    for name, module in [('mlx',package), ('mlx.core',mx), ('mlx.nn',nn), ('mlx.optimizers',optim),
                         ('mlx.utils',SimpleNamespace(tree_flatten=lambda x:list(x.items())))]:
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(trainer, 'save_optimizer_state',
        lambda opt,path: path.write_text(json.dumps(opt.state)))
    optimizer_restores = []
    def restore(opt, model, path):
        optimizer_restores.append(str(path))
        opt.state = json.loads(path.read_text())
    monkeypatch.setattr(trainer, 'load_optimizer_state', restore)
    def publish(out, provenance, training):
        (out/'adapters.safetensors.provenance.json').write_text(json.dumps({
            'training':training, 'provenance':provenance.model_dump(mode='json')}))
    monkeypatch.setattr(trainer, 'write_adapter_sidecar', publish)
    result = dpo.train_preference(parent, child, [pair()], child, steps=2)
    assert result['resumed_from'] == 0
    assert installed and set(installed) == {str(child)}
    assert result['reference'] == 'frozen_sft_child'
    assert result['reference_identity'] == sft_child_identity
    assert json.loads((child/'dpo/reference.json').read_text())['identity']['parent'] == sft_child_identity
    assert (child/'adapters.safetensors').read_bytes() == b'DPO-weights'
    assert dpo.read_complete_checkpoint(child/'dpo')['iteration'] == 2
    # The legacy SFT trainer may republish its finished SFT checkpoint on restart.
    (child/'adapters.safetensors').write_bytes(b'SFT')
    result = dpo.train_preference(parent, child, [pair()], child, steps=2)
    assert result['resumed_from'] == 2
    assert result['pairs_scored'] == 0
    assert optimizer_restores[-1].endswith('0000002_optimizer.safetensors')
    assert restored[-1].endswith('0000002_adapters.safetensors')
    assert (child/'adapters.safetensors').read_bytes() == b'DPO-weights'
    assert not list(child.glob('*_state.json'))  # SFT numbered checkpoints untouched.


@pytest.fixture
def cached_rejection(monkeypatch):
    from llm.exit import preferences
    from stowage.model import MovePlan
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    chosen = MovePlan(id='chosen', instance_id=instance.id, call_index=instance.call_index)
    rejected = chosen.model_copy(update={'id':'rejected'})
    sequence = dict(prompt=[10], tokens=[1, 2], token_ids=[1, 2],
        allowed_sets=[[1, 3], [2]], action_mask=[True, True], status='complete')
    monkeypatch.setattr(preferences, 'prepare_masked_sequence',
                        lambda *a, **kw: copy.deepcopy(sequence))
    value = dict(rejected=copy.deepcopy(sequence),
                 rejected_witness={'plan':rejected.model_dump(mode='json'), 'token_ids':[1, 2]},
                 evidence={'kind':'strictly_more_moves'})
    return instance, chosen, rejected, sequence, value


def test_cached_rejection_remeasures_moves_and_plan_trace(cached_rejection, monkeypatch):
    from llm.exit import selection
    instance, chosen, rejected, sequence, value = cached_rejection
    monkeypatch.setattr(selection, 'score_candidate', lambda i,c: 3 if c.plan.id=='chosen' else 7)
    dpo._admit_rejected(instance, value, chosen)
    monkeypatch.setattr(selection, 'score_candidate', lambda i,c: 3)
    with pytest.raises(ValueError, match='not strictly more moves'):
        dpo._admit_rejected(instance, value, chosen)


@pytest.mark.parametrize('field,changed', [
    ('allowed_sets', [[1, 3, 9], [2]]), ('prompt',[11]),
    ('token_ids',[3, 2]), ('action_mask',[True, False]), ('status','dead_end'),
    ('dead_end_loss_start',1)])
def test_cached_rejection_trace_cannot_change_support(cached_rejection, field, changed):
    instance, chosen, rejected, sequence, value = cached_rejection
    value['rejected'][field] = changed
    with pytest.raises(ValueError, match='layered replay'):
        dpo._admit_rejected(instance, value, chosen)


def test_cached_deadend_requires_current_deadend_not_historical_label(cached_rejection):
    instance, chosen, rejected, sequence, value = cached_rejection
    value['evidence'] = {'kind':'dead_end'}
    with pytest.raises(ValueError, match='no longer dead-ends'):
        dpo._admit_rejected(instance, value, chosen)
    sequence['status'] = value['rejected']['status'] = 'dead_end'
    dpo._admit_rejected(instance, value, chosen)


def test_cached_checker_failure_requires_bound_current_report(cached_rejection):
    from stowage.model import CheckReport
    from stowage.checker.full import _input_bindings
    from stowage.versions import CHECKER_VERSION
    instance, chosen, rejected, sequence, value = cached_rejection
    report = CheckReport(passed=False,
        parts={name:{'name':name} for name in
               ('moves','rules','voyage','stability','strength','visibility','lashing','constraints')},
        violations=[dict(rule_id='objective.all_loaded',severity='hard',call_index=instance.call_index,
                         message='Load-list box not loaded.')],
        provenance={'versions':{'checker':CHECKER_VERSION},
        'parents':list(_input_bindings(instance, rejected))})
    value['evidence'] = {'kind':'checker_failure', 'verification':{'report':report.model_dump(mode='json')}}
    dpo._admit_rejected(instance, value, chosen)
    value['evidence']['verification']['report']['provenance']['parents'] = []
    with pytest.raises(ValueError, match='content binding'):
        dpo._admit_rejected(instance, value, chosen)


@pytest.mark.parametrize('failure', ['timeout', 'internal_error', 'incomplete'])
def test_cached_checker_unknown_outcome_cannot_be_relabelled_failure(cached_rejection, failure):
    from stowage.model import CheckReport
    from stowage.checker.full import _input_bindings, _failed
    from stowage.versions import CHECKER_VERSION
    instance, chosen, rejected, sequence, value = cached_rejection
    part = _failed(instance, 'stability', TimeoutError('budget exhausted'))
    report = CheckReport(passed=False,
        parts={name:{'name':name} for name in
               ('moves','rules','voyage','stability','strength','visibility','lashing','constraints')},
        violations=part.violations,
        provenance={'versions':{'checker':CHECKER_VERSION},
                    'parents':list(_input_bindings(instance, rejected))})
    if failure == 'timeout':
        report.parts['stability'] = part
    elif failure == 'incomplete':
        del report.parts['strength']
        report.violations[0].rule_id = 'objective.all_loaded'
    value['evidence'] = {'kind':'checker_failure', 'verification':{'report':report.model_dump(mode='json')}}
    with pytest.raises(ValueError, match='definitive hard plan violation'):
        dpo._admit_rejected(instance, value, chosen)

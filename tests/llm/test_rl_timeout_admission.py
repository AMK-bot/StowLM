"""Exercise production abort/checkpoint ordering with CPU-only dependency fakes."""
from dataclasses import asdict
import json
import time
import multiprocessing
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from llm.rl import modal_grpo as grpo
from llm.rl.reward import RewardConfig, advance_timeout_policy
from llm.rl.lineage import rollout_data_identity
from stowage.model.fixtures import build_instance


def test_modal_third_bad_step_persists_before_any_optimizer_update(tmp_path, monkeypatch):
    import llm.stowlm.format_prompt as prompt_module
    import stowage.feasibility as feasibility
    import stowage.frozen as frozen

    instance = build_instance().model_dump(mode='json')
    manifest = grpo.PinManifest.capture('a' * 40, tmp_path)
    bad = {'checker_timeout_drop_rate': .5}
    policy = advance_timeout_policy([bad])
    policy = advance_timeout_policy([bad], previous=policy)
    training_config = {'group_size': 2, 'max_tokens': 2, 'temperature': grpo.ROLLOUT_TEMPERATURE,
                       'learning_rate': 1e-5, 'clip': .2, 'beta': 0., 'reward': grpo.RL_VERSION,
                       'step_timeout_s': 600,
                       'rollout_data_identity': rollout_data_identity(rule_catalogue=manifest.versions.get('rule_catalogue')),
                       'mask_engine': dict(grpo.MVP_SPEC),
                       'reward_config': asdict(grpo.mvp_reward_config())}
    previous = {'step': 2, 'manifest': asdict(manifest), 'instance': instance,
                'training_config': training_config, 'timeout_policy': policy}
    class Checkpoint(grpo.StepCheckpoint):
        def load(self):
            # No prior GPU state is needed to test CPU admission ordering.
            return None, previous
    monkeypatch.setattr(grpo, 'StepCheckpoint', Checkpoint)
    monkeypatch.setattr(grpo, 'consume_reservation', lambda *a: None)
    monkeypatch.setattr(grpo, 'verify_manifest', lambda *a, **kw: None)
    monkeypatch.setattr(frozen, 'require_clean', lambda *a, **kw: None)
    monkeypatch.setattr(prompt_module, 'encode_prompt', lambda *a: [1])
    monkeypatch.setattr(grpo, 'mvp_engine', lambda *a: SimpleNamespace(
        dead_end=lambda: None, is_complete=lambda: False))

    updates = []
    optimizer = SimpleNamespace(zero_grad=lambda: updates.append('zero_grad'),
                                step=lambda: updates.append('step'), state_dict=lambda: {})
    model = SimpleNamespace(train=lambda: None, gradient_checkpointing_enable=lambda: None,
                            enable_input_require_grads=lambda: None, parameters=lambda: [],
                            save_pretrained=lambda path: path.mkdir())
    torch = ModuleType('torch')
    torch.manual_seed = lambda *a: None
    torch.bfloat16 = 'synthetic'
    torch.optim = SimpleNamespace(AdamW=lambda *a, **kw: optimizer)
    torch.cuda = SimpleNamespace(set_device=lambda *a: None, get_rng_state_all=lambda: [])
    torch.get_rng_state = lambda: None
    torch.save = lambda value, path: Path(path).write_bytes(b'synthetic CPU checkpoint')
    monkeypatch.setitem(sys.modules, 'torch', torch)
    transformers = ModuleType('transformers')
    tokenizer = SimpleNamespace(eos_token_id=0, encode=lambda s, **kw: [ord(c) for c in s])
    transformers.AutoTokenizer = SimpleNamespace(from_pretrained=lambda *a, **kw: tokenizer)
    transformers.MuseGlimmerForConditionalGeneration = SimpleNamespace(from_pretrained=lambda *a, **kw: model)
    monkeypatch.setitem(sys.modules, 'transformers', transformers)
    peft = ModuleType('peft')
    peft.get_peft_model_state_dict = lambda model: {}
    monkeypatch.setitem(sys.modules, 'peft', peft)
    monkeypatch.setattr(grpo, 'initialize_policy_adapter', lambda *a: model)
    monkeypatch.setattr(grpo, 'make_masked_grpo_trainer', lambda *a: type('CPUTrainer', (), {}))
    monkeypatch.setattr(grpo, 'sync_policy_weights', lambda *a: None)
    monkeypatch.setattr(grpo, 'measure_agreement_floor', lambda *a, **kw: None)

    connection = SimpleNamespace(send=lambda *a: None, close=lambda: None)
    process = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
    context = SimpleNamespace(Pipe=lambda: (connection, connection), Process=lambda **kw: process)
    monkeypatch.setattr(multiprocessing, 'get_context', lambda *a: context)
    responses = iter([{}, {'tokens': [[], []]}])
    monkeypatch.setattr(grpo, '_receive', lambda *a: next(responses))
    monkeypatch.setattr(grpo, 'score_rollout', lambda *a, **kw: (
        [], [], SimpleNamespace(value=None, passed=False)))

    with pytest.raises(RuntimeError, match='timeout.*drop'):
        grpo.run_gpu_smoke(instance, manifest, checkpoint_root=str(tmp_path / 'checkpoints'),
                           volume=None, cap=3, steps=3, group_size=2, max_tokens=2, resume=True,
                           reservation={'prior_spend': 0, 'seconds': 600, 'reserved_dollars': 3,
                                        'deadline_epoch': time.time() + 600})
    state = json.loads((tmp_path / 'checkpoints/step-000003/complete.json').read_text())
    assert not updates
    assert state['optimizer_updated'] is False
    assert state['timeout_policy']['consecutive_bad_steps'] == 3
    assert state['timeout_policy']['abort']
    assert state['reward_stats']['checker_timeout_drop_count'] == 2
    assert len(state['dropped_samples']) == 2
    assert state['dropped_samples'][0]['gm_m'] is None
    assert state['dropped_samples'][0]['tank_masses_kg']
    assert state['training_config']['rollout_data_identity']['versions']['prompt']
    assert state['training_config']['rollout_data_identity']['historical_sft_versions'] is None

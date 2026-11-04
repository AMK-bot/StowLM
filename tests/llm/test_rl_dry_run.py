"""Offline loop integration, mask replay and deterministic checkpoint resume."""
from types import SimpleNamespace

import pytest

from llm.rl.dry_run import DryRunConfig, run_dry_loop
from llm.rl.mlx_grpo import MLXGRPOConfig, train
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig


def run(path, steps=1, score=None):
    kwargs = {} if score is None else {'score': score}
    return run_dry_loop([tiny_instance(0, n_load=2, n_other=0, p40=0)], path,
                        config=DryRunConfig(steps=steps, group_size=2),
                        feasibility_config=FeasibilityConfig(extra={'global_screen': 0}), **kwargs)


def test_real_reward_gate_and_resume(tmp_path):
    first = run(tmp_path / 'resume.json')
    assert first['step'] == 1
    assert len(first['history'][0]) == 2
    assert all(not r['passed'] for r in first['history'][0])  # fixture has no hull
    assert all(r['reward'] == -10000 for r in first['history'][0])
    assert all(r['tokens'] > 0 for r in first['history'][0])
    assert any(0.0 in r['logps'] for r in first['history'][0])
    resumed = run(tmp_path / 'resume.json', steps=2)
    fresh = run(tmp_path / 'fresh.json', steps=2)
    assert resumed == fresh
    assert run(tmp_path / 'resume.json', steps=2) == resumed


def test_fake_policy_update_and_version_refusal(tmp_path):
    def score(instance, result):
        return SimpleNamespace(value=-float(sum(int(m.dst) for m in result.plan.moves if m.kind == 'load')),
                               passed=True)
    checkpoint = tmp_path / 'state.json'
    state = run(checkpoint, score=score)
    assert state['theta'] != 0.0
    import json
    state['identity']['mask'] = 'changed'
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError, match='versions'):
        run(checkpoint)


def test_mlx_fallback_is_explicit_stub(tmp_path):
    with pytest.raises(NotImplementedError, match='GPU owner'):
        train([], MLXGRPOConfig(tmp_path, tmp_path))


def test_reward_timeout_drop_stats_persist_across_checkpoint_resume(tmp_path):
    import json

    def scorer():
        values = iter([None, -2])
        return lambda *args: SimpleNamespace(value=next(values), passed=False)

    checkpoint = tmp_path / 'drops.json'
    first = run(checkpoint, score=scorer())
    expected = {'sample_count': 2, 'reward_count': 1,
                'checker_timeout_drop_count': 1, 'checker_timeout_drop_rate': 0.5}
    assert first['reward_stats'] == [expected]
    assert first['identity']['rollout_data_identity']['versions']['prompt']
    assert first['identity']['rollout_data_identity']['versions']['format']
    assert json.loads(checkpoint.read_text())['reward_stats'] == [expected]
    assert first['history'][0][0]['reward'] is None
    assert first['theta'] == 0  # Only one evaluable sample has zero relative advantage.
    resumed = run(checkpoint, steps=2, score=scorer())
    assert resumed['reward_stats'] == [expected, expected]
    # Older checkpoints have reward values but no aggregate availability stats.
    resumed.pop('reward_stats')
    checkpoint.write_text(json.dumps(resumed))
    with pytest.raises(RuntimeError, match='timeout.*drop'):
        run(checkpoint, steps=3, score=scorer())
    upgraded = json.loads(checkpoint.read_text())
    assert upgraded['reward_stats'] == [expected, expected, expected]
    assert upgraded['timeout_policy'][-1]['abort']
    assert upgraded['timeout_policy'][-1]['consecutive_bad_steps'] == 3
    assert upgraded['theta'] == first['theta']
    assert upgraded['dropped_samples'][-1][0]['trim_m'] is None
    assert 'tank_masses_kg' in upgraded['dropped_samples'][-1][0]
    with pytest.raises(RuntimeError, match='timeout.*drop'):
        run(checkpoint, steps=4, score=scorer())

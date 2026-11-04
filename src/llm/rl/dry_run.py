"""Offline infrastructure rehearsal with a tiny scalar policy, never model weights.

This is a test double, not a Glimmer training result. It samples from the real
feasibility mask, recomputes masked log-probabilities by replay, scores complete
plans through the ballast/checker gate, and checkpoints every policy update.
Its single scalar uses an ordinary policy-gradient update to exercise group
advantages; production uses TRL's clipped GRPO objective (DeepSeekMath §4,
https://arxiv.org/abs/2402.03300). Forced tokens have zero log-probability and
contribute no gradient. No physics is substituted in the default reward.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import random
from typing import Callable, Sequence

from llm.rl.lineage import rollout_data_identity
from llm.rl.reward import (advance_timeout_policy, dropped_sample_diagnostics,
                           evaluate_reward, normalize_group_rewards, require_timeout_admission)
from llm.stowlm.format import ActionCodec
from llm.stowlm.mask import MASK_VERSION, MaskConfig, MaskedDecoder, allowed_sets
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance, PlannerResult
from stowage.versions import component_versions


class CharacterTokenizer:
    """Deterministic ASCII tokenizer for offline tests, independent of local weights."""

    def encode(self, text, add_special_tokens=False):
        return [ord(c) for c in text]

    def decode(self, ids, **kwargs):
        return ''.join(chr(t) for t in ids)


@dataclass(frozen=True)
class DryRunConfig:
    steps: int = 1
    group_size: int = 4
    seed: int = 0
    max_tokens: int = 512
    learning_rate: float = 0.01


def _distribution(theta, allowed):
    scores = [theta * (t % 7) / 7 for t in allowed]
    peak = max(scores)
    weights = [math.exp(s - peak) for s in scores]
    z = sum(weights)
    return [w / z for w in weights]


def run_dry_loop(instances: Sequence[Instance], checkpoint: Path, *,
                 config: DryRunConfig | None = None,
                 feasibility_config: FeasibilityConfig | None = None,
                 score: Callable = evaluate_reward) -> dict:
    """Resume up to ``steps`` using a fake CPU policy and an atomic JSON checkpoint.

    Callers may inject a scorer to test nonzero gradients. Defaults use the full
    checker. Fixture callers explicitly disable the global feasibility screen
    only when their fixture vessel has no hull; production callers keep it on.
    """
    config = config or DryRunConfig()
    if (not instances or config.steps < 1 or config.group_size < 2 or config.max_tokens < 1
            or not math.isfinite(config.learning_rate) or config.learning_rate <= 0):
        raise ValueError('need instances, positive steps/tokens/rate, and group_size >= 2')
    checkpoint = Path(checkpoint)
    identity = dict(components=component_versions(), mask=MASK_VERSION,
                    dry_policy='scalar@1', config={k: v for k, v in asdict(config).items() if k != 'steps'},
                    instances=[i.model_dump(mode='json') for i in instances],
                    feasibility=repr(feasibility_config), rollout_data_identity=rollout_data_identity())
    state = dict(identity=identity, step=0, theta=0.0, history=[], reward_stats=[],
                 timeout_policy=[], dropped_samples=[])
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text())
        if state['identity'] != identity:
            raise ValueError('checkpoint versions, instances, or configuration differ')
        if (type(state.get('step')) is not int or state['step'] < 0
                or not isinstance(state.get('theta'), (int, float)) or not math.isfinite(state['theta'])
                or not isinstance(state.get('history'), list) or len(state['history']) != state['step']):
            raise ValueError('invalid checkpoint step, policy, or history')
        if 'reward_stats' not in state:
            # Upgrade earlier checkpoints using their recorded reward availability.
            state['reward_stats'] = []
            for rows in state['history']:
                dropped = sum(row['reward'] is None for row in rows)
                state['reward_stats'].append(dict(
                    sample_count=len(rows), reward_count=len(rows) - dropped,
                    checker_timeout_drop_count=dropped,
                    checker_timeout_drop_rate=dropped / len(rows) if rows else 0.0))
        if 'timeout_policy' not in state:
            state['timeout_policy'] = []
            for rows in state['history']:
                groups = [rows[i:i + config.group_size] for i in range(0, len(rows), config.group_size)]
                stats = [dict(checker_timeout_drop_rate=sum(row['reward'] is None for row in group) / len(group))
                         for group in groups]
                state['timeout_policy'].append(advance_timeout_policy(
                    stats, previous=state['timeout_policy'][-1] if state['timeout_policy'] else None))
        state.setdefault('dropped_samples', [[] for _ in state['history']])
        if state['timeout_policy']:
            require_timeout_admission(state['timeout_policy'][-1], lambda: None)
    space = get_action_space('boxslot')
    for step in range(state['step'], config.steps):
        gradient = 0.0
        summary = []
        reward_stats = dict(sample_count=0, reward_count=0, checker_timeout_drop_count=0)
        group_stats, dropped_samples = [], []
        for prompt, instance in enumerate(instances):
            engine = make_feasibility_engine(instance, space, config=feasibility_config)
            codec = ActionCodec(CharacterTokenizer(), [c.id for c in instance.containers])
            group = []
            for member in range(config.group_size):
                rng = random.Random(config.seed + 1_000_003 * step + 1009 * prompt + member)
                decoder = MaskedDecoder(engine.copy(), codec, MaskConfig())
                rollout_sets = []
                for _ in range(config.max_tokens):
                    allowed = decoder.allowed_token_ids()
                    if not allowed:
                        break
                    rollout_sets.append(allowed)
                    decoder.advance(rng.choices(allowed, weights=_distribution(state['theta'], allowed))[0])
                replay = allowed_sets(engine, codec, decoder.tokens)
                if replay != rollout_sets:
                    raise RuntimeError('rollout/training mask disagreement')
                logps, derivative = [], 0.0
                for token, allowed in zip(decoder.tokens, replay):
                    probs = _distribution(state['theta'], allowed)
                    logps.append(math.log(probs[allowed.index(token)]))
                    derivative += (token % 7) / 7 - sum(p * (t % 7) / 7 for p, t in zip(probs, allowed))
                status = 'ok' if decoder.status == 'complete' else ('dead_end' if decoder.status == 'dead_end' else 'timeout')
                result = PlannerResult(status=status, plan=decoder.engine.to_plan(),
                                       planner='rl.fake', planner_version='scalar@1', seed=config.seed)
                reward = score(instance, result)
                if reward.value is None:
                    dropped_samples.append(dropped_sample_diagnostics(instance, reward))
                group.append((reward.value, derivative))
                summary.append(dict(reward=reward.value, passed=reward.passed, status=status,
                                    tokens=len(decoder.tokens), logps=logps))
            advantages = normalize_group_rewards([r for r, _ in group])
            group_stats.append(advantages.stats)
            for key in reward_stats:
                reward_stats[key] += advantages.stats[key]
            gradient += sum(a * d for a, (_, d) in zip(advantages, group)) / config.group_size
        policy = advance_timeout_policy(
            group_stats, previous=state['timeout_policy'][-1] if state['timeout_policy'] else None)
        if not policy['abort']:
            state['theta'] += config.learning_rate * gradient / len(instances)
        state['step'] = step + 1
        state['history'].append(summary)
        reward_stats['checker_timeout_drop_rate'] = (
            reward_stats['checker_timeout_drop_count'] / reward_stats['sample_count']
            if reward_stats['sample_count'] else 0.0)
        state['reward_stats'].append(reward_stats)
        state['timeout_policy'].append(policy)
        state['dropped_samples'].append(dropped_samples)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = checkpoint.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, sort_keys=True, allow_nan=False))
        temporary.replace(checkpoint)
        require_timeout_admission(policy, lambda: None)  # State above is durable before abort.
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=Path('experiments/grpo/dry-checkpoint.json'))
    parser.add_argument('--steps', type=int, default=1)
    args = parser.parse_args()
    from stowage.actions.samples import tiny_instance
    state = run_dry_loop([tiny_instance(0, n_load=2, n_other=0, p40=0)], args.checkpoint,
                         config=DryRunConfig(steps=args.steps),
                         feasibility_config=FeasibilityConfig(extra={'global_screen': 0}))
    print(json.dumps(dict(step=state['step'], theta=state['theta'], rollouts=state['history'][-1],
                          reward_stats=state['reward_stats'][-1],
                          timeout_policy=state['timeout_policy'][-1],
                          dropped_samples=state['dropped_samples'][-1])))


if __name__ == '__main__':
    main()

"""Prepare a synthetic production-physics tiny prompt, offline and without GPUs.

Run only after the normal cache warm-up is complete. This script preserves the
generator's physics and demand model and refuses an initial mask dead end. It
does not claim the base model will finish a checker-passing rollout.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--output', type=Path, default=Path('experiments/grpo/smoke-instance.json'))
    args = parser.parse_args()
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    from stowage.frozen import require_clean
    from stowage.generate.instances import generate_simulated_instance
    from stowage.io import save_instance
    instance = generate_simulated_instance('tiny', seed=args.seed, n_boxes=2)
    require_clean(instance, what='GRPO smoke instance')
    engine = make_feasibility_engine(instance, get_action_space())
    if engine.dead_end() is not None or engine.is_complete():
        raise RuntimeError('smoke needs an initially live mask with actions to generate; choose another seed')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_instance(instance, args.output)
    print(args.output)


if __name__ == '__main__':
    main()

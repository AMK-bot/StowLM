"""Interface for the unimplemented Mac fallback; no MLX import or GPU allocation.

The fallback would sample groups with MaskedDecoder, replay allowed_sets for
masked log-probabilities, and update an MLX LoRA adapter using group advantages.
GRPO compares plans for the same prompt instead of learning a value model
(Shao et al., DeepSeekMath, 2024, §4, https://arxiv.org/abs/2402.03300).
The GPU owner must implement clipped updates, shared-prompt KV caches,
checkpoint/resume, and a wall-time budget before this backend can be selected.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from stowage.model import Instance


@dataclass(frozen=True)
class MLXGRPOConfig:
    adapter: Path
    checkpoint_dir: Path
    steps: int = 1
    group_size: int = 8
    seed: int = 0
    max_wall_time_s: float = 1500.0


class MLXGRPOBackend(Protocol):
    def train(self, instances: Sequence[Instance], config: MLXGRPOConfig) -> Path:
        """Return the last complete, resumable adapter checkpoint."""
        ...


def train(instances: Sequence[Instance], config: MLXGRPOConfig) -> Path:
    """Reserved interface; selecting the fallback never silently runs a mock."""
    raise NotImplementedError("MLX GRPO is reserved for the GPU owner; use CPU dry_run for infrastructure tests")

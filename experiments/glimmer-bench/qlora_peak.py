# Run `mlx_lm lora` (same CLI args) and print MLX's peak-memory counter at the end; the 0.32.2 CLI doesn't report it.
# Usage: uv run python experiments/glimmer-bench/qlora_peak.py <mlx_lm lora args...>
import sys
import time

import mlx.core as mx
from mlx_lm import lora

sys.argv = ["mlx_lm.lora"] + sys.argv[1:]
t = time.perf_counter()
lora.main()
print(f"PEAK_GB {mx.get_peak_memory() / 1e9:.2f} WALL_S {time.perf_counter() - t:.0f}", flush=True)

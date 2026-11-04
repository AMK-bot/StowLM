"""MLX buffer-cache limit for every StowLM process (leader 29 Oct 21:42).

MLX keeps freed Metal buffers in a cache for reuse; without a limit the cache grows toward the device limit, so
several decode processes on one machine (measured: 31-37 GB each for a ~17 GB model+KV working set) swap. A limit
releases freed buffers beyond it; allocations still succeed (the cache is reuse only). Source: MLX docs,
``mlx.core.set_cache_limit`` / ``get_cache_memory``.

``STOWLM_MLX_CACHE_LIMIT_GB`` (decode default 2, training default 8; ``none`` = MLX default, unlimited).
"""
from __future__ import annotations

import os

DEFAULT_GB = 2.0


def apply_cache_limit(default_gb: float = DEFAULT_GB) -> float | None:
    """Set the cache limit once per process; returns the limit in GB (None = unlimited)."""
    import mlx.core as mx

    raw = os.environ.get("STOWLM_MLX_CACHE_LIMIT_GB", str(default_gb)).strip().lower()
    if raw in ("none", "off", ""):
        return None
    gb = float(raw)
    mx.set_cache_limit(int(gb * (1 << 30)))
    return gb


def memory_gb() -> dict[str, float]:
    import mlx.core as mx

    return {"mlx_active_gb": round(mx.get_active_memory() / 1e9, 2), "mlx_cache_gb": round(mx.get_cache_memory() / 1e9, 2),
            "mlx_peak_gb": round(mx.get_peak_memory() / 1e9, 2)}

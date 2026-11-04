# Isolate where batched decode time goes: one decode step of the bare model (no BatchGenerator, no sampler) at
# batch B and KV length T, vs the same through mlx-lm's batch caches. Answers "is batch scaling limited by the
# model kernels or by the generator?" Usage: uv run python experiments/glimmer-bench/bench_step.py MODEL OUT
import json
import sys
import time

import mlx.core as mx
from mlx_lm import load
from mlx_lm.models.cache import make_prompt_cache

model, _ = load(sys.argv[1])
rows = []
SIZES = tuple(int(b) for b in sys.argv[3].split(",")) if len(sys.argv) > 3 else (1, 2, 4, 8, 16, 32)
KV = (2048,) if len(sys.argv) > 3 else (2048, 12288)
for T in KV:
    for B in (SIZES if T == 2048 else (1, 8, 16)):
        cache = make_prompt_cache(model)
        # fill the cache with T tokens in chunks (random ids)
        x = mx.random.randint(1000, 100000, (B, T))
        for s in range(0, T, 2048):
            model(x[:, s:s + 2048], cache=cache)
            mx.eval([c.state for c in cache])
        tok = mx.random.randint(1000, 100000, (B, 1))
        for _ in range(3):  # warm-up
            mx.eval(model(tok, cache=cache))
        n = 20
        t = time.perf_counter()
        for _ in range(n):
            out = model(tok, cache=cache)
            mx.eval(out)
        ms = (time.perf_counter() - t) / n * 1000
        r = dict(kv_len=T, batch=B, ms_per_step=round(ms, 1), tok_s_agg=round(B / ms * 1000, 1),
                 peak_gb=round(mx.get_peak_memory() / 1e9, 1))
        print(json.dumps(r), flush=True)
        rows.append(r)
        del cache
        mx.clear_cache()
json.dump(rows, open(sys.argv[2], "w"), indent=1)

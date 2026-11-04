"""Task 5.1: CPU simulation of the lock-step batched loop's host cost, with and without ``MaskedDecoder.prefetch``.

No model: B decoders advance one uniformly random allowed token per step, in lock step (as in ``generate_masked``).
Each step's host work is timed: ``must`` = allowed sets for all live rows (what the step can't start without),
plus prefetch work spent inside the GPU window. The simulated step time is ``max(window, must + prefetch)`` with
``window`` = a measured masked b64 GPU step (``--window-ms``). This estimates the stall; it is not a GPU measurement.

Usage: uv run python experiments/mask/simulate_lockstep.py --rung full --batch 64 --steps 600
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")


def run(rung: str, seed: int, batch: int, steps: int, window_ms: float, prefetch: bool) -> dict:
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskedDecoder
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.generate.random_yard import generate_random_instance

    inst = generate_random_instance(rung, seed=seed)
    root = make_feasibility_engine(inst, get_action_space("boxslot"), config=FeasibilityConfig(extra={"global_screen": 0}))
    codec = ActionCodec(load_tokenizer(), [c.id for c in inst.containers])
    decs = [MaskedDecoder(root.copy(), codec) for _ in range(batch)]
    rngs = [random.Random(1000 + k) for k in range(batch)]
    active = list(range(batch))
    sim_ms, must_ms, pre_ms, stalls, fetched = [], [], [], 0, 0
    window = window_ms / 1e3
    for _ in range(steps):
        t = time.perf_counter()
        sets = [decs[i].allowed_token_ids() for i in active]
        must = time.perf_counter() - t
        used = 0.0
        if prefetch:
            t2 = time.perf_counter()
            for i in active:
                if must + (time.perf_counter() - t2) >= 0.9 * window:
                    break
                fetched += decs[i].prefetch()
            used = time.perf_counter() - t2
        step = max(window, must + used)
        stalls += step > window * 1.001
        sim_ms.append(step * 1e3)
        must_ms.append(must * 1e3)
        pre_ms.append(used * 1e3)
        nxt = []
        for k, i in enumerate(active):
            if sets[k]:
                decs[i].advance(sets[k][rngs[i].randrange(len(sets[k]))])
                if decs[i].status == "running":
                    nxt.append(i)
        active = nxt
        if not active:
            break
    n = len(sim_ms)
    s = sorted(sim_ms)
    return dict(rung=rung, seed=seed, batch=batch, prefetch=prefetch, window_ms=window_ms, steps=n,
                sim_total_s=round(sum(sim_ms) / 1e3, 2), ideal_s=round(n * window, 2),
                host_bound_extra_pct=round(100 * (sum(sim_ms) / (n * window_ms) - 1), 1),
                stall_steps=stalls, step_ms_p50=round(s[n // 2], 1), step_ms_p95=round(s[int(.95 * n)], 1),
                step_ms_max=round(s[-1], 1), must_ms_p95=round(sorted(must_ms)[int(.95 * n)], 1),
                prefetched=fetched, final_active=len(active))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rung", nargs="+", default=["medium", "full"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--steps", type=int, default=600)
    ap.add_argument("--window-ms", type=float, default=270.0)
    ap.add_argument("--out", default="experiments/mask/simulate_lockstep.jsonl")
    args = ap.parse_args()
    with open(args.out, "a") as f:
        for rung in args.rung:
            for pf in (False, True):
                r = run(rung, args.seed, args.batch, args.steps, args.window_ms, pf)
                print(json.dumps(r), flush=True)
                f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()

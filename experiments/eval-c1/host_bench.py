"""CPU-only mask host benchmark: B lock-step uniform-random masked rollouts of one instance, in-thread vs process pool.

Measures what the GPU loop would wait on: per lock-step the parent needs every active row's allowed set. In-thread
(the current ``generate_masked`` path) computes them one after another; the pool computes them on W processes. Also
asserts both backends produce identical allowed sets for the same token stream (the pool is a pure relocation).
No GPU, no model: token choices are uniform over the allowed set (seeded), as in the perf-1028 rollouts.

    uv run python experiments/eval-c1/host_bench.py --instance <instance.json> --batch 8 --workers 0 2 4 \
        --max-steps 400 [--c2] --out experiments/eval-c1/bench/<name>.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def rollout(instance, *, batch: int, workers: int, max_steps: int, seed: int, c2: bool, check_against=None):
    import stowage.interfaces  # noqa: F401
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.mask import MaskedDecoder
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine

    from pool import MaskWorkerPool

    pool = None
    t_build = time.perf_counter()
    if workers:
        from stowage.rules.catalogue import rule_freeze_v1_enabled

        pool = MaskWorkerPool(workers, c2=c2, rule_freeze=rule_freeze_v1_enabled())
        decoders = pool.decoders(instance, batch)
    else:
        engine = make_feasibility_engine(instance, get_action_space())
        codec = codec_for(instance, load_tokenizer())
        decoders = [MaskedDecoder(engine if i == 0 else engine.copy(), codec) for i in range(batch)]
    rngs = [random.Random(seed * 1000 + i) for i in range(batch)]
    active, trace, steps, host_s = list(range(batch)), [], 0, 0.0
    first = True
    try:
        while active:
            t = time.perf_counter()
            sets = [decoders[i].allowed_token_ids() for i in active]
            dt = time.perf_counter() - t
            if first:  # includes engine construction in the workers
                build_s, first = time.perf_counter() - t_build, False
            else:
                host_s += dt
            trace.append([list(s) for s in sets])
            if check_against is not None and check_against[len(trace) - 1] != trace[-1]:
                raise AssertionError(f"allowed sets differ at lock-step {len(trace) - 1}")
            if steps >= max_steps:
                break
            still = []
            for k, i in enumerate(active):
                if sets[k]:
                    decoders[i].advance(rngs[i].choice(sets[k]))
                    still.append(i)
            active = still
            steps += 1
        statuses = [d.status for d in decoders]
        dead_ends = [(d.diagnostics().get("dead_end") if hasattr(d, "diagnostics") else None) for d in decoders]
        actions = [len(d.actions) for d in decoders]
        worker_stats = pool.worker_stats() if pool else []
    finally:
        if pool is not None:
            pool.close()
    return {"workers": workers, "batch": batch, "lock_steps": steps, "build_s": round(build_s, 3),
            "host_s": round(host_s, 3), "host_ms_per_lock_step": round(1e3 * host_s / max(1, steps - 1), 3),
            "actions_per_row": actions, "statuses": statuses, "dead_ends": dead_ends,
            "host_s_per_row_action": round(host_s / max(1, sum(actions)), 4),
            "worker_busy_s": [round(s["busy_s"], 3) for s in worker_stats]}, trace


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--workers", type=int, nargs="+", default=[0, 2, 4])
    ap.add_argument("--max-steps", type=int, default=100_000, help="lock-step (token) cap")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--c2", action="store_true", help="real engine with the C2 global screen (default: C2 off stub)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import contextlib

    import stowage.interfaces  # noqa: F401  (registers frozen interfaces)
    from stowage.frozen import stub_override
    from stowage.io import load_instance

    from pool import STUB_REASON, c2_off_engine

    instance = load_instance(a.instance)
    ctx = (contextlib.nullcontext() if a.c2
           else stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON))
    runs, reference = [], None
    with ctx:
        for w in a.workers:
            res, trace = rollout(instance, batch=a.batch, workers=w, max_steps=a.max_steps, seed=a.seed,
                                 c2=a.c2, check_against=reference)
            if reference is None:
                reference = trace
            res["allowed_sets_identical_to_first_backend"] = True  # rollout raises on the first difference
            runs.append(res)
            print(json.dumps(res), flush=True)
    out = {"instance": a.instance, "instance_id": instance.id, "n_boxes": len(instance.load_list), "c2": a.c2,
           "label": "diagnostic host benchmark (uniform-random masked rollouts); C2 " + ("on" if a.c2 else "off"),
           "runs": runs}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

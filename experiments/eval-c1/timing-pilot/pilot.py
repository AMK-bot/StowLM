"""Masked-decode timing pilot for the 6.2 / C1 GPU budget (TRACKS main-track item 5; logic-mask High 2 re-fit).

Untuned base Glimmer 4-bit, prompt 3.0 (rule freeze v1 switched on for this process), best-of-k at T = 1, mask
decoders on a process pool started ONCE. No exact legal-action count in the loop (it moved to trace replay). Each
plan stops at --cap-minutes or --cap-actions, whichever comes first; per-plan time is extrapolated to a full load.
Timing only: plans aren't checked; never evidence.

Per action (lock-step batch): decode wall, parent time waiting for allowed sets (exposed mask time), worker busy time
(total mask CPU work), and the share of that work the pool hides behind the GPU forward and across workers.

    uv run python experiments/eval-c1/timing-pilot/pilot.py --inputs <medium.json> <full.json> --ks 4 8 \
        --workers 4 --out experiments/eval-c1/timing-pilot/results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
REPO = HERE.parents[2]


class Stop(Exception):
    pass


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_one(model, pool, instance, k: int, *, cap_s: float, cap_actions: int) -> dict:
    import mlx.core as mx

    from decode_trace import RecordingFactory
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import active_prompt_version, encode_prompt
    from llm.stowlm.mask_mlx import generate_masked, prefill
    from llm.stowlm.sft_data import codec_for
    from pool import PooledDecoderFactory
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine

    tokenizer, space = load_tokenizer(), get_action_space()
    prompt = encode_prompt(instance, tokenizer)
    mx.reset_peak_memory()
    t = time.perf_counter()
    cache = prefill(model, prompt)
    mx.eval([c.state for c in cache])
    prefill_s = time.perf_counter() - t
    del cache
    engine = make_feasibility_engine(instance, space)
    factory = RecordingFactory(PooledDecoderFactory(pool))
    decoders = factory(instance, engine, codec_for(instance, tokenizer), None, k)
    busy0 = sum(s["busy_s"] for s in pool.worker_stats())
    wait0 = pool.wait_s
    t0 = time.perf_counter()

    def actions_done() -> int:
        return max(sum(1 for s in d.steps if s.get("boundary")) for d in decoders)

    def on_step(step, n_active):
        if time.perf_counter() - t0 > cap_s:
            raise Stop("time cap")
        if actions_done() >= cap_actions:
            raise Stop("action cap")

    stop, stats = "finished", None
    try:
        _, stats = generate_masked(model, prompt, decoders, temperature=1.0, max_tokens=60_000, seed=0,
                                   on_step=on_step)
    except Stop as exc:
        stop = str(exc)
    wall = time.perf_counter() - t0
    busy = sum(s["busy_s"] for s in pool.worker_stats()) - busy0
    wait = pool.wait_s - wait0
    n_actions = actions_done()
    tokens = sum(len(d.steps) for d in decoders)
    statuses = [d.status for d in decoders]
    # steady-state seconds per action: the second half of sample 0's action boundaries (lock-step batch)
    bt = [s["t"] for s in decoders[0].steps if s.get("boundary")]
    half = bt[len(bt) // 2:]
    steady = (half[-1] - half[0]) / (len(half) - 1) if len(half) > 2 else (wall / max(1, n_actions))
    return {"instance": instance.id, "rung": instance.size_class, "ship": instance.vessel.id, "k": k,
            "prompt_version": active_prompt_version(), "prompt_tokens": len(prompt), "prefill_s": round(prefill_s, 2),
            "stop": stop, "actions": n_actions, "load_list": len(instance.load_list), "decode_wall_s": round(wall, 1),
            "tokens": tokens, "tok_s": round(tokens / wall, 1) if wall else None,
            "s_per_action_mean": round(wall / max(1, n_actions), 3), "s_per_action_steady": round(steady, 3),
            "mask_exposed_s": round(wait, 1), "mask_exposed_s_per_action": round(wait / max(1, n_actions), 3),
            "mask_worker_busy_s": round(busy, 1), "mask_busy_s_per_action": round(busy / max(1, n_actions), 3),
            "mask_hidden_share": round(1 - wait / busy, 3) if busy else None,
            "statuses": {s: statuses.count(s) for s in set(statuses)},
            "peak_memory_gb": round(mx.get_peak_memory() / 1e9, 1),
            "gen_summary": None if stats is None else {k2: v for k2, v in stats.summary().items()
                                                       if not isinstance(v, (list, dict))}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--ks", nargs="+", type=int, default=[4, 8])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cap-minutes", type=float, default=45.0)
    ap.add_argument("--cap-actions", type=int, default=300)
    ap.add_argument("--c2", action="store_true", help="real engine with C2 (default: C2 off stub, labelled)")
    ap.add_argument("--model-path", default=str(REPO / "models/glimmer-30b-4bit"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import contextlib

    import stowage.interfaces  # noqa: F401
    from mlx_lm import load
    from stowage.frozen import stub_override
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1

    from pool import STUB_REASON, MaskWorkerPool, c2_off_engine

    hashes = {p: file_sha(REPO / p) for p in ("src/llm/stowlm/mask.py", "src/llm/stowlm/mask_mlx.py",
                                              "src/stowage/feasibility/_engine.py")}
    out = Path(a.out)
    results = {"label": "TIMING PILOT, base Glimmer 4-bit + mask, switch on" + ("" if a.c2 else ", C2 off (stub)")
               + "; plans unchecked; never evidence", "mask_file_sha256": hashes, "workers": a.workers,
               "cap_minutes": a.cap_minutes, "cap_actions": a.cap_actions, "runs": []}
    ctx = contextlib.nullcontext() if a.c2 else stub_override("feasibility.make_engine", c2_off_engine,
                                                              reason=STUB_REASON)
    with rule_freeze_v1(enabled=True), ctx:
        pool = MaskWorkerPool(a.workers, c2=a.c2, rule_freeze=True)  # started once, before any plan
        try:
            model, _ = load(a.model_path)
            for path in a.inputs:
                instance = load_instance(path)
                for k in a.ks:
                    row = run_one(model, pool, instance, k, cap_s=60 * a.cap_minutes, cap_actions=a.cap_actions)
                    results["runs"].append(row)
                    out.write_text(json.dumps(results, indent=1))
                    print(json.dumps({x: row[x] for x in ("instance", "k", "stop", "actions", "s_per_action_steady",
                                                         "mask_busy_s_per_action", "mask_hidden_share", "tok_s",
                                                         "prefill_s", "peak_memory_gb")}), flush=True)
        finally:
            pool.close()
    results["mask_file_sha256_end"] = {p: file_sha(REPO / p) for p in hashes}
    out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()

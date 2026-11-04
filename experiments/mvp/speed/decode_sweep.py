"""mvp-speed (1): batched masked decode throughput vs batch size B on small TRAIN maps.

Same path as the generations loop (``llm.exit.runtime.generate``): pilot adapter, production mask ->
lookahead -> securing decoders (``llm.exit.selection.decoder_factory``), ``mask_mlx.generate_masked``.
For each map: B=1 greedy (T=0, the loop's greedy sample), then B=4/8/16 at T=0.8 (k samples of one prompt;
the prompt is prefilled once and its KV copied B times). Records tok/s, ms per action, GPU-host stall, peak
Metal memory, and per-sample traces (token ids + action boundaries) for the pruning estimate (prune_estimate.py).

Run: uv run python experiments/mvp/speed/decode_sweep.py --maps 6 --batches 1,4,8,16
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "out"
SMALL = ROOT / "experiments/mvp/corpus/train/small"


def pick_maps(n: int) -> list[Path]:
    """n small TRAIN maps, spread over the three feeder classes (never test)."""
    per = {}
    for ship in ("feeder_500", "feeder_800", "feeder_1700"):
        per[ship] = sorted(p for p in (SMALL / ship / "simulated_yard").glob("seed-*.json")
                           if not p.name.endswith(".audit.json"))
    out, i = [], 0
    while len(out) < n:
        for ship in per:
            if i < len(per[ship]) and len(out) < n:
                out.append(per[ship][i])
        i += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", type=int, default=6)
    ap.add_argument("--batches", default="1,4,8,16")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--adapter", default=str(ROOT / "experiments/mvp/sft/pilot-tiny/adapter"))
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--deadline", type=float, default=0, help="unix time; skip remaining runs after it")
    ap.add_argument("--no-prefetch", action="store_true", help="generate_masked(prefetch=False)")
    ap.add_argument("--skip-maps", type=int, default=0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--pool", type=int, default=0, help="W>0: mask work in W worker processes (patch 02)")
    ap.add_argument("--pool-dir", default="", help="directory holding the patched eval-c1 pool.py")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    import mlx.core as mx
    from mlx_lm import load
    from llm.exit.resources import execution_lock
    from llm.exit.selection import decoder_factory
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt
    from llm.stowlm.mask_mlx import generate_masked
    from llm.stowlm.planner import DEFAULT_MODEL
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    assert not rule_freeze_v1_enabled()
    batches = [int(b) for b in args.batches.split(",")]
    rows_path = OUT / f"decode_sweep{args.tag}.jsonl"
    with execution_lock():  # the mvp-exit GPU lock: no ExIt smoke can start while we hold the GPU
        t0 = time.perf_counter()
        model, _ = load(str(DEFAULT_MODEL), adapter_path=args.adapter)
        model.eval()
        load_s = time.perf_counter() - t0
        tok = load_tokenizer()
        space = get_action_space()
        factory = decoder_factory()
        pool = None
        if args.pool:
            import sys
            sys.path.insert(0, args.pool_dir)
            sys.path.insert(1, str(ROOT / "experiments/eval-c1"))
            from pool import MaskWorkerPool, PooledDecoderFactory
            pool = MaskWorkerPool(args.pool, c2=True, layers="exit")
            factory = PooledDecoderFactory(pool, engine_config=FeasibilityConfig(extra={"global_screen": 0}))
        print(f"loaded in {load_s:.1f}s", flush=True)
        for path in pick_maps(args.maps)[args.skip_maps:]:
            inst = load_instance(path)
            codec = codec_for(inst, tok)
            prompt = encode_prompt(inst, tok)
            for B in batches:
                if args.deadline and time.time() > args.deadline:
                    print("deadline reached; stopping", flush=True)
                    return
                engine = make_feasibility_engine(inst, space, config=FeasibilityConfig(extra={"global_screen": 0}))
                decoders = factory(inst, engine, codec, None, B)
                temp = 0.0 if B == 1 else args.temperature
                mx.reset_peak_memory()
                tw = time.perf_counter()
                results, stats = generate_masked(model, prompt, decoders, temperature=temp,
                                                 max_tokens=args.max_tokens, seed=17 + B,
                                                 prefetch=not args.no_prefetch)
                wall = time.perf_counter() - tw
                peak = mx.get_peak_memory() / 1e9
                s = stats.summary()
                samples = []
                for r, d in zip(results, decoders):
                    # token index (exclusive end) at which each committed action finished
                    ends, pos = [], 0
                    for a in d.actions:
                        pos += len(codec.encode_action(a))
                        ends.append(pos)
                    samples.append(dict(status=r.status, n_tokens=len(r.tokens), n_actions=len(d.actions),
                                        action_ends=ends, actions=[space.encode(a) for a in d.actions]))
                n_actions = sum(x["n_actions"] for x in samples)
                row = dict(map=str(path.relative_to(ROOT)), instance_id=inst.id, prefetch=not args.no_prefetch,
                           pool=args.pool, prompt_tokens=len(prompt), B=B,
                           temperature=temp, map_wall_s=round(wall, 3), prefill_s=s["prefill_s"],
                           decode_wall_s=s["wall_s"], steps=s["steps"], tokens=s["tokens"], tok_s=s["tok_s"],
                           map_tok_s=round(s["tokens"] / wall, 2), actions=n_actions,
                           ms_per_action=round(1e3 * s["wall_s"] / max(1, n_actions), 2),
                           host_wait_s=s["host_wait_s"], stall_frac=round(s["host_wait_s"] / max(1e-9, s["wall_s"]), 4),
                           host_mask_s=s["host_mask_s"], wait_s=s["wait_s"], advance_s=s["advance_s"],
                           step_ms_p50=s["step_ms_p50"], step_ms_p95=s["step_ms_p95"], peak_gb=round(peak, 2),
                           statuses={k: sum(x["status"] == k for x in samples) for k in {x["status"] for x in samples}},
                           batch_sizes=stats.batch_sizes, step_ms=[round(x, 1) for x in stats.step_ms],
                           samples=samples)
                with rows_path.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"{inst.id} B={B} T={temp} tok/s={s['tok_s']} map_tok/s={row['map_tok_s']} "
                      f"ms/action={row['ms_per_action']} stall={row['stall_frac']} peak={peak:.1f}GB "
                      f"wall={wall:.1f}s statuses={row['statuses']}", flush=True)


if __name__ == "__main__":
    main()

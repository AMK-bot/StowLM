"""Task 5.1, GPU part: masked vs unmasked decode step time at batch 1/32/64, and plans sampled by the untuned base
model (Muse Glimmer 30B 4-bit) under the mask.

Overhead: the same lock-step loop (``mask_mlx.generate_masked``) runs a fixed number of steps unmasked (greedy argmax
over the full vocabulary: model step + the cheapest sampler) and masked (temperature 1) on the same prompt, after a
warm-up; ms/step is compared over the steps where the masked batch is still full (sequences that finish or
dead-end leave the batch). Plans: whole generations to end-of-plan or dead end; tokens and outcomes go to a JSONL
file so the CPU checker pass (``check_plans.py``) runs after the GPU is released.

APPROXIMATION (diagnostic): engine C2 off (``global_screen: 0``); see measure_cpu.py.

Usage: uv run python experiments/mask/measure_gpu.py --parts overhead plans --out experiments/mask/gpu.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
import time
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "models/glimmer-30b-4bit"
RUNGS = {  # rung -> instance glob (v2 tiny; small+ from instances-sim: v2 small+ dead-end at depth 0, see README)
    "tiny": "experiments/instances-v2/random_yard/tiny/seed-*[0-9].json",
    "small": "experiments/instances-sim/small/seed-*[0-9].json",
    "medium": "experiments/instances-sim/medium/seed-*[0-9].json",
    "full": "experiments/instances-sim/full/seed-*[0-9].json",
}
PLANS = {"tiny": (4, 32, 200), "small": (2, 64, 600), "medium": (1, 64, 1300), "full": (1, 32, 700)}


FRESH = False  # --fresh: current generator, seeds 1..n


def instance_paths(rung: str, n: int) -> list[str]:
    if FRESH:
        return [f"fresh:{rung}:{seed}" for seed in range(1, n + 1)]
    return sorted(glob.glob(str(ROOT / RUNGS[rung])))[:n]


def setup(path: str):
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance

    if path.startswith("fresh:"):  # current 3.5 generator
        from stowage.generate.random_yard import generate_random_instance

        _, rung, seed = path.split(":")
        inst = generate_random_instance(rung, seed=int(seed))
    else:
        inst = load_instance(path)
    eng = make_feasibility_engine(inst, get_action_space("boxslot"), config=FeasibilityConfig(extra={"global_screen": 0}))
    tok = load_tokenizer()
    return inst, eng, ActionCodec(tok, [c.id for c in inst.containers]), encode_prompt(inst, tok)


def full_batch_ms(stats, b: int, skip: int = 2) -> list[float]:
    return [ms for ms, n in zip(stats.step_ms, stats.batch_sizes) if n == b][skip:]


def overhead(model, rungs, batches, steps, lookahead) -> list[dict]:
    import mlx.core as mx

    from llm.stowlm.mask import MaskConfig, MaskedDecoder
    from llm.stowlm.mask_mlx import generate_masked

    rows = []
    for rung in rungs:
        path = instance_paths(rung, 1)[0]
        inst, eng, codec, prompt = setup(path)
        for b in batches:
            generate_masked(model, prompt, None, batch=b, max_tokens=4, seed=0)  # warm-up (kernels, allocator)
            # baseline = the same loop unmasked and greedy: model step + argmax, the cheapest possible sampler
            _, su = generate_masked(model, prompt, None, batch=b, max_tokens=steps, seed=1, temperature=0.0)
            decs = [MaskedDecoder(eng.copy(), codec, MaskConfig(lookahead=lookahead)) for _ in range(b)]
            res, sm = generate_masked(model, prompt, decs, max_tokens=steps, seed=1)
            u, m = full_batch_ms(su, b), full_batch_ms(sm, b)
            row = dict(rung=rung, instance=inst.id, prompt_tokens=len(prompt), batch=b, steps=steps,
                       lookahead=lookahead, unmasked_ms_p50=round(statistics.median(u), 2) if u else None,
                       masked_ms_p50=round(statistics.median(m), 2) if m else None, masked_full_batch_steps=len(m),
                       unmasked=su.summary(), masked=sm.summary(),
                       masked_status=[r.status for r in res], peak_gb=round(mx.get_peak_memory() / 1e9, 1))
            if u and m:
                row["overhead_pct"] = round(100 * (row["masked_ms_p50"] / row["unmasked_ms_p50"] - 1), 1)
                row["masked_tok_s_full_batch"] = round(b * 1000 / row["masked_ms_p50"], 1)
                row["unmasked_tok_s"] = round(b * 1000 / row["unmasked_ms_p50"], 1)
            print(json.dumps({k: v for k, v in row.items() if k not in ("unmasked", "masked", "masked_status")}),
                  flush=True)
            rows.append(row)
            mx.clear_cache()
    return rows


def plans(model, rungs, lookahead, plans_out: Path) -> list[dict]:
    import mlx.core as mx

    from llm.stowlm.mask import MaskConfig, MaskedDecoder
    from llm.stowlm.mask_mlx import generate_masked

    rows = []
    with plans_out.open("a") as f:
        for rung in rungs:
            n_inst, b, max_tokens = PLANS[rung]
            for path in instance_paths(rung, n_inst):
                inst, eng, codec, prompt = setup(path)
                decs = [MaskedDecoder(eng.copy(), codec, MaskConfig(lookahead=lookahead)) for _ in range(b)]
                t = time.perf_counter()
                res, st = generate_masked(model, prompt, decs, max_tokens=max_tokens, temperature=1.0, seed=7)
                wall = time.perf_counter() - t
                counts: dict[str, int] = {}
                for k, r in enumerate(res):
                    counts[r.status] = counts.get(r.status, 0) + 1
                    d = r.decoder
                    f.write(json.dumps(dict(
                        rung=rung, path=path if path.startswith("fresh:") else str(Path(path).relative_to(ROOT)), instance=inst.id, sample=k,
                        lookahead=lookahead, status=r.status, depth=d.engine.depth, n_load=len(inst.load_list),
                        tokens=r.tokens, logprob=round(r.logprob, 3), lookahead_evals=d.lookahead_evals,
                        dead_end=None if d.dead_end is None else dict(depth=d.dead_end.depth, reason=d.dead_end.reason,
                                                                      rule_ids=list(d.dead_end.rule_ids)))) + "\n")
                f.flush()
                row = dict(rung=rung, instance=inst.id, batch=b, max_tokens=max_tokens, wall_s=round(wall, 1),
                           status=counts, stats=st.summary(),
                           mean_depth=round(statistics.mean(r.decoder.engine.depth for r in res), 1),
                           peak_gb=round(mx.get_peak_memory() / 1e9, 1))
                print(json.dumps(row), flush=True)
                rows.append(row)
                mx.clear_cache()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--parts", nargs="+", default=["overhead", "plans"])
    ap.add_argument("--rungs", nargs="+", default=["tiny", "small", "medium", "full"])
    ap.add_argument("--batches", type=int, nargs="+", default=[1, 32, 64])
    ap.add_argument("--steps", type=int, default=48)
    ap.add_argument("--lookahead", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plans-out", default=str(ROOT / "experiments/mask/gpu-plans.jsonl"))
    ap.add_argument("--fresh", action="store_true", help="instances from the current 3.5 generator")
    ap.add_argument("--plan-batch", type=int, default=None, help="override the plan batch size")
    ap.add_argument("--plan-max-tokens", type=int, default=None)
    args = ap.parse_args()
    global FRESH
    FRESH = args.fresh
    for k, (n, b, m) in list(PLANS.items()):
        PLANS[k] = (n, args.plan_batch or b, args.plan_max_tokens or m)
    import mlx.core as mx
    from mlx_lm import load

    t = time.perf_counter()
    model, _ = load(str(MODEL))
    out = dict(model=str(MODEL.relative_to(ROOT)), load_s=round(time.perf_counter() - t, 1), args=vars(args))
    if "overhead" in args.parts:
        out["overhead"] = overhead(model, args.rungs, args.batches, args.steps, args.lookahead)
    if "plans" in args.parts:
        out["plans"] = plans(model, args.rungs, args.lookahead, Path(args.plans_out))
    out["peak_gb"] = round(mx.get_peak_memory() / 1e9, 1)
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"done in {time.perf_counter() - t:.0f} s", file=sys.stderr)


if __name__ == "__main__":
    main()

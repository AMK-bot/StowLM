"""mvp-speed (3): LoRA training step time, normal path vs CACHED frozen lower-layer activations.

LoRA sits on the last 16 of Glimmer's 52 decoder layers, so layers 0..35 are pure base weights: their output
h36 (the hidden state entering layer 36) is the same for every adapter, child and generation. The normal step
runs all 52 layers forward (plus the backward/recompute of the top 16). The cached step reads h36 from memory
and runs only layers 36..51 + norm + lm_head. Prototype only (not production): same loss
(``llm.stowlm.train.completion_loss`` math), same grad checkpointing and blocked attention as the trainer.

Checks: loss and every LoRA gradient match between paths on 2 rows (bf16 tolerance), then times microsteps
(forward+backward+Adam update, batch 1) for each path, and records cache size per row.

Run: uv run python experiments/mvp/speed/train_bench.py
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "out"
SPLIT = 36  # first LoRA layer = 52 - 16


def rows_from_sweep(n: int) -> list[dict]:
    """Rows = prompt + greedy plan from decode_sweep (complete samples), longest first."""
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.io import load_instance
    tok, space, seen, rows = load_tokenizer(), get_action_space(), set(), []
    for line in (OUT / "decode_sweep.jsonl").read_text().splitlines():
        r = json.loads(line)
        for s in r["samples"]:
            if s["status"] != "complete" or r["instance_id"] in seen:
                continue
            inst = load_instance(ROOT / r["map"])
            prompt = list(encode_prompt(inst, tok))
            plan = list(codec_for(inst, tok).encode_plan([space.decode(a) for a in s["actions"]]))
            rows.append(dict(id=r["instance_id"], ids=prompt + plan, prompt_len=len(prompt)))
            seen.add(r["instance_id"])
    rows.sort(key=lambda x: -len(x["ids"]))
    return rows[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=str(ROOT / "experiments/mvp/sft/pilot-tiny/adapter"))
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--steps", type=int, default=5)
    ap.add_argument("--numerics-only", action="store_true",
                    help="loss/grad agreement incl. normal-vs-normal and cached-vs-cached repeat baselines")
    args = ap.parse_args()

    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten
    from mlx_lm import load
    from mlx_lm.models import muse_glimmer
    from mlx_lm.tuner.trainer import grad_checkpoint
    from llm.exit.resources import execution_lock
    from llm.stowlm.planner import DEFAULT_MODEL
    from llm.stowlm.train import blocked_attention, completion_loss

    rows = rows_from_sweep(args.rows)
    # APPROXIMATION (timing only): the sweep's small prompts are 0.76-0.94k tokens, the SFT config's small mean is
    # 1.6k, so one extra 1.6k row is built by repeating real token ids (prompt 1450 + completion 150); its loss
    # value is meaningless but step time depends only on length.
    base = rows[0]["ids"]
    rows.append(dict(id="synthetic-1600", ids=(base * 3)[:1600], prompt_len=1450))
    print("rows:", [(r["id"], len(r["ids"]), r["prompt_len"]) for r in rows], flush=True)
    with execution_lock():
        model, _ = load(str(DEFAULT_MODEL), adapter_path=args.adapter)
        model.freeze()
        for _, m in model.named_modules():  # trainable = existing LoRA leaves only (as llm.exit.runtime does)
            keys = [k for k in ("lora_a", "lora_b") if hasattr(m, k)]
            if keys:
                m.unfreeze(recurse=False, keys=keys, strict=True)
        model.train()
        n_train = sum(v.size for _, v in tree_flatten(model.trainable_parameters()))
        lora_layers = sorted({int(k.split(".")[2]) for k, _ in tree_flatten(model.trainable_parameters())})
        assert lora_layers[0] == SPLIT, lora_layers
        # Same process patches as llm.stowlm.train: blocked attention (block 1024) and per-layer grad checkpointing.
        muse_glimmer.scaled_dot_product_attention = blocked_attention(
            1024, model.args.sliding_window, muse_glimmer.scaled_dot_product_attention)
        grad_checkpoint(model.layers[0])
        inner, a = model.model, model.args

        def lower(inputs):
            """h entering layer SPLIT: embed + scaleless RMSNorm + frozen layers 0..SPLIT-1 (base only)."""
            h = mx.fast.rms_norm(inner.embed_tokens(inputs), None, inner.embed_eps)
            masks = inner._masks(h, [None] * len(inner.layers))
            for layer, m in zip(inner.layers[:SPLIT], masks[:SPLIT]):
                h = layer(h, m, None)
            return h

        def upper_logits(h):
            masks = inner._masks(h, [None] * len(inner.layers))
            for layer, m in zip(inner.layers[SPLIT:], masks[SPLIT:]):
                h = layer(h, m, None)
            out = model.lm_head(inner.norm(h)) * a.output_multiplier
            return mx.tanh(out / a.final_logit_softcapping) * a.final_logit_softcapping

        def cached_loss(model_, h, batch, lengths):
            """completion_loss with the lower layers replaced by the cached h (identical masking of targets)."""
            targets = batch[:, 1:]
            logits = upper_logits(h)
            steps = mx.arange(1, targets.shape[1] + 1)
            mask = mx.logical_and(steps >= lengths[:, 0:1], steps < lengths[:, 1:])
            ce = nn.losses.cross_entropy(logits, targets) * mask
            ntoks = mask.sum()
            return ce.astype(mx.float32).sum() / ntoks, ntoks

        normal_vg = nn.value_and_grad(model, completion_loss)
        cached_vg = nn.value_and_grad(model, cached_loss)
        report = dict(trainable_params=n_train, split_layer=SPLIT, rows=[])
        if args.numerics_only:
            def flat(g):
                return {k: v.astype(mx.float32) for k, v in tree_flatten(g)}

            def compare(ga, gb):
                num = sum(mx.sum((ga[k] - gb[k]) ** 2).item() for k in ga)
                den = sum(mx.sum(ga[k] ** 2).item() for k in ga)
                dot = sum(mx.sum(ga[k] * gb[k]).item() for k in ga)
                nb = sum(mx.sum(gb[k] ** 2).item() for k in gb)
                per = sorted(((mx.linalg.norm(ga[k] - gb[k]).item() / max(mx.linalg.norm(ga[k]).item(), 1e-30), k)
                              for k in ga), reverse=True)
                return dict(global_rel_l2=(num / den) ** .5, cosine=dot / (den * nb) ** .5,
                            worst_tensor_rel_l2=per[0][0], worst_tensor=per[0][1],
                            median_tensor_rel_l2=per[len(per) // 2][0])
            out = []
            for r in rows[:2]:
                batch = mx.array([r["ids"]])
                lengths = mx.array([[r["prompt_len"], len(r["ids"])]])
                h = mx.stop_gradient(lower(batch[:, :-1])); mx.eval(h)
                res = {}
                for name, vg, inp in (("n1", normal_vg, (batch, lengths)), ("n2", normal_vg, (batch, lengths)),
                                      ("c1", cached_vg, (h, batch, lengths)), ("c2", cached_vg, (h, batch, lengths))):
                    (l, _), g = vg(model, *inp)
                    mx.eval(l, g)
                    res[name] = (l.item(), flat(g))
                h2 = mx.stop_gradient(lower(batch[:, :-1])); mx.eval(h2)
                row = dict(id=r["id"], tokens=len(r["ids"]), losses={k: v[0] for k, v in res.items()},
                           h_rebuild_max_abs=mx.abs(h.astype(mx.float32) - h2.astype(mx.float32)).max().item(),
                           normal_vs_normal=compare(res["n1"][1], res["n2"][1]),
                           cached_vs_cached=compare(res["c1"][1], res["c2"][1]),
                           normal_vs_cached=compare(res["n1"][1], res["c1"][1]))
                print(json.dumps(row), flush=True)
                out.append(row)
            (OUT / "train_numerics.json").write_text(json.dumps(out, indent=1))
            return
        for r in rows:
            batch = mx.array([r["ids"]])
            lengths = mx.array([[r["prompt_len"], len(r["ids"])]])
            # cache build (once per row, valid for every adapter since layers < SPLIT carry no LoRA)
            mx.synchronize(); t = time.perf_counter()
            h = mx.stop_gradient(lower(batch[:, :-1]))
            mx.eval(h); mx.synchronize()
            cache_s = time.perf_counter() - t
            cache_mb = h.nbytes / 1e6
            # numerics: same params, both paths
            (ln, _), gn = normal_vg(model, batch, lengths)
            (lc, _), gc = cached_vg(model, h, batch, lengths)
            mx.eval(ln, gn, lc, gc)
            fn, fc = dict(tree_flatten(gn)), dict(tree_flatten(gc))
            worst_rel, worst_abs = 0.0, 0.0
            for k in fn:
                d = (fn[k].astype(mx.float32) - fc[k].astype(mx.float32))
                nrm = mx.linalg.norm(fn[k].astype(mx.float32)).item()
                worst_abs = max(worst_abs, mx.abs(d).max().item())
                worst_rel = max(worst_rel, mx.linalg.norm(d).item() / max(nrm, 1e-30))
            del gn, gc, fn, fc
            row = dict(id=r["id"], tokens=len(r["ids"]), prompt_len=r["prompt_len"], loss_normal=ln.item(),
                       loss_cached=lc.item(), loss_abs_diff=abs(ln.item() - lc.item()),
                       grad_worst_rel_l2=worst_rel, grad_worst_abs=worst_abs, n_grad_tensors=len(dict(tree_flatten(model.trainable_parameters()))),
                       cache_build_s=round(cache_s, 3), cache_mb=round(cache_mb, 2), cache_dtype=str(h.dtype))
            # timing: microstep = forward + backward + Adam update, batch 1, 1 warm-up + args.steps timed
            for name, vg, inp in (("normal", normal_vg, (batch, lengths)), ("cached", cached_vg, (h, batch, lengths))):
                opt = optim.Adam(learning_rate=5e-5)
                times = []
                mx.reset_peak_memory()
                for i in range(args.steps + 1):
                    mx.synchronize(); t = time.perf_counter()
                    (loss, _), g = vg(model, *inp)
                    opt.update(model, g)
                    mx.eval(loss, model.trainable_parameters(), opt.state)
                    mx.synchronize()
                    if i:
                        times.append(time.perf_counter() - t)
                row[f"{name}_step_s"] = round(sorted(times)[len(times) // 2], 3)
                row[f"{name}_steps_s"] = [round(x, 3) for x in times]
                row[f"{name}_peak_gb"] = round(mx.get_peak_memory() / 1e9, 2)
                # undo the updates so the second path starts from the same adapter
                model.load_weights(args.adapter + "/adapters.safetensors", strict=False)
            row["speedup"] = round(row["normal_step_s"] / row["cached_step_s"], 3)
            report["rows"].append(row)
            print(json.dumps(row), flush=True)
        (OUT / "train_bench.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

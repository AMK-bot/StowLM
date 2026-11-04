"""Gradient agreement: U = normal unpatched, CU = cached unpatched, N = normal with trainer patches
(blocked attention 1024 + grad checkpoint, i.e. production), C = cached with the same patches. 2 rows."""
import json, sys
from pathlib import Path
import mlx.core as mx
import mlx.nn as nn
from mlx.utils import tree_flatten
from mlx_lm import load
from mlx_lm.models import muse_glimmer
from mlx_lm.tuner.trainer import grad_checkpoint
sys.path.insert(0, str(Path(__file__).parent))
from train_bench import rows_from_sweep, SPLIT
from llm.stowlm.planner import DEFAULT_MODEL
from llm.stowlm.train import completion_loss, blocked_attention
ROOT = Path(__file__).resolve().parents[3]
model, _ = load(str(DEFAULT_MODEL), adapter_path=str(ROOT / "experiments/mvp/sft/pilot-tiny/adapter"))
model.freeze()
for _, m in model.named_modules():
    keys = [k for k in ("lora_a", "lora_b") if hasattr(m, k)]
    if keys:
        m.unfreeze(recurse=False, keys=keys, strict=True)
model.train()
inner, a = model.model, model.args
def lower(x):
    h = mx.fast.rms_norm(inner.embed_tokens(x), None, inner.embed_eps)
    masks = inner._masks(h, [None] * 52)
    for layer, m in zip(inner.layers[:SPLIT], masks[:SPLIT]):
        h = layer(h, m, None)
    return h
def cached_loss(model_, h, batch, lengths):
    targets = batch[:, 1:]
    masks = inner._masks(h, [None] * 52)
    for layer, m in zip(inner.layers[SPLIT:], masks[SPLIT:]):
        h = layer(h, m, None)
    out = model.lm_head(inner.norm(h)) * a.output_multiplier
    logits = mx.tanh(out / a.final_logit_softcapping) * a.final_logit_softcapping
    steps = mx.arange(1, targets.shape[1] + 1)
    mask = mx.logical_and(steps >= lengths[:, 0:1], steps < lengths[:, 1:])
    ce = nn.losses.cross_entropy(logits, targets) * mask
    return ce.astype(mx.float32).sum() / mask.sum(), mask.sum()
def grads(fn, *args):
    (l, _), g = nn.value_and_grad(model, fn)(model, *args)
    g = {k: v.astype(mx.float32) for k, v in tree_flatten(g)}
    mx.eval(l, g)
    return l.item(), g
def rel(ga, gb):
    num = sum(mx.sum((ga[k] - gb[k]) ** 2).item() for k in ga); den = sum(mx.sum(ga[k] ** 2).item() for k in ga)
    dot = sum(mx.sum(ga[k] * gb[k]).item() for k in ga); nb = sum(mx.sum(gb[k] ** 2).item() for k in gb)
    return dict(rel_l2=round((num / den) ** .5, 4), cos=round(dot / (den * nb) ** .5, 5))
rows = rows_from_sweep(2)
res = {}
for r in rows:
    b = mx.array([r["ids"]]); ln = mx.array([[r["prompt_len"], len(r["ids"])]])
    h = mx.stop_gradient(lower(b[:, :-1])); mx.eval(h)
    res[r["id"]] = dict(U=grads(completion_loss, b, ln), CU=grads(cached_loss, h, b, ln))
orig = muse_glimmer.scaled_dot_product_attention
muse_glimmer.scaled_dot_product_attention = blocked_attention(1024, a.sliding_window, orig)
grad_checkpoint(model.layers[0])
out = []
for r in rows:
    b = mx.array([r["ids"]]); ln = mx.array([[r["prompt_len"], len(r["ids"])]])
    h = mx.stop_gradient(lower(b[:, :-1])); mx.eval(h)
    d = res[r["id"]]; d["N"] = grads(completion_loss, b, ln); d["C"] = grads(cached_loss, h, b, ln)
    row = dict(id=r["id"], tokens=len(r["ids"]), loss={k: round(v[0], 6) for k, v in d.items()},
               U_vs_CU=rel(d["U"][1], d["CU"][1]), U_vs_N=rel(d["U"][1], d["N"][1]),
               U_vs_C=rel(d["U"][1], d["C"][1]), N_vs_C=rel(d["N"][1], d["C"][1]))
    print(json.dumps(row), flush=True); out.append(row)
(Path(__file__).parent / "out/train_grad_agreement.json").write_text(json.dumps(out, indent=1))

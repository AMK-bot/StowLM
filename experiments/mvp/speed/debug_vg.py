"""Debug: loss of normal vs cached path, plain forward vs inside value_and_grad, eval vs train mode."""
import sys
from pathlib import Path
import mlx.core as mx
import mlx.nn as nn
from mlx_lm import load
sys.path.insert(0, str(Path(__file__).parent))
from train_bench import rows_from_sweep, SPLIT
from llm.stowlm.planner import DEFAULT_MODEL
from llm.stowlm.train import completion_loss
ROOT = Path(__file__).resolve().parents[3]
model, _ = load(str(DEFAULT_MODEL), adapter_path=str(ROOT / "experiments/mvp/sft/pilot-tiny/adapter"))
inner, a = model.model, model.args
r = rows_from_sweep(1)[0]
batch = mx.array([r["ids"]]); lengths = mx.array([[r["prompt_len"], len(r["ids"])]])
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
h = mx.stop_gradient(lower(batch[:, :-1])); mx.eval(h)
def show(tag):
    ln = completion_loss(model, batch, lengths)[0].item(); lc = cached_loss(model, h, batch, lengths)[0].item()
    (vn, _), _ = nn.value_and_grad(model, completion_loss)(model, batch, lengths)
    (vc, _), _ = nn.value_and_grad(model, cached_loss)(model, h, batch, lengths)
    print(tag, "fwd normal", ln, "fwd cached", lc, "vg normal", vn.item(), "vg cached", vc.item(), flush=True)
show("eval, all params trainable-default")
model.freeze()
for _, m in model.named_modules():
    keys = [k for k in ("lora_a", "lora_b") if hasattr(m, k)]
    if keys:
        m.unfreeze(recurse=False, keys=keys, strict=True)
show("eval, lora-only trainable")
model.train()
show("train mode")

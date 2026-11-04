"""Debug: which trainer patch makes cached != normal at L > 1024? Forward loss only, row from train_bench."""
import json, sys
from pathlib import Path
import mlx.core as mx
from mlx_lm import load
from mlx_lm.models import muse_glimmer
from mlx_lm.tuner.trainer import grad_checkpoint
sys.path.insert(0, str(Path(__file__).parent))
from train_bench import rows_from_sweep, SPLIT
from llm.stowlm.planner import DEFAULT_MODEL
from llm.stowlm.train import blocked_attention
ROOT = Path(__file__).resolve().parents[3]
model, _ = load(str(DEFAULT_MODEL), adapter_path=str(ROOT / "experiments/mvp/sft/pilot-tiny/adapter"))
inner, a = model.model, model.args
r = rows_from_sweep(1)[0]
x = mx.array([r["ids"][:-1]])
def full():
    return model(x)
def split():
    h = mx.fast.rms_norm(inner.embed_tokens(x), None, inner.embed_eps)
    masks = inner._masks(h, [None] * 52)
    for layer, m in zip(inner.layers[:SPLIT], masks[:SPLIT]):
        h = layer(h, m, None)
    h = mx.stop_gradient(h); mx.eval(h)
    masks = inner._masks(h, [None] * 52)
    for layer, m in zip(inner.layers[SPLIT:], masks[SPLIT:]):
        h = layer(h, m, None)
    out = model.lm_head(inner.norm(h)) * a.output_multiplier
    return mx.tanh(out / a.final_logit_softcapping) * a.final_logit_softcapping
def d(tag):
    f, s = full(), split()
    print(tag, "L", x.shape[1], "max|full-split|", mx.abs(f.astype(mx.float32) - s.astype(mx.float32)).max().item(), flush=True)
m = inner._masks(inner.embed_tokens(x), [None]*52)
print("mask types", {inner.layers[i].self_attn.layer_type: (m[i] if isinstance(m[i], str) else (m[i].shape, m[i].dtype)) for i in (0, 3)})
d("no patches")
orig = muse_glimmer.scaled_dot_product_attention
muse_glimmer.scaled_dot_product_attention = blocked_attention(1024, a.sliding_window, orig)
d("blocked attention")
muse_glimmer.scaled_dot_product_attention = orig
grad_checkpoint(model.layers[0])
d("grad checkpoint")
muse_glimmer.scaled_dot_product_attention = blocked_attention(1024, a.sliding_window, orig)
d("both")

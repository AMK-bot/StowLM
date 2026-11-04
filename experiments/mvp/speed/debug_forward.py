"""Debug: does upper(lower(x)) reproduce model(x) bit-for-bit (forward only, with and without trainer patches)?"""
import json
from pathlib import Path
import mlx.core as mx
from mlx_lm import load
from llm.stowlm.planner import DEFAULT_MODEL
ROOT = Path(__file__).resolve().parents[3]
model, _ = load(str(DEFAULT_MODEL), adapter_path=str(ROOT / "experiments/mvp/sft/pilot-tiny/adapter"))
inner, a = model.model, model.args
r = json.loads((Path(__file__).parent / "out/decode_sweep.jsonl").read_text().splitlines()[0])
from llm.stowlm.format import load_tokenizer
from llm.stowlm.format_prompt import encode_prompt
from stowage.io import load_instance
x = mx.array([list(encode_prompt(load_instance(ROOT / r["map"]), load_tokenizer()))[:700]])
def run(split):
    h = mx.fast.rms_norm(inner.embed_tokens(x), None, inner.embed_eps)
    masks = inner._masks(h, [None] * 52)
    hs = []
    for i, (layer, m) in enumerate(zip(inner.layers, masks)):
        if i == split:
            mx.eval(h); h = mx.array(h)  # materialise like a cache
            masks = inner._masks(h, [None] * 52)
            m = masks[i]
        h = layer(h, m, None)
    out = model.lm_head(inner.norm(h)) * a.output_multiplier
    return mx.tanh(out / a.final_logit_softcapping) * a.final_logit_softcapping
ref = model(x)
for name, y in (("manual-no-split", run(-1)), ("split36", run(36))):
    print(name, ref.dtype, y.dtype, mx.abs(ref.astype(mx.float32) - y.astype(mx.float32)).max().item())
print("masks", [type(m).__name__ if not isinstance(m, str) else m for m in inner._masks(inner.embed_tokens(x), [None]*52)][:4])
for i in (0, 35, 36, 51):
    L = inner.layers[i]; print(i, type(L).__name__, L.self_attn.layer_type, [k for k in L.self_attn.__dict__.get('_modules', {})] if hasattr(L.self_attn,'__dict__') else '')

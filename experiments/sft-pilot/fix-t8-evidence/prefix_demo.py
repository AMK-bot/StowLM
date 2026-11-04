"""Before/after evidence: run the review scenarios against the pre-fix train() (train_old.py) and the fixed one."""
import json, sys, tempfile
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import mlx.core as mx
from mlx.utils import tree_flatten
from mlx_lm.models import muse_glimmer
import train_old
from llm.stowlm import train as T
from llm.stowlm.sft_data import SFTExample, write_dataset

def ex(i, n_prompt, n_completion, split="train", stubs=None):
    ids = [1 + (7 * i + j) % 120 for j in range(n_prompt + n_completion)]
    return SFTExample(instance_id=f"fx{i}-{split}", split=split, input_ids=ids, prompt_len=n_prompt,
                      n_actions=1, n_boxes=1, plan_id="p", stubs=stubs or {})

root = Path(tempfile.mkdtemp(dir=sys.argv[1]))
md = root / "m"; md.mkdir()
config = {"model_type": "muse_glimmer", "hidden_size": 64, "num_hidden_layers": 4, "intermediate_size": 128,
          "num_attention_heads": 4, "num_key_value_heads": 2, "head_dim": 16, "vocab_size": 128, "sliding_window": 16}
mx.random.seed(123)
m = muse_glimmer.Model(muse_glimmer.ModelArgs.from_dict(config))
mx.save_safetensors(str(md / "model.safetensors"), dict(tree_flatten(m.parameters()))); (md / "config.json").write_text(json.dumps(config))
data = root / "sft-pilot" / "data"
write_dataset([ex(i, 20 + 3 * i, 10 + i) for i in range(6)] + [ex(9, 25, 12, "val"), ex(10, 30, 8, "val")], data)
def C(name, **kw):
    b = dict(model=str(md), num_layers=2, rank=4, scale=2.0, dropout=0.2, learning_rate=1e-2, batch_size=2, iters=7,
             steps_per_report=1, steps_per_eval=3, steps_per_save=2, max_seq_length=128, seed=5, diagnostic=True); b.update(kw)
    return T.TrainConfig(data_dir=str(data), adapter_dir=str(root / "sft-pilot" / name), **b)
def W(c): return mx.load(str(Path(c.adapter_dir) / "adapters.safetensors"))
def maxdiff(a, b): return max(float(mx.abs(a[k] - b[k]).max()) for k in a)

# row 3: final non-interval checkpoint (iters 3 -> 7) ; row 4: data order/RNG restart
full = C("old-full"); train_old.train(full)
s = C("old-short", iters=3); train_old.train(s)
print("OLD row3: numbered after iters=3, save=2:", sorted(p.name for p in Path(s.adapter_dir).glob("0*_adapters*")))
train_old.train(replace(s, iters=7))
print("OLD row3/4: max |w_full - w_extended| =", maxdiff(W(full), W(replace(s, iters=7))))
# row 5: patch persistence
orig = muse_glimmer.scaled_dot_product_attention
train_old.train(C("old-blocked", attn_block=8, iters=2))
print("OLD row5: sdpa restored after attn_block run:", muse_glimmer.scaled_dot_product_attention is orig)
muse_glimmer.scaled_dot_product_attention = orig
# row 2: tainted checkpoint resumed on clean data (same tainted dir name allowed)
td = root / "sft-pilot" / "tdata"; write_dataset([ex(i, 20, 10, stubs={"checker": "tiny"}) for i in range(4)] + [ex(9, 20, 10, "val", {"checker": "tiny"}), ex(8, 22, 10, "val", {"checker": "tiny"})], td)
tc = replace(C("old-taint", iters=2), data_dir=str(td)); train_old.train(tc)
print("OLD row2: run.json stubs after tainted run:", json.loads((Path(tc.adapter_dir) / "run.json").read_text())["stubs"])
train_old.train(replace(tc, data_dir=str(data), iters=4))
print("OLD row2: run.json stubs after clean-data resume:", json.loads((Path(tc.adapter_dir) / "run.json").read_text())["stubs"],
      "| sidecar exists:", (Path(tc.adapter_dir) / T.SIDECAR).exists())
# NEW for comparison
nf = C("new-full"); T.train(nf); ns = C("new-short", iters=3); T.train(ns); T.train(replace(ns, iters=7))
print("NEW row3/4: max |w_full - w_extended| =", maxdiff(W(nf), W(replace(ns, iters=7))))
nt = replace(C("new-taint", iters=2), data_dir=str(td)); T.train(nt)
T.train(replace(nt, data_dir=str(data), iters=4)) if False else None
try:
    T.train(replace(nt, data_dir=str(data), iters=4))
except RuntimeError as e:
    print("NEW row2: clean-data resume of tainted checkpoint refused:", str(e)[:80])
print("NEW row2: sidecar stubs:", sorted(json.loads((Path(nt.adapter_dir) / T.SIDECAR).read_text())["provenance"]["stubs"]))

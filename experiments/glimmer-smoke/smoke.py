# Day 1 smoke test: can mlx-lm build, run, mask-decode and LoRA-train Muse Glimmer's text tower?
# No weights needed: builds the real 30B architecture lazily (shapes only) to count parameters, then runs a
# shrunken copy of the same architecture end to end. Usage: uv run python experiments/glimmer-smoke/smoke.py CONFIG_JSON
import json
import sys

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
from mlx.utils import tree_flatten

from mlx_lm.models.muse_glimmer import Model, ModelArgs
from mlx_lm.tuner.utils import linear_to_lora_layers

config = json.load(open(sys.argv[1]))

# 1. Real architecture, lazily: MLX doesn't allocate until arrays are evaluated
full = Model(ModelArgs.from_dict(config))
n = sum(v.size for _, v in tree_flatten(full.parameters()))
print(f"text tower parameters: {n / 1e9:.2f}B  (bf16 ~{n * 2 / 1e9:.0f} GB, 4-bit ~{n * 0.56 / 1e9:.0f} GB)")
del full

# 2. Same architecture, shrunk: 8 layers keeps two full [local x3, global] cycles
small = dict(config["text_config"], hidden_size=256, intermediate_size=512, num_hidden_layers=8,
             num_attention_heads=4, num_key_value_heads=2, head_dim=64, vocab_size=1000, sliding_window=16)
small["layer_types"] = config["text_config"]["layer_types"][:8]
small["layer_rope_theta"] = config["text_config"]["layer_rope_theta"][:8]
model = Model(ModelArgs.from_dict(small))
mx.eval(model.parameters())

x = mx.random.randint(0, 1000, (2, 48))  # longer than the sliding window, so local attention is exercised
logits = model(x)
print("forward:", logits.shape, "finite:", bool(mx.all(mx.isfinite(logits)).item()))

# 3. Masked greedy decoding: the hook StowLM's feasibility mask will use (here: only even token ids allowed)
allowed = mx.arange(1000) % 2 == 0
tokens = [1]
for _ in range(10):
    step = model(mx.array([tokens]))[:, -1, :]
    step = mx.where(allowed, step, -mx.inf)
    tokens.append(int(mx.argmax(step, axis=-1).item()))
print("masked decode:", tokens[1:], "all allowed:", all(t % 2 == 0 for t in tokens[1:]))

# 4. LoRA: freeze base, add adapters to the last 4 blocks, take a few training steps
model.freeze()
linear_to_lora_layers(model, 4, {"rank": 8, "scale": 20.0, "dropout": 0.0})
trainable = sum(v.size for _, v in tree_flatten(model.trainable_parameters()))
print(f"LoRA trainable params: {trainable}")


def loss_fn(m, inputs, targets):
    return nn.losses.cross_entropy(m(inputs), targets).mean()


opt = optim.Adam(learning_rate=1e-3)
step_fn = nn.value_and_grad(model, loss_fn)
batch = mx.random.randint(0, 1000, (4, 33))
losses = []
for _ in range(20):
    loss, grads = step_fn(model, batch[:, :-1], batch[:, 1:])
    opt.update(model, grads)
    mx.eval(model.parameters(), opt.state)
    losses.append(loss.item())
print(f"LoRA loss: {losses[0]:.3f} -> {losses[-1]:.3f}  decreased: {losses[-1] < losses[0]}")

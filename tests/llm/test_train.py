"""SFT training (plan 8.1): dataset adapter, loss mask, checkpoints, taint guard. Tests that touch MLX (loss mask,
optimizer state, smoke) are marked ``gpu`` so Metal-less sandboxes never import it (docs/architecture.md); the rest
are plain CPU."""

from pathlib import Path

import pytest

from llm.stowlm.sft_data import SFTExample, write_dataset
from llm.stowlm.train import (TokenDataset, TrainConfig, _latest_checkpoint, load_optimizer_state, lora_parameters,
                              save_optimizer_state, train)

REPO = Path(__file__).resolve().parents[2]


def ex(n_prompt, n_completion, split="train", stubs=None):
    return SFTExample(instance_id=f"i{n_prompt}-{n_completion}-{split}", split=split,
                      input_ids=list(range(1, n_prompt + n_completion + 1)), prompt_len=n_prompt,
                      n_actions=1, n_boxes=1, plan_id="p", stubs=stubs or {})


def test_token_dataset_refuses_truncation():
    with pytest.raises(ValueError, match="end-of-plan"):
        TokenDataset([ex(10, 10)], max_seq_length=15)
    with pytest.raises(ValueError, match="empty"):
        TokenDataset([], max_seq_length=15)
    ds = TokenDataset([ex(10, 5)], max_seq_length=15)
    assert len(ds) == 1 and ds[0] == (list(range(1, 16)), 10) and ds.process(ds[0]) == ds[0]


class _Recorder:
    """Stands in for the model: records which targets the loss mask keeps (via uniform logits, CE = log V)."""

    V = 64

    def __call__(self, inputs):
        import mlx.core as mx

        return mx.zeros((*inputs.shape, self.V))


@pytest.mark.gpu
def test_loss_mask_covers_exactly_the_completion():
    """completion_loss trains exactly the completion tokens (prompt_len .. end). mlx-lm 0.32.2's default_loss also
    trains one padding token after the end (the off-by-one this module works around)."""
    import math

    import mlx.core as mx

    from mlx_lm.tuner.trainer import default_loss, iterate_batches

    from llm.stowlm.train import completion_loss

    ds = TokenDataset([ex(7, 4), ex(3, 2)], max_seq_length=64)
    batch, lengths = next(iterate_batches(ds, batch_size=2, max_seq_length=64))
    loss, ntoks = completion_loss(_Recorder(), batch, lengths)
    assert int(ntoks) == 4 + 2 and math.isclose(float(loss), math.log(_Recorder.V), rel_tol=1e-5)
    _, ntoks_default = default_loss(_Recorder(), batch, lengths)
    assert int(ntoks_default) == 4 + 2 + 2  # one padding target per sequence: the upstream off-by-one
    targets = batch[:, 1:]
    steps = mx.arange(1, targets.shape[1] + 1)
    mask = mx.logical_and(steps >= lengths[:, 0:1], steps < lengths[:, 1:])
    for row in range(2):
        off, length = lengths[row].tolist()
        trained = [int(t) for t, m in zip(targets[row].tolist(), mask[row].tolist()) if m]
        assert trained == list(range(off + 1, length + 1))  # ids are 1..n: these are the completion tokens


def test_latest_checkpoint_and_lora_parameters(tmp_path):
    assert _latest_checkpoint(tmp_path) is None
    for it in (50, 150, 100):
        (tmp_path / f"{it:07d}_adapters.safetensors").write_bytes(b"")
    path, it = _latest_checkpoint(tmp_path)
    assert it == 150 and path.name == "0000150_adapters.safetensors"
    cfg = TrainConfig(data_dir="d", adapter_dir="a", rank=16, scale=10.0, keys=["self_attn.q_proj"])
    assert lora_parameters(cfg) == {"rank": 16, "scale": 10.0, "dropout": 0.0, "keys": ["self_attn.q_proj"]}
    assert "keys" not in lora_parameters(TrainConfig(data_dir="d", adapter_dir="a"))


@pytest.mark.gpu
def test_optimizer_state_roundtrip_gives_identical_next_update(tmp_path):
    """Resume must restore Adam's moments and step: fresh moments made the loss jump 0.45 -> 2.5 in the tiny chain."""
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten, tree_unflatten

    mx.random.seed(0)
    m = nn.Linear(4, 3)
    x, y = mx.random.normal((8, 4)), mx.random.normal((8, 3))
    vg = nn.value_and_grad(m, lambda m: ((m(x) - y) ** 2).mean())
    opt = optim.Adam(learning_rate=0.1)
    for _ in range(3):
        opt.update(m, vg(m)[1])
        mx.eval(m.parameters(), opt.state)
    save_optimizer_state(opt, tmp_path / "o.safetensors")
    weights = [(k, mx.array(v)) for k, v in tree_flatten(m.parameters())]
    opt.update(m, vg(m)[1])
    reference = mx.array(m.weight)
    m.update(tree_unflatten(weights))
    restored = optim.Adam(learning_rate=0.1)
    load_optimizer_state(restored, m, tmp_path / "o.safetensors")
    restored.update(m, vg(m)[1])
    assert float(mx.abs(m.weight - reference).max()) == 0.0


def test_tainted_data_only_trains_under_tiny_chain(tmp_path):
    d = tmp_path / "tiny-chain" / "sft"
    write_dataset([ex(5, 3, stubs={"x": "tiny"}), ex(5, 3, "val", stubs={"x": "tiny"})], d)
    with pytest.raises(RuntimeError, match="tiny-chain"):
        train(TrainConfig(data_dir=str(d), adapter_dir=str(tmp_path / "adapters")))


@pytest.mark.gpu
@pytest.mark.slow
def test_two_step_smoke(tmp_path):
    """Two QLoRA steps on the real 4-bit model with synthetic token data: loss is finite, checkpoint + run.json written."""
    model = REPO / "models/glimmer-30b-4bit"
    if not (model / "config.json").exists():
        pytest.skip("local Glimmer weights missing")
    d = tmp_path / "sft-pilot" / "data"  # synthetic token fixture: diagnostic only, confined to sft-pilot/
    write_dataset([ex(40, 20), ex(30, 30), ex(40, 20, "val")], d)
    out = tmp_path / "sft-pilot" / "a"
    rec = train(TrainConfig(data_dir=str(d), adapter_dir=str(out), iters=2, steps_per_report=1,
                            steps_per_eval=2, steps_per_save=1, num_layers=2, max_seq_length=128, diagnostic=True))
    assert rec["train_loss_last"] is not None and rec["train_loss_last"] == rec["train_loss_last"]
    assert (out / "run.json").exists() and _latest_checkpoint(out)[1] == 2
    assert "stowlm.train.dataset" in rec["stubs"]  # never shippable
    import json

    assert json.loads((out / "adapters.safetensors.provenance.json").read_text())["training"]["iteration"] == 2


@pytest.mark.gpu
def test_learning_rate_schedule_warmup_then_cosine():
    import mlx.core as mx

    from llm.stowlm.train import learning_rate_schedule

    assert learning_rate_schedule(TrainConfig(data_dir="d", adapter_dir="a", learning_rate=3e-5)) == 3e-5
    cfg = TrainConfig(data_dir="d", adapter_dir="a", learning_rate=1e-4, iters=100, warmup_frac=0.05,
                      lr_schedule="cosine", min_lr_frac=0.1)
    f = learning_rate_schedule(cfg)
    lr = [float(f(mx.array(s))) for s in (0, 1, 5, 6, 52, 100)]
    assert lr[0] == 0.0 and abs(lr[1] - 2e-5) < 1e-9 and abs(lr[2] - 1e-4) < 1e-9  # 5 warmup steps up to the peak
    assert 1e-5 < lr[4] < 1e-4 and abs(lr[5] - 1e-5) < 1e-9  # cosine down to min_lr_frac * peak
    with pytest.raises(ValueError):
        learning_rate_schedule(TrainConfig(data_dir="d", adapter_dir="a", lr_schedule="linear"))


@pytest.mark.gpu
@pytest.mark.parametrize("window", [None, 50])
def test_blocked_attention_is_exact_forward_and_backward(window):
    """Query-blocked + checkpointed attention == mlx-lm's attention, outputs and gradients (causal and sliding)."""
    import mlx.core as mx
    from mlx_lm.models.base import create_causal_mask, scaled_dot_product_attention

    from llm.stowlm.train import blocked_attention

    mx.random.seed(0)
    q, k, v = (mx.random.normal((1, 4, 300, 16)) for _ in range(3))
    k, v = k[:, :2], v[:, :2]  # grouped-query attention, as in Glimmer (more query heads than KV heads)
    mask = "causal" if window is None else create_causal_mask(300, window_size=window)
    blocked = blocked_attention(64, window or 2048, scaled_dot_product_attention)

    def loss(fn):
        return lambda q, k, v: (fn(q, k, v, None, scale=0.25, mask=mask) ** 2).sum()

    ref, ref_g = mx.value_and_grad(loss(scaled_dot_product_attention), argnums=(0, 1, 2))(q, k, v)
    out, out_g = mx.value_and_grad(loss(blocked), argnums=(0, 1, 2))(q, k, v)
    assert abs(float(ref) - float(out)) <= 1e-3 * abs(float(ref))
    for a, b in zip(ref_g, out_g):
        assert float(mx.abs(a - b).max()) < 1e-3

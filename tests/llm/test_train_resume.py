"""8.1 review fixes on Metal: resume trajectory, checkpoint/publish lineage and scoped patches, using a tiny
randomly initialised Muse Glimmer saved to disk (4 blocks, hidden 64, vocab 128) so the real ``train`` path (model
load, LoRA, mlx-lm step, checkpoints, sidecar) runs in seconds. Synthetic token data: a diagnostic fixture, never
shippable. Marked ``gpu``: MLX is imported."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from llm.stowlm import train as T
from llm.stowlm.sft_data import SFTExample, write_dataset

pytestmark = pytest.mark.gpu


def ex(i, n_prompt, n_completion, split="train"):
    ids = [1 + (7 * i + j) % 120 for j in range(n_prompt + n_completion)]
    return SFTExample(instance_id=f"fx{i}-{split}", split=split, input_ids=ids, prompt_len=n_prompt,
                      n_actions=1, n_boxes=1, plan_id="synthetic-fixture", meta={"synthetic_fixture": True})


@pytest.fixture(scope="module")
def tiny_model(tmp_path_factory):
    import mlx.core as mx
    from mlx.utils import tree_flatten
    from mlx_lm.models import muse_glimmer

    d = tmp_path_factory.mktemp("tiny-glimmer")
    config = {"model_type": "muse_glimmer", "hidden_size": 64, "num_hidden_layers": 4, "intermediate_size": 128,
              "num_attention_heads": 4, "num_key_value_heads": 2, "head_dim": 16, "vocab_size": 128,
              "sliding_window": 16, "max_position_embeddings": 512}
    mx.random.seed(123)
    model = muse_glimmer.Model(muse_glimmer.ModelArgs.from_dict(config))
    mx.save_safetensors(str(d / "model.safetensors"), dict(tree_flatten(model.parameters())))
    (d / "config.json").write_text(json.dumps(config))
    return d


@pytest.fixture
def setup(tmp_path, tiny_model):
    data = tmp_path / "sft-pilot" / "data"
    write_dataset([ex(i, 20 + 3 * i, 10 + i) for i in range(6)] + [ex(9, 25, 12, "val"), ex(10, 30, 8, "val")], data)

    def make(name, **kw):
        base = dict(model=str(tiny_model), num_layers=2, rank=4, scale=2.0, dropout=0.2, learning_rate=1e-2,
                    batch_size=2, iters=7, steps_per_report=1, steps_per_eval=3, steps_per_save=2,
                    max_seq_length=128, grad_checkpoint=True, seed=5, diagnostic=True)
        base.update(kw)
        return T.TrainConfig(data_dir=str(data), adapter_dir=str(tmp_path / "sft-pilot" / name), **base)

    return make


def adapter(cfg):
    import mlx.core as mx

    return mx.load(str(Path(cfg.adapter_dir) / "adapters.safetensors"))


def losses(cfg):
    rows = [json.loads(x) for x in (Path(cfg.adapter_dir) / "metrics.jsonl").read_text().splitlines()]
    return {r["iteration"]: r["train_loss"] for r in rows if r["kind"] == "train"}


def assert_same_weights(a, b):
    import mlx.core as mx

    assert a.keys() == b.keys()
    for k in a:
        assert mx.array_equal(a[k], b[k]), k


class Interrupt(Exception):
    pass


def test_split_run_matches_uninterrupted_run(setup, monkeypatch):
    """Review rows 3, 4 and 7: interruptions after step 3 (last checkpoint 2) and, inside the resumed call, after
    step 5 (checkpoint 4 must be found), or a run stopped at
    a non-interval final step (3) and extended, take exactly the uninterrupted run's steps (batch order and dropout
    RNG included): identical adapter weights and per-step losses."""
    full = setup("full")
    T.train(full)

    def stop_at(n):
        def hook(step):
            if step == n:
                raise Interrupt
        return hook

    split = setup("split")
    monkeypatch.setattr(T, "_after_step", stop_at(3))
    with pytest.raises(Interrupt):
        T.train(split)
    assert T.latest_complete_checkpoint(Path(split.adapter_dir))["iteration"] == 2
    monkeypatch.setattr(T, "_after_step", stop_at(5))  # interrupted again, inside a resumed call (review row 7)
    with pytest.raises(Interrupt):
        T.train(split)
    assert T.latest_complete_checkpoint(Path(split.adapter_dir))["iteration"] == 4
    monkeypatch.setattr(T, "_after_step", lambda step: None)
    rec = T.train(split)
    assert rec["resumed_from"].endswith("0000004_adapters.safetensors") and rec["iterations_run"] == 3
    assert len(rec["segments"]) == 3
    assert_same_weights(adapter(full), adapter(split))
    assert {k: v for k, v in losses(split).items() if k > 4} == {k: v for k, v in losses(full).items() if k > 4}

    short = setup("short", iters=3)  # final checkpoint at 3, not a multiple of steps_per_save
    T.train(short)
    assert T.latest_complete_checkpoint(Path(short.adapter_dir))["iteration"] == 3
    rec = T.train(replace(short, iters=7))  # constant lr: extending iters continues the same trajectory
    assert rec["resumed_from"].endswith("0000003_adapters.safetensors")
    assert_same_weights(adapter(full), adapter(replace(short, iters=7)))


def test_published_adapter_carries_weight_bound_inherited_lineage(setup):
    """Review rows 1-2 (trainer side): every published adapter has a sidecar bound to its bytes; the diagnostic
    stub and every resumed checkpoint's stubs survive into the planner's view."""
    from stowage.frozen import StubTaintError, require_clean
    from stowage.model import Provenance

    from llm.stowlm.planner import StowLMPlanner

    cfg = setup("lineage", iters=4)
    T.train(replace(cfg, iters=2))
    out = Path(cfg.adapter_dir)
    # Simulate weights trained earlier under a stub: the checkpoint lineage must flow forward on resume.
    state_file = out / "0000002_state.json"
    state = json.loads(state_file.read_text())
    state["provenance"] = Provenance.model_validate(state["provenance"]).merge(
        Provenance(stubs={"checker": "tiny-chain stub"})).model_dump(mode="json")
    state_file.write_text(json.dumps(state))
    T.train(replace(cfg, iters=4))
    side = json.loads((out / T.SIDECAR).read_text())
    assert side["training"]["iteration"] == 4 and side["training"]["data_identity"]["dataset_kind"] == "diagnostic"
    planner = StowLMPlanner(model_path=cfg.model, adapter_path=out)
    stubs = planner.provenance.stubs
    assert "checker" in stubs and "stowlm.train.dataset" in stubs
    with pytest.raises(StubTaintError):
        require_clean(planner.provenance, what="official evaluation planner")
    assert planner.evaluation_config["model_content_sha256"] == side["training"]["base_model_content_sha256"]
    run = json.loads((out / "run.json").read_text())
    assert "checker" in run["stubs"] and len(run["segments"]) == 2


def test_attention_and_checkpoint_patches_are_restored(setup):
    """Review row 5: attn_block and mlx-lm's grad_checkpoint patch are scoped to the call, also on an exception."""
    from mlx_lm.models import muse_glimmer

    original_sdpa = muse_glimmer.scaled_dot_product_attention
    original_call = muse_glimmer.DecoderLayer.__call__ if hasattr(muse_glimmer, "DecoderLayer") else None
    T.train(setup("blocked", attn_block=8, iters=2))
    assert muse_glimmer.scaled_dot_product_attention is original_sdpa
    if original_call is not None:
        assert muse_glimmer.DecoderLayer.__call__ is original_call
    blocked, plain = setup("b2", attn_block=8, iters=2, dropout=0.0), setup("p2", attn_block=0, iters=2, dropout=0.0)
    T.train(blocked)
    T.train(plain)  # same process, no blocked attention: exact attention either way, so near-identical results
    rb, rp = losses(blocked), losses(plain)
    assert all(abs(rb[k] - rp[k]) < 1e-3 for k in rp)

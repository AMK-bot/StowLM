"""Sync, worker and adapter tests. Real-PEFT bf16 tests need torch+peft:
`uv run --with torch --with peft==0.21.2 pytest tests/llm/test_rl_adapter.py`."""
import inspect
import pickle
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from llm.rl import modal_grpo as grpo


class Parameter:
    def __init__(self, value):
        self.value = np.asarray(value, dtype=float)

    def detach(self):
        return self

    def cpu(self):
        return self


class TinyAdapter:
    """Two-token linear policy W+B A with a trainable, nonzero rank-one delta."""
    def __init__(self):
        self.weight = Parameter([[.2, -.1], [-.3, .4]])
        self.a = Parameter([[.5, -.2]])
        self.b = Parameter([[.6], [-.4]])
        self.merged = False
        self.events = []

    def logits(self, x):
        return x @ (self.weight.value + (0 if self.merged else self.b.value @ self.a.value)).T

    def merge_adapter(self):
        self.events.append("merge")
        self.weight.value += self.b.value @ self.a.value
        self.merged = True

    def unmerge_adapter(self):
        self.events.append("unmerge")
        self.weight.value -= self.b.value @ self.a.value
        self.merged = False

    def named_parameters(self):
        return [("base_model.model.head.base_layer.weight", self.weight),
                ("base_model.model.head.lora_A.default.weight", self.a),
                ("base_model.model.head.lora_B.default.weight", self.b),
                ("base_model.model.vision_tower.weight", Parameter([999.]))]


class FakeSGD:
    """Analytic squared-error update on B; no torch, Metal, CUDA or downloads."""
    def __init__(self, model):
        self.model = model

    def step(self, x):
        prediction = self.model.logits(x)
        gradient = np.outer(prediction - np.array([.8, -.2]), self.model.a.value @ x)
        self.model.b.value -= .1 * gradient


class Worker:
    def __init__(self, model):
        self.model = model
        self.weight = model.weight.value.copy()

    def load_weights(self, weights):
        self.model.events.append("load_weights")
        weights = dict(weights)
        assert set(weights) == {"head.weight"}
        self.weight = weights["head.weight"].value.copy()

    def apply_model(self, fn):
        self.model.events.append("apply_model")
        return fn(self)


def install_torch(monkeypatch, model):
    torch = ModuleType("torch")
    def save(weights, path):
        model.events.append("export")
        Path(path).write_bytes(pickle.dumps(weights))
    torch.save = save
    torch.load = lambda path, **kwargs: pickle.loads(Path(path).read_bytes())
    monkeypatch.setitem(sys.modules, "torch", torch)


class Connection:
    def __init__(self, worker):
        self.worker = worker
        self.response = None

    def send(self, request):
        self.response = grpo.apply_exported_weights(self.worker, request["weights"])

    def poll(self, timeout):
        return True

    def recv(self):
        return self.response


def _peft_bf16_policy(torch, peft, b_value=.002):
    """Real PEFT LoRA on a bf16 linear: the dtype where merge/unmerge rounding destroyed the base (review)."""
    class Policy(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.head = torch.nn.Linear(2, 2, bias=False, dtype=torch.bfloat16)
            self.head.weight.data.fill_(1.)
        def forward(self, x):
            return self.head(x)
    model = peft.get_peft_model(Policy(), peft.LoraConfig(r=1, lora_alpha=1, lora_dropout=0,
                                                          target_modules=["head"]))
    layer = model.base_model.model.head
    layer.lora_A["default"].weight.data = torch.ones_like(layer.lora_A["default"].weight)
    layer.lora_B["default"].weight.data = torch.full_like(layer.lora_B["default"].weight, b_value)
    return model, layer


class TorchWorker:
    """Sampler side: a plain bf16 linear that receives exported weights through the production worker path."""
    def __init__(self, torch, weight, events):
        self.torch, self.weight, self.events = torch, weight.detach().clone(), events
    def load_weights(self, weights):
        weights = dict(weights)
        assert set(weights) == {"head.weight"}
        self.events.append("load_weights")
        self.weight = weights["head.weight"].clone()
    def apply_model(self, fn):
        return fn(self)
    def reset_prefix_cache(self):
        self.events.append("reset_prefix_cache")


class TorchConnection:
    def __init__(self, worker):
        self.worker, self.response = worker, None
    def send(self, request):
        self.response = grpo.apply_exported_weights(self.worker, request["weights"])
        self.worker.events.append("ack")
    def poll(self, timeout):
        return True
    def recv(self):
        return self.response


@pytest.mark.slow
def test_sync_never_modifies_bf16_base_over_repeated_cycles_and_updates(tmp_path):
    """Review w2-rev-grpo: PEFT merge/unmerge in bf16 moved the frozen base 1 -> 0.99609375 in one sync."""
    torch = pytest.importorskip("torch")
    peft = pytest.importorskip("peft")
    model, layer = _peft_bf16_policy(torch, peft)
    base = layer.base_layer.weight.detach().clone()
    events = []
    worker = TorchWorker(torch, base, events)
    connection = TorchConnection(worker)
    optimizer = torch.optim.SGD([p for p in model.parameters() if p.requires_grad], lr=.05)
    x = torch.tensor([[.01, .5]], dtype=torch.bfloat16)
    for cycle in range(6):
        if cycle % 2:  # interleave optimizer updates with syncs
            optimizer.zero_grad()
            model(x).float().pow(2).sum().backward()
            optimizer.step()
        grpo.sync_policy_weights(model, connection, time.monotonic() + 5, weight_file=tmp_path / "sync.pt")
        assert torch.equal(layer.base_layer.weight, base), f"base changed at cycle {cycle}"
        a, b = layer.lora_A["default"].weight.detach().float(), layer.lora_B["default"].weight.detach().float()
        expected = (base.float() + (b @ a) * layer.scaling["default"]).to(torch.bfloat16)
        assert torch.equal(worker.weight, expected), "export must be the fp32 merge rounded once"
        assert not (tmp_path / "sync.pt").exists()
        assert not getattr(layer, "merged", False)
    assert events == ["load_weights", "reset_prefix_cache", "ack"] * 6
    # The sampler sees the adapter (nonzero effect), and the trainer's unmerged forward agrees within bf16 rounding.
    plain = torch.nn.functional.linear(x, base)
    sampled = torch.nn.functional.linear(x, worker.weight)
    assert (sampled.float() - plain.float()).abs().max() > 1e-3
    np.testing.assert_allclose(model(x).detach().float().numpy(), sampled.float().numpy(), atol=2e-2)


def test_export_rejects_in_place_merged_adapter():
    torch = pytest.importorskip("torch")
    peft = pytest.importorskip("peft")
    model, layer = _peft_bf16_policy(torch, peft)
    layer.merge()
    with pytest.raises(RuntimeError, match="merged in place"):
        grpo.merged_lora_weights(model)


def test_worker_loads_then_resets_prefix_cache_before_acknowledging(tmp_path, monkeypatch):
    """A stale prefix cache would keep KV computed with the old weights (sampler != trainer behavior)."""
    events = []
    worker = SimpleNamespace(apply_model=lambda fn: fn(worker),
                             load_weights=lambda w: events.append(("load_weights", sorted(dict(w)))),
                             reset_prefix_cache=lambda: events.append("reset_prefix_cache"))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        load=lambda path, **kw: pickle.loads(Path(path).read_bytes())))
    (tmp_path / "w.pt").write_bytes(pickle.dumps({"head.weight": 1}))
    ack = grpo.apply_exported_weights(worker, tmp_path / "w.pt")
    assert events == [("load_weights", ["head.weight"]), "reset_prefix_cache"]
    assert ack["synced"] is True


def test_sync_failure_removes_export_and_leaves_trainer_untouched(tmp_path, monkeypatch):
    model = TinyAdapter()
    install_torch(monkeypatch, model)
    monkeypatch.setattr(grpo, "merged_lora_weights", lambda m: {"head.weight": m.weight})
    original = model.logits(np.ones(2)).copy()
    connection = SimpleNamespace(send=lambda request: (_ for _ in ()).throw(RuntimeError("worker failed")))
    with pytest.raises(RuntimeError, match="worker failed"):
        grpo.sync_policy_weights(model, connection, time.monotonic() + 5, weight_file=tmp_path / "sync.pt")
    np.testing.assert_allclose(model.logits(np.ones(2)), original)
    assert "merge" not in model.events and not model.merged
    assert not (tmp_path / "sync.pt").exists()


def test_sync_rejects_missing_worker_ack(tmp_path, monkeypatch):
    model = TinyAdapter()
    install_torch(monkeypatch, model)
    monkeypatch.setattr(grpo, "merged_lora_weights", lambda m: {"head.weight": m.weight})
    connection = SimpleNamespace(send=lambda request: None, poll=lambda timeout: True, recv=lambda: {})
    with pytest.raises(RuntimeError, match="acknowledge"):
        grpo.sync_policy_weights(model, connection, time.monotonic() + 5, weight_file=tmp_path / "sync.pt")
    assert not model.merged


def test_sync_refuses_empty_export(tmp_path, monkeypatch):
    model = TinyAdapter()
    install_torch(monkeypatch, model)
    monkeypatch.setattr(grpo, "merged_lora_weights", lambda m: {})
    with pytest.raises(RuntimeError, match="no LoRA-wrapped"):
        grpo.sync_policy_weights(model, SimpleNamespace(), time.monotonic() + 5, weight_file=tmp_path / "s.pt")


@pytest.mark.parametrize("sampler", [[float("nan")], [float("inf")], [-1.], []])
def test_agreement_rejects_nonfinite_different_and_missing_logps(sampler):
    with pytest.raises(RuntimeError, match="mismatch"):
        grpo.assert_masked_agreement([0.], sampler)


def test_sft_adapter_initialization_is_local_and_trainable(tmp_path, monkeypatch):
    peft = ModuleType("peft")
    calls = []
    peft.PeftModel = SimpleNamespace(from_pretrained=lambda base, path, **kw: calls.append((base, path, kw)) or "adapted")
    monkeypatch.setitem(sys.modules, "peft", peft)
    base = object()
    assert grpo.initialize_policy_adapter(base, tmp_path) == "adapted"
    assert calls == [(base, str(tmp_path), {"is_trainable": True, "local_files_only": True})]
    assert "sft_adapter" in inspect.signature(grpo.run_gpu_smoke).parameters


def test_fresh_adapter_remains_available_for_base_smoke(monkeypatch):
    peft = ModuleType("peft")
    peft.LoraConfig = lambda **kw: kw
    peft.get_peft_model = lambda base, config: (base, config)
    monkeypatch.setitem(sys.modules, "peft", peft)
    model, config = grpo.initialize_policy_adapter("base")
    assert model == "base"
    assert config["lora_dropout"] == 0
    assert config["r"] == 8


def test_production_rollout_worker_uses_processed_masked_behavior_logps(monkeypatch):
    """The fixed production temperature is one on both sides; no cloud imports."""
    vllm = ModuleType("vllm")
    calls = {}
    class SamplingParams:
        def __init__(self, **kwargs):
            calls["sampling"] = kwargs
            self.temperature = kwargs["temperature"]
    class LLM:
        def __init__(self, **kwargs):
            calls["engine"] = kwargs
        def generate(self, prompts, params):
            logps, _ = grpo.masked_statistics([[999., .4, -.1]], [1], [(1, 2)],
                                             temperature=params.temperature)
            return [SimpleNamespace(outputs=[SimpleNamespace(token_ids=[1],
                       logprobs=[{1: SimpleNamespace(logprob=logps[0])}])])]
    vllm.LLM, vllm.SamplingParams = LLM, SamplingParams
    monkeypatch.setitem(sys.modules, "vllm", vllm)
    # Restore worker-only environment settings when the test exits.
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    monkeypatch.setenv("VLLM_ENABLE_V1_MULTIPROCESSING", "")
    messages = iter([{"group_size": 2, "max_tokens": 4, "seed": 0,
                      "prompt_ids": [2], "spec": {"eos_token_id": 0}}, None])
    responses = []
    conn = SimpleNamespace(recv=lambda: next(messages), send=responses.append, close=lambda: None)
    grpo._rollout_process(conn)
    assert calls["engine"]["logprobs_mode"] == "processed_logprobs"
    assert calls["sampling"]["temperature"] == 1.0
    assert calls["sampling"]["extra_args"] == {grpo.MASK_KEY: {"eos_token_id": 0}}
    assert responses[0] == {"ready": True}
    assert responses[1]["tokens"] == [[1]]
    expected, _ = grpo.masked_statistics([[999., .4, -.1]], [1], [(1, 2)], temperature=1.)
    np.testing.assert_allclose(responses[1]["logps"], [expected])


def test_merge_from_checkpoint_matches_fp32_formula_and_reads_only_targets(tmp_path):
    """Falsifier 4 / 10.1 first sync: merge a PEFT adapter into base checkpoint weights on CPU, no trainer model."""
    torch = pytest.importorskip("torch")
    from safetensors.torch import save_file
    import json as _json
    w = torch.full((2, 2), 1., dtype=torch.bfloat16)
    other = torch.zeros(3, dtype=torch.bfloat16)
    save_file({"model.language_model.layers.0.self_attn.q_proj.weight": w, "lm_head.weight": other},
              str(tmp_path / "shard-1.safetensors"))
    (tmp_path / "model.safetensors.index.json").write_text(_json.dumps({"weight_map": {
        "model.language_model.layers.0.self_attn.q_proj.weight": "shard-1.safetensors",
        "lm_head.weight": "shard-1.safetensors"}}))
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    a, b = torch.ones(1, 2), torch.full((2, 1), .002)
    save_file({"base_model.model.model.language_model.layers.0.self_attn.q_proj.lora_A.weight": a,
               "base_model.model.model.language_model.layers.0.self_attn.q_proj.lora_B.weight": b},
              str(adapter / "adapter_model.safetensors"))
    (adapter / "adapter_config.json").write_text(_json.dumps({"r": 1, "lora_alpha": 2.}))
    out = grpo.merged_weights_from_checkpoint(tmp_path, adapter)
    assert set(out) == {"model.language_model.layers.0.self_attn.q_proj.weight"}
    expected = (w.float() + (b @ a) * 2.).to(torch.bfloat16)
    assert torch.equal(out["model.language_model.layers.0.self_attn.q_proj.weight"], expected)
    (adapter / "adapter_config.json").write_text(_json.dumps({"r": 1, "lora_alpha": 2., "use_dora": True}))
    with pytest.raises(NotImplementedError):
        grpo.merged_weights_from_checkpoint(tmp_path, adapter)

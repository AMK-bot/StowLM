"""CPU-only decode diagnostics: per-rule dead ends and action screen snapshots."""

import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from stowage.feasibility import DeadEnd


@pytest.fixture
def mask_mlx_cpu_actual(monkeypatch):
    """Run the real batching control flow without importing Metal or allocating a GPU."""
    mx = ModuleType("mlx.core")
    for name in ("array", "float32", "where", "take_along_axis", "argmax", "arange", "repeat"):
        setattr(mx, name, getattr(np, name))
    mx.eval = mx.async_eval = lambda *args: None
    mx.random = SimpleNamespace(key=lambda seed: seed, split=lambda key: (key + 1, key + 2))
    parent = ModuleType("mlx")
    parent.core = mx
    monkeypatch.setitem(sys.modules, "mlx", parent)
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    name = "_mask_diagnostics_numpy"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] /
                                                "src/llm/stowlm/mask_mlx.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mask_mlx_cpu(mask_mlx_cpu_actual, monkeypatch):
    monkeypatch.setattr(mask_mlx_cpu_actual, "rule_freeze_v1_enabled", lambda: True)
    return mask_mlx_cpu_actual


class Model:
    def make_cache(self):
        return []

    def __call__(self, ids, cache):
        return np.broadcast_to(np.array([0, 2], dtype=np.float32), (*ids.shape, 2))


class Decoder:
    def __init__(self, rules=(), *, act=False):
        self.status = "running"
        self.rules = rules
        self.dead_end = None
        self.actions = []
        self.act = act
        self.counts = {"expert.e2.weight_order": 3}
        self.engine = SimpleNamespace(depth=0, screen_diagnostics=lambda: {"rejected": self.counts.copy()})

    def allowed_token_ids(self):
        if self.act and not self.actions:
            return (1,)
        self.status = "dead_end"
        self.dead_end = DeadEnd(len(self.actions), "all remaining options rejected", self.rules)
        return ()

    def advance(self, token):
        action = SimpleNamespace(box="B1")
        self.actions.append(action)
        self.engine = SimpleNamespace(depth=1, screen_diagnostics=lambda: {"rejected": {"next-state": 7}})
        return action

    def diagnostics(self):
        dead = None if self.dead_end is None else {"depth": self.dead_end.depth,
                "reason": self.dead_end.reason, "rule_ids": list(self.dead_end.rule_ids)}
        return {"status": self.status, "depth": len(self.actions), "dead_end": dead,
                "dead_ends_by_rule": {rule: 1 for rule in self.rules} if dead else {},
                "screens": self.engine.screen_diagnostics()}


def test_generation_counts_dead_end_sequences_per_rule_once(mask_mlx_cpu):
    decoders = [Decoder(("expert.e2.weight_order", "expert.port.self_blocking")),
                Decoder(("expert.port.self_blocking",)), Decoder()]
    results, stats = mask_mlx_cpu.generate_masked(Model(), [1], decoders, prefetch=False)
    assert [r.status for r in results] == ["dead_end"] * 3
    summary = stats.summary()
    assert summary["dead_ends_by_rule"] == {"expert.e2.weight_order": 1, "expert.port.self_blocking": 2}
    assert summary["dead_ends_unattributed"] == 1
    assert len(summary["decode_trace"]) == 3
    assert results[0].diagnostics["dead_end"]["rule_ids"] == list(decoders[0].rules)
    assert results[0].screen_trace[0]["event"] == "dead_end"
    json.dumps(summary)


def test_action_trace_preserves_queried_state_screens_and_terminal_dead_end(mask_mlx_cpu):
    decoder = Decoder(("expert.port.self_blocking",), act=True)
    results, stats = mask_mlx_cpu.generate_masked(Model(), [1], [decoder], temperature=0, prefetch=False)
    result = results[0]
    assert result.tokens == [1]
    assert [row["event"] for row in result.screen_trace] == ["action", "dead_end"]
    action, terminal = result.screen_trace
    assert action["step"] == 0 and action["depth"] == 1
    assert action["screens"]["rejected"] == {"expert.e2.weight_order": 3}
    assert terminal["dead_end"]["rule_ids"] == ["expert.port.self_blocking"]
    assert stats.summary()["decode_trace"] == [{"sample": 0, **row} for row in result.screen_trace]
    decoder.counts.clear()
    assert action["screens"]["rejected"] == {"expert.e2.weight_order": 3}
    json.dumps(result.screen_trace)


def test_old_decoder_without_diagnostics_keeps_working(mask_mlx_cpu):
    class LegacyDeadEndDecoder:
        status = "dead_end"

        def allowed_token_ids(self):
            return ()

    results, stats = mask_mlx_cpu.generate_masked(Model(), [1], [LegacyDeadEndDecoder()], prefetch=False)
    assert results[0].status == "dead_end"
    assert stats.summary()["dead_ends_unattributed"] == 1


@pytest.mark.parametrize("complete", [False, True])
def test_completion_and_token_limit_never_increment_dead_end_counts(mask_mlx_cpu, complete):
    class LiveDecoder(Decoder):
        def advance(self, token):
            action = super().advance(token)
            if complete:
                self.status = "complete"
            return action

    results, stats = mask_mlx_cpu.generate_masked(Model(), [1], [LiveDecoder(act=True)],
                                                max_tokens=1, temperature=0, prefetch=False)
    assert results[0].status == ("complete" if complete else "max_tokens")
    assert stats.summary()["dead_ends_by_rule"] == {}
    assert stats.summary()["dead_ends_unattributed"] == 0
    assert [row["event"] for row in results[0].screen_trace] == ["action"]


def test_switch_off_preserves_legacy_generation_without_diagnostic_queries(mask_mlx_cpu, monkeypatch):
    monkeypatch.setattr(mask_mlx_cpu, "rule_freeze_v1_enabled", lambda: False)
    decoder = Decoder(act=True)

    def forbidden():
        raise AssertionError("legacy decoding must not query diagnostics")

    decoder.diagnostics = forbidden
    results, stats = mask_mlx_cpu.generate_masked(Model(), [1], [decoder], max_tokens=1,
                                                temperature=0, prefetch=False)
    assert results[0].tokens == [1] and results[0].status == "max_tokens"
    assert results[0].diagnostics == {} and results[0].screen_trace == []
    assert "dead_ends_by_rule" not in stats.summary()
    assert "decode_trace" not in stats.summary()


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "activated"])
@pytest.mark.parametrize("prefetch", [False, True], ids=["no-prefetch", "prefetch"])
def test_host_work_inherits_real_activation_context(mask_mlx_cpu_actual, monkeypatch, enabled, prefetch):
    """The real engine/codec must see the caller's catalogue inside host work."""
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskConfig, MaskedDecoder
    from stowage.actions import get_action_space
    from stowage.actions.samples import tiny_instance
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.rules.catalogue import rule_freeze_v1, rule_freeze_v1_enabled

    module = mask_mlx_cpu_actual
    tokenizer = load_tokenizer()
    observations = []
    prefetched = threading.Event()
    caller = threading.get_ident()
    with rule_freeze_v1(enabled=enabled):
        instance = tiny_instance(3, n_load=2, n_other=0, p40=0)
        instance.vessel.hull = None
        engine = make_feasibility_engine(instance, get_action_space("boxslot"),
                                        config=FeasibilityConfig(extra={"global_screen": 0}))
        codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
        decoder = MaskedDecoder(engine, codec, MaskConfig(lookahead=0))
        for name in ("legal_index", "legal_box_ids"):
            original = getattr(type(engine), name)

            def observe(self, *args, _name=name, _original=original, **kwargs):
                observations.append((_name, rule_freeze_v1_enabled(), threading.get_ident()))
                return _original(self, *args, **kwargs)

            monkeypatch.setattr(type(engine), name, observe)
        original_prefetch = decoder.prefetch

        def observe_prefetch():
            observations.append(("prefetch", rule_freeze_v1_enabled(), threading.get_ident()))
            try:
                return original_prefetch()
            finally:
                prefetched.set()

        monkeypatch.setattr(decoder, "prefetch", observe_prefetch)
        if prefetch:
            # Hold the stand-in forward until host prefetch runs; avoid depending
            # on CPU scheduling to exercise the thread's second responsibility.
            def await_host(*args):
                assert prefetched.wait(10), "host did not reach prefetch"
            monkeypatch.setattr(module.mx, "async_eval", await_host)

        class RealCodecModel(Model):
            def __call__(self, ids, cache):
                return np.zeros((*ids.shape, tokenizer.get_vocab_size()), dtype=np.float32)

        results, stats = module.generate_masked(RealCodecModel(), [1], [decoder],
                                                max_tokens=1, temperature=0, prefetch=prefetch)
        assert rule_freeze_v1_enabled() is enabled
        assert results[0].status == "max_tokens" and len(results[0].tokens) == 1
        assert stats.screen_diagnostics_enabled is enabled
        host = [(name, active) for name, active, thread in observations if thread != caller]
        assert host and all(active is enabled for _, active in host), observations
        assert ("legal_box_ids" if enabled else "legal_index", enabled) in host
        assert decoder._stack[0].lazy is enabled
        if prefetch:
            assert ("prefetch", enabled) in host

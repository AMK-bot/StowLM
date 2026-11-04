"""Exact maskspeed replay guards use only local tokenizer/CPU implementations."""
import os
from pathlib import Path

import pytest

_environment = dict(os.environ)
try:
    from llm.maskspeed.replay import (HERE, BaselineTraceUnreplayable, LayerTimer,
                                     assert_equal, main, replay_trace,
                                     runtimes, saved_traces, small_maps)
finally:
    os.environ.clear()
    os.environ.update(_environment)
    del _environment


@pytest.mark.parametrize("seed", [0, 1, 4])
def test_frozen_and_live_full_tiny_trace_backtrack_and_prefetch(seed):
    old, new = runtimes()
    row = replay_trace(dict(id=f"test-tiny-{seed}", kind="tiny", seed=seed,
                            lookahead=8 if seed % 2 == 0 else 0), old, new)
    assert row["exact"] and row["steps"] > 30
    assert row["status"] in ("complete", "dead_end")
    assert row["backtracks"] == 1
    assert row["prefetch_probes"] > 0


def test_saved_model_traces_deduplicate_and_keep_dead_ends():
    traces = list(saved_traces())
    assert len(traces) >= 100
    assert {t["expected_status"] for t in traces} >= {"complete", "dead_end"}
    keys = [(t["map"], repr(t["actions"])) for t in traces]
    assert len(keys) == len(set(keys))
    assert len({t["map"] for t in traces}) >= 6


def test_six_small_train_maps_span_three_feeder_classes():
    maps = small_maps()
    assert len(maps) == len(set(maps)) == 6
    assert {p.parent.parent.name for p in maps} == {"feeder_500", "feeder_800", "feeder_1700"}
    assert all("train" in p.parts and p.exists() for p in maps)


def test_guard_rejects_token_difference_even_when_actions_match():
    class Decoder:
        status = "running"
        actions = []
        def __init__(self, legal):
            self.legal = legal
        def allowed_token_ids(self):
            return self.legal
    with pytest.raises(AssertionError, match="legal IDs differ"):
        assert_equal(Decoder((1, 2)), Decoder((1, 3)), trace_id="injected", step=7)


def test_layer_timer_restores_method_after_exception():
    old, _ = runtimes()
    original = old.mask.MaskedDecoder.allowed_token_ids
    timer = LayerTimer()
    with pytest.raises(RuntimeError, match="injected"):
        with timer.active(old):
            assert old.mask.MaskedDecoder.allowed_token_ids is not original
            raise RuntimeError("injected")
    assert old.mask.MaskedDecoder.allowed_token_ids is original


def test_cli_rejects_unpinned_interpreter(monkeypatch):
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    monkeypatch.setattr("sys.argv", ["replay", "--mode", "guard", "--saved", "0", "--tiny", "0"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_historical_baseline_rejection_is_distinct_from_old_new_mismatch(monkeypatch):
    from types import SimpleNamespace
    import llm.maskspeed.replay as replay
    class Decoder:
        status = "running"
        actions = []
        def allowed_token_ids(self):
            return (1,)
    codec = SimpleNamespace(encode_action=lambda action:(999,))
    monkeypatch.setattr(replay, "instance_for", lambda trace:object())
    monkeypatch.setattr(replay, "codec_for", lambda instance:codec)
    monkeypatch.setattr(replay, "make_decoder", lambda *args, **kwargs:Decoder())
    trace = dict(id="historical", kind="small", expected_status="complete",
                 actions=[dict(op="P", box="test", slot="0010102")])
    with pytest.raises(BaselineTraceUnreplayable, match="rejected by both arms"):
        replay_trace(trace, None, None)

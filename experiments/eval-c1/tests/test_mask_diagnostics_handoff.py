"""5.1 per-rule dead-end diagnostics through the pool and into the run folder (w2-l5-mask handoff).

    uv run pytest -q -p no:cacheprovider experiments/eval-c1/tests/test_mask_diagnostics_handoff.py -o addopts=""
"""

import sys
import types
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
TINY = HERE.parents[1] / "experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard/tiny/seed-110280000.json"
RULE = "expert.e2.weight_order"


class Transport:
    """Worker stand-in: an action-completing token (1), then a dead end attributed to RULE."""

    def __init__(self):
        self.advanced = False

    def _send(self, worker, message):
        if message[0] == "advance":
            self.advanced = True

    def _await(self, worker, row, kind="allowed"):
        if self.advanced:
            return {"allowed": (), "status": "dead_end", "depth": 1, "boundary": False, "completing": (),
                    "dead_end": {"depth": 1, "reason": "kg order", "rule_ids": [RULE]}}
        return {"allowed": (1,), "status": "running", "depth": 0, "boundary": True,
                "screens": {"rejected": {RULE: 7}}, "completing": (), "action_tokens": (1,), "dead_end": None}


def test_proxy_reports_actions_dead_end_rules_and_source_screens():
    from pool import RemoteDecoder

    d = RemoteDecoder(Transport(), 0, 0)
    assert d.allowed_token_ids() == (1,)
    source = d.engine
    assert d.advance(1)  # truthy marker: the token completed an action in the worker
    assert source.screen_diagnostics() == {"rejected": {RULE: 7}} and d.diagnostics()["depth"] == 1
    assert d.allowed_token_ids() == () and d.status == "dead_end"
    diag = d.diagnostics()
    assert diag["dead_end"]["rule_ids"] == [RULE] and diag["dead_ends_by_rule"] == {RULE: 1}


@pytest.fixture
def generate(monkeypatch):
    mx = types.ModuleType("mlx.core")
    mx.array = np.array
    mx.async_eval = lambda x: None
    mx.eval = lambda *x: None
    mx.random = types.SimpleNamespace(key=lambda x: x, split=lambda x: (x + 1, x + 2))
    mlx = types.ModuleType("mlx")
    mlx.core = mx
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.delitem(sys.modules, "llm.stowlm.mask_mlx", raising=False)
    from llm.stowlm import mask_mlx

    monkeypatch.setattr(mask_mlx, "masked_sample",
                        lambda logits, idx, valid, temperature, key: (idx[:, 0], np.zeros(len(idx))))
    yield mask_mlx.generate_masked
    sys.modules.pop("llm.stowlm.mask_mlx", None)


class Model:
    def make_cache(self):
        return []

    def __call__(self, x, cache=None):
        return np.zeros((x.shape[0], x.shape[1], 6))


def test_generation_summary_attributes_pooled_dead_ends_by_rule(generate):
    """Before: proxies returned no action and no rule ids, so the mask counted the dead end as unattributed."""
    from stowage.rules.catalogue import rule_freeze_v1

    from decode_trace import RecordingDecoder
    from pool import RemoteDecoder

    with rule_freeze_v1(enabled=True):
        decoder = RecordingDecoder(RemoteDecoder(Transport(), 0, 0))
        results, stats = generate(Model(), [0], [decoder], max_tokens=10, temperature=0)
    summary = stats.summary()
    assert results[0].status == "dead_end"
    assert summary["dead_ends_by_rule"] == {RULE: 1} and summary["dead_ends_unattributed"] == 0
    events = [(r["event"], (r.get("screens") or {}).get("rejected")) for r in summary["decode_trace"]]
    assert events[0] == ("action", {RULE: 7}) and events[-1][0] == "dead_end"  # the mask may add label keys


def test_trace_merge_adds_screens_and_terminal_dead_end_rows():
    from decode_trace import merge_mask_trace

    rows = [{"step": 0, "chosen": {"action_id": {"box": "B"}, "text": "a"}},
            {"step": 1, "chosen": {"action_id": None, "text": "<partial action: dead_end> t", "partial": True}}]
    mask = [{"sample": 0, "event": "action", "screens": {"rejected": {RULE: 2}}},
            {"sample": 0, "event": "dead_end", "depth": 1, "dead_end": {"rule_ids": [RULE]},
             "dead_ends_by_rule": {RULE: 1}, "screens": {"rejected": {RULE: 9}}}]
    merged = merge_mask_trace(rows, mask)
    assert [r["event"] for r in merged] == ["action", "partial", "dead_end"]
    assert merged[0]["screens"] == {"rejected": {RULE: 2}} and merged[2]["dead_ends_by_rule"] == {RULE: 1}
    root = merge_mask_trace([], [mask[1]])  # a dead end before any token still gets its attributed row
    assert len(root) == 1 and root[0]["event"] == "dead_end" and root[0]["step"] == 0


def test_manifest_aggregates_dead_ends_by_rule():
    from run_folder import aggregate_dead_ends

    agg = aggregate_dead_ends([{"n_dead_end": 3, "gen_dead_ends_by_rule": {RULE: 2, "c1a": 1},
                                "gen_dead_ends_unattributed": 0},
                               {"n_dead_end": 1, "gen_dead_ends_by_rule": {RULE: 1}, "gen_dead_ends_unattributed": 1}])
    assert agg["by_rule"] == {RULE: 3, "c1a": 1} and agg["unattributed"] == 1 and agg["dead_end_samples"] == 4
    assert aggregate_dead_ends([{"n_dead_end": 2}])["unattributed"] is None  # legacy path: not collected


def test_worker_payload_marks_action_completing_tokens():
    from pool import action_tokens, reply_payload

    leaf = types.SimpleNamespace(group=None, action="A", children={})
    head = types.SimpleNamespace(group="g", action=None, children={})
    mid = types.SimpleNamespace(group=None, action=None, children={9: leaf})
    node = types.SimpleNamespace(children={1: leaf, 2: head, 3: mid})
    engine = types.SimpleNamespace(depth=4, screen_diagnostics=lambda: {"rejected": {RULE: 1}})
    d = types.SimpleNamespace(status="running", _node=node, _prefix=[], dead_end=None, n_legal=3, engine=engine,
                              _view=lambda: types.SimpleNamespace(complete=False))
    assert action_tokens(d, (1, 2, 3)) == (1,)
    payload = reply_payload(d, (1, 2, 3), physically_possible=lambda e: 5)
    assert payload["action_tokens"] == (1,) and payload["screens"] == {"rejected": {RULE: 1}}
    assert payload["depth"] == 4 and payload["boundary"] is True
    assert "n_legal" not in payload  # no exact count inside the batched decode (logic-mask High 2)


@pytest.mark.skipif(not TINY.exists(), reason="pilot-d58 tiny input missing")
def test_real_workers_follow_the_parent_switch_and_report_the_same_dead_ends():
    """Spawned workers inherit the parent's rule_freeze_v1 override (a context doesn't cross spawn); allowed sets
    and per-sample dead ends match the in-thread decoder."""
    import stowage.interfaces  # noqa: F401
    from stowage.frozen import stub_override
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1

    from host_bench import rollout
    from pool import STUB_REASON, c2_off_engine

    instance = load_instance(TINY)
    with rule_freeze_v1(enabled=True), stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON):
        ref, trace = rollout(instance, batch=4, workers=0, max_steps=10_000, seed=5, c2=False)
        got, _ = rollout(instance, batch=4, workers=2, max_steps=10_000, seed=5, c2=False, check_against=trace)
    assert got["statuses"] == ref["statuses"]
    assert [None if e is None else e["rule_ids"] for e in got["dead_ends"]] == \
        [None if e is None else e["rule_ids"] for e in ref["dead_ends"]]

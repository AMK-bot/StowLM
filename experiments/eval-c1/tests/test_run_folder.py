"""Run-folder contract (experiments/fleet/orders/w2-stowlm-run-contract.md) with a tiny stub decoder and a numpy
"model" (no MLX): trace rows, unmasked top-5 with legal flags, masked log-probs, manifest keys, result.json.

    uv run pytest -q -p no:cacheprovider experiments/eval-c1/tests/test_run_folder.py -o addopts=""
"""

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from decode_trace import RecordingDecoder, RecordingFactory, build_trace, score_positions  # noqa: E402

V = 12  # vocabulary: 0..11; tokens 1-3 = box ids, 4-6 = slot ids, 9 = end of plan


class StubDecoder:
    """Two actions of two tokens each (box, slot), then end-of-plan; MaskedDecoder's boundary convention."""

    PLAN = [((1, 2), (4, 5)), ((2,), (6,))]  # allowed sets per token: action 0 then action 1

    def __init__(self):
        self._prefix, self.status, self.dead_end, self.tokens, self._done = [], "running", None, [], []
        self.n_legal = 3

    def _position(self):
        return len(self.tokens)

    def allowed_token_ids(self):
        sets = [s for action in self.PLAN for s in action] + [(9,)]
        return sets[self._position()] if self._position() < len(sets) else ()

    def advance(self, token):
        self.tokens.append(token)
        self._prefix.append(token)
        if len(self._prefix) == 2 or token == 9:
            self._done.append(tuple(self._prefix))
            self._prefix = []
            self.n_legal = 1 if len(self._done) == 1 else 0
        if token == 9:
            self.status = "complete"

    def prefetch(self):
        return False

    @property
    def actions(self):
        return [SimpleNamespace(box=f"B{a[0]}", slot=f"S{a[1]}") for a in self._done if a != (9,)]


def run_stub(chosen=(2, 4, 2, 6, 9)):
    ticks = iter(range(100))
    rec = RecordingDecoder(StubDecoder(), clock=lambda: next(ticks) * 1e-3)
    for tok in chosen:
        rec.allowed_token_ids()
        rec.advance(tok)
    return rec


def fake_logits(n):
    rng = np.random.default_rng(0)
    logits = rng.normal(size=(n, V)).astype(np.float32)
    logits[0, 7] = 9.0  # the model's favourite first token is 7, which the mask forbids
    return logits


def test_score_positions_matches_direct_softmax():
    logits = fake_logits(5)
    tokens, sets = [2, 4, 2, 6, 9], [(1, 2), (4, 5), (2,), (6,), (9,)]
    out = score_positions(np, logits, tokens, sets, temperature=2.0)
    scaled = logits / 2.0
    logp = scaled - np.log(np.exp(scaled).sum(-1, keepdims=True))
    np.testing.assert_allclose(out["chosen_lp"], logp[np.arange(5), tokens], rtol=1e-5)
    masked0 = scaled[0, 2] - np.log(np.exp(scaled[0, [1, 2]]).sum())
    assert math.isclose(out["masked_lp"][0], masked0, rel_tol=1e-5)
    assert out["masked_lp"][2] == pytest.approx(0.0, abs=1e-6)  # one allowed token: probability 1 under the mask
    assert set(out["top_ids"][0]) == set(np.argsort(-logits[0])[:5])


# Stub token texts in the codec's shape: opcode + 3-digit box index merged ("P002"), then a slot field (" 004").
TEXT = {1: "P001", 2: "P002", 3: "P003", 4: " 004", 5: " 005", 6: " 006", 7: "{", 9: "<|end|>"}


def token_text(t):
    return TEXT.get(int(t), f"t{t}")


def test_trace_rows_follow_the_contract():
    rec = run_stub()
    logits = fake_logits(len(rec.steps))
    scores = score_positions(np, logits, [s["token"] for s in rec.steps], [s["allowed"] for s in rec.steps], 1.0)
    instance = SimpleNamespace(load_list=["B1", "B2"])
    exact = [{"n_legal": 3, "n_possible": 4, "box": 3, "bay": 2}, {"n_legal": 1, "n_possible": 1, "box": 1, "bay": 1}]
    rows = build_trace(instance, rec, scores, decode_token=lambda ids: " ".join(f"t{i}" for i in ids),
                       encode_action=lambda a: {"box": a.box, "slot": a.slot}, n_slots=10,
                       token_text=token_text, exact=exact)
    assert [r["step"] for r in rows] == [0, 1, 2]
    a0, a1, eop = rows
    assert (a0["n_legal"], a0["n_place_pairs_raw"]) == (3, 20) and (a1["n_legal"], a1["n_place_pairs_raw"]) == (1, 10)
    assert [(d["field"], d["position"], d["n_allowed"], d["n_legal"]) for d in a0["decisions"]] == \
        [("box", 0, 2, 3), ("bay", 1, 2, 2)]
    assert a0["top_k_field"] == "box" and a0["top_k"] == a0["decisions"][0]["top_k"]
    assert a0["decisions"][0]["overridden"] is True  # the model's favourite first token (7) is illegal
    assert a0["format_override"] is None  # no separate opcode token in this stub
    assert a0["chosen"] == {"action_id": {"box": "B2", "slot": "S4"}, "text": "t2 t4"}
    assert eop["chosen"]["action_id"] is None and eop["chosen"]["text"].startswith("<end of plan>")
    assert eop["chosen"].get("end_of_plan") is True
    keys = {"step", "n_legal", "n_actions", "chosen", "chosen_logprob", "top_k", "mask_ms", "decode_ms"}
    assert keys <= set(a0)
    assert len(a0["top_k"]) == 5 and a0["top_k"][0]["text"] == "t7" and a0["top_k"][0]["legal"] is False
    assert [t["logprob"] for t in a0["top_k"]] == sorted((t["logprob"] for t in a0["top_k"]), reverse=True)
    assert all(t["legal"] == (t["text"] in ("t1", "t2")) for t in a0["top_k"])
    assert a0["chosen_logprob"] == pytest.approx(float(scores["masked_lp"][0] + scores["masked_lp"][1]))
    assert a0["chosen_logprob"] > a0["chosen_logprob_unmasked"]  # masking renormalises over legal tokens
    assert a0["decode_ms"] > 0 and a1["decode_ms"] > 0


def test_recording_factory_is_transparent():
    factory = RecordingFactory(lambda instance, engine, codec, cfg, n: [StubDecoder() for _ in range(n)])
    decoders = factory(None, SimpleNamespace(n_slots=10), None, None, 2)
    assert factory.n_slots == 10 and len(decoders) == 2 and factory.last == decoders
    assert decoders[0].allowed_token_ids() == (1, 2) and decoders[0].status == "running"
    decoders[0].advance(1)
    assert decoders[0].tokens == [1] and decoders[0].inner.tokens == [1]


def test_manifest_and_instance_files(tmp_path):
    from llm.stowlm.planner import StowLMPlanner
    from run_folder import build_manifest, export_harness_records, write_instance, write_manifest

    model = tmp_path / "model"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"w")
    (model / "config.json").write_text(json.dumps({"quantization": {"bits": 4, "group_size": 64}}))
    manifest = build_manifest(StowLMPlanner(model_path=model, n_samples=8, temperature=1.0), temperature=1.0,
                              n_samples=8, seed=0, mask_workers=4, c2=True, epoch_id="e1",
                              instance_root_sha256="r" * 64, mode="official", argv=["run_c1.py", "--x"])
    required = {"planner_kind", "base_model", "base_model_revision", "quantisation", "adapter_sha256",
                "adapter_lineage", "runtime", "mask_version", "feasibility_version", "checker_version",
                "prompt_version", "format_version", "epoch_id", "instance_root_sha256", "decoding", "created_at",
                "command"}
    assert required <= set(manifest)
    assert manifest["planner_kind"] == "base_glimmer_mask" and manifest["adapter_sha256"] is None
    assert manifest["quantisation"] == "mlx-4bit-g64" and len(manifest["base_model_revision"]) == 64
    assert {"temperature", "top_p", "samples_per_instance", "seed", "pool_size", "c2"} <= set(manifest["decoding"])
    write_manifest(tmp_path / "run", manifest)
    write_instance(tmp_path / "run", "inst-1", {"status": "ok", "plan": {"moves": []}}, None)
    folder = tmp_path / "run" / "instances" / "inst-1"
    assert json.loads((folder / "result.json").read_text())["status"] == "ok"
    assert (folder / "trace.jsonl").read_text() == ""
    (tmp_path / "run" / "instances" / "spec-a.json").write_text(json.dumps(
        {"instance_id": "inst-2", "result": {"status": "ok"}, "report": {"passed": True}}))
    assert export_harness_records(tmp_path / "run") == 1
    assert json.loads((tmp_path / "run" / "instances" / "inst-2" / "report.json").read_text())["passed"] is True


def trace_for(rec, temperature=1.0):
    logits = fake_logits(len(rec.steps))
    scores = score_positions(np, logits, [s["token"] for s in rec.steps], [s["allowed"] for s in rec.steps],
                             temperature)
    return build_trace(SimpleNamespace(load_list=["B1", "B2"]), rec, scores,
                       decode_token=lambda ids: " ".join(f"t{i}" for i in ids),
                       encode_action=lambda a: {"box": a.box, "slot": a.slot}, n_slots=10)


def test_partial_action_is_not_an_end_of_plan():
    """Code review Low (decode_trace:157): an unfinished action (timeout / max tokens) is a partial row."""
    rows = trace_for(run_stub(chosen=(2,)))
    assert len(rows) == 1 and rows[0]["chosen"].get("partial") is True
    assert "<end of plan>" not in rows[0]["chosen"]["text"] and "running" in rows[0]["chosen"]["text"]
    rows = trace_for(run_stub(chosen=(2, 4, 2)))  # one full action, then a partial one
    assert rows[0]["chosen"]["action_id"] is not None and rows[1]["chosen"].get("partial") is True


def test_greedy_replay_uses_the_deterministic_behaviour_logprob():
    """Code review Medium (decode_trace:104): T = 0 scores the first allowed maximum 0 and anything else -inf,
    like mask_mlx._behavior_logprobs, instead of substituting T = 1."""
    logits = np.array([[0., 1., 2., 3., 4., 5.], [0., 1., 2., 3., 5., 5.]])
    out = score_positions(np, logits, [5, 5], [(4, 5), (4, 5)], temperature=0)
    assert out["masked_lp"][0] == 0.0  # 5 is the allowed maximum
    assert out["masked_lp"][1] == -math.inf  # tie: the first maximum in allowed order (4) is the greedy choice
    assert float(out["display_temperature"]) == 1.0
    assert score_positions(np, logits[:1], [5], [(4, 5)], temperature=1)["masked_lp"][0] == pytest.approx(
        5 - np.log(np.exp(4) + np.exp(5)))


def test_override_rate_covers_all_samples_and_dropouts():
    """Logic review: the mask-override rate is over all k samples' decode positions (batched pass), with rows
    mapped to samples as generate_masked keeps them (finished samples drop out of the batch)."""
    from decode_trace import override_stats

    long, short = run_stub(chosen=(2, 4, 2, 6, 9)), run_stub(chosen=(1, 5))
    short.allowed_token_ids()  # sample 2 is asked once more, then stops (e.g. dead end)
    short._pending["allowed"] = ()
    # step t rows: [sample0, sample1] while both are queried, then sample0 only
    argmax = [[7, 1], [4, 5], [7, 3], [6], [9]]  # sample 1 is still in the step-2 batch, dropped after it
    stats = override_stats([long, short], [[0]] + argmax, token_text=token_text)  # leading prefill chunk dropped
    assert stats["mask_override_token_positions"] == 7
    assert stats["mask_override_rate_all_tokens"] == pytest.approx(2 / 7)  # step 0 sample 0 and step 2 sample 0
    # decisions = box and slot tokens only (the end-of-plan token is not a decision): long 2 box + 2 bay, short 1 + 1
    assert stats["mask_override_decision_positions"] == 6
    assert stats["mask_override_rate_decision"] == pytest.approx(2 / 6)
    assert stats["mask_override_positions_by_field"] == {"box": 3, "bay": 3, "row": 0, "tier": 0}
    assert stats["mask_override_rate_by_field"]["box"] == pytest.approx(2 / 3)
    assert stats["mask_override_rate_by_field"]["bay"] == 0.0
    assert "override_error" in override_stats([long, short], [[1, 2, 3]])  # wrong batch shape is reported


def test_physically_possible_counts_length_class_matches():
    from decode_trace import physically_possible

    T = SimpleNamespace(slot_n=np.array([1, 2, 1, 1, 2]), box_ids=["a", "b", "c"])
    engine = SimpleNamespace(T=T, _ensure=lambda: None, _open_slots=lambda st: np.array([0, 1, 2, 4]),
                             _liftable=lambda st: np.array([True, True, False]),
                             _attr={"a": (0, 1), "b": (0, 2), "c": (0, 1)})
    assert physically_possible(engine) == 2 + 2  # box a (20 ft): slots 0, 2; box b (long): slots 1, 4
    assert physically_possible(None) is None


def test_override_screen_mix_comes_from_engine_diagnostics_at_overridden_states():
    from decode_trace import override_stats

    class Engine:
        def __init__(self, counts):
            self.counts = counts

        def screen_diagnostics(self):
            return {"rejected": self.counts}

    rec = run_stub(chosen=(2, 4))
    rec.steps[0]["screens"] = {"expert.e2.weight_order": 5, "c1a": 1}
    stats = override_stats([rec], [[7], [4]], token_text=token_text)  # box (step 0) overridden, bay legal
    assert stats["override_state_screen_rejections"] == {"expert.e2.weight_order": 5, "c1a": 1}
    assert stats["decision_state_screen_rejections"] == {"expert.e2.weight_order": 5, "c1a": 1}
    from decode_trace import _screen_rejections
    assert _screen_rejections(SimpleNamespace(engine=Engine({"x": 2}))) == {"x": 2}
    assert _screen_rejections(SimpleNamespace()) is None



def test_opcode_overrides_are_format_overrides_not_decisions():
    """logic-mask High 1: with a separate opcode token, its override goes to format_override and is excluded from
    the decision rate; the box index and bay/row/tier first tokens are the decisions."""
    from decode_trace import token_fields

    assert token_fields(["P", "002", " ", "003", "02", "82", "\n"]) == \
        ["opcode", "box", "sep", "bay", "row", "tier", "newline"]
    assert token_fields(["P", "002", " 003", "0282", "\n"]) == ["opcode", "box", "bay", "row", "newline"]
    assert token_fields(["<|end|>", "x"]) == ["eop", "eop"]
    assert token_fields(["D", "002", "\n"]) == ["opcode", "box", "newline"]


def test_time_limit_is_its_own_outcome(tmp_path):
    """Leader decision (logic-mask): a draft that hit --time-limit is "mask time limit", not a model failure, even
    if 5.7 repair later delivered a plan; outcome.json per instance and counts in the manifest."""
    from run_folder import MASK_TIME_LIMIT, count_outcomes, instance_outcome, write_instance

    timed_out = {"status": "ok", "repaired": True, "metrics": {"repair_original_metrics": {
        "n_samples": 8, "deadline_exceeded": True}}}
    got = instance_outcome(timed_out, {"passed": True})
    assert {k: got[k] for k in ("outcome", "repaired", "checker_passed", "delivered_after_time_limit")} == \
        {"outcome": MASK_TIME_LIMIT, "repaired": True, "checker_passed": True, "delivered_after_time_limit": True}
    assert instance_outcome({"status": "timeout", "metrics": {"n_samples": 8}}, None)["outcome"] == MASK_TIME_LIMIT
    assert instance_outcome({"status": "ok", "metrics": {"n_samples": 8}}, {"passed": True})["outcome"] == "own pass"
    assert instance_outcome({"status": "ok", "repaired": True, "metrics": {"repair_original_metrics": {
        "n_samples": 8, "n_complete": 3}}}, {"passed": True})["outcome"] == "repaired pass"
    assert instance_outcome({"status": "error", "repaired": True, "metrics": {"repair_original_metrics": {
        "n_samples": 8, "n_complete": 0, "n_dead_end": 8}}}, {"passed": False})["outcome"] == "dead end"
    assert instance_outcome({"status": "ok", "metrics": {"n_samples": 8, "n_complete": 8}},
                            {"passed": False})["outcome"] == "checker fail"
    write_instance(tmp_path, "a", timed_out, {"passed": True})
    write_instance(tmp_path, "b", {"status": "ok", "metrics": {"n_samples": 8}}, {"passed": True})
    assert count_outcomes(tmp_path) == {MASK_TIME_LIMIT: 1, "own pass": 1}


def test_decode_loop_never_asks_for_the_exact_legal_count():
    """logic-mask High 2: the exact count (~1.3 s/action at full) is out of the batched decode."""
    class NoCount(StubDecoder):
        @property
        def n_legal(self):
            raise AssertionError("exact legal count requested inside the decode loop")

        @n_legal.setter
        def n_legal(self, value):
            pass

    rec = RecordingDecoder(NoCount())
    for tok in (2, 4, 2, 6, 9):
        rec.allowed_token_ids()
        rec.advance(tok)
    assert [s["boundary"] for s in rec.steps] == [True, False, True, False, True]
    assert all("n_legal" not in s for s in rec.steps)


def test_exact_counts_narrow_with_each_decision():
    """Exact counts, replay only: all legal actions -> slots for the chosen box -> its bay -> bay and row."""
    from decode_trace import exact_counts

    class Engine:
        def __init__(self):
            self.T = SimpleNamespace(box_ids=["A", "B"], slot_ids=["0010202", "0010204", "0010402", "0030202"])
            self.pushed = []

        def legal_pairs(self):
            return np.array([0, 0, 0, 0, 1]), np.array([0, 1, 2, 3, 0])

        def push(self, action):
            self.pushed.append(action)

    engine = Engine()
    rows = exact_counts(engine, [SimpleNamespace(box="A", slot="0010204")])
    assert {k: rows[0][k] for k in ("n_legal", "box", "bay", "row", "tier")} == \
        {"n_legal": 5, "box": 5, "bay": 4, "row": 3, "tier": 2}
    assert len(engine.pushed) == 1


def test_outcome_summary_reports_pass_at_1_beside_best_of_k_and_time_limits_apart(tmp_path):
    """D76 + leader decision: per instance, pass@1 (sample 0, no selection) beside best-of-k; the summary counts
    mask time limits apart from model failures (checker fail, dead end, error)."""
    from run_folder import outcome_summary, write_instance

    def result(**draft):
        return {"status": "ok", "repaired": False, "metrics": {"n_samples": 8, **draft}}
    write_instance(tmp_path, "a", result(pass_at_1={"checker_passed": True}, n_checker_feasible=3), {"passed": True})
    write_instance(tmp_path, "b", result(pass_at_1={"checker_passed": False}, n_checker_feasible=1), {"passed": True})
    write_instance(tmp_path, "c", result(pass_at_1={"checker_passed": False}, n_checker_feasible=0, n_complete=8),
                   {"passed": False})
    write_instance(tmp_path, "d", {"status": "timeout", "metrics": {"n_samples": 8, "deadline_exceeded": True,
                                                                     "pass_at_1": {"checker_passed": None}}}, None)
    s = outcome_summary(tmp_path)
    assert (s["instances"], s["k"], s["pass_at_1"], s["pass_at_1_unchecked"]) == (4, 8, 1, 1)
    assert s["best_of_k_checker_feasible"] == 2 and s["own_pass"] == 2
    assert s["mask_time_limit"] == 1 and s["model_failures"] == 1
    assert json.loads((tmp_path / "instances" / "b" / "outcome.json").read_text())["pass_at_1"] is False


def test_deadline_after_a_complete_decode_is_a_finisher_time_limit():
    from run_folder import FINISHER_TIME_LIMIT, MASK_TIME_LIMIT, instance_outcome

    complete = {"status": "timeout", "repaired": False,
                "metrics": {"n_samples": 1, "n_complete": 1, "deadline_exceeded": True}}
    assert instance_outcome(complete, None)["outcome"] == FINISHER_TIME_LIMIT
    mid_decode = {"status": "timeout", "repaired": False,
                  "metrics": {"n_samples": 1, "n_complete": 0, "deadline_exceeded": True}}
    assert instance_outcome(mid_decode, None)["outcome"] == MASK_TIME_LIMIT


def test_finisher_budget_timeout_is_its_own_outcome():
    from run_folder import FINISHER_TIME_LIMIT, instance_outcome

    r = {"status": "ok", "repaired": False, "metrics": {"n_samples": 1, "n_complete": 1, "n_checker_feasible": 0,
                                                         "finisher_budget_timeouts": [0],
                                                         "candidate_failures": {"0": "ballast deadline"}}}
    assert instance_outcome(r, {"passed": False})["outcome"] == FINISHER_TIME_LIMIT
    r["metrics"]["candidate_failures"] = {"0": "ballast deadline", "1": "full checker rejected candidate"}
    assert instance_outcome(r, {"passed": False})["outcome"] == "checker fail"  # another candidate failed the checker

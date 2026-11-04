"""CPU-only mocks for the pinned CUDA orchestration; no cloud client is created."""
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
import json
import sys

import numpy as np
import pytest

from llm.rl import modal_grpo as grpo


def test_manifest_pins_source_rules_versions_and_stack(tmp_path, monkeypatch):
    (tmp_path / "rule.toml").write_text("version=1")
    (tmp_path / "a.py").write_text("x=1")
    manifest = grpo.PinManifest.capture("a" * 40, tmp_path)
    grpo.verify_manifest(manifest, tmp_path)
    with pytest.raises(RuntimeError, match="dependency"):
        grpo.verify_manifest(replace(manifest, stack={}), tmp_path)
    monkeypatch.setattr(grpo, "current_versions", lambda: {"checker": "new"})
    with pytest.raises(RuntimeError, match="pinned"):
        grpo.verify_manifest(manifest, tmp_path)
    monkeypatch.undo()
    (tmp_path / "rule.toml").write_text("version=2")
    with pytest.raises(RuntimeError, match="pinned"):
        grpo.verify_manifest(manifest, tmp_path)
    with pytest.raises(ValueError):
        grpo.PinManifest.capture("main", tmp_path)


def test_spend_cap_and_resume_are_bounded():
    budget = grpo.SpendCap(3, 1, .01)
    assert budget.timeout_s == 200
    budget.authorize(200)
    with pytest.raises(RuntimeError, match="spend cap"):
        budget.authorize(201)
    budget.charge(100)
    assert budget.spent_dollars == 2
    assert budget.timeout_s == 100
    for invalid in (float("inf"), float("nan"), -1):
        with pytest.raises(ValueError):
            grpo.SpendCap(invalid)
        with pytest.raises(ValueError):
            budget.authorize(invalid)
    with pytest.raises(ValueError):
        grpo.SpendCap(1, 2)


def test_checkpoint_commits_only_complete_state_and_restores(tmp_path):
    calls = []
    volume = SimpleNamespace(commit=lambda: calls.append("commit"), reload=lambda: calls.append("reload"))
    store = grpo.StepCheckpoint(tmp_path, volume)
    assert store.load() is None
    def write(target):
        (target / "optimizer.bin").write_bytes(b"state")
    store.save(1, {"step": 1, "spent_dollars": .1}, write)
    restored, state = store.load()
    assert (restored / "optimizer.bin").read_bytes() == b"state"
    assert state == {"step": 1, "spent_dollars": .1}
    assert calls == ["reload", "commit", "reload"]
    with pytest.raises(FileExistsError):
        store.save(1, {})
    def interrupted(target):
        raise RuntimeError("preemption")
    with pytest.raises(RuntimeError):
        store.save(2, {}, interrupted)
    assert store.load()[1]["step"] == 1
    (tmp_path / "latest").write_text("../../elsewhere")
    with pytest.raises(RuntimeError, match="pointer"):
        store.load()


def test_reward_checkpoint_metrics_preserve_timeout_drop_evidence(tmp_path):
    from llm.rl.reward import normalize_group_rewards

    scores = [-2.0, None, -6.0]
    advantages = normalize_group_rewards(scores)
    metrics = grpo.reward_checkpoint_metrics(scores, advantages, passed_count=2)
    store = grpo.StepCheckpoint(tmp_path)
    store.save(1, {'step': 1, **metrics})
    state = store.load()[1]
    assert state['scores'] == scores
    assert state['checker_passed'] == 2
    assert state['learning_signal'] is True and state['nonzero_advantages'] == 2
    assert state['reward_stats'] == {
        'sample_count': 3, 'reward_count': 2,
        'checker_timeout_drop_count': 1, 'checker_timeout_drop_rate': 1 / 3,
    }


def test_masked_statistics_forced_tokens_and_forbidden_logits():
    logps, entropy = grpo.masked_statistics([[1000, 2, 2], [1, 999, 2]], [1, 2], [(1, 2), (2,)])
    np.testing.assert_allclose(logps, [-np.log(2), 0])
    np.testing.assert_allclose(entropy, [np.log(2), 0])
    with pytest.raises(ValueError):
        grpo.masked_statistics([[1, 2]], [0], [(1,)])
    with pytest.raises(ValueError):
        grpo.masked_statistics([[1]], [0], [(0,)], temperature=0)


class Tensor(np.ndarray):
    def __new__(cls, values, **kwargs):
        return np.asarray(values).view(cls)
    @property
    def device(self):
        return "cpu"
    def index_select(self, dim, index):
        return np.take(self, index, axis=dim).view(Tensor)
    def fill_(self, value):
        self[:] = value
    def index_copy_(self, dim, index, values):
        assert dim == 0
        self[index] = values
    def float(self):
        return self.astype(float).view(Tensor)
    def exp(self):
        return np.exp(self).view(Tensor)


def fake_torch(monkeypatch):
    module = ModuleType("torch")
    module.tensor = lambda data, **kw: Tensor(data)
    module.long = int
    module.stack = lambda data: Tensor(np.stack(data))
    module.tanh = lambda data: Tensor(np.tanh(data))
    module.logsumexp = lambda data, dim: Tensor(np.logaddexp.reduce(data, axis=dim))
    module.nn = SimpleNamespace(functional=SimpleNamespace(linear=lambda h, w: Tensor(h @ w.T)))
    monkeypatch.setitem(sys.modules, "torch", module)


def test_vllm_v2_processor_tracks_slots_row_order_first_position_and_dead_end(monkeypatch):
    torch = pytest.importorskip("torch")
    interface = ModuleType("vllm.v1.worker.gpu.sample.logits_processor")
    interface.LogitsProcessor = object
    monkeypatch.setitem(sys.modules, interface.__name__, interface)
    class Decoder:
        def __init__(self):
            self.tokens = []
        def advance(self, token):
            assert token in self.allowed_token_ids()
            self.tokens.append(token)
        def allowed_token_ids(self):
            return (1, 2) if not self.tokens else ()
    cls = grpo.make_mask_processor(lambda spec: Decoder())
    prompt_len = np.array([3, 5, 4])
    processor = cls(None, SimpleNamespace(prompt_len=SimpleNamespace(np=prompt_len)))
    masked = SimpleNamespace(extra_args={grpo.MASK_KEY: {"instance": {}, "eos_token_id": 0, "rid": "r0"}})
    assert processor.add_request(2, masked) is True
    assert processor.add_request(0, SimpleNamespace(extra_args=None)) is False
    def ctx(slots, fed, pos):
        return SimpleNamespace(expanded_idx_mapping=torch.tensor(slots), input_ids=torch.tensor(fed),
                               pos=torch.tensor(pos))
    # First output position: the fed token is the last prompt token (pos == prompt_len - 1) and advances nothing.
    logits = torch.tensor([[5., 6., 7.], [1000., 2., 3.]])
    processor.apply(logits, ctx([0, 2], [9, 9], [4, 3]))
    assert torch.equal(logits[0], torch.tensor([5., 6., 7.]))  # unmasked request untouched
    assert logits[1, 0] == float("-inf") and torch.equal(logits[1, 1:], torch.tensor([2., 3.]))
    # Rows reorder; the sampled token 2 is fed at pos 4 >= prompt_len: decoder completes, only EOS stays legal.
    logits = torch.tensor([[1., 2., 3.], [1., 2., 3.]])
    processor.apply(logits, ctx([2, 0], [2, 9], [4, 5]))
    assert logits[0, 0] == 1. and torch.isinf(logits[0, 1:]).all()
    assert cls.decoders["r0"].tokens == [2]
    # Re-applying the same position doesn't advance twice; a recycled slot is reinitialised.
    processor.apply(torch.zeros(1, 3), ctx([2], [2], [4]))
    assert cls.decoders["r0"].tokens == [2]
    assert processor.add_request(2, SimpleNamespace(extra_args=None)) is False and 2 not in processor.slots
    with pytest.raises(ValueError):
        cls.validate_params(SimpleNamespace(extra_args={grpo.MASK_KEY: {}}))


def test_trl_subclass_uses_mask_and_glimmer_softcap(monkeypatch):
    fake_torch(monkeypatch)
    trl = ModuleType("trl")
    trl.GRPOTrainer = object
    monkeypatch.setitem(sys.modules, "trl", trl)
    cls = grpo.make_masked_grpo_trainer(lambda ids, am, n: [(1, 2)])
    trainer = cls()
    trainer.temperature = 2.
    head = Tensor([[999., 999.], [1., 0.], [0., 1.]])
    base = SimpleNamespace(
        config=SimpleNamespace(text_config=SimpleNamespace(output_multiplier=2., final_logit_softcapping=3.)),
        model=lambda **kw: SimpleNamespace(last_hidden_state=Tensor([[[1., 2.], [3., 4.], [5., 6.]]])),
        get_output_embeddings=lambda: SimpleNamespace(weight=head))
    logps, entropies, aux = trainer._get_per_token_logps_and_entropies(
        base, Tensor([[0, 1, 2]]), Tensor([[1, 1, 1]]), 1, compute_entropy=True)
    z = 3 * np.tanh(np.array([6., 8.]) / 3)
    expected, ent = grpo.masked_statistics([z], [1], [(0, 1)], temperature=2)
    np.testing.assert_allclose(logps[0], expected)
    np.testing.assert_allclose(entropies[0], ent)
    assert aux is None
    with pytest.raises(NotImplementedError):
        trainer._get_per_token_logps_and_entropies(base, Tensor([[0, 1, 2]]), Tensor([[1, 1, 1]]), 1,
                                                 compute_aux_loss=True)


def test_decoder_spec_uses_frozen_engine_signature(monkeypatch, empty_ship_instance):
    import llm.stowlm.format as fmt
    import llm.stowlm.mask as mask
    import stowage.feasibility as feasibility
    import stowage.actions as actions
    tokenizer = object()
    monkeypatch.setattr(fmt, "load_tokenizer", lambda path: tokenizer)
    grpo._TOKENIZERS.clear()
    space = object()
    monkeypatch.setattr(actions, "get_action_space", lambda: space)
    def engine(instance, action_space, config=None):
        assert action_space is space and config is None
        assert instance.id == empty_ship_instance.id
        return "engine"
    monkeypatch.setattr(feasibility, "make_feasibility_engine", engine)
    def codec(tok, boxes):
        assert tok is tokenizer
        assert boxes == [c.id for c in empty_ship_instance.containers]
        return "codec"
    monkeypatch.setattr(fmt, "ActionCodec", codec)
    monkeypatch.setattr(mask, "MaskedDecoder", lambda e, c, config: (e, c, config.lookahead))
    result = grpo.decoder_from_spec({"instance": empty_ship_instance.model_dump(mode="json"), "config": {"lookahead": 8}})
    assert result == ("engine", "codec", 8)


def test_budget_admission_survives_preemption_and_rejects_reuse(tmp_path):
    manifest = grpo.PinManifest.capture("a" * 40, tmp_path)
    calls = []
    volume = SimpleNamespace(commit=lambda: calls.append("commit"), reload=lambda: calls.append("reload"))
    initial = grpo.reserve_run_budget(tmp_path / "run", manifest, cap=3, seconds=600,
                                      prior_spend=0, resume=False, volume=volume)
    assert initial["reserved_dollars"] == pytest.approx(600 * grpo.RATE_PER_SECOND)
    assert calls == ["reload", "commit"]
    grpo.consume_reservation(tmp_path / "run", initial, volume)
    with pytest.raises(RuntimeError, match="consumed"):
        grpo.consume_reservation(tmp_path / "run", initial, volume)
    # Simulated preemption: no completed checkpoint exists, but the reservation
    # remains charged. Re-running with a stale prior total cannot buy more GPUs.
    with pytest.raises(RuntimeError, match="ledger"):
        grpo.reserve_run_budget(tmp_path / "run", manifest, cap=3, seconds=600,
                                prior_spend=0, resume=True, volume=volume)
    with pytest.raises(RuntimeError, match="spend cap"):
        grpo.reserve_run_budget(tmp_path / "run", manifest, cap=3, seconds=600,
                                prior_spend=initial["reserved_dollars"], resume=True, volume=volume)
    second = grpo.reserve_run_budget(tmp_path / "run", manifest, cap=5, seconds=600,
                                    prior_spend=initial["reserved_dollars"], resume=True, volume=volume)
    assert second["reserved_dollars"] == pytest.approx(1200 * grpo.RATE_PER_SECOND)


def test_params_are_pinned_too(tmp_path):
    src, params = tmp_path / "src", tmp_path / "params"
    src.mkdir(); params.mkdir()
    (params / "fleet.toml").write_text("mass=1")
    manifest = grpo.PinManifest.capture("a" * 40, src)
    grpo.verify_manifest(manifest, src)
    (params / "fleet.toml").write_text("mass=2")
    with pytest.raises(RuntimeError, match="parameter"):
        grpo.verify_manifest(manifest, src)


def test_modal_app_is_lazy_and_timeout_bounded(monkeypatch, tmp_path):
    (tmp_path / "src").mkdir(); (tmp_path / "params").mkdir()
    manifest = grpo.PinManifest.capture("a" * 40, tmp_path / "src")
    calls, functions = [], []
    class Image:
        @classmethod
        def from_registry(cls, *args, **kw):
            calls.append(("image", args, kw)); return cls()
        def pip_install(self, *args):
            calls.append(("pins", args)); return self
        def env(self, *args): return self
        def workdir(self, path):
            assert path == "/root/stowlm"; return self
        def add_local_dir(self, *args, **kw):
            calls.append(("source", args, kw)); return self
    class App:
        def __init__(self, name): self.name = name
        def function(self, **kw):
            functions.append(kw)
            return lambda fn: fn
    class Volume:
        @classmethod
        def from_name(cls, name, **kw):
            calls.append(("volume", name, kw)); return SimpleNamespace(name=name)
    modal = ModuleType("modal")
    modal.Image, modal.App, modal.Volume = Image, App, Volume
    monkeypatch.setitem(sys.modules, "modal", modal)
    app, admit, smoke = grpo.build_modal_app(tmp_path, manifest, cap=3, timeout_s=600)
    assert app.name == "stowlm-grpo"
    assert callable(admit) and callable(smoke)
    assert len(functions) == 2
    assert "gpu" not in functions[0]  # durable CPU admission precedes allocation
    assert functions[0]["max_containers"] == 1
    assert functions[1]["gpu"] == "H100:2"
    assert functions[1]["timeout"] == 600
    assert functions[1]["retries"] == 0
    pins = next(call[1] for call in calls if call[0] == "pins")
    assert all(f"{k}=={v}" in pins for k, v in grpo.STACK.items())
    assert any(call[:2] == ("volume", "stowlm-physics-cache") and not call[2]["create_if_missing"]
               for call in calls)
    with pytest.raises(RuntimeError, match="spend cap"):
        grpo.build_modal_app(tmp_path, manifest, cap=.1, timeout_s=600)


@pytest.mark.parametrize("status,expected", [("complete", "ok"), ("dead_end", "dead_end"), ("running", "timeout")])
def test_score_rollout_preserves_failure_status_and_model_contract(monkeypatch, empty_ship_instance, status, expected):
    import llm.stowlm.mask as mask
    import llm.rl.reward as reward
    from stowage.model import MovePlan
    plan = MovePlan(id="rl-test", instance_id=empty_ship_instance.id, call_index=empty_ship_instance.call_index)
    def to_plan():
        if status != "complete":  # the real engine refuses an incomplete action sequence
            raise ValueError("to_plan requires a complete action sequence")
        return plan
    engine = SimpleNamespace(copy=lambda: engine, to_plan=to_plan)
    class Decoder:
        def __init__(self, e, c):
            self.engine = e
            self.status = status
            self.seen = []
        def allowed_token_ids(self): return (1, 2) if self.status == "running" else ()
        def advance(self, token): self.seen.append(token)
    monkeypatch.setattr(mask, "MaskedDecoder", Decoder)
    monkeypatch.setattr(mask, "allowed_sets", lambda e, c, actual: [(1, 2)] * len(actual))
    def evaluate(instance, result, *, dead_end):
        assert result.status == expected
        assert result.planner == "stowlm.grpo"
        assert result.planner_version == grpo.RL_VERSION
        assert result.plan == (plan if status == "complete" else None)
        assert dead_end == (status == "dead_end")
        return SimpleNamespace(value=-1, passed=status == "complete")
    monkeypatch.setattr(reward, "evaluate_reward", evaluate)
    actual, sets, outcome = grpo.score_rollout(empty_ship_instance, engine, None, [1], seed=0)
    assert actual == ([1] if status == "running" else [])
    assert len(sets) == len(actual)


def test_checkpoint_recovers_completed_step_with_stale_or_missing_pointer(tmp_path):
    store = grpo.StepCheckpoint(tmp_path)
    store.save(1, {"step": 1})
    second = store.save(2, {"step": 2})
    # Simulate a persisted completion marker with an older directory pointer.
    (tmp_path / "latest").write_text("step-000001")
    assert store.load() == (second, {"step": 2})
    # An even later, interrupted writer never committed a completion marker.
    incomplete = tmp_path / "step-000003"
    incomplete.mkdir()
    (incomplete / "state.tmp").write_text('{"step": 3}')
    (tmp_path / "latest").unlink()
    assert store.load() == (second, {"step": 2})
    (tmp_path / "latest").write_text("../../escape")
    with pytest.raises(RuntimeError, match="pointer"):
        store.load()


def test_hard_stop_kills_at_absolute_deadline_whatever_is_running():
    now = {"t": 100.}
    exits = []
    def sleep(_):
        now["t"] += 1.
    watch = grpo.hard_stop(103., clock=lambda: now["t"], sleep=sleep, exit_fn=exits.append, start=False)
    watch()
    assert exits == [43] and now["t"] == 103.
    # A restarted incarnation that boots after the deadline exits at once.
    exits.clear()
    grpo.hard_stop(103., clock=lambda: 200., sleep=sleep, exit_fn=exits.append, start=False)()
    assert exits == [43]
    with pytest.raises(ValueError):
        grpo.hard_stop(float("inf"), start=False)


def _fake_modal(monkeypatch, functions):
    class Image:
        @classmethod
        def from_registry(cls, *a, **kw): return cls()
        def pip_install(self, *a): return self
        def env(self, *a): return self
        def workdir(self, path): return self
        def add_local_dir(self, *a, **kw): return self
    class App:
        def __init__(self, name): self.name = name
        def function(self, **kw):
            functions.append(kw)
            return lambda fn: fn
    class Volume:
        @classmethod
        def from_name(cls, name, **kw): return SimpleNamespace(name=name)
    modal = ModuleType("modal")
    modal.Image, modal.App, modal.Volume = Image, App, Volume
    monkeypatch.setitem(sys.modules, "modal", modal)


def test_resume_gpu_invocation_is_hard_bounded_by_its_remaining_reservation(monkeypatch, tmp_path):
    """Review w2-rev-grpo: checkpoint 4/5, 600 s/step reserved 600 s while the GPU timeout stayed 3,000 s."""
    import time as _time
    (tmp_path / "src").mkdir(); (tmp_path / "params").mkdir()
    manifest = grpo.PinManifest.capture("a" * 40, tmp_path / "src")
    functions, order = [], []
    _fake_modal(monkeypatch, functions)
    prior = 3000 * grpo.RATE_PER_SECOND  # the original 5-step reservation, already charged
    app, admit, smoke = grpo.build_modal_app(tmp_path, manifest, cap=30, prior_spend=prior, timeout_s=600, steps=5,
                                             project_receipt={"run_id": "run"})
    # Admission on resume reserves only the remaining step (as admit does after reading checkpoint 4/5).
    root = tmp_path / "ckpt"
    root.mkdir()
    (root / "budget.json").write_text(json.dumps({"reserved_dollars": prior, "manifest": grpo.asdict(manifest),
                                                  "started": True}))
    t0 = _time.time()
    reservation = grpo.reserve_run_budget(root, manifest, cap=30, seconds=600, prior_spend=prior, resume=True)
    assert reservation["seconds"] == 600
    assert t0 + 600 <= reservation["deadline_epoch"] <= _time.time() + 600
    monkeypatch.setattr(grpo, "hard_stop", lambda deadline: order.append(("hard_stop", deadline)))
    monkeypatch.setattr(grpo, "run_gpu_smoke", lambda *a, **kw: order.append(("run", kw["reservation"])))
    smoke({}, "run", steps=5, resume=True, reservation=reservation)
    # The hard stop is armed first, at the reservation's own deadline (600 s), not the 3,000 s Modal timeout.
    assert order[0] == ("hard_stop", reservation["deadline_epoch"]) and order[1][0] == "run"
    assert functions[1]["timeout"] == 3000  # outer backstop only
    with pytest.raises(RuntimeError, match="deadline"):
        smoke({}, "run", steps=5, resume=True, reservation={"seconds": 600})


def test_grpo_loss_matches_independent_expectation_and_ignores_forced_tokens():
    """Numerical check of the decision-token normalisation (not bookkeeping counts): loss and gradient against a
    closed-form NumPy expectation, and inserting forced positions changes neither."""
    torch = pytest.importorskip("torch")
    old = np.array([-1.2, -0.4, -2.0, -0.7])
    new = np.array([-1.0, -0.9, -1.5, -0.7])
    for advantage in (1.5, -0.8):
        group, clip = 4, .2
        ratio = np.exp(new - old)
        surrogate = np.minimum(ratio * advantage, np.clip(ratio, 1 - clip, 1 + clip) * advantage)
        expected_loss = -surrogate.mean() / group
        # d loss / d new_t: the unclipped branch is active where ratio*A <= clipped*A; else the gradient is zero.
        unclipped = ratio * advantage <= np.clip(ratio, 1 - clip, 1 + clip) * advantage
        inside = (ratio > 1 - clip) & (ratio < 1 + clip)
        expected_grad = np.where(unclipped | inside, -advantage * ratio / len(new) / group, 0.)
        for layout in ("decision only", "with forced"):
            if layout == "decision only":
                cur_v, old_v, decision = new, old, [True] * 4
            else:  # forced positions (log-prob 0) interleaved; they must not change loss or decision gradients
                cur_v = np.insert(new, [0, 2, 4], 0.)
                old_v = np.insert(old, [0, 2, 4], 0.)
                decision = [False, True, True, False, True, True, False]
            current = torch.tensor(np.array([cur_v]), dtype=torch.float64, requires_grad=True)
            loss = grpo.grpo_rollout_loss(current, torch.tensor(np.array([old_v]), dtype=torch.float64), advantage,
                                          decision, group_size=group, clip=clip)
            loss.backward()
            grad = current.grad[0].numpy()
            np.testing.assert_allclose(float(loss.detach()), expected_loss, rtol=1e-12)
            np.testing.assert_allclose(grad[np.array(decision)], expected_grad, rtol=1e-12)
            assert np.all(grad[~np.array(decision)] == 0)
    with pytest.raises(ValueError, match="no decision"):
        grpo.grpo_rollout_loss(torch.zeros(1, 2), torch.zeros(1, 2), 1., [False, False], group_size=2)


def _stats(mean, p99, mx):
    return {"positions": 3000, "mean_abs_nats": mean, "p99_abs_nats": p99, "max_abs_nats": mx}


def test_agreement_gate_matches_leader_policy():
    """D26 "Agreement gate": mean <= 0.01, p99 <= 0.1, max <= 0.5 nat; floor in the same run; synced within it."""
    assert grpo.AGREEMENT_GATE == {"mean_abs_nats": .01, "p99_abs_nats": .1, "max_abs_nats": .5}
    floor = _stats(.0068, .097, .316)
    assert grpo.agreement_failures(_stats(.0064, .072, .224), floor) == []
    assert grpo.agreement_failures(_stats(.0072, .076, .367), floor) == []
    assert any("mean" in f for f in grpo.agreement_failures(_stats(.011, .05, .2), floor))
    assert any("p99" in f for f in grpo.agreement_failures(_stats(.005, .11, .2), floor))
    assert any("max" in f for f in grpo.agreement_failures(_stats(.005, .05, .51), floor))
    # The floor itself must pass the gate, and the synced path must stay within 2x the floor's mean and p99.
    assert any("base floor" in f for f in grpo.agreement_failures(_stats(.005, .05, .2), _stats(.02, .05, .2)))
    tight = _stats(.002, .02, .1)
    fails = grpo.agreement_failures(_stats(.0064, .072, .224), tight)
    assert any("x base floor" in f and "mean" in f for f in fails) and any("p99" in f for f in fails)
    # A near-zero floor can't demand the impossible: resolution 1e-3 / 1e-2 nat.
    assert grpo.agreement_failures(_stats(.0015, .015, .1), _stats(0., 0., 0.)) == []


def test_recorded_f2_rerun_satisfies_the_agreement_gate():
    """Regression on the recorded 29 Oct F2 re-run (post out-of-place sync): both syncs pass against its floor."""
    path = Path(__file__).resolve().parents[2] / "experiments/grpo-venue/runs/falsify-64-1793249379.json"
    if not path.exists():
        pytest.skip("recorded falsifier artifact not present")
    gpu = json.loads(path.read_text())["gpu"]
    def stats(row):
        return _stats(row["mean_abs_nats"], row["quantiles"]["p99"], row["max_abs_nats"])
    floor = stats(gpu["F2_base_floor"])
    assert grpo.agreement_failures(floor) == []
    for key in ("F2_after_sync1", "F2_after_sync2"):
        assert grpo.agreement_failures(stats(gpu[key]), floor) == [], key
    assert grpo.agreement_failures(stats(gpu["F2_witness_presync"]), floor)  # the witness must fail


def test_agreement_stats_drops_nothing():
    stats = grpo.agreement_stats([([0., -1.], [0., -1.1]), ([-2.], [-2.])])
    assert stats["positions"] == 3 and stats["max_abs_nats"] == pytest.approx(.1)
    for bad in ([float("nan")], [float("inf")], [], [0., 0.]):
        with pytest.raises(RuntimeError, match="mismatch|no positions"):
            grpo.agreement_stats([([0.], bad)])
    with pytest.raises(RuntimeError, match="no positions"):
        grpo.agreement_stats([])
    assert grpo.assert_masked_agreement([0.], [-.4]) == pytest.approx(.4)  # under the 0.5 per-position max
    with pytest.raises(RuntimeError, match="mismatch"):
        grpo.assert_masked_agreement([0.], [-.6])


def test_floor_is_measured_on_base_weights_with_adapter_disabled(monkeypatch):
    import contextlib
    import llm.stowlm.mask as mask
    torch = pytest.importorskip("torch")
    class Decoder:
        def __init__(self, engine, codec):
            self.n = 0
        def allowed_token_ids(self):
            return (1, 2) if self.n < 2 else ()
        def advance(self, token):
            self.n += 1
    monkeypatch.setattr(mask, "MaskedDecoder", Decoder)
    monkeypatch.setattr(mask, "allowed_sets", lambda e, c, actual: [(1, 2)] * len(actual))
    events = []
    @contextlib.contextmanager
    def disable_adapter():
        events.append("adapter off"); yield; events.append("adapter on")
    model = SimpleNamespace(disable_adapter=disable_adapter)
    class Trainer:
        allowed_override = None
        def _get_per_token_logps_and_entropies(self, model, ids, att, n):
            events.append(("forward", n, tuple(self.allowed_override)))
            return torch.tensor([[-.5, -.7]]), None, None
    sent = []
    connection = SimpleNamespace(send=sent.append)
    monkeypatch.setattr(grpo, "_receive", lambda conn, t: {"tokens": [[1, 2, 0]], "logps": [[-.51, -.69, 0.]]})
    engine = SimpleNamespace(copy=lambda: engine)
    floor = grpo.measure_agreement_floor(model, Trainer(), connection, prompt=[9], spec={"eos_token_id": 0},
                                         engine=engine, codec=None, group_size=1, max_tokens=4,
                                         deadline=1e18, device="cpu")
    assert events == ["adapter off", ("forward", 2, ((1, 2), (1, 2))), "adapter on"]
    assert floor["positions"] == 2 and floor["max_abs_nats"] == pytest.approx(.01, abs=1e-6)
    assert sent[0]["seed"] == -1 and "weights" not in sent[0]  # sampled before any sync
    monkeypatch.setattr(grpo, "_receive", lambda conn, t: {"tokens": [[1, 2]], "logps": [[-1.2, -.7]]})
    with pytest.raises(RuntimeError, match="floor fails the gate"):
        grpo.measure_agreement_floor(model, Trainer(), connection, prompt=[9], spec={"eos_token_id": 0},
                                     engine=engine, codec=None, group_size=1, max_tokens=4, deadline=1e18, device="cpu")


def test_decoder_spec_accepts_instance_as_json_string(monkeypatch, empty_ship_instance):
    """vLLM rejects extra_args integers outside int64; the worker spec carries the instance as a JSON string."""
    import json as _json
    import llm.stowlm.format as fmt
    import llm.stowlm.mask as mask
    import stowage.feasibility as feasibility
    monkeypatch.setattr(fmt, "load_tokenizer", lambda path: object())
    grpo._TOKENIZERS.clear()
    seen = []
    monkeypatch.setattr(feasibility, "make_feasibility_engine", lambda inst, space, config=None: seen.append(inst.id) or "e")
    monkeypatch.setattr(fmt, "ActionCodec", lambda tok, boxes: "codec")
    monkeypatch.setattr(mask, "MaskedDecoder", lambda e, c, config: (e, c))
    text = _json.dumps(empty_ship_instance.model_dump(mode="json"))
    assert grpo.decoder_from_spec({"instance": text}) == ("e", "codec")
    assert seen == [empty_ship_instance.id]


def test_mvp_rollout_config_matches_the_mvp_evaluation(monkeypatch):
    """GRPO samples, replays and scores under the MVP setting: D79 C2 off, D95 securing, D88 V4 finisher."""
    assert grpo.MVP_SPEC == {"feasibility_extra": {"global_screen": 0}, "securing": True}
    assert grpo.mvp_reward_config().ballast_v4_finalize is True
    assert grpo.TRAINING_IMAGE_SCOPE["c2"].startswith("off") and "D95" in grpo.TRAINING_IMAGE_SCOPE["securing"]
    import llm.securing.mask_filter as mf
    import stowage.feasibility as feasibility
    calls = []
    monkeypatch.setattr(feasibility, "make_feasibility_engine", lambda inst, space, config=None: calls.append(config) or "inner")
    monkeypatch.setattr(mf, "wrap_engine", lambda engine: ("secured", engine))
    assert grpo.mvp_engine(SimpleNamespace()) == ("secured", "inner")
    assert calls[0].extra == {"global_screen": 0}


def test_decoder_spec_applies_securing_layer_when_requested(monkeypatch, empty_ship_instance):
    import llm.securing.mask_filter as mf
    import llm.stowlm.format as fmt
    import llm.stowlm.mask as mask
    import stowage.feasibility as feasibility
    monkeypatch.setattr(fmt, "load_tokenizer", lambda path: object())
    grpo._TOKENIZERS.clear()
    monkeypatch.setattr(feasibility, "make_feasibility_engine", lambda inst, space, config=None: ("engine", config.extra))
    monkeypatch.setattr(mf, "wrap_engine", lambda engine: ("secured", engine))
    monkeypatch.setattr(fmt, "ActionCodec", lambda tok, boxes: "codec")
    monkeypatch.setattr(mask, "MaskedDecoder", lambda e, c, config: e)
    spec = {"instance": empty_ship_instance.model_dump(mode="json"), **grpo.MVP_SPEC}
    assert grpo.decoder_from_spec(spec) == ("secured", ("engine", {"global_screen": 0}))


def test_score_batch_cli_preserves_job_order(tmp_path, monkeypatch):
    import llm.rl.score_batch as sb
    monkeypatch.setattr(grpo, "score_rollout_job", lambda job: {"value": -float(len(job[1])), "tokens": job[1]})
    jobs = [["{}", [1, 2, 3], 0, "tok"], ["{}", [4], 0, "tok"]]
    (tmp_path / "jobs.json").write_text(json.dumps(jobs))
    sb.main([str(tmp_path / "jobs.json"), str(tmp_path / "out.json"), "--workers", "1"])
    out = json.loads((tmp_path / "out.json").read_text())
    assert [r["value"] for r in out["results"]] == [-3., -1.] and out["numpy"]

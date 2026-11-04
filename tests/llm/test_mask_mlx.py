"""MLX mask: CPU distribution-contract tests, plus marked local-model GPU integration."""

import glob
import importlib.util
from pathlib import Path
import sys
import types

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
MODEL = REPO / "models/glimmer-30b-4bit"


@pytest.fixture
def cpu_mask_mlx(monkeypatch):
    """Load the actual MLX control flow against NumPy, without importing Metal.

    Categorical records its input and chooses its mode: distribution assertions
    compare all supplied probabilities, rather than relying on noisy draws.
    This covers arithmetic/control flow only; actual MLX remains GPU-tested below.
    """
    mx = types.ModuleType("mlx.core")
    for name in ("array", "float32", "where", "take_along_axis", "argmax", "arange", "repeat"):
        setattr(mx, name, getattr(np, name))

    def logsumexp(values, axis=-1, keepdims=False):
        maximum = np.max(values, axis=axis, keepdims=True)
        result = maximum + np.log(np.exp(values - maximum).sum(axis=axis, keepdims=True))
        return result if keepdims else np.squeeze(result, axis=axis)

    def categorical(values, key=None):
        mx.categorical_logits = values.copy()
        return np.argmax(values, axis=-1)

    mx.logsumexp = logsumexp
    mx.eval = mx.async_eval = lambda *args: None
    mx.random = types.SimpleNamespace(categorical=categorical, key=lambda seed: seed,
                                      split=lambda key: (key + 1, key + 2))
    parent = types.ModuleType("mlx")
    parent.core = mx
    monkeypatch.setitem(sys.modules, "mlx", parent)
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    name = "_mask_mlx_numpy_contract"
    spec = importlib.util.spec_from_file_location(name, REPO / "src/llm/stowlm/mask_mlx.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module, mx


class FixedLogitsModel:
    def __init__(self, logits):
        self.logits = np.array(logits, dtype=np.float32)
        self.calls = 0

    def make_cache(self):
        self.calls += 1
        return []

    def __call__(self, ids, cache):
        self.calls += 1
        return np.broadcast_to(self.logits, (*ids.shape, len(self.logits)))


def test_sampled_logprobs_follow_temperature_distribution(cpu_mask_mlx):
    module, mx = cpu_mask_mlx
    idx, valid = module._index_matrix([(0, 2), (2,)])
    logits = np.array([[0, 100, 2], [0, 100, 2]], dtype=np.float32)
    tokens, logprobs = module.masked_sample(logits, idx, valid, temperature=2)
    expected = np.array([-np.log1p(np.exp(-1)), 0])
    np.testing.assert_array_equal(tokens, [2, 2])
    np.testing.assert_allclose(logprobs, expected, atol=1e-6)
    probabilities = np.exp(mx.categorical_logits)
    probabilities /= probabilities.sum(axis=-1, keepdims=True)
    np.testing.assert_allclose(probabilities, [[1 / (1 + np.e), 1 / (1 + np.exp(-1))], [1, 0]], atol=1e-6)


@pytest.mark.parametrize("temperature", [0, -1])
def test_greedy_logprobs_are_deterministic_with_first_allowed_tie(cpu_mask_mlx, temperature):
    module, _ = cpu_mask_mlx
    idx, valid = module._index_matrix([(2, 0)])
    tokens, logprobs = module.masked_sample(np.array([[2, 100, 2]]), idx, valid, temperature)
    np.testing.assert_array_equal(tokens, [2])
    np.testing.assert_array_equal(logprobs, [0])


def test_replayed_logprobs_follow_temperature_distribution(cpu_mask_mlx):
    module, _ = cpu_mask_mlx
    result = module.masked_logprobs(FixedLogitsModel([0, 100, 2]), [1], [2], [(0, 2)], temperature=2)
    np.testing.assert_allclose(result, [-np.log1p(np.exp(-1))], atol=1e-6)
    assert result.dtype == np.float32


@pytest.mark.parametrize("token,expected", [(2, 0), (0, -np.inf)])
def test_replayed_greedy_logprobs_reject_other_tied_token(cpu_mask_mlx, token, expected):
    module, _ = cpu_mask_mlx
    result = module.masked_logprobs(FixedLogitsModel([2, 100, 2]), [1], [token], [(2, 0)], temperature=0)
    np.testing.assert_array_equal(result, [expected])


def test_empty_logprob_replay_returns_float32_without_prefill(cpu_mask_mlx):
    module, _ = cpu_mask_mlx
    model = FixedLogitsModel([0, 2])
    result = module.masked_logprobs(model, [1], [], [])
    assert result.shape == (0,) and result.dtype == np.float32
    assert model.calls == 0


def test_immediate_dead_end_logprob_replay(cpu_mask_mlx):
    module, _ = cpu_mask_mlx

    class DeadEndDecoder:
        status = "dead_end"

        def allowed_token_ids(self):
            return ()

    model = FixedLogitsModel([0, 2])
    results, _ = module.generate_masked(model, [1], [DeadEndDecoder()], prefetch=False)
    assert results[0].status == "dead_end" and results[0].tokens == []
    model.calls = 0
    replay = module.masked_logprobs(model, [1], results[0].tokens, [])
    assert replay.shape == (0,) and replay.dtype == np.float32
    assert model.calls == 0


@pytest.mark.parametrize("temperature,expected", [(2, -np.log1p(np.exp(-1))), (0, 0)])
def test_unmasked_baseline_reports_behavior_logprobs(cpu_mask_mlx, temperature, expected):
    module, _ = cpu_mask_mlx
    results, _ = module.generate_masked(FixedLogitsModel([0, 2]), [1], None,
                                        batch=2, temperature=temperature, max_tokens=2)
    for result in results:
        assert result.status == "unmasked"
        assert result.tokens == [1, 1]
        np.testing.assert_allclose(result.masked_logprobs, [expected, expected], atol=1e-6)


def test_temperature_replay_is_length_preserving_across_chunks(cpu_mask_mlx, monkeypatch):
    module, _ = cpu_mask_mlx
    monkeypatch.setattr(module, "PREFILL_CHUNK", 2)
    result = module.masked_logprobs(FixedLogitsModel([0, 100, 2]), [1], [2] * 5,
                                   [(0, 2), (2,), (0, 2), (2,), (0, 2)], temperature=2)
    expected = -np.log1p(np.exp(-1))
    np.testing.assert_allclose(result, [expected, 0, expected, 0, expected], atol=1e-6)
    assert result.dtype == np.float32


@pytest.mark.gpu
@pytest.mark.slow
def test_mlx_temperature_greedy_and_empty_contract():
    """Actual MLX array/API coverage without loading the 30B model; run only on the leader GPU."""
    mx = pytest.importorskip("mlx.core")
    from llm.stowlm.mask_mlx import _index_matrix, masked_logprobs, masked_sample

    class Model:
        def __init__(self, values=(0.0, 100.0, 2.0)):
            self.values = values

        def make_cache(self):
            return []

        def __call__(self, ids, cache):
            return mx.broadcast_to(mx.array(self.values), (*ids.shape, 3))

    idx, valid = _index_matrix([(0, 2)])
    tokens, sampled = masked_sample(mx.array([[0, 100, 2]]), idx, valid, 2, mx.random.key(7))
    replay = masked_logprobs(Model(), [1], tokens.tolist(), [(0, 2)], temperature=2)
    np.testing.assert_allclose(replay, np.array(sampled), atol=1e-6)
    expected = [-np.log1p(np.exp(1)), -np.log1p(np.exp(-1))]
    assert replay[0] == pytest.approx(expected[int(tokens.item() == 2)], abs=1e-6)
    idx, valid = _index_matrix([(2, 0)])
    tokens, scores = masked_sample(mx.array([[2, 100, 2]]), idx, valid, 0)
    assert tokens.item() == 2 and scores.item() == 0
    for token, expected in ((2, 0), (0, -np.inf)):
        replay = masked_logprobs(Model((2.0, 100.0, 2.0)), [1], [token], [(2, 0)], temperature=0)
        np.testing.assert_array_equal(replay, [expected])
    empty = masked_logprobs(object(), [1], [], [])
    assert empty.shape == (0,) and empty.dtype == np.float32


@pytest.fixture(scope="module")
def setup():
    mx = pytest.importorskip("mlx.core")
    from mlx_lm import load

    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance

    if not (MODEL / "config.json").exists():
        pytest.skip("local Glimmer weights missing")
    model, _ = load(str(MODEL))
    tok = load_tokenizer()
    path = sorted(glob.glob(str(REPO / "experiments/instances-v2/random_yard/tiny/seed-*[0-9].json")))[0]
    inst = load_instance(path)
    # APPROXIMATION (diagnostic): C2 off — the 3.7 linear model can't currently be built for v2 hulls.
    eng = make_feasibility_engine(inst, get_action_space("boxslot"), config=FeasibilityConfig(extra={"global_screen": 0}))
    codec = ActionCodec(tok, [c.id for c in inst.containers])
    return mx, model, tok, inst, eng, codec, encode_prompt(inst, tok)


@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.parametrize("temperature", [1.0, 2.0])
def test_batched_masked_generation_and_logprobs(setup, temperature):
    mx, model, tok, inst, eng, codec, prompt = setup
    from llm.stowlm.mask import MaskedDecoder, allowed_sets
    from llm.stowlm.mask_mlx import generate_masked, masked_logprobs

    decs = [MaskedDecoder(eng.copy(), codec) for _ in range(4)]
    results, stats = generate_masked(model, prompt, decs, temperature=temperature, max_tokens=200, seed=0)
    assert stats.steps > 0
    for r in results:
        assert r.status in ("complete", "dead_end")
        if r.status == "complete":
            assert codec.decode_plan(r.tokens) == r.decoder.actions
        sets = allowed_sets(eng, codec, r.tokens)  # every sampled token was allowed, and replay is deterministic
        assert all(t in s for t, s in zip(r.tokens, sets))
        assert all(lp <= 1e-4 for lp in r.masked_logprobs)
    r = next(r for r in results if r.tokens)
    lp = masked_logprobs(model, prompt, r.tokens, allowed_sets(eng, codec, r.tokens), temperature=temperature)
    # batched bf16 decode vs one teacher-forced pass: same function, small numeric differences only
    assert np.allclose(lp, np.array(r.masked_logprobs), atol=0.25)


@pytest.mark.gpu
@pytest.mark.slow
def test_unmasked_baseline_and_processor(setup):
    mx, model, tok, inst, eng, codec, prompt = setup
    from mlx_lm.generate import generate_step

    from llm.stowlm.mask import MaskedDecoder
    from llm.stowlm.mask_mlx import MaskLogitsProcessor, generate_masked

    results, stats = generate_masked(model, prompt, None, batch=2, max_tokens=4)
    assert stats.steps == 4 and all(len(r.tokens) == 4 for r in results)

    dec = MaskedDecoder(eng.copy(), codec)
    proc = MaskLogitsProcessor(dec, len(prompt), codec.end_plan_tokens[0])
    out = []
    for (t, _), _ in zip(generate_step(mx.array(prompt), model, logits_processors=[proc]), range(60)):
        out.append(int(t))
        if dec.status != "running" or t == codec.end_plan_tokens[0]:
            break
    assert out and out[:len(dec.tokens)] == dec.tokens[:len(out)]

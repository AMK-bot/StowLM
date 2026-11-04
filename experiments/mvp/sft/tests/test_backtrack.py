"""Backtracking masked decode (llm.stowlm.backtrack): exact single-action forbidding, rewind on dead ends, KV-cache
bookkeeping. CPU only: a fake model with deterministic logits and a trimmable fake cache."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
TINY = sorted((ROOT / "experiments/mvp/corpus/test/tiny").rglob("seed-*[0-9].json"))
pytestmark = pytest.mark.skipif(not TINY, reason="frozen tiny test corpus missing")


def make_decoder(path):
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.mask import MaskedDecoder
    from llm.stowlm.sft_data import codec_for, d79_engine_config
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    from stowage.io import load_instance

    instance = load_instance(path)
    tok = load_tokenizer()
    engine = make_feasibility_engine(instance, get_action_space(), config=d79_engine_config())
    return instance, tok, MaskedDecoder(engine, codec_for(instance, tok))


def feed(d, ids):
    for t in ids:
        if t not in d.allowed_token_ids():
            return False
        d.advance(t)
    return True


def test_forbidding_removes_exactly_one_action():
    from llm.stowlm.backtrack import ForbiddingDecoder

    _, _, inner = make_decoder(TINY[0])
    d = ForbiddingDecoder(inner)
    legal = list(inner.engine.legal_actions())
    assert len(legal) > 1
    target = legal[len(legal) // 2]
    assert feed(d, d._ids(target)) and len(d.actions) == 1
    assert d.rewind(1) == 1 and len(d.actions) == 0
    blocked = []
    for a in legal:
        ok = feed(d, d._ids(a))
        if ok and len(d.actions) == 1:
            d._base().pop_action()
        else:
            d.rewind(0)  # drop the partial action only
            blocked.append(a)
    assert blocked == [target]


class FakeCache:
    def __init__(self):
        self.offset = 0

    def is_trimmable(self):
        return True

    def trim(self, n):
        n = min(self.offset, n)
        self.offset -= n
        return n

    @property
    def state(self):
        import mlx.core as mx
        return mx.array([self.offset])


class FakeModel:
    """Deterministic logits that prefer low token ids; counts every fed token in the cache."""

    def __init__(self, vocab):
        self.vocab = vocab

    def make_cache(self):
        return [FakeCache()]

    def __call__(self, x, cache=None):
        import mlx.core as mx
        cache[0].offset += x.shape[1]
        return mx.broadcast_to(-mx.arange(self.vocab, dtype=mx.float32)[None, None, :] / 1000.0,
                               (1, x.shape[1], self.vocab))


class Trap:
    """Transparent wrapper: the first time depth 2 is reached, report a dead end (forces one rewind)."""

    def __init__(self, inner):
        self.inner = inner
        self.sprung = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def allowed_token_ids(self):
        if len(self.inner.actions) == 2 and self.sprung is None:
            self.sprung = self.inner.actions[1]
            self.inner.status = "dead_end"
            return ()
        return self.inner.allowed_token_ids()

    def advance(self, t):
        return self.inner.advance(t)


@pytest.mark.parametrize("temperature", [0.0, 0.7])
def test_backtracking_recovers_from_a_dead_end_and_keeps_the_cache_aligned(temperature):
    from llm.stowlm.backtrack import ForbiddingDecoder, generate_backtracking
    from llm.stowlm.format_prompt import encode_prompt

    instance, tok, inner = make_decoder(TINY[0])
    if len(instance.load_list) < 3:
        pytest.skip("needs three boxes")
    trap = Trap(inner)
    d = ForbiddingDecoder(trap)
    prompt = encode_prompt(instance, tok)
    model = FakeModel(len(tok.get_vocab()) + 512)
    result, stats = generate_backtracking(model, prompt, d, temperature=temperature, seed=1, max_backtracks=8)
    assert result.status == "complete", stats
    assert stats["backtracks"] == 1 and stats["rewound_actions"] == 1 and stats["cache_trims"] == 1
    assert inner.actions[1] != trap.sprung  # the trapped choice was forbidden and replaced
    # cache = prompt[:-1] + [prompt[-1]] + all plan tokens but the last
    assert len(prompt) - 1 + stats["in_cache"] == len(prompt) + len(result.tokens) - 1

"""Feasibility mask (plan 5.1), framework-neutral part: CPU only, fixture instances (C2 off: the fixture has no hull)."""

import random

import pytest

from llm.stowlm.format import ActionCodec, TokenTrie, load_tokenizer
from llm.stowlm.mask import (MaskConfig, MaskedDecoder, MaskError, allowed_sets, allowed_token_ids,
                             random_masked_plan)
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.moves.engine import apply_plan

SPACE = get_action_space("boxslot")
NO_C2 = FeasibilityConfig(extra={"global_screen": 0})  # fixture vessels have no hull: the 3.7 model can't be built


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


def setup(tokenizer, seed, **kw):
    inst = tiny_instance(seed, **kw)
    return inst, make_feasibility_engine(inst, SPACE, config=NO_C2), ActionCodec(tokenizer, [c.id for c in inst.containers])


@pytest.mark.parametrize("layover", [False, True])
@pytest.mark.parametrize("seed", range(4))
def test_mask_equals_trie_over_engine_legal_set(tokenizer, seed, layover):
    """lookahead=0: at every prefix of a random walk the allowed set is exactly the 4.5 trie over legal_actions()."""
    inst, eng, codec = setup(tokenizer, seed, layover=layover, n_load=8)
    dec = MaskedDecoder(eng.copy(), codec, MaskConfig(lookahead=0))
    rng = random.Random(seed)
    for _ in range(400):
        if dec.status != "running":
            break
        legal = dec.engine.legal_actions()
        trie = codec.trie(legal, allow_end=dec.engine.is_complete())
        prefix = dec.tokens[len(dec.tokens) - len(dec._prefix):]
        expected = tuple(sorted(trie.allowed_next_token_ids(prefix)))
        assert dec.allowed_token_ids() == expected
        if not expected:
            break
        before = dec.engine.depth
        done = dec.advance(expected[rng.randrange(len(expected))])
        if done is not None:  # the completed action's tokens are canonical
            assert dec.engine.depth == before + 1 and dec.engine.actions()[-1] == done
            assert tuple(dec.tokens[-len(codec.encode_action(done)):]) == codec.encode_action(done)
    assert dec.status in ("complete", "dead_end")


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("lookahead", [0, 8])
def test_random_plans_complete_and_replay_or_report_dead_end(tokenizer, seed, lookahead):
    inst, eng, codec = setup(tokenizer, seed, layover=seed % 2 == 1, n_load=8)
    dec = random_masked_plan(eng, codec, random.Random(seed), MaskConfig(lookahead=lookahead))
    if dec.status == "complete":
        assert codec.decode_plan(dec.tokens) == dec.actions
        assert dec.tokens[-len(codec.end_plan_tokens):] == list(codec.end_plan_tokens)
        assert apply_plan(inst, dec.engine.to_plan()).legal
        assert dec.allowed_token_ids() == ()
    else:
        assert dec.status == "dead_end" and dec.dead_end is not None
        assert not dec.engine.is_complete()


def test_lookahead_only_allows_live_successors(tokenizer):
    """With lookahead, an action completed when ≤ k candidates remained never lands in a dead end."""
    checked = 0
    for seed in range(10):
        inst, eng, codec = setup(tokenizer, seed, n_load=8)
        dec = MaskedDecoder(eng.copy(), codec, MaskConfig(lookahead=8))
        rng = random.Random(seed)
        while dec.allowed_token_ids():
            small = dec._node.count <= 8
            done = dec.advance(dec.allowed_token_ids()[rng.randrange(len(dec.allowed_token_ids()))])
            if done is not None and small:
                checked += 1
                assert dec.engine.is_complete() or dec.engine.legal_actions()
    assert checked > 20


def test_deterministic_replay_and_allowed_sets(tokenizer):
    inst, eng, codec = setup(tokenizer, 3, layover=True, n_load=8)
    a = random_masked_plan(eng, codec, random.Random(7))
    b = random_masked_plan(eng, codec, random.Random(7))
    assert a.tokens == b.tokens and a.status == b.status
    sets = allowed_sets(eng, codec, a.tokens)
    assert len(sets) == len(a.tokens) and all(t in s for t, s in zip(a.tokens, sets))
    assert eng.depth == 0  # the caller's engine is untouched


def test_one_shot_function_and_errors(tokenizer):
    inst, eng, codec = setup(tokenizer, 0, n_load=8)
    first = allowed_token_ids(eng, codec)
    legal = eng.legal_actions()
    assert set(first) == {codec.encode_action(a)[0] for a in legal}
    action = legal[0]
    ids = codec.encode_action(action)
    assert allowed_token_ids(eng, codec, ids[:3]) == tuple(sorted(
        {codec.encode_action(a)[3] for a in legal if codec.encode_action(a)[:3] == ids[:3]}))
    with pytest.raises(MaskError):
        allowed_token_ids(eng, codec, ids)  # completes an action
    dec = MaskedDecoder(eng.copy(), codec)
    with pytest.raises(MaskError):
        dec.advance(codec.end_plan_tokens[0])  # end of plan before everything is loaded


def test_pop_action_restores_tokens_and_state(tokenizer):
    inst, eng, codec = setup(tokenizer, 1, n_load=8)
    dec = MaskedDecoder(eng.copy(), codec)
    rng = random.Random(1)
    at_depth: dict[int, tuple[list[int], tuple[int, ...]]] = {}
    while dec.engine.depth < 3:
        if dec.advance(dec.allowed_token_ids()[rng.randrange(len(dec.allowed_token_ids()))]) is not None:
            at_depth[dec.engine.depth] = (list(dec.tokens), dec.allowed_token_ids())
    dec.advance(dec.allowed_token_ids()[0])  # a partial fourth action is dropped too
    dec.pop_action()
    assert dec.engine.depth == 2
    assert (dec.tokens, dec.allowed_token_ids()) == at_depth[2]
    assert codec.decode_plan(list(dec.tokens) + list(codec.end_plan_tokens)) == dec.actions


@pytest.mark.parametrize("layover", [False, True])
@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("lookahead", [0, 8])
def test_index_array_path_equals_action_path_on_every_prefix(tokenizer, seed, layover, lookahead):
    """The index-array build (engine v2.3) gives the same allowed set as the legal_actions() build at every prefix."""
    inst, eng, codec = setup(tokenizer, seed, layover=layover, n_load=8)
    fast = MaskedDecoder(eng.copy(), codec, MaskConfig(lookahead=lookahead))
    ref = MaskedDecoder(eng.copy(), codec, MaskConfig(lookahead=lookahead, index_arrays=False))
    rng = random.Random(100 + seed)
    steps = 0
    while True:
        allowed = fast.allowed_token_ids()
        assert allowed == ref.allowed_token_ids()
        assert fast.status == ref.status and fast.n_legal == ref.n_legal
        if not allowed:
            break
        t = allowed[rng.randrange(len(allowed))]
        assert fast.advance(t) == ref.advance(t)
        steps += 1
    assert steps > 0 or fast.status == "dead_end"
    assert fast.tokens == ref.tokens and fast.actions == ref.actions


@pytest.mark.parametrize("seed", range(6))
def test_prefetch_changes_nothing(tokenizer, seed):
    """prefetch() (build the successor of a determined action early) leaves every allowed set and action unchanged."""
    inst, eng, codec = setup(tokenizer, seed, layover=seed % 2 == 1, n_load=8)
    a = MaskedDecoder(eng.copy(), codec)
    b = MaskedDecoder(eng.copy(), codec)
    rng = random.Random(seed)
    fetched = 0
    while True:
        allowed = a.allowed_token_ids()
        assert allowed == b.allowed_token_ids()
        fetched += b.prefetch()
        if not allowed:
            break
        t = allowed[rng.randrange(len(allowed))]
        assert a.advance(t) == b.advance(t)
    assert a.tokens == b.tokens and a.status == b.status and fetched > 0

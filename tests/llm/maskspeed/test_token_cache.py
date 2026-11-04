"""Batch rows share static encodings while retaining independent mask state."""
import gc
import weakref

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskedDecoder, _TokenCache
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine


def setup():
    instance = tiny_instance(23, n_load=3, n_other=0)
    engine = make_feasibility_engine(instance, get_action_space('boxslot'),
        config=FeasibilityConfig(extra={'global_screen': 0}))
    codec = ActionCodec(load_tokenizer(), [box.id for box in instance.containers])
    return engine, codec


def test_shared_encodings_do_not_share_tries_or_advanced_state():
    engine, codec = setup()
    first = MaskedDecoder(engine.copy(), codec)
    sibling = MaskedDecoder(engine.copy(), codec)
    assert first._cache is sibling._cache
    original = sibling.allowed_token_ids()
    action = engine.legal_actions()[0]
    for token in codec.encode_action(action):
        assert token in first.allowed_token_ids()
        first.advance(token)
    assert first.actions == [action] and sibling.actions == []
    assert sibling.allowed_token_ids() == original
    assert first._stack[0].root is not sibling._stack[0].root
    assert first.pop_action() == action
    assert first.allowed_token_ids() == original


def test_explicit_cache_and_different_codecs_remain_independent():
    engine, codec = setup()
    first = MaskedDecoder(engine.copy(), codec)
    explicit = _TokenCache(codec)
    second = MaskedDecoder(engine.copy(), codec, token_cache=explicit)
    other_codec = ActionCodec(codec.tokenizer, codec.box_index)
    third = MaskedDecoder(engine.copy(), other_codec)
    assert second._cache is explicit and second._cache is not first._cache
    assert third._cache is not first._cache
    assert first.allowed_token_ids() == second.allowed_token_ids() == third.allowed_token_ids()


def test_shared_cache_is_released_with_last_decoder_even_if_codec_lives():
    engine, codec = setup()
    decoder = MaskedDecoder(engine, codec)
    cached = weakref.ref(decoder._cache)
    del decoder
    gc.collect()
    assert cached() is None
    assert codec.box_index.ids


def test_index_table_identity_cache_preserves_value_key_fallback():
    engine, codec = setup()
    cache = _TokenCache(codec)
    heads, tails = cache.index_tables(engine)
    assert cache.index_tables(engine) == (heads, tails)

    class EqualIndices:
        index_boxes = tuple(list(engine.index_boxes))
        index_slots = tuple(list(engine.index_slots))

    assert EqualIndices.index_slots is not engine.index_slots
    other_heads, other_tails = cache.index_tables(EqualIndices())
    assert other_heads is heads and other_tails is tails
    for action in engine.legal_actions():
        flat = engine.index_of(action)
        assert heads[flat // engine.n_slots] + tails[flat % engine.n_slots] == codec.encode_action(action)

"""Real CPU Glimmer tokenizer tests; no model weights or GPU required."""
from pathlib import Path

import pytest

from llm.stowlm.format import ActionCodec, BoxIndex, TokenTrie, build_sequence, load_tokenizer
from stowage.actions.samples import random_actions, tiny_instance
from stowage.actions.space import Discharge, Place, Relocate, RestowOff
from stowage.io import load_instance


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


@pytest.fixture
def codec(tokenizer):
    return ActionCodec(tokenizer, ["b", "a"])


@pytest.mark.parametrize("action", [
    Place("a", "0010182"), Place("b", "0020002"), Discharge("a"),
    Discharge("b", "A-001-01"), Relocate("a", "AB12-999-99"), RestowOff("b"),
])
def test_all_action_variants(codec, action):
    ids = codec.encode_action(action)
    assert codec.decode_action(ids) == action
    assert codec.tokenizer.decode(list(ids), skip_special_tokens=False).endswith("\n")
    assert codec.decode_plan(codec.encode_plan([action, action])) == [action, action]


def test_box_numbering():
    index = BoxIndex(["b", "a"])
    assert index.ids == ("a", "b")
    assert index.by_id == {"a": 0, "b": 1}
    assert index.box(index.index("b")) == "b"
    with pytest.raises(ValueError):
        BoxIndex(["a", "a"])
    for bad in [-1, 2]:
        with pytest.raises(ValueError):
            index.box(bad)
    with pytest.raises(ValueError):
        index.index("missing")


def test_empty_plan_and_end_trie(codec):
    assert codec.decode_plan(codec.encode_plan([])) == []
    assert len(codec.end_plan_tokens) == 1
    trie = codec.trie([], allow_end=True)
    assert trie.allowed_next_token_ids(()) == set(codec.end_plan_tokens)
    assert trie.is_complete(codec.end_plan_tokens)
    assert not trie.allowed_next_token_ids(codec.end_plan_tokens)
    assert codec.trie([]).allowed_next_token_ids(()) == set()


def test_field_boundaries_and_forced_tokens(tokenizer):
    codec = ActionCodec(tokenizer, [f"box{i:04d}" for i in range(1002)])
    for index in [0, 1, 999, 1000, 1001]:
        action = Place(codec.box_index.box(index), "0010182")
        fragments = codec.action_fragments(action)
        fields = [build_sequence(tokenizer, [f]) for f in fragments]
        ids = codec.encode_action(action)
        assert ids == sum(fields, ())
        assert fields[3] == build_sequence(tokenizer, ["001"])
        assert fields[4] == build_sequence(tokenizer, ["01"])
        assert fields[5] == build_sequence(tokenizer, ["82"])
        assert len(ids) == (7 if index < 1000 else 8)
        assert codec.decode_action(ids) == action
        assert tokenizer.decode(list(ids), skip_special_tokens=False) == "".join(fragments)
    # The serialized IDs deliberately preserve row/tier boundaries even when
    # ordinary full-text BPE would group their adjacent digits differently.
    canonical = codec.encode_action(Place("box0000", "0010182"))
    retokenized = build_sequence(tokenizer, ["P000 0010182\n"])
    assert canonical != retokenized
    with pytest.raises(ValueError, match="noncanonical"):
        codec.decode_action(retokenized)


def test_trie_exact_union_at_every_prefix(codec):
    allowed = [Place("a", "0010182"), Place("a", "0010282"),
               Place("b", "0020002"), Discharge("a"), Relocate("b", "A-001-01")]
    encoded = [codec.encode_action(a) for a in allowed]
    trie = codec.trie(allowed)
    for sequence in encoded:
        for length in range(len(sequence) + 1):
            prefix = sequence[:length]
            expected = {candidate[length] for candidate in encoded
                        if len(candidate) > length and candidate[:length] == prefix}
            assert trie.allowed_next_token_ids(prefix) == expected
            assert trie.is_complete(prefix) == (prefix in encoded)
        assert codec.allowed_next_token_ids(sequence[:2], allowed) == trie.allowed_next_token_ids(sequence[:2])
    assert trie.allowed_next_token_ids([9999999]) == set()
    assert trie.node_count == trie.edge_count + 1
    assert TokenTrie(encoded * 2).node_count == trie.node_count


@pytest.mark.parametrize("text", ["X000\n", "P000\n", "O000 A-001-01\n", "R000\n",
                                  "P000 0010103\n", "D999\n", "P000 0010182", "O000\nextra"])
def test_malformed_actions(codec, text):
    with pytest.raises(ValueError):
        codec.decode_action(build_sequence(codec.tokenizer, [text]))


def test_malformed_plan_and_relocate(codec):
    with pytest.raises(ValueError, match="end-of-plan"):
        codec.decode_plan(codec.encode_action(RestowOff("a")))
    with pytest.raises(ValueError, match="relocation"):
        codec.encode_action(Relocate("a", None))
    with pytest.raises(ValueError):
        codec.decode_plan((*codec.end_plan_tokens, *codec.end_plan_tokens))
    with pytest.raises(ValueError):
        build_sequence(codec.tokenizer, [[-1]])


@pytest.mark.parametrize("rung", ["tiny", "small", "medium", "full"])
def test_roundtrip_each_real_ladder_rung(tokenizer, rung):
    paths = sorted(p for p in (Path("experiments/instances") / rung).glob("*.json")
                   if not p.name.endswith(".audit.json"))
    assert paths, f"missing ladder rung {rung}"
    for path in paths:
        instance = load_instance(path)
        codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
        # Format round trips are independent of legality, covered by the checker.
        actions = [Place(box, instance.vessel.cells[i % len(instance.vessel.cells)].id)
                   for i, box in enumerate(instance.load_list)]
        assert codec.decode_plan(codec.encode_plan(actions)) == actions


def test_expanded_sample_action_plans(tokenizer):
    for seed in range(4):
        instance = tiny_instance(seed, layover=bool(seed % 2))
        actions = random_actions(instance, seed)
        codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
        assert codec.decode_plan(codec.encode_plan(actions)) == actions

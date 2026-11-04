"""CPU-only local-tokenizer fixtures; no model, sealed data or network access."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from tokenizers import Tokenizer

from experiments.mvp.reader.schema import REFUSAL_REASONS, SCHEMA_VERSION, validate_answer
from experiments.mvp.reader.v3.decoder import (
    DECODING_POLICY, ReaderDecoder, ReaderLogitsProcessor, TokenVocabulary,
    context_json_schema, masked_logits,
)

TOKENIZER = Path(__file__).resolve().parents[4] / "models/glimmer-30b-4bit/tokenizer.json"
CONTEXT = {"bays": [1, 2, 3], "max_tiers": {"hold": 4, "deck": 3},
           "max_tiers_by_bay": {"1": {"hold": 2, "deck": 0}},
           "has_accommodation": True, "hatch_covers": ["h1"]}


@pytest.fixture(scope="module")
def tokenizer():
    # Actual production vocabulary and byte mapping, not a character tokenizer.
    return Tokenizer.from_file(str(TOKENIZER))


@pytest.fixture(scope="module")
def vocabulary(tokenizer):
    return TokenVocabulary(tokenizer)


def encoded(tokenizer, text):
    return tokenizer.encode(text, add_special_tokens=False).ids


def envelope(payload=None, code=None):
    if code:
        payload = {"refused": True, "code": code, "reason": REFUSAL_REASONS[code]}
    return {"schema_version": SCHEMA_VERSION, "refusal" if code else "constraint": payload}


def compact(answer):
    return json.dumps(answer, separators=(",", ":"))


VALID = [
    envelope({"type": "close_bay", "bay": 2}),
    envelope({"type": "max_tiers", "bay": 1, "zone": "hold", "max_tiers": 2}),
    envelope({"type": "max_tiers", "bay": 1, "zone": "deck", "max_tiers": 0}),
    envelope({"type": "max_tiers", "bay": None, "zone": "hold", "max_tiers": 4}),
    envelope({"type": "max_tiers", "bay": 3, "zone": "deck", "max_tiers": 3}),
    envelope({"type": "dg_keep_away", "imdg_class": "2.1", "min_bays": 20,
              "away_from": {"kind": "bay", "bay": 3}}),
    envelope({"type": "dg_keep_away", "imdg_class": "9", "min_bays": 1,
              "away_from": {"kind": "accommodation"}}),
    envelope({"type": "one_port_per_hatch"}),
    *[envelope(code=code) for code in REFUSAL_REASONS],
]


@pytest.mark.parametrize("answer", VALID)
def test_full_text_tokenization_passes_every_prefix(tokenizer, vocabulary, answer):
    text = compact(answer)
    decoder = ReaderDecoder(tokenizer, CONTEXT, vocabulary=vocabulary)
    ids = encoded(tokenizer, text)
    # Full-text BPE can cross grammar fragment boundaries; no field retokenizing.
    assert any(len(vocabulary.text_by_id[t]) > 1 for t in ids)
    assert "".join(vocabulary.text_by_id[t] for t in ids) == text
    for token in ids:
        assert decoder.status == "running"
        assert token in decoder.allowed_token_ids()
        decoder.advance(token)
    assert decoder.status == "complete"
    assert decoder.text == text
    assert decoder.tokens == ids
    assert decoder.allowed_token_ids() == ()
    assert decoder.validate_output(text) == text
    validate_answer(text, CONTEXT)


def test_exact_bytelevel_mapping_and_special_exclusion(tokenizer, vocabulary):
    for text in ('{"schema_version":', '"stowlm.reader/2",', '"bay":123}', 'true',
                 '"reason":"The instruction would relax a constraint."', ' ,\n'):
        ids = encoded(tokenizer, text)
        assert "".join(vocabulary.text_by_id[t] for t in ids) == text
        assert tokenizer.decode(ids, skip_special_tokens=False) == text
    for special in ("<|eot|>", "<|eom|>", "<|end_of_text|>", "<|begin_of_text|>"):
        assert tokenizer.token_to_id(special) not in vocabulary.text_by_id


@pytest.mark.parametrize("payload", [
    {"type": "close_bay", "bay": 5},
    {"type": "close_bay", "bay": True},
    {"type": "max_tiers", "bay": 1, "zone": "hold", "max_tiers": 3},
    {"type": "max_tiers", "bay": 1, "zone": "deck", "max_tiers": 1},
    {"type": "max_tiers", "bay": None, "zone": "deck", "max_tiers": 4},
    {"type": "dg_keep_away", "imdg_class": "3.1", "min_bays": 1,
     "away_from": {"kind": "bay", "bay": 1}},
    {"type": "dg_keep_away", "imdg_class": "1", "min_bays": 21,
     "away_from": {"kind": "bay", "bay": 1}},
    {"type": "close_bay", "bay": 1, "extra": 2},
])
def test_invalid_tokenized_routes_cannot_complete(tokenizer, vocabulary, payload):
    text = compact(envelope(payload))
    decoder = ReaderDecoder(tokenizer, CONTEXT, vocabulary=vocabulary)
    rejected = False
    for token in encoded(tokenizer, text):
        if token not in decoder.allowed_token_ids():
            rejected = True
            with pytest.raises(ValueError, match="outside reader schema"):
                decoder.advance(token)
            break
        decoder.advance(token)
    assert rejected
    with pytest.raises(ValueError):
        decoder.validate_output(text)


def test_context_filters_hatches_accommodation_and_empty_bays(tokenizer, vocabulary):
    context = {"bays": [], "max_tiers": {"hold": 0, "deck": 0},
               "has_accommodation": False, "hatch_covers": []}
    decoder = ReaderDecoder(tokenizer, context, vocabulary=vocabulary)
    for answer in [envelope({"type": "close_bay", "bay": 1}),
                   envelope({"type": "one_port_per_hatch"}),
                   envelope({"type": "dg_keep_away", "imdg_class": "1", "min_bays": 1,
                             "away_from": {"kind": "accommodation"}})]:
        with pytest.raises(ValueError):
            decoder.validate_output(compact(answer))
    assert decoder.validate_output(compact(envelope({"type": "max_tiers", "bay": None,
                                                    "zone": "hold", "max_tiers": 0})))
    assert decoder.validate_output(compact(envelope(code="unsupported")))


@pytest.mark.parametrize("bad_suffix", ["x", "}", "\n", " {}", ",\"extra\":1"])
def test_no_suffix_or_second_object(tokenizer, vocabulary, bad_suffix):
    decoder = ReaderDecoder(tokenizer, CONTEXT, vocabulary=vocabulary)
    good = compact(envelope(code="loosening"))
    with pytest.raises(ValueError):
        decoder.validate_output(good + bad_suffix)
    with pytest.raises(ValueError):
        decoder.validate_output(good[:-1])


def test_fixed_reason_and_schema_shared_with_spark(tokenizer, vocabulary):
    schema = context_json_schema(CONTEXT)
    assert len(schema["anyOf"]) == 2
    constraint = schema["anyOf"][0]["properties"]["constraint"]["anyOf"]
    tiers = [c["properties"] for c in constraint if c["properties"]["type"] == {"const": "max_tiers"}]
    assert next(p["max_tiers"]["maximum"] for p in tiers
                if p["bay"] == {"const": 1} and p["zone"] == {"const": "deck"}) == 0
    refusals = schema["anyOf"][1]["properties"]["refusal"]["anyOf"]
    assert {r["properties"]["code"]["const"]: r["properties"]["reason"]["const"]
            for r in refusals} == REFUSAL_REASONS
    assert all(not item["additionalProperties"] for item in schema["anyOf"])
    changed = envelope(code="loosening")
    changed["refusal"]["reason"] = "a different valid free-form reason"
    decoder = ReaderDecoder(tokenizer, CONTEXT, vocabulary=vocabulary)
    with pytest.raises(ValueError):
        decoder.validate_output(compact(changed))
    assert DECODING_POLICY == "reader-v3-schema-mask/2-d224"


def test_logits_processor_prompt_offset_repeats_and_completion(tokenizer, vocabulary):
    decoder = ReaderDecoder(tokenizer, CONTEXT, vocabulary=vocabulary)
    stop = tokenizer.token_to_id("<|eot|>")
    prompt = [11, 22, 33]
    processor = ReaderLogitsProcessor(decoder, len(prompt), stop)
    logits = np.linspace(-1, 1, tokenizer.get_vocab_size(), dtype=np.float32)[None]
    answer = compact(envelope({"type": "close_bay", "bay": 2}))
    ids = encoded(tokenizer, answer)
    history = list(prompt)
    for token in ids:
        masked = processor(np.array(history), logits)
        allowed = decoder.allowed_token_ids()
        assert np.array_equal(np.flatnonzero(np.isfinite(masked[0])), np.array(allowed))
        assert np.array_equal(masked[0, list(allowed)], logits[0, list(allowed)])
        assert np.isneginf(masked[0, stop])
        # Repeating a callback without a new sampled token must be idempotent.
        assert np.array_equal(masked, processor(np.array(history), logits))
        history.append(token)
    finished = processor(np.array(history), logits)
    assert decoder.status == "complete"
    assert np.flatnonzero(np.isfinite(finished[0])).tolist() == [stop]
    assert processor.validate_output(answer) == answer
    with pytest.raises(ValueError):
        processor.validate_output(answer[:-1])
    history.append(stop)
    assert np.flatnonzero(np.isfinite(processor(np.array(history), logits)[0])).tolist() == [stop]
    with pytest.raises(ValueError, match="reused"):
        processor(np.array(prompt), logits)


def test_output_validation_catches_max_tokens_before_callback(tokenizer, vocabulary):
    decoder = ReaderDecoder(tokenizer, CONTEXT, vocabulary=vocabulary)
    # generate can stop immediately after the last sampled token, before another
    # logits callback. Validation must inspect the emitted text, not stale state.
    assert decoder.status == "running"
    assert decoder.validate_output(compact(envelope(code="ambiguous")))
    with pytest.raises(ValueError):
        decoder.validate_output('{"schema_version":')


def test_mask_bias_fails_closed_and_preserves_allowed_logits():
    logits = np.array([3.0, 7.0, 2.0], dtype=np.float32)
    masked = masked_logits(logits, [0, 2])
    assert masked.tolist() == [3.0, -np.inf, 2.0]
    assert logits.tolist() == [3.0, 7.0, 2.0]
    for allowed in ([], [-1], [3]):
        with pytest.raises(ValueError):
            masked_logits(logits, allowed)
    with pytest.raises(ValueError, match="batch size one"):
        masked_logits(np.zeros((2, 3)), [0])


def test_reject_unsupported_tokenizer_and_bad_context():
    class WrongTokenizer:
        def to_str(self):
            return json.dumps({"model": {"type": "WordPiece"}, "decoder": {"type": "WordPiece"}})
    with pytest.raises(ValueError, match="ByteLevel"):
        TokenVocabulary(WrongTokenizer())
    with pytest.raises(ValueError, match="bays"):
        context_json_schema(dict(CONTEXT, bays=[True]))
    with pytest.raises(ValueError, match="tier limits"):
        context_json_schema(dict(CONTEXT, max_tiers={"hold": -1, "deck": 3}))


@pytest.mark.parametrize('key', ['allowed_imdg_classes', 'imdg_classes', 'declared_imdg_classes'])
def test_context_class_declarations_mask_absent_schema_valid_classes(tokenizer, vocabulary, key):
    context = dict(CONTEXT, **{key: ['2.1', '3']})
    decoder = ReaderDecoder(tokenizer, context, vocabulary=vocabulary)
    good = compact(envelope({'type': 'dg_keep_away', 'imdg_class': '2.1', 'min_bays': 1,
                             'away_from': {'kind': 'bay', 'bay': 1}}))
    assert decoder.validate_output(good) == good
    absent = good.replace('"2.1"', '"2"')  # No invented whole-class expansion.
    validate_answer(absent, context)  # Base schema permits it; v3 context mask cannot.
    with pytest.raises(ValueError):
        decoder.validate_output(absent)
    for token in encoded(tokenizer, absent):
        if token not in decoder.allowed_token_ids():
            break
        decoder.advance(token)
    else:
        pytest.fail('absent class reached a complete masked output')


def test_legacy_class_fallback_and_explicit_empty_declaration(tokenizer, vocabulary):
    answer = compact(envelope({'type': 'dg_keep_away', 'imdg_class': '9', 'min_bays': 1,
                               'away_from': {'kind': 'bay', 'bay': 1}}))
    # Missing/empty cargo does not mean there are zero classes authorized.
    for context in (CONTEXT, dict(CONTEXT, cargo=[]), dict(CONTEXT, containers=[])):
        assert ReaderDecoder(tokenizer, context, vocabulary=vocabulary).validate_output(answer)
    explicit_empty = ReaderDecoder(tokenizer, dict(CONTEXT, allowed_imdg_classes=[]), vocabulary=vocabulary)
    with pytest.raises(ValueError):
        explicit_empty.validate_output(answer)

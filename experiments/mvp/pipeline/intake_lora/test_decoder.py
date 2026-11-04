"""Offline grammar tests: mock BPE vocabulary, including cross-field tokens."""
import json
import string
from types import SimpleNamespace

import numpy as np
import pytest

from .decoder import IntakeDecoder, IntakeLogitsProcessor, TokenVocabulary, _IntakeGrammar
from .schema import compact_json, context_from_instance, context_json_schema, prompt_messages, validate_answer

CONTEXT = {'pods': ['SGSIN', 'THLCH'], 'iso_types': ['22G1', '45R1'], 'dg_classes': ['3', '9']}
ROW = {'id': 'SYN U1234567'.replace(' ', ''), 'iso_type': '22G1', 'length_ft': 20,
       'gross_mass_kg': 23456.125, 'pod': 'SGSIN', 'reefer': False,
       'reefer_setpoint_c': None, 'dg': None}


@pytest.fixture
def vocabulary():
    chunks = list(string.ascii_letters + string.digits + string.punctuation) + ['{"id":"', '","iso_type":"', 'null,"dg":null}']
    vocab = {text: i for i, text in enumerate(chunks)}
    spec = {'decoder': {'type': 'ByteLevel'}, 'model': {'type': 'BPE', 'vocab': vocab}}
    tokenizer = SimpleNamespace(to_str=lambda: json.dumps(spec))
    return TokenVocabulary(tokenizer)


def test_mask_all_prefixes_and_cross_field_tokens(vocabulary):
    decoder = IntakeDecoder(None, CONTEXT, vocabulary=vocabulary)
    remaining = compact_json(ROW)
    while remaining:
        ids = decoder.allowed_token_ids()
        candidates = [i for i in ids if remaining.startswith(vocabulary.text_by_id[i])]
        assert candidates
        token = max(candidates, key=lambda i: len(vocabulary.text_by_id[i]))
        decoder.advance(token)
        remaining = remaining[len(vocabulary.text_by_id[token]):]
    assert decoder.status == 'complete'
    assert decoder.allowed_token_ids() == ()
    assert decoder.validate_output(compact_json(ROW)) == compact_json(ROW)


@pytest.mark.parametrize('update', [
    {'pod': 'USLAX'}, {'iso_type': 'L5G1'}, {'gross_mass_kg': 100000},
    {'gross_mass_kg': 0}, {'gross_mass_kg': True}, {'length_ft': 40},
    {'reefer': True}, {'reefer_setpoint_c': -18}, {'id': 'ABCU1234'},
    {'dg': {'un_number': '1203', 'imdg_class': '8'}},
])
def test_reject_wrong_and_outside_values(update, vocabulary):
    decoder = IntakeDecoder(None, CONTEXT, vocabulary=vocabulary)
    with pytest.raises(ValueError):
        decoder.validate_output(compact_json({**ROW, **update}))


@pytest.mark.parametrize('value', [1, 1.001, 99, 99999, 99999.0, 23456.125])
def test_mass_endpoints(value, vocabulary):
    row = {**ROW, 'gross_mass_kg': value}
    IntakeDecoder(None, CONTEXT, vocabulary=vocabulary).validate_output(compact_json(row))


@pytest.mark.parametrize('value', [-60, -60.0, -59.999, -0.5, 0, 0.001, 39.999, 40.0])
def test_setpoint_endpoints(value, vocabulary):
    row = {**ROW, 'iso_type': '45R1', 'length_ft': 40, 'reefer': True, 'reefer_setpoint_c': value}
    IntakeDecoder(None, CONTEXT, vocabulary=vocabulary).validate_output(compact_json(row))


@pytest.mark.parametrize('text', ['{}', ' {"refusal":{"code":"AMBIGUOUS"}}',
                                 '{"refusal":{"code":"AMBIGUOUS"}} trailing',
                                 '{"refusal":{"code":"AMBIGUOUS","code":"CONFLICT"}}'])
def test_never_repair_invalid_or_truncated_output(text, vocabulary):
    with pytest.raises(ValueError):
        IntakeDecoder(None, CONTEXT, vocabulary=vocabulary).validate_output(text)


def test_refusal_and_eos(vocabulary):
    decoder = IntakeDecoder(None, CONTEXT, vocabulary=vocabulary)
    token_ids = {text: i for i, text in vocabulary.text_by_id.items()}
    text = '{"refusal":{"code":"AMBIGUOUS"}}'
    for char in text:
        decoder.advance(token_ids[char])
    stop = max(vocabulary.text_by_id) + 1
    processor = IntakeLogitsProcessor(decoder, 0, stop)
    scores = processor([], np.zeros(stop + 1))
    assert np.flatnonzero(np.isfinite(scores)).tolist() == [stop]


def test_context_does_not_read_target_or_text():
    base = {'rotation': {'calls': [{'locode': 'THLCH'}, {'locode': 'SGSIN'}]}, 'call_index': 0,
            'vessel': {'allowed_iso_types': ['22G1'], 'allowed_imdg_classes': ['3']},
            'containers': [{'pod': 'USLAX', 'iso_type': 'L5G1', 'dg': {'imdg_class': '9'}}]}
    context = context_from_instance(base)
    assert context == {'pods': ['SGSIN'], 'iso_types': ['22G1'], 'dg_classes': ['3']}
    messages = prompt_messages('Thai ไทย', context)
    assert [m['role'] for m in messages] == ['system', 'user']
    assert messages[-1]['content'] == 'Thai ไทย'
    assert 'USLAX' not in messages[0]['content']


def test_number_grammar_size_is_bounded():
    grammar = _IntakeGrammar(context_json_schema(CONTEXT))
    seen, pending = set(), list(grammar.start)
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(node.epsilon)
        pending.extend(child for choices in node.edges.values() for child in choices)
    assert len(seen) < 2000  # excludes per-weight enumeration

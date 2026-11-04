"""Offline checks generate development examples only; sealed bytes stay unopened."""
import hashlib
import json

import pytest

from experiments.mvp.pipeline.intake_lora import build_data as data
from experiments.mvp.pipeline.intake_lora.schema import REFUSAL_CODES, compact_json, prompt_messages, validate_answer


def test_development_shapes_prompt_parity_and_refusal_coverage():
    for split, count in [('train', 192), ('valid', 96)]:
        rows = list(data.make_rows(split, count))
        assert {row['expected']['refusal']['code'] for row in rows if 'refusal' in row['expected']} == set(REFUSAL_CODES)
        assert {row['family'] for row in rows} == set(data.TEMPLATES[split])
        assert any(any('\u0e00' <= char <= '\u0e7f' for char in row['text']) for row in rows)
        for row in rows:
            assert set(row) == {'id', 'family', 'text', 'context', 'expected', 'messages'}
            assert row['context'] == data.CONTEXT
            assert row['messages'][:-1] == prompt_messages(row['text'], row['context'])
            assert row['messages'][-1] == {'role': 'assistant', 'content': compact_json(row['expected'])}
            assert validate_answer(row['expected'], row['context']) == row['expected']
            if 'refusal' not in row['expected']:
                assert row['expected']['id'] in row['text']
                assert row['expected']['pod'] in row['text']
                if row['expected']['dg']:
                    assert row['expected']['dg']['un_number'] in row['text']
                    assert f"class {row['expected']['dg']['imdg_class']}" in row['text']
                else:
                    assert 'non-DG' in row['text'] or 'no dangerous goods' in row['text']


def test_split_metadata_has_disjoint_templates_identity_and_numeric_pools():
    # Inspect public generator design, never instantiate sealed examples.
    splits = list(data.SPLITS)
    for i, left in enumerate(splits):
        for right in splits[i + 1:]:
            a, b = data.SPLITS[left], data.SPLITS[right]
            assert set(data.TEMPLATES[left]).isdisjoint(data.TEMPLATES[right])
            assert a['owner'] != b['owner']
            assert set(a['setpoints']).isdisjoint(b['setpoints'])
            assert a['mass_start'] + a['count'] * 2 <= b['mass_start']
            assert a['serial_start'] + 5000 + a['count'] < b['serial_start']


def test_development_values_do_not_leak_between_splits():
    train = [r['expected'] for r in data.make_rows('train', 192) if 'refusal' not in r['expected']]
    valid = [r['expected'] for r in data.make_rows('valid', 96) if 'refusal' not in r['expected']]
    for key in ('id', 'gross_mass_kg'):
        assert {r[key] for r in train}.isdisjoint(r[key] for r in valid)
    assert {r['reefer_setpoint_c'] for r in train if r['reefer']}.isdisjoint(r['reefer_setpoint_c'] for r in valid if r['reefer'])


def test_writer_is_exclusive_and_hashes_the_bytes_it_writes(tmp_path):
    path = tmp_path / 'development.jsonl'
    result = data.write_jsonl_exclusive(path, data.make_rows('valid', 12))
    content = path.read_bytes()  # Development-only fixture, never sealed.
    assert result['sha256'] == hashlib.sha256(content).hexdigest()
    assert result['count'] == len(content.splitlines()) == 12
    assert [json.loads(line)['id'] for line in content.splitlines()] == [f'valid-{i:05d}' for i in range(12)]
    with pytest.raises(FileExistsError):
        data.write_jsonl_exclusive(path, data.make_rows('valid', 1))
    assert path.read_bytes() == content


def test_synthetic_container_id_checksum_matches_known_example():
    assert data.container_id('CSQ', 305438) == 'CSQU3054383'


def test_missing_and_unclear_fields_are_refusals():
    rows = list(data.make_rows('train', 96))
    missing = [r for r in rows if r['expected'] == {'refusal': {'code': 'MISSING_FIELD'}}]
    assert len(missing) == 16
    assert all('not supplied' in row['text'] for row in missing)
    ambiguous = [r for r in rows if r['expected'] == {'refusal': {'code': 'AMBIGUOUS'}}]
    assert all('unconfirmed' in row['text'] for row in ambiguous)
    outside = [r for r in rows if r['expected'] == {'refusal': {'code': 'OUT_OF_CONTEXT'}}]
    assert all('HKHKG' in row['text'] and 'HKHKG' not in row['context']['pods'] for row in outside)


def test_public_context_cannot_contain_identity_mass_or_setpoint():
    assert set(data.CONTEXT) == {'pods', 'iso_types', 'dg_classes'}
    for row in data.make_rows('valid', 12):
        if 'refusal' not in row['expected']:
            encoded = compact_json(row['context'])
            assert row['expected']['id'] not in encoded
            assert str(row['expected']['gross_mass_kg']) not in encoded

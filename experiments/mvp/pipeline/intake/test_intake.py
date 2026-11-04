"""Public API, immutable recordings and the base-instance assembly invariant."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import IntakeResult, sha256_file, sha256_json
from experiments.mvp.pipeline.intake import answer_questions, read_documents
from experiments.mvp.pipeline.intake.measure import assemble_rows, score_case
from experiments.mvp.pipeline.intake.spark_reader import RecordedClient
from stowage.model.instance import Instance

HERE = Path(__file__).parent
CASE = HERE / 'fixtures' / 'dev'


def _inputs():
    return (json.loads((CASE / 'documents.json').read_text()),
            json.loads((CASE / 'base_instance.json').read_text()),
            json.loads((CASE / 'spark_raw.json').read_text()))


def test_real_recording_all_traps_and_assembly():
    metrics = score_case(CASE, save=False)
    assert metrics['traps_caught'] == metrics['traps_total'] == 7
    assert metrics['before_answers']['silent_wrong_values'] == 0
    assert metrics['after_answers']['silent_wrong_values'] == 0
    assert metrics['after_answers']['row_exact_accuracy'] == 1
    assert metrics['assembled_sha256_equals_base']
    assert metrics['status'] == 'complete'


def test_answers_survive_json_roundtrip():
    documents, base, raw = _inputs()
    # Old receipts and their existing answer files remain usable by the demo.
    result = IntakeResult.model_validate_json((CASE / 'reading.json').read_text())
    result = IntakeResult.model_validate_json(result.model_dump_json())
    answers = json.loads((CASE / 'answers.json').read_text())
    final = answer_questions(result, answers)
    # Legacy receipt shapes still round-trip, but newly required confirmations
    # cannot be inherited from a formerly permissive validator.
    assert {q.reason for q in final.questions} <= {'unsupported_source', 'unverified_transcription', 'missing_field'}
    truth = {row['id']: row for row in json.loads((CASE / 'expected.json').read_text())['rows']}
    extra = {}
    for q in final.questions:
        value = truth[final.rows[q.row]['id']]
        for part in q.field.split('.'):
            value = value[int(part)] if isinstance(value, list) else value[part]
        extra[q.id] = value
    final = answer_questions(final, extra)
    assert final.status == 'complete', final.questions
    assert sha256_json(assemble_rows(base, final.rows).model_dump(mode='json')) == sha256_json(Instance.model_validate(base).model_dump(mode='json'))
    assert all(record.mode.value == 'recorded' for record in final.records)
    assert any(source['document'].startswith('planner-answer-') for source in final.field_sources)


def test_invalid_answer_is_questioned_again():
    documents, base, raw = _inputs()
    result = read_documents(documents, base_instance=base, client=RecordedClient(raw))
    question = next(q for q in result.questions if q.field == 'gross_mass_kg')
    final = answer_questions(result.model_dump(mode='json'), {question.id: -1})
    assert final.status == 'needs_answers'
    assert any(q.field == 'gross_mass_kg' and q.row == question.row for q in final.questions)
    with pytest.raises(ValueError, match='unknown question'):
        answer_questions(result, {'made-up-question': 1})


def test_id_correction_moves_extraction_group():
    documents, base, raw = _inputs()
    rid = base['load_list'][0]
    raw = deepcopy(raw)
    for observation in raw['observations']:
        if observation['row_id'] == rid:
            observation['row_id'] = 'misread-id'
    result = read_documents(documents, base_instance=base, client=RecordedClient(raw))
    question = next(q for q in result.questions if q.id.startswith('misread-id:') and q.field == 'id')
    field_question = next(q for q in result.questions if q.id.startswith('misread-id:pod:'))
    final = answer_questions(result, {question.id: rid, field_question.id: 'HKHKG'})
    assert not any(row.get('id') == 'misread-id' for row in final.rows)
    assert not any(q.reason == 'row_identity_mismatch' for q in final.questions)
    assert not any(q.id.startswith('misread-id:') for q in final.questions)


def test_explicit_parent_correction_clears_false_dg_declaration():
    documents, base, raw = _inputs()
    rid = next(row['id'] for row in base['containers'] if row['id'] in base['load_list'] and row['dg'] is None)
    raw['observations'] = [o for o in raw['observations'] if not (o['row_id'] == rid and o['field'] == 'dg')]
    text = json.dumps({'id': rid, 'dg': {'un_number': '9999', 'imdg_class': '3'}})
    documents.append({'id': 'dg_declaration', 'text': text})
    raw['observations'].append({'row_id': rid, 'field': 'dg.un_number', 'value': '9999', 'document': 'dg_declaration', 'quote': text})
    result = read_documents(documents, base_instance=base, client=RecordedClient(raw))
    question = next(q for q in result.questions if q.id.startswith(rid + ':dg.') and q.reason == 'missing_field')
    final = answer_questions(result, {question.id: {'field': 'dg', 'value': None, 'quote': 'Planner confirms non-DG cargo'}})
    row_index = next(i for i, row in enumerate(final.rows) if row.get('id') == rid)
    assert final.rows[row_index]['dg'] is None
    assert not any(q.row == row_index and (q.field or '').startswith('dg') for q in final.questions)
    with pytest.raises(ValueError, match='questioned field'):
        answer_questions(result, {question.id: {'field': 'pod', 'value': 'HKHKG'}})


def test_frozen_inputs_unchanged():
    freeze = HERE / 'fixtures' / 'freeze.json'
    assert sha256_file(freeze) == (freeze.with_suffix('.sha256')).read_text().split()[0]
    manifest = json.loads(freeze.read_text())
    assert manifest['frozen_before_scoring'] is True
    for case in manifest['cases']:
        for name, expected in case['files'].items():
            assert sha256_file(HERE / 'fixtures' / case['split'] / name) == expected

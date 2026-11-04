"""The public scoring entry point must enforce the same gates as its wrapper."""
import hashlib
import json

import pytest

from experiments.mvp.pipeline.intake import evaluate_v2, measure
from experiments.mvp.pipeline.intake.spark_reader import document_content, extraction_prompt
from experiments.mvp.pipeline.contract.schema import sha256_json


@pytest.fixture
def heldout_case(tmp_path):
    cohort = tmp_path / 'heldout_v2'
    case = cohort / 'case_01'
    case.mkdir(parents=True)
    (cohort / 'freeze.json').write_text(json.dumps({'evaluation_split': 'heldout_v2', 'cases': [{'split': case.name}]}))
    return case


def test_public_scorer_checks_freeze_before_reading_or_persisting(heldout_case, monkeypatch):
    def refuse(*args, **kwargs):
        raise ValueError('frozen implementation changed')
    monkeypatch.setattr(evaluate_v2, 'verify_frozen', refuse)
    with pytest.raises(ValueError, match='frozen implementation changed'):
        measure.score_case(heldout_case)
    assert not (heldout_case / 'metrics.json').exists()


def test_public_scorer_cannot_substitute_another_case_recording(heldout_case, tmp_path):
    other = tmp_path / 'development.json'
    other.write_text('{}')
    with pytest.raises(ValueError, match='recording substitution'):
        measure.score_case(heldout_case, raw_path=other, save=False)


def test_missing_manifest_cannot_turn_heldout_into_development(heldout_case):
    (heldout_case.parent / 'freeze.json').unlink()
    with pytest.raises(FileNotFoundError, match='SOURCE_FREEZE'):
        measure.score_case(heldout_case, save=False)


def test_symlink_alias_cannot_bypass_heldout_classification(heldout_case, tmp_path, monkeypatch):
    alias = tmp_path / 'development_alias'
    alias.symlink_to(heldout_case, target_is_directory=True)
    def refuse(*args, **kwargs):
        raise ValueError('heldout gate reached')
    monkeypatch.setattr(evaluate_v2, 'verify_frozen', refuse)
    with pytest.raises(ValueError, match='heldout gate reached'):
        measure.score_case(alias, save=False)


def test_heldout_recording_must_bind_exact_frozen_documents(heldout_case, monkeypatch):
    monkeypatch.setattr(evaluate_v2, 'verify_frozen', lambda *args: {'cases': [{'split': heldout_case.name}]})
    for name, value in [('expected', {'rows': []}), ('base_instance', {}),
                        ('documents', [{'id': 'booking', 'text': 'Synthetic booking A'}]),
                        ('spark_raw', {'observations': [], 'metadata': {'documents_sha256': 'another case'}})]:
        (heldout_case / f'{name}.json').write_text(json.dumps(value))
    monkeypatch.setattr(measure, 'read_documents', lambda *a, **kw: pytest.fail('unbound recording reached validator'))
    with pytest.raises(ValueError, match='recording.*documents'):
        measure.score_case(heldout_case, save=False)


@pytest.fixture
def bound_recording(heldout_case, monkeypatch):
    monkeypatch.setattr(evaluate_v2, 'verify_frozen', lambda *a: {'cases': [{'split': heldout_case.name}]})
    documents = [{'id': 'booking', 'text': 'Synthetic booking A'}]
    proposal = {'observations': [], 'duplicates': [], 'notes': []}
    text = json.dumps(proposal)
    metadata = {'documents_sha256': sha256_json(document_content(documents)),
                'prompt_sha256': hashlib.sha256(extraction_prompt().encode()).hexdigest(),
                'request_id': 'synthetic-request', 'raw_text': text}
    for name, value in [('expected', {'rows': []}), ('base_instance', {}), ('documents', documents),
                        ('spark_raw', {**proposal, 'metadata': metadata}),
                        ('spark_response', {'text': text, 'request_id': 'synthetic-request'})]:
        (heldout_case / f'{name}.json').write_text(json.dumps(value))
    return heldout_case


def test_matching_recording_reaches_validator(bound_recording, monkeypatch):
    class ReachedValidator(Exception):
        pass
    def reached(*args, **kwargs):
        raise ReachedValidator
    monkeypatch.setattr(measure, 'read_documents', reached)
    with pytest.raises(ReachedValidator):
        measure.score_case(bound_recording, save=False)


@pytest.mark.parametrize('mutation,reason', [('prompt', 'different prompt'),
                                         ('response', 'captured response'),
                                         ('proposal', 'proposal changed')])
def test_recording_capture_cannot_be_detached(bound_recording, monkeypatch, mutation, reason):
    path = bound_recording / 'spark_raw.json'
    raw = json.loads(path.read_text())
    if mutation == 'prompt':
        raw['metadata']['prompt_sha256'] = 'changed'
    elif mutation == 'response':
        raw['metadata']['request_id'] = 'from another case'
    else:
        raw['notes'] = ['modified after capture']
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(measure, 'read_documents', lambda *a, **kw: pytest.fail('detached recording reached validator'))
    with pytest.raises(ValueError, match=reason):
        measure.score_case(bound_recording, save=False)

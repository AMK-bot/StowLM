"""Fail-closed source/input freezes, independent of any held-out reading."""
import hashlib
import json

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file
from experiments.mvp.pipeline.intake import evaluate_v2


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    here = tmp_path / 'intake'
    cohort = here / 'fixtures' / 'heldout_v2'
    case = cohort / 'case_01'
    case.mkdir(parents=True)
    source = here / 'source.py'
    source.write_text('frozen implementation')
    document = case / 'document.txt'
    document.write_text('Synthetic input')
    manifest = cohort / 'freeze.json'
    manifest.write_text(json.dumps({'cases': [{'split': 'case_01', 'files': {'document.txt': sha256_file(document)}}]}))
    (cohort / 'freeze.sha256').write_text(sha256_file(manifest))
    dependency = tmp_path / 'cargo.py'
    dependency.write_text('schema and validation dependency')
    dependencies = lambda: {'cargo.py': sha256_file(dependency)}
    monkeypatch.setattr(evaluate_v2, 'dependency_fingerprints', dependencies, raising=False)
    monkeypatch.setattr(evaluate_v2, 'runtime_dependencies', lambda: {'python': 'test'}, raising=False)
    implementation = {'input_freeze_sha256': sha256_file(manifest), 'source_files': {'source.py': sha256_file(source)},
                      'prompt_sha256': hashlib.sha256(evaluate_v2.extraction_prompt().encode()).hexdigest(),
                      'dependency_files': dependencies(), 'runtime_dependencies': {'python': 'test'}}
    (cohort / 'SOURCE_FREEZE.json').write_text(json.dumps(implementation))
    monkeypatch.setattr(evaluate_v2, 'HERE', here)
    monkeypatch.setattr(evaluate_v2, 'COHORT', cohort)
    return here, cohort, document


def test_requires_source_freeze_before_recording(frozen):
    _, cohort, _ = frozen
    (cohort / 'SOURCE_FREEZE.json').unlink()
    with pytest.raises(FileNotFoundError):
        evaluate_v2.heldout()


def test_changed_source_cannot_be_scored(frozen):
    here, _, _ = frozen
    (here / 'source.py').write_text('changed implementation')
    with pytest.raises(ValueError, match='implementation changed'):
        evaluate_v2.verify_frozen()


def test_changed_source_document_cannot_be_scored(frozen):
    _, _, document = frozen
    document.write_text('changed input')
    with pytest.raises(ValueError, match='input changed'):
        evaluate_v2.verify_frozen()


def test_rehashed_manifest_cannot_replace_frozen_manifest(frozen):
    _, cohort, _ = frozen
    manifest = cohort / 'freeze.json'
    manifest.write_text(manifest.read_text() + '\n')
    (cohort / 'freeze.sha256').write_text(sha256_file(manifest))
    with pytest.raises(ValueError, match='different input freeze'):
        evaluate_v2.verify_frozen()


def test_effective_prompt_change_fails_before_recording(frozen, monkeypatch):
    monkeypatch.setattr(evaluate_v2, 'extraction_prompt', lambda: 'changed effective schema prompt')
    monkeypatch.setattr(evaluate_v2, 'record_case', lambda *a: pytest.fail('paid read reached'))
    with pytest.raises(ValueError, match='prompt changed'):
        evaluate_v2.heldout()


def test_effective_prompt_is_read_from_the_active_reader(frozen, monkeypatch):
    monkeypatch.setattr(evaluate_v2.spark_reader, 'extraction_prompt', lambda: 'active reader changed')
    with pytest.raises(ValueError, match='prompt changed'):
        evaluate_v2.verify_frozen()


def test_external_validation_dependency_change_fails_closed(frozen):
    here, _, _ = frozen
    (here.parent / 'cargo.py').write_text('changed external validation')
    with pytest.raises(ValueError, match='dependencies changed'):
        evaluate_v2.verify_frozen()


def test_historical_freeze_without_dependency_pins_cannot_authorize_new_score(frozen):
    _, cohort, _ = frozen
    path = cohort / 'SOURCE_FREEZE.json'
    source = json.loads(path.read_text())
    del source['dependency_files']
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match='dependency pins'):
        evaluate_v2.verify_frozen()


def test_runtime_dependency_change_fails_closed(frozen, monkeypatch):
    monkeypatch.setattr(evaluate_v2, 'runtime_dependencies', lambda: {'python': 'changed'})
    with pytest.raises(ValueError, match='runtime dependencies changed'):
        evaluate_v2.verify_frozen()


def test_dependency_closure_covers_schema_validation_and_data():
    pins = evaluate_v2.dependency_fingerprints()
    required = {'src/stowage/model/cargo.py', 'src/stowage/model/base.py',
                'src/stowage/model/instance.py', 'src/stowage/model/provenance.py',
                'src/stowage/rules/imdg/dgl.py', 'src/stowage/rules/imdg/data/dgl.json',
                'src/stowage/rules/catalogue.toml', 'experiments/mvp/pipeline/contract/schema.py',
                'pyproject.toml', 'uv.lock'}
    assert required <= pins.keys()
    assert all(len(digest) == 64 for digest in pins.values())

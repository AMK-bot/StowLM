"""Fixture mutation and missing-input regressions without geometry or solves."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('historical_fixture_test_subject', HERE/'historical_fixture.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def fixture_file(tmp_path):
    path = tmp_path/subject.HISTORICAL_INSTANCE
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"cargo": 12, "tanks": [1, 2]}')
    return path


def test_hash_is_of_the_same_single_byte_read_given_to_parser(tmp_path, monkeypatch):
    path = fixture_file(tmp_path)
    original = Path.read_bytes
    reads = []
    payload = original(path)

    def changing_read(p):
        if p == path:
            reads.append(p)
            return payload if len(reads) == 1 else b'changed after read'
        return original(p)

    class Parser:
        @staticmethod
        def model_validate_json(data):
            assert isinstance(data, bytes)
            assert data is payload
            return json.loads(data)

    monkeypatch.setattr(Path, 'read_bytes', changing_read)
    instance, identity = subject.read_historical_fixture(tmp_path, Parser)
    assert instance['cargo'] == 12
    assert len(reads) == 1
    assert identity['source_instance_sha256'] == hashlib.sha256(payload).hexdigest()
    with pytest.raises(RuntimeError, match='changed'):
        subject.verify_historical_identity(tmp_path, identity)


@pytest.mark.parametrize('legacy', [True, False])
def test_saved_digest_aliases_read_real_fixture_and_reject_mutation_or_missing(tmp_path, legacy):
    path = fixture_file(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    record = ({'representative_condition': {
        'source_path': subject.HISTORICAL_INSTANCE, 'source_sha256': digest}}
        if legacy else {'source_instance_sha256': digest})
    assert subject.verify_historical_identity(tmp_path, record) == digest
    path.write_bytes(b'CORRUPTED INSTANCE')
    with pytest.raises(RuntimeError, match='changed'):
        subject.verify_historical_identity(tmp_path, record)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        subject.verify_historical_identity(tmp_path, record)


def test_source_map_cannot_omit_fixture_or_substitute_another_digest():
    identity = {'source_path': subject.HISTORICAL_INSTANCE, 'source_instance_sha256': 'abc'}
    with pytest.raises(RuntimeError, match='source map'):
        subject.require_fixture_hash({}, identity)
    with pytest.raises(RuntimeError, match='source map'):
        subject.require_fixture_hash({subject.HISTORICAL_INSTANCE: 'other'}, identity)
    subject.require_fixture_hash({subject.HISTORICAL_INSTANCE: 'abc'}, identity)

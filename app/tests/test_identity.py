"""Identity is complete content, with separate file and canonical conventions."""
import hashlib
import json

import pytest

from app.identity import instance_content_digest, record_file_digest, record_instance_digest
from stowage.checker.full import _input_bindings


def test_instance_digest_matches_checker_complete_input_binding(replay_case):
    instance, plan = replay_case
    assert 'checker.instance.sha256:' + instance_content_digest(instance) in _input_bindings(instance, plan)
    changed = instance.model_copy(update={'seed': (instance.seed or 0) + 1})
    assert changed.id == instance.id
    assert instance_content_digest(changed) != instance_content_digest(instance)


def test_raw_file_checksum_is_distinct_from_canonical_model_digest(replay_case):
    instance, _ = replay_case
    raw = json.dumps(instance.model_dump(mode='json'), indent=2).encode()
    checksum = hashlib.sha256(raw).hexdigest()
    assert checksum != instance_content_digest(instance)
    assert record_file_digest({'input_sha256': checksum}) == checksum


def test_record_binding_uses_direct_report_and_rejects_conflicts(replay_case):
    instance, _ = replay_case
    digest = instance_content_digest(instance)
    assert record_instance_digest({'report': {'provenance': {
        'parents': ['checker.instance.sha256:' + digest]}}}) == digest
    assert record_instance_digest({'result': {'provenance': {
        'parents': ['checker.instance.sha256:' + digest]}}}) is None
    with pytest.raises(ValueError, match='Conflicting'):
        record_instance_digest({'report': {'provenance': {'parents': [
            'checker.instance.sha256:' + digest, 'checker.instance.sha256:' + 'f' * 64]}}})


@pytest.mark.parametrize('record', [
    {'input_sha256': 'broken'}, {'input_sha256': 3},
    {'report': {'provenance': {'parents': ['checker.instance.sha256:broken']}}},
])
def test_malformed_binding_never_degrades_to_name_matching(record):
    with pytest.raises(ValueError, match='Malformed'):
        record_file_digest(record)
        record_instance_digest(record)

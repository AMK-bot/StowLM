"""Public synthetic fixtures only; never reads actual sealed reviewer artifacts."""
import json

import pytest

from experiments.mvp.reader import sealed_v2 as harness


def fixture_rows(count=120, loosening=30):
    rows = []
    for i in range(count):
        loose = i < loosening
        label = ({'refusal': {'refused': True, 'code': 'loosening', 'reason': 'Fixture relaxation.'}}
                 if loose else {'constraint': {'type': 'close_bay', 'bay': 1}})
        rows.append({'id': f'sealed-v2-{i+1:03}',
                     'sentence': f'SYNTHETIC UNIT TEST ONLY sentence {i+1}',
                     'expected': {'schema_version': 'stowlm.reader/2', **label},
                     'kind': 'loosening' if loose else 'close_bay',
                     'family': 'unit-test-fixture', 'loosening': loose})
    return rows


@pytest.fixture
def packet(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, 'OUT', tmp_path / 'fixture-sealed-v2')
    harness.packet()
    harness.write_json(harness.OUT / 'invocation.json', {'fixture_only': True})
    return harness.OUT


def response(rows):
    # This emulates the CLI container only; it is never stored in real OUT.
    harness.write_json(harness.OUT / 'claude_response.json', {
        'result': '\n'.join(json.dumps(row) for row in rows),
        'is_error': False, 'modelUsage': {'synthetic-unit-test-fixture': {}}})


def test_ingest_minimum_and_preserves_authored_fields(packet):
    authored = fixture_rows()
    response(authored)
    harness.ingest()
    actual = harness.rows_and_verify()
    assert len(actual) == 120
    assert sum(row['loosening'] for row in actual) == 30
    for before, after in zip(authored, actual, strict=True):
        assert {key: after[key] for key in before} == before
        assert set(after) - set(before) == {'context', 'source'}


@pytest.mark.parametrize(('count', 'loose', 'message'), [
    (119, 30, 'at least 120'), (120, 29, 'At least 30')])
def test_ingest_rejects_insufficient_counts(packet, count, loose, message):
    response(fixture_rows(count, loose))
    with pytest.raises(ValueError, match=message):
        harness.ingest()
    assert not (packet / 'cases.jsonl').exists()


def test_ingest_rejects_invalid_expected_label_without_repair(packet):
    rows = fixture_rows()
    rows[0]['expected']['refusal']['invented_field'] = 1
    response(rows)
    original = (packet / 'claude_response.json').read_bytes()
    with pytest.raises(ValueError, match='Invalid independently authored'):
        harness.ingest()
    assert (packet / 'claude_response.json').read_bytes() == original
    assert not (packet / 'cases.jsonl').exists()


def test_packet_mutation_fails_closed(packet):
    response(fixture_rows())
    path = packet / 'reviewer_prompt.txt'
    path.write_text(path.read_text() + '\nmutation')
    with pytest.raises(ValueError, match='Frozen reviewer input changed'):
        harness.ingest()
    assert not (packet / 'cases.jsonl').exists()


def test_no_silent_overwrite(packet):
    with pytest.raises(ValueError, match='already frozen'):
        harness.packet()
    response(fixture_rows())
    harness.ingest()
    original = (packet / 'cases.jsonl').read_bytes()
    with pytest.raises(ValueError, match='Never overwrite'):
        harness.ingest()
    assert (packet / 'cases.jsonl').read_bytes() == original


def test_audit_reports_aggregate_only(packet, capsys):
    response(fixture_rows())
    harness.ingest()
    rows = harness.rows_and_verify()
    other = packet / 'fixture-nonsealed.jsonl'
    other.write_text(json.dumps(rows[0]) + '\n' + json.dumps({
        'sentence': 'SYNTHETIC OTHER UNIT TEST SENTENCE', 'context': rows[0]['context']}) + '\n')
    capsys.readouterr()
    harness.audit([other])
    output = capsys.readouterr().out
    assert 'SYNTHETIC' not in output
    report = json.loads((packet / 'overlap-audit.json').read_text())
    assert report['sentence_context_duplicate_count'] == 1
    assert report['other_rows'] == 2
    assert report['prior_sealed_accessed'] is False


def test_tampered_sealed_bytes_fail_scoring(packet):
    response(fixture_rows())
    harness.ingest()
    path = packet / 'cases.jsonl'
    path.write_text(path.read_text() + '\n')
    with pytest.raises(ValueError, match='Frozen evaluation input changed'):
        harness.rows_and_verify()

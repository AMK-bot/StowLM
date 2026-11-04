"""Real archived readings and approval; engine work is an explicit unit double."""
from copy import deepcopy
import json

import pytest

from experiments.mvp.pipeline.binding.review3 import recorded
from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
from experiments.mvp.reader import public_brief
from experiments.mvp.reader.schema import context_from_instance


@pytest.mark.parametrize('kind', recorded.KINDS)
def test_every_case_uses_exact_original_recorded_request_and_context(kind):
    instance, case = recorded.load_case(kind)
    parser = recorded.RecordedParser(case)
    context = context_from_instance(instance)
    assert case['scope'].startswith('historical public validation/development')
    for row in case['readings']:
        answer, provider = parser.parse(row['sentence'], context)
        assert answer.model_dump() == row['parsed']['constraint']
        assert provider['request_id'] == row['provider']['request_id']
        assert provider['raw'] == row['raw']
        assert provider['recorded'] and provider['context_bound']
        assert provider['parser'] == 'SparkParser' and provider['model'] == 'muse-spark-1.3'
    assert len(parser.calls) == 2


def test_parser_refuses_sentence_or_context_transfer():
    _, case = recorded.load_case('max_tiers')
    parser = recorded.RecordedParser(case)
    with pytest.raises(ValueError, match='no exact archived'):
        parser.parse('invented oracle instruction', case['context'])
    with pytest.raises(ValueError, match='context mismatch'):
        parser.parse(case['readings'][1]['sentence'], dict(case['context'], id='another map'))


def test_modified_raw_response_cannot_reuse_genuine_provider_provenance(monkeypatch):
    cases = recorded.cases()
    cases[0]['readings'][1]['raw'] = '{}'
    monkeypatch.setattr(recorded, 'cases', lambda: deepcopy(cases))
    with pytest.raises(ValueError, match='pinned provider output'):
        recorded.load_case('close_bay')


@pytest.mark.parametrize('kind', recorded.KINDS)
def test_authenticated_confirmed_execution_reconstructs_recorded_reading_before_search(kind):
    seen = []
    def engine(instance, receipt, *, on_incumbent, **kwargs):
        confirmation = verify_confirmation(receipt)
        reading = public_brief._verify(instance, receipt)
        seen.append(reading)
        assert confirmation['source'] == recorded.CONFIRMATION_SOURCE
        voyage = [row for row in reading['sentences'] if row['role'] == 'voyage']
        assert len(voyage) == 1 and voyage[0]['parsed']['constraint']['type'] == kind
        assert all(row['parser']['recorded'] for row in reading['sentences'])
        assert kwargs == {'budget_s': 0.}
        on_incumbent({'passed': True, 'status': 'checked_plan', 'synthetic_test_event': True})
        return None, {'status': 'no_checked_plan', 'passed': False, 'engine_version': 'unit double'}
    result, evidence = recorded.execute_case(kind, executor=engine, budget_s=0.)
    assert result is None and len(seen) == 1
    metadata = evidence['recorded_reader_execution']
    assert metadata['context_bound'] and metadata['confirmation_source']
    assert not metadata['fresh_model_call'] and not metadata['reader_accuracy_measurement']
    assert metadata['first_checked_wall_s'] is not None
    assert metadata['source_role_transfer'] == (kind == 'one_port_per_hatch')
    if kind == 'one_port_per_hatch':
        assert metadata['original_response_reused'] and len(metadata['unique_original_request_ids']) == 1


def test_four_case_queue_has_no_outcome_dependent_selection():
    completed = []
    for kind in recorded.KINDS:
        case = recorded.next_case(completed)
        assert case['case_id'] == kind
        completed.append(kind)
    assert recorded.next_case(completed) is None


def test_run_case_persists_scope_reading_confirmation_timing_and_never_overwrites(monkeypatch, tmp_path):
    monkeypatch.setattr(recorded, 'HERE', tmp_path)
    path = tmp_path / 'recorded.json'
    def engine(*args, **kwargs):
        return None, {'status': 'constraint_unavailable', 'passed': False,
                      'reason': 'synthetic search seam only; no numerical engine run'}
    row = recorded.run_case('close_bay', path, executor=engine, budget_s=0.)
    saved = json.loads(path.read_text())
    assert saved == row and saved['status'] == 'constraint_unavailable'
    metadata = row['evidence']['recorded_reader_execution']
    assert metadata['recording_sha256'] and metadata['input_sha256'] and metadata['fixture_sha256']
    assert metadata['mode'] == 'recorded_reader_live_engine' and metadata['wall_s'] >= 0
    assert metadata['reading']['sentences'][1]['parsed']['constraint']['type'] == 'close_bay'
    with pytest.raises(FileExistsError):
        recorded.run_case('close_bay', path, executor=engine)

"""Interrupted reference publication must retain failed attempt denominators."""
import importlib.util
import json
from pathlib import Path

import pytest
from eval.harness_metrics import aggregate_results

spec = importlib.util.spec_from_file_location('reference_reporter', Path(__file__).with_name('summarize_refs.py'))
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)


def row(identity, passed):
    return dict(instance_id=identity, rung='tiny', result={'repaired': False},
                report={'passed': passed, 'violations': [], 'parts': {}},
                shippable=passed, report_binding_valid=True)


def save(folder, identity, value):
    path = folder / 'instances' / identity / 'record.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.mark.parametrize('stale_content', [False, True])
def test_saved_failure_survives_stale_journal(tmp_path, stale_content):
    passing, failed = row('pass', True), row('failed', False)
    save(tmp_path, 'pass', passing)
    save(tmp_path, 'failed', failed)
    # An archived pilot is neither a new attempt nor a duplicate of this attempt.
    archived = tmp_path / 'instances/failed/pilot-before-source-snapshot/record.json'
    archived.parent.mkdir()
    archived.write_text(json.dumps(row('failed', True)))
    journal_rows = [passing, row('failed', True)] if stale_content else [passing]
    journal = tmp_path / 'records.jsonl'
    journal.write_text(''.join(json.dumps(r) + '\n' for r in journal_rows))

    rows = reporter.load_records(tmp_path, {'tiny'})
    summary = aggregate_results(rows, population='own')
    assert summary['n_instances'] == 2
    assert summary['n_passed'] == 1
    assert summary['own_pass_rate'] == 0.5
    assert {r['instance_id']: r for r in map(json.loads, journal.read_text().splitlines())} == {
        'pass': passing, 'failed': failed}


@pytest.mark.parametrize('duplicate_source', ['checkpoints', 'journal'])
def test_duplicate_identities_rejected(tmp_path, duplicate_source):
    passing, failed = row('same', True), row('same', False)
    save(tmp_path, 'first', passing)
    if duplicate_source == 'checkpoints':
        save(tmp_path, 'second', failed)
    else:
        (tmp_path / 'records.jsonl').write_text(json.dumps(passing) + '\n' + json.dumps(failed) + '\n')
    with pytest.raises(ValueError, match='Duplicate instance IDs'):
        reporter.load_records(tmp_path, {'tiny'})


def test_orphaned_journal_failure_is_not_silently_dropped(tmp_path):
    journal = tmp_path / 'records.jsonl'
    original = json.dumps(row('missing-checkpoint', False)) + '\n'
    journal.write_text(original)
    with pytest.raises(ValueError, match='lack direct checkpoints'):
        reporter.load_records(tmp_path, {'tiny'})
    assert journal.read_text() == original

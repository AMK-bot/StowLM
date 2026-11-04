"""Real recorded package verification and named, independently reported tampering."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import socket

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file
import experiments.mvp.pipeline.package as package_module
from experiments.mvp.pipeline.package import verify_package, write_package
from stowage.model import CheckReport, Violation


HERE = Path(__file__).resolve().parent


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


@pytest.fixture
def package_dir(tmp_path):
    source = HERE / 'fixtures' / 'complete'
    assert (source / 'package.json').is_file(), 'record a complete checked package fixture'
    directory = tmp_path / 'package'
    shutil.copytree(source, directory)
    return directory


@pytest.fixture
def real_checker_admission():
    from experiments.mvp.pipeline.package.build_fixture import wait_for_admission

    wait_for_admission()


@pytest.fixture
def recorded_checker(package_dir, monkeypatch):
    """Integrity-only cases reuse evidence; the four required cases rerun physics."""
    report = CheckReport.model_validate(_read(package_dir / 'checker-report.json'))
    monkeypatch.setattr(package_module, '_checker', lambda instance, plan: report)
    return report


def _file_error(result, name, text):
    return any(row['file'] == name and text in row['error'] for row in result['files']['errors'])


def test_complete_190_box_package_verifies_offline(package_dir, real_checker_admission, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('package verification attempted network access')

    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    result = verify_package(package_dir / 'package.json')
    print(json.dumps({'measurement': '190-box full package verify', 'wall_s': result['wall_s'],
                      'checker_wall_s': result['checker']['wall_s']}))
    assert result['passed'], result
    for name in ('files', 'chain', 'signature', 'checker', 'rules'):
        assert result[name]['passed'], (name, result[name])
    assert result['checker']['full_checker_passed'] is True
    assert result['checker']['report']['parts']
    assert result['checker']['wall_s'] > 0
    assert result['wall_s'] >= result['checker']['wall_s']
    instance = _read(package_dir / 'instance.json')
    assert len(instance['load_list']) == 190


def test_edit_move_is_named_and_checker_reruns(package_dir, real_checker_admission):
    plan_path = package_dir / 'plan.json'
    plan = _read(plan_path)
    move = next(row for row in plan['moves'] if row['kind'] == 'load')
    move['dst'] = '9999982'
    _write(plan_path, plan)
    result = verify_package(package_dir)
    assert not result['passed']
    assert _file_error(result, 'plan.json', 'sha256 mismatch')
    assert result['chain']['passed']
    assert result['signature']['passed']
    assert not result['checker']['passed']
    assert any('plan_sha256' in row['error'] for row in result['checker']['errors'])
    assert result['checker']['wall_s'] > 0


def test_edit_receipt_row_breaks_chain_and_signature(package_dir, real_checker_admission):
    receipt_path = package_dir / 'receipt.json'
    receipt = _read(receipt_path)
    assert len(receipt['steps']) >= 2
    receipt['steps'][0]['model'] = 'tampered model'
    _write(receipt_path, receipt)
    result = verify_package(package_dir)
    assert not result['passed']
    assert _file_error(result, 'receipt.json', 'sha256 mismatch')
    assert not result['chain']['passed']
    assert result['chain']['errors'][0]['step'] == 1
    assert result['chain']['errors'][0]['stage'] == receipt['steps'][1]['stage']
    assert not result['signature']['passed']
    assert result['checker']['passed']


def test_edit_index_hash_is_named(package_dir, real_checker_admission):
    index_path = package_dir / 'package.json'
    index = _read(index_path)
    index['files']['plan.json'] = '0' * 64
    _write(index_path, index)
    result = verify_package(package_dir)
    assert not result['passed']
    assert _file_error(result, 'plan.json', 'sha256 mismatch')
    assert _file_error(result, 'package.json', 'manifest differs from signed receipt')
    assert result['signature']['passed']
    assert result['checker']['passed']


@pytest.mark.parametrize('malformed', ['[', '[]', '{"files": []}'])
def test_malformed_index_returns_failure(package_dir, malformed, recorded_checker):
    (package_dir / 'package.json').write_text(malformed)
    result = verify_package(package_dir)
    assert not result['passed']
    assert not result['files']['passed']
    assert result['files']['errors']
    assert result['chain']['passed']
    assert result['signature']['passed']
    assert result['checker']['passed']


def test_index_path_escape_is_named(package_dir, recorded_checker):
    index_path = package_dir / 'package.json'
    index = _read(index_path)
    outside = package_dir.parent / 'outside.json'
    outside.write_text('synthetic outside file')
    index['files']['../outside.json'] = sha256_file(outside)
    _write(index_path, index)
    result = verify_package(package_dir)
    assert not result['passed']
    assert _file_error(result, '../outside.json', 'unsafe file path')


def test_symlink_payload_is_named(package_dir, recorded_checker):
    path = package_dir / 'bay-plan.html'
    outside = package_dir.parent / 'outside.html'
    shutil.move(path, outside)
    path.symlink_to(outside)
    result = verify_package(package_dir)
    assert not result['passed']
    assert _file_error(result, 'bay-plan.html', 'unsafe file path')


def test_unsigned_index_metadata_edit_is_named(package_dir, recorded_checker):
    index_path = package_dir / 'package.json'
    index = _read(index_path)
    index['status'] = 'relaxed_plan'
    _write(index_path, index)
    result = verify_package(package_dir)
    assert not result['passed']
    assert _file_error(result, 'package.json', 'index metadata differs from signed receipt')
    assert result['chain']['passed']
    assert result['signature']['passed']


@pytest.mark.parametrize('tamper', ['manifest', 'metadata'])
def test_matching_index_and_receipt_edits_still_require_signature(package_dir, tamper, recorded_checker):
    index_path, receipt_path = package_dir / 'package.json', package_dir / 'receipt.json'
    index, receipt = _read(index_path), _read(receipt_path)
    if tamper == 'manifest':
        html_path = package_dir / 'bay-plan.html'
        html_path.write_text(html_path.read_text() + '\n<!-- synthetic tampering -->\n')
        index['files']['bay-plan.html'] = sha256_file(html_path)
        receipt['checker']['package_files']['bay-plan.html'] = sha256_file(html_path)
    else:
        index['label'] = 'generated'
        receipt['checker']['package_index']['label'] = 'generated'
    _write(receipt_path, receipt)
    index['files']['receipt.json'] = sha256_file(receipt_path)
    _write(index_path, index)
    result = verify_package(package_dir)
    assert not result['passed']
    assert result['files']['passed'], result['files']
    assert result['chain']['passed']
    assert not result['signature']['passed']
    assert result['checker']['passed']


def test_missing_and_unlisted_files_are_named(package_dir, recorded_checker):
    (package_dir / 'plan.json').unlink()
    (package_dir / 'unlisted.txt').write_text('synthetic extra artifact')
    result = verify_package(package_dir)
    assert not result['passed']
    assert _file_error(result, 'plan.json', 'missing or unreadable file')
    assert _file_error(result, 'unlisted.txt', 'unlisted package file')


def test_malformed_optional_packet_does_not_suppress_checker(package_dir, recorded_checker, monkeypatch):
    calls = []
    def checker(instance, plan):
        calls.append(plan.id)
        return recorded_checker
    monkeypatch.setattr(package_module, '_checker', checker)
    (package_dir / 'packet.json').write_text('{')
    result = verify_package(package_dir)
    assert calls
    assert result['checker']['full_checker_passed'] is True
    assert not result['rules']['passed']
    assert not result['passed']
    assert any('packet.json' in row['error'] for row in result['checker']['errors'])


def test_csv_tracks_gate_and_cover_staging():
    from io import StringIO
    import csv
    from stowage.model import MovePlan
    from stowage.model.fixtures import build_instance
    plan = MovePlan.model_validate(dict(id='csv', instance_id='synthetic', call_index=0, moves=[
        dict(kind='hatch_cover_off', call_index=0, cover_id='HC-01', dst='HC-02'),
        dict(kind='hatch_cover_on', call_index=0, cover_id='HC-01'),
        dict(kind='gate_out', call_index=0, container_id=build_instance().containers[0].id, src='A-001-01-01'),
    ]))
    text = package_module._crane_csv(plan, 'synthetic', False)
    rows = list(csv.DictReader(StringIO('\n'.join(line for line in text.splitlines() if not line.startswith('# ')))))
    assert rows[0]['from'] == 'HC-01' and rows[0]['to'] == 'HC-02'
    assert rows[1]['from'] == 'HC-02' and rows[1]['to'] == 'HC-01'
    assert rows[2]['to'] == 'gate'


def test_writer_replays_recorded_outcome_without_mutating_it(tmp_path, monkeypatch, recorded_checker):
    from experiments.mvp.pipeline.package import receipt as receipt_module

    outcome = _read(HERE / 'fixtures' / 'outcome.json')
    original = deepcopy(outcome)
    monkeypatch.setattr(receipt_module, 'KEY_DIR', tmp_path / '.keys')
    index = write_package(outcome, tmp_path / 'written')
    assert outcome == original
    assert index['synthetic'] is True
    assert index['status'] == 'checked_plan'
    public_key = _read(tmp_path / 'written' / 'receipt.json')['public_key']
    assert verify_package(tmp_path / 'written', expected_public_key=public_key)['passed']
    assert not (tmp_path / 'written' / '.keys').exists()


def test_relaxed_warning_and_integrity_do_not_imply_checker_pass(tmp_path, monkeypatch, recorded_checker):
    """Injected synthetic failure checks labels and verifier semantics, not physics."""
    from experiments.mvp.pipeline.package import receipt as receipt_module

    outcome = _read(HERE / 'fixtures' / 'outcome.json')
    outcome['status'] = 'relaxed_plan'
    outcome['evidence'] = {'level': 1, 'broken_rules': ['expert.e6.heavy_under_deck'], 'boxes_ashore': []}
    outcome['broken_rules'] = ['expert.e6.heavy_under_deck']
    outcome.pop('note_selection', None)
    violation = Violation(rule_id='expert.e6.heavy_under_deck', severity='hard',
                          call_index=outcome['instance']['call_index'],
                          message='Injected synthetic checker failure for relaxed-label testing.')
    failing = recorded_checker.model_copy(update={'passed': False, 'violations': [violation]})
    monkeypatch.setattr(package_module, '_checker', lambda instance, plan: failing)
    monkeypatch.setattr(receipt_module, 'KEY_DIR', tmp_path / '.keys')
    directory = tmp_path / 'relaxed'
    index = write_package(outcome, directory)
    assert index['status'] == 'relaxed_plan'
    for name in ('bay-plan.html', 'crane-sequence.csv', 'note-to-master.md'):
        first = (directory / name).read_text().splitlines()[0]
        assert 'RELAXED PLAN' in first, (name, first)
        assert 'expert.e6.heavy_under_deck' in first
    assert 'RELAXED PLAN' in _read(directory / 'stability.json')['banner']
    result = verify_package(directory, expected_public_key=_read(directory / 'receipt.json')['public_key'])
    assert not result['passed']
    assert result['files']['passed']
    assert result['chain']['passed']
    assert result['signature']['passed']
    assert result['rules']['passed']
    assert not result['checker']['passed']
    assert result['checker']['full_checker_passed'] is False
    assert result['checker']['broken_rules'] == ['expert.e6.heavy_under_deck']

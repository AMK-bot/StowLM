"""Offline regressions for v6 logic/code review package findings."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import StageRecord, sha256_file, sha256_json
import experiments.mvp.pipeline.package as package_module
from experiments.mvp.pipeline.package import receipt as receipt_module
from stowage.model import CheckReport, Instance, MovePlan
from experiments.mvp.reader.schema import answer_envelope
from llm.instructions.schema import CloseBay

HERE = Path(__file__).resolve().parent


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


@pytest.fixture
def outcome():
    return _read(HERE / 'fixtures/outcome.json')


@pytest.fixture
def recorded_checker(monkeypatch, tmp_path):
    report = CheckReport.model_validate(_read(HERE / 'fixtures/complete/checker-report.json'))
    monkeypatch.setattr(package_module, '_checker', lambda instance, plan: report)
    monkeypatch.setattr(receipt_module, 'KEY_DIR', tmp_path / '.keys')
    return report


def _sentences(*texts):
    return {'voyage_sentences': [{'text': text, 'origin': 'typed'} for text in texts]}


def _rewrite_signed(directory, receipt):
    """Update authentic hashes and re-sign, isolating semantic checks from edits."""
    index = _read(directory / 'package.json')
    for name in index['files']:
        if name != 'receipt.json':
            index['files'][name] = sha256_file(directory / name)
    receipt['checker']['package_files'] = {
        name: digest for name, digest in index['files'].items() if name != 'receipt.json'
    }
    signed = receipt_module.sign_receipt(receipt)
    _write(directory / 'receipt.json', signed)
    index['files']['receipt.json'] = sha256_file(directory / 'receipt.json')
    _write(directory / 'package.json', index)
    return signed['public_key']


@pytest.mark.parametrize('texts,rules', [
    (['Close bay 1.'], []),
    (['Close bay 1.'], [{'sentence': 'Close bay 2.'}]),
    (['Close bay 1.', 'Close bay 1.'], [{'sentence': 'Close bay 1.'}]),
    (['Close bay 1.', 'Close bay 2.'], [{'sentence': 'Close bay 2.'}, {'sentence': 'Close bay 1.'}]),
])
def test_rule_coverage_requires_exact_order_and_duplicate_count(outcome, texts, rules):
    result = package_module._rule_checks(
        Instance.model_validate(outcome['instance']), MovePlan.model_validate(outcome['plan']),
        rules, packet=_sentences(*texts),
    )
    assert not result['passed']
    assert result['errors']
    assert any('sentence' in row['error'].lower() for row in result['errors'])


def test_writer_refuses_rule_stripping(outcome, recorded_checker, tmp_path):
    outcome['packet']['voyage_sentences'] = _sentences('Close bay 1.')['voyage_sentences']
    outcome['rules'] = []
    with pytest.raises(ValueError, match='(?i)sentence|instruction|rule'):
        package_module.write_package(outcome, tmp_path / 'stripped')


def test_writer_rejects_conflicting_rule_sources(outcome, recorded_checker, tmp_path):
    outcome['packet']['voyage_sentences'] = _sentences('Close bay 1.')['voyage_sentences']
    outcome['rules'] = [{'sentence': 'Close bay 1.', 'reading': answer_envelope(CloseBay(type='close_bay', bay=2))}]
    outcome['receipt'] = {'rules': [
        {'sentence': 'Close bay 1.', 'reading': answer_envelope(CloseBay(type='close_bay', bay=1))},
    ]}
    with pytest.raises(ValueError, match='(?i)conflict|differ|disagree'):
        package_module.write_package(outcome, tmp_path / 'conflicting')


def test_verifier_rejects_authentically_signed_empty_rule_list(outcome, recorded_checker, tmp_path):
    directory = tmp_path / 'written'
    package_module.write_package(outcome, directory)
    packet = _read(directory / 'packet.json')
    packet['voyage_sentences'] = _sentences('Close bay 1.')['voyage_sentences']
    _write(directory / 'packet.json', packet)
    receipt = _read(directory / 'receipt.json')
    receipt['packet_sha256'] = sha256_json(packet)
    receipt['rules'] = []
    key = _rewrite_signed(directory, receipt)
    result = package_module.verify_package(directory, expected_public_key=key)
    assert result['files']['passed'], result['files']
    assert result['signature']['passed'], result['signature']
    assert not result['rules']['passed']
    assert any('sentence' in row['error'].lower() for row in result['rules']['errors'])
    assert not result['passed']


def test_writer_requires_packet_artifact(outcome, recorded_checker, tmp_path):
    packet = outcome.pop('packet')
    outcome['packet_sha256'] = sha256_json(packet)
    with pytest.raises(ValueError, match='(?i)packet'):
        package_module.write_package(outcome, tmp_path / 'no-packet')


def test_upstream_stage_records_are_preserved_and_chained(outcome, recorded_checker, tmp_path):
    records = [StageRecord(
        stage=stage, mode='recorded', input_sha256=str(index) * 64,
        output_sha256=str(index + 1) * 64, source_sha256='f' * 64,
        model=model, adapter_sha256='a' * 64, prompt_sha256='b' * 64,
        wall_s=float(index), cost_usd=0, notes='Synthetic recorded upstream evidence.',
    ).model_dump(mode='json') for index, (stage, model) in enumerate([
        ('intake', 'local-intake-reader'), ('yard', 'synthetic-yard-vision'),
        ('voice', 'recorded-voice-transcribe'),
    ], 1)]
    outcome['upstream_records'] = records
    original = deepcopy(outcome)
    directory = tmp_path / 'upstream'
    package_module.write_package(outcome, directory)
    assert outcome == original
    receipt = _read(directory / 'receipt.json')
    for expected, actual in zip(records, receipt['steps']):
        assert {k: v for k, v in actual.items() if k != 'prev_sha256'} == {
            k: v for k, v in expected.items() if k != 'prev_sha256'
        }
    assert receipt_module.verify_receipt(receipt, expected_public_key=receipt['public_key'])['chain']['passed']


def test_unfrozen_pipeline_is_explicit_in_receipt_and_handover(outcome, recorded_checker, tmp_path):
    outcome.pop('pipeline_sha256', None)
    directory = tmp_path / 'unfrozen'
    index = package_module.write_package(outcome, directory)
    receipt = _read(directory / 'receipt.json')
    assert receipt['pipeline_sha256'] is None
    assert receipt['checker']['pipeline_freeze'] == 'not frozen yet'
    assert index['pipeline_sha256'] == 'not frozen yet'
    for name in ('bay-plan.html', 'note-to-master.md'):
        assert 'Pipeline: not frozen yet' in (directory / name).read_text(), name


def test_writer_materializes_actual_pinned_engine_manifest(outcome, recorded_checker, tmp_path):
    from experiments.mvp.pipeline.binding.adapter import MANIFEST
    from engine import VERSION

    outcome.pop('engine_manifest', None)
    outcome['engine_version'] = VERSION
    outcome['engine_manifest_sha256'] = sha256_file(MANIFEST)
    directory = tmp_path / 'pinned-manifest'
    index = package_module.write_package(outcome, directory)
    manifest = directory / 'engine-manifest.json'
    assert manifest.read_bytes() == MANIFEST.read_bytes()
    receipt = _read(directory / 'receipt.json')
    assert receipt['engine_manifest_sha256'] == sha256_file(manifest)
    assert index['files']['engine-manifest.json'] == sha256_file(manifest)


def test_missing_engine_manifest_cannot_be_invented(outcome, recorded_checker, tmp_path):
    outcome.pop('engine_manifest', None)
    outcome['engine_manifest_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='(?i)manifest'):
        package_module.write_package(outcome, tmp_path / 'invented-manifest')


@pytest.mark.parametrize('claim,value', [
    ('lifts', 123456), ('level', 5), ('broken_rules', ['invented.rule']),
    ('boxes_ashore', ['invented-box']),
])
def test_writer_rejects_claims_that_disagree_with_fresh_checker(
    outcome, recorded_checker, tmp_path, claim, value,
):
    if claim == 'lifts':
        outcome[claim] = value
    else:
        outcome.setdefault('evidence', {})[claim] = value
    with pytest.raises(ValueError, match='(?i)' + claim):
        package_module.write_package(outcome, tmp_path / 'false-claim')


@pytest.mark.parametrize('claim,value', [
    ('lifts', 123456), ('level', 5), ('broken_rules', ['invented.rule']),
    ('boxes_ashore', ['invented-box']),
])
def test_verifier_recomputes_signed_checker_claims(outcome, recorded_checker, tmp_path, claim, value):
    directory = tmp_path / 'false-claim'
    package_module.write_package(outcome, directory)
    receipt = _read(directory / 'receipt.json')
    if claim == 'lifts':
        receipt[claim] = value
    else:
        receipt['checker'][claim] = value
    key = _rewrite_signed(directory, receipt)
    result = package_module.verify_package(directory, expected_public_key=key)
    assert result['files']['passed'], result['files']
    assert result['signature']['passed'], result['signature']
    assert not result['checker']['passed']
    assert any(claim in row['error'] for row in result['checker']['errors'])


def test_checked_stored_artifacts_each_keep_provenance(outcome, recorded_checker, tmp_path):
    outcome['label'] = 'stored'
    outcome['computed_utc'] = '2026-11-01T12:00:00Z'
    directory = tmp_path / 'stored'
    package_module.write_package(outcome, directory)
    for name in ('bay-plan.html', 'crane-sequence.csv', 'note-to-master.md'):
        first_line = (directory / name).read_text().splitlines()[0]
        assert 'Stored plan' in first_line, name
        assert outcome['computed_utc'] in first_line, name


def test_verifier_requires_engine_manifest_even_after_resigning(outcome, recorded_checker, tmp_path):
    directory = tmp_path / 'missing-manifest'
    package_module.write_package(outcome, directory)
    (directory / 'engine-manifest.json').unlink()
    index = _read(directory / 'package.json')
    index['files'].pop('engine-manifest.json')
    _write(directory / 'package.json', index)
    receipt = _read(directory / 'receipt.json')
    key = _rewrite_signed(directory, receipt)
    result = package_module.verify_package(directory, expected_public_key=key)
    assert result['signature']['passed'], result['signature']
    assert not result['passed']
    assert any(row.get('file') == 'engine-manifest.json' for row in result['files']['errors'])


def test_valid_resigned_forgery_fails_published_key_by_name(recorded_checker, tmp_path):
    import shutil
    directory = tmp_path / 'forgery'
    shutil.copytree(HERE / 'fixtures/complete', directory)
    original = _read(directory / 'receipt.json')
    assert package_module.verify_package(directory)['passed']
    forged = receipt_module.sign_receipt(original, key_dir=tmp_path / 'attacker')
    _write(directory / 'receipt.json', forged)
    index = _read(directory / 'package.json')
    index['files']['receipt.json'] = sha256_file(directory / 'receipt.json')
    _write(directory / 'package.json', index)
    for expected in (None, original['public_key']):
        result = package_module.verify_package(directory, expected_public_key=expected)
        for check in ('files', 'chain', 'checker', 'rules', 'coverage'):
            assert result[check]['passed'], (check, result[check])
        assert not result['passed']
        assert any('untrusted public key' in row['error'] for row in result['signature']['errors'])


def test_trusted_signature_cannot_assert_false_active_rule_inventory(outcome, recorded_checker, tmp_path):
    directory = tmp_path / 'false-inventory'
    package_module.write_package(outcome, directory)
    receipt = _read(directory / 'receipt.json')
    receipt['checker']['active_rules'].pop()
    key = _rewrite_signed(directory, receipt)
    result = package_module.verify_package(directory, expected_public_key=key)
    assert result['signature']['passed']
    assert not result['coverage']['passed']
    assert any('active_rules' in row['error'] for row in result['coverage']['errors'])


def test_runner_key_override_cannot_change_package_published_signer(outcome, recorded_checker, tmp_path):
    directory = tmp_path / 'published-signer'
    package_module.write_package(outcome, directory)
    receipt = _read(directory / 'receipt.json')
    assert receipt_module.verify_receipt(receipt)['signature']['passed']

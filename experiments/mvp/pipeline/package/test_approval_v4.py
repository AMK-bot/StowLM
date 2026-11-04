"""The package boundary requires approval, and retains its original evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
import experiments.mvp.pipeline.package as package
from experiments.mvp.pipeline.package.__main__ import main
from experiments.mvp.pipeline.package import receipt as receipt_module
from experiments.mvp.pipeline.package.confirmation import BATCH_CONFIRMATION_SOURCE
from stowage.model import CheckReport, Instance, MovePlan

HERE = Path(__file__).resolve().parent
MISSING = object()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


@pytest.fixture
def handover():
    hero = read(HERE / 'fixtures/hero-run/outcome.json')
    outcome = {key: deepcopy(hero[key]) for key in ('status', 'instance', 'plan', 'packet', 'rules')}
    outcome.update({key: hero['receipt'][key] for key in ('engine_version', 'engine_manifest_sha256')})
    return outcome


@pytest.fixture
def recorded_checker(monkeypatch, tmp_path):
    report = CheckReport.model_validate(read(HERE / 'fixtures/hero-run/package/checker-report.json'))
    monkeypatch.setattr(package, '_checker', lambda *args: report)
    original = receipt_module.sign_receipt
    monkeypatch.setattr(package, 'sign_receipt', lambda receipt, **kwargs:
                        original(receipt, key_dir=tmp_path / 'test-key'))


@pytest.mark.parametrize('confirmed', [MISSING, None, 0, 'unconfirmed', False],
                         ids=['missing', 'null', 'zero', 'string', 'false'])
def test_unapproved_instruction_refused_by_audit_writer_and_cli(handover, tmp_path, capsys, confirmed):
    row = handover['rules'][0]
    row.pop('confirmed', None)
    if confirmed is not MISSING:
        row['confirmed'] = confirmed
    sentence = row['sentence']
    result = package._rule_checks(Instance.model_validate(handover['instance']),
        MovePlan.model_validate(handover['plan']), handover['rules'], packet=handover['packet'])
    assert not result['passed']
    assert sentence in str(result['errors'])
    assert 'has no recorded confirmation' in str(result['errors'])
    directory = tmp_path / 'refused'
    with pytest.raises(ValueError, match='Instructions failed confirmation or deterministic recheck') as error:
        package.write_package(handover, directory)
    assert sentence in str(error.value)
    assert not directory.exists()
    source = tmp_path / 'outcome.json'
    source.write_text(json.dumps(handover), encoding='utf-8')
    assert main(['write', str(source), str(directory)]) == 1
    assert sentence in json.loads(capsys.readouterr().out)['error']
    assert not directory.exists()


def test_literal_true_is_signed_and_verifies(handover, recorded_checker, tmp_path):
    handover['rules'][0]['confirmed'] = True
    directory = tmp_path / 'accepted'
    package.write_package(handover, directory)
    receipt = read(directory / 'receipt.json')
    assert receipt['rules'] == handover['rules']
    assert receipt['checker']['instruction_approval']['rules'] == handover['rules']
    result = package.verify_package(directory, expected_public_key=receipt['public_key'])
    assert result['passed'], result


def test_unapproved_reading_still_reports_intrinsic_violations(handover):
    from experiments.mvp.reader.schema import answer_envelope
    from llm.instructions.schema import CloseBay
    instance = Instance.model_validate(handover['instance'])
    plan = MovePlan.model_validate(handover['plan'])
    bay = int(next(move.dst for move in plan.moves if move.kind == 'load')[:3])
    sentence = 'Close this occupied bay.'
    rule = dict(sentence=sentence, reading=answer_envelope(CloseBay(type='close_bay', bay=bay)))
    result = package._rule_checks(instance, plan, [rule])
    assert not result['passed']
    assert any(sentence in error['error'] and 'has no recorded confirmation' in error['error'] for error in result['errors'])
    assert any('closed' in error['error'] for error in result['errors'])


@pytest.mark.parametrize('confirmed', [MISSING, None, 0, 'unconfirmed', False],
                         ids=['missing', 'null', 'zero', 'string', 'false'])
def test_verifier_refuses_authentically_signed_unapproved_rules(handover, recorded_checker, tmp_path, confirmed):
    handover['rules'][0]['confirmed'] = True
    directory = tmp_path / 'signed-unapproved'
    package.write_package(handover, directory)
    receipt = read(directory / 'receipt.json')
    for row in (receipt['rules'][0], receipt['checker']['instruction_approval']['rules'][0]):
        row.pop('confirmed')
        if confirmed is not MISSING:
            row['confirmed'] = confirmed
    signed = receipt_module.sign_receipt(receipt, key_dir=tmp_path / 'resign-key')
    (directory / 'receipt.json').write_text(json.dumps(signed), encoding='utf-8')
    index = read(directory / 'package.json')
    index['files']['receipt.json'] = sha256_file(directory / 'receipt.json')
    (directory / 'package.json').write_text(json.dumps(index), encoding='utf-8')
    result = package.verify_package(directory, expected_public_key=signed['public_key'])
    assert result['signature']['passed'], result['signature']
    assert result['files']['passed'], result['files']
    assert not result['rules']['passed']
    assert handover['rules'][0]['sentence'] in str(result['rules']['errors'])
    assert 'has no recorded confirmation' in str(result['rules']['errors'])
    assert not result['passed']


def test_runner_batch_approval_recovers_missing_flags_before_gate(handover, recorded_checker, tmp_path):
    hero = read(HERE / 'fixtures/hero-run/outcome.json')
    handover['receipt'] = hero['receipt']
    handover['confirmation'] = hero['confirmation']
    handover['reading'] = hero['reading']
    directory = tmp_path / 'runner-approved'
    package.write_package(handover, directory)
    receipt = read(directory / 'receipt.json')
    assert all(row['confirmed'] is True for row in receipt['rules'])
    approval = receipt['checker']['instruction_approval']
    assert approval['rules'] == hero['receipt']['rules']
    assert approval['confirmation'] == hero['confirmation']
    assert approval['steps'] == hero['receipt']['steps']
    assert 'Confirmed by: ' + BATCH_CONFIRMATION_SOURCE in (
        directory / 'note-to-master.md').read_text()
    result = package.verify_package(directory, expected_public_key=receipt['public_key'])
    assert result['passed'], result


@pytest.mark.parametrize('tamper', ['source', 'count', 'confirmation', 'rules', 'reading_payload'])
def test_verification_reaudits_signed_runner_approval(handover, recorded_checker, tmp_path, tamper):
    hero = read(HERE / 'fixtures/hero-run/outcome.json')
    handover['receipt'] = hero['receipt']
    handover['confirmation'] = hero['confirmation']
    handover['reading'] = hero['reading']
    directory = tmp_path / 'rechecked'
    package.write_package(handover, directory)
    receipt = read(directory / 'receipt.json')
    approval = receipt['checker']['instruction_approval']
    step = next(row for row in approval['steps'] if row['stage'] == 'confirmation')
    if tamper == 'source':
        step['source_sha256'] = '0' * 64
    elif tamper == 'count':
        notes = json.loads(step['notes'])
        notes['confirmed_count'] = 0
        step['notes'] = json.dumps(notes)
    elif tamper == 'confirmation':
        approval['confirmation']['source'] = 'unrecorded approval'
    elif tamper == 'rules':
        approval['rules'] = []
    else:
        # Changing non-semantic timing still breaks the exact recorded digest.
        approval['reading']['sentences'][-1]['wall_s'] += 1
    signed = receipt_module.sign_receipt(receipt, key_dir=tmp_path / 'resign-key')
    (directory / 'receipt.json').write_text(json.dumps(signed), encoding='utf-8')
    index = read(directory / 'package.json')
    index['files']['receipt.json'] = sha256_file(directory / 'receipt.json')
    (directory / 'package.json').write_text(json.dumps(index), encoding='utf-8')
    result = package.verify_package(directory, expected_public_key=signed['public_key'])
    assert result['signature']['passed'], result['signature']
    assert result['files']['passed'], result['files']
    assert not result['rules']['passed']
    assert not result['passed']


@pytest.mark.parametrize('confirmed', [None, 0, 'unconfirmed', False])
def test_runner_evidence_does_not_override_explicit_invalid_approval(handover, confirmed):
    hero = read(HERE / 'fixtures/hero-run/outcome.json')
    rules = deepcopy(handover['rules'])
    rules[0]['confirmed'] = confirmed
    normalised = package._normalise_approval(rules, hero['confirmation'], hero['receipt']['steps'])
    assert normalised[0]['confirmed'] is confirmed
    result = package._rule_checks(Instance.model_validate(handover['instance']),
        MovePlan.model_validate(handover['plan']), normalised, packet=handover['packet'])
    assert not result['passed']


def replace_rule(rules, packet, alteration):
    if alteration in {'sentence', 'same_count_replacement'}:
        rules[0]['sentence'] = 'Never approved by the recorded host.'
        packet['voyage_sentences'][0]['text'] = rules[0]['sentence']
    if alteration in {'reading', 'same_count_replacement'}:
        rules[0]['reading']['constraint']['bay'] = 11


@pytest.mark.parametrize('alteration', ['sentence', 'reading', 'same_count_replacement'])
def test_copied_confirmation_cannot_approve_replacement_writer_rules(handover, tmp_path, alteration):
    hero = read(HERE / 'fixtures/hero-run/outcome.json')
    handover.update(receipt=hero['receipt'], confirmation=hero['confirmation'], reading=hero['reading'])
    replace_rule(handover['rules'], handover['packet'], alteration)
    # Even a valid signature over substituted runner rows cannot bind those
    # rows to the copied confirmation's unchanged recorded reading payload.
    handover['receipt']['rules'] = deepcopy(handover['rules'])
    handover['receipt']['packet_sha256'] = sha256_json(handover['packet'])
    handover['receipt'] = receipt_module.sign_receipt(handover['receipt'], key_dir=tmp_path / 'runner-key')
    approved = package._normalise_approval(handover['rules'], handover['confirmation'],
        handover['receipt']['steps'], reading=handover['reading'], runner_receipt=handover['receipt'],
        packet=handover['packet'])
    assert all(row.get('confirmed') is not True for row in approved)
    directory = tmp_path / 'transplanted'
    with pytest.raises(ValueError, match='has no recorded confirmation'):
        package.write_package(handover, directory)
    assert not directory.exists()


@pytest.mark.parametrize('alteration', ['sentence', 'reading', 'same_count_replacement'])
def test_signed_verifier_refuses_same_count_approval_transplants(handover, recorded_checker, tmp_path, alteration):
    hero = read(HERE / 'fixtures/hero-run/outcome.json')
    handover.update(receipt=hero['receipt'], confirmation=hero['confirmation'], reading=hero['reading'])
    directory = tmp_path / 'transplanted-verification'
    package.write_package(handover, directory)
    receipt = read(directory / 'receipt.json')
    approval = receipt['checker']['instruction_approval']
    packet = read(directory / 'packet.json')
    replace_rule(approval['rules'], packet, alteration)
    replace_rule(receipt['rules'], packet, alteration)
    approval['runner_receipt']['rules'] = deepcopy(approval['rules'])
    approval['runner_receipt']['packet_sha256'] = sha256_json(packet)
    approval['runner_receipt'] = receipt_module.sign_receipt(approval['runner_receipt'],
                                                           key_dir=tmp_path / 'runner-key')
    (directory / 'packet.json').write_text(json.dumps(packet), encoding='utf-8')
    receipt['packet_sha256'] = sha256_json(packet)
    index = read(directory / 'package.json')
    index['packet_sha256'] = receipt['packet_sha256']
    index['files']['packet.json'] = sha256_file(directory / 'packet.json')
    receipt['checker']['package_index']['packet_sha256'] = receipt['packet_sha256']
    receipt['checker']['package_files']['packet.json'] = index['files']['packet.json']
    signed = receipt_module.sign_receipt(receipt, key_dir=tmp_path / 'package-key')
    (directory / 'receipt.json').write_text(json.dumps(signed), encoding='utf-8')
    index['files']['receipt.json'] = sha256_file(directory / 'receipt.json')
    (directory / 'package.json').write_text(json.dumps(index), encoding='utf-8')
    result = package.verify_package(directory, expected_public_key=signed['public_key'])
    assert result['signature']['passed'], result['signature']
    assert result['files']['passed'], result['files']
    assert not result['rules']['passed']
    assert not result['passed']


def test_normalised_alias_keeps_original_approval_shape(handover, recorded_checker, tmp_path):
    source = handover['rules'][0]
    source['confirmed'] = True
    source['canonical_envelope'] = source.pop('reading')
    original = deepcopy(handover)
    directory = tmp_path / 'alias'
    package.write_package(handover, directory)
    receipt = read(directory / 'receipt.json')
    assert handover == original
    assert receipt['checker']['instruction_approval']['rules'] == original['rules']
    assert 'canonical_envelope' not in receipt['rules'][0]
    assert receipt['rules'][0]['reading'] == source['canonical_envelope']
    assert package.verify_package(directory, expected_public_key=receipt['public_key'])['passed']

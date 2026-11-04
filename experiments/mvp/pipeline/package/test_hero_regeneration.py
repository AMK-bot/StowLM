"""Recorded hero acceptance: real run_packet evidence, no model or numeric rerun."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from experiments.mvp.pipeline.package.confirmation import BATCH_CONFIRMATION_SOURCE
from experiments.mvp.pipeline.package.provenance import (
    confirmed_instruction_rows, hero_provenance,
)
from experiments.mvp.pipeline.package.receipt import verify_receipt

HERE = Path(__file__).resolve().parent
RUN = HERE / 'fixtures/hero-run'


def read(path):
    assert path.is_file(), f'regenerate the recorded hero package: missing {path}'
    return json.loads(path.read_text())


def test_lattice_bare_envelopes_match_annotated_runner_rules():
    from experiments.mvp.pipeline.package import _select_rules
    instruction = read(HERE.parent / 'lattice/fixtures/hero-instruction.json')
    rules = [dict(sentence=text, reading=reading, confirmed=True)
             for text, reading in zip(instruction['sentences'], instruction['canonical_rules'])]
    assert _select_rules({'rules': rules}, {'runner_rules': rules, 'rules': instruction['canonical_rules']},
                         {'rules': rules}) == rules


def test_selection_rejects_primary_instruction_on_backup():
    from experiments.mvp.pipeline.package.regenerate import selected_inputs, LATTICE
    with pytest.raises(ValueError, match='instruction does not identify'):
        selected_inputs(LATTICE / 'hero.json', 'backup', LATTICE / 'fixtures/hero-instruction.json')


def test_adopted_reader_recording_requires_independent_hash():
    from experiments.mvp.pipeline.package.regenerate import regenerate
    with pytest.raises(ValueError, match='custom readings need'):
        regenerate(readings_path=HERE / 'not-a-recording.json', out_dir=HERE / 'fixtures/.test-no-write')
    assert not (HERE / 'fixtures/.test-no-write').exists()


@pytest.fixture
def hero_run():
    return read(RUN / 'outcome.json')


def test_hero_package_must_come_from_run_packet(hero_run):
    """No mocked checker or invented runner stage list satisfies this release gate."""
    result = hero_provenance(hero_run)
    assert result['eligible'], result['errors']
    assert hero_run['status'] == 'checked_plan'
    assert hero_run['passed'] is True
    assert hero_run['label'] == 'stored'
    receipt = hero_run['receipt']
    assert receipt['engine_version'] == '0.13.0'
    package = RUN / 'package'
    assert read(package / 'package.json')['presentation'] == 'hero'
    assert read(package / 'runner-receipt.json') == receipt
    assert read(package / 'hero-provenance.json') == result
    handover = read(package / 'receipt.json')
    verified = verify_receipt(handover)
    assert verified['chain']['passed'], verified
    assert verified['signature']['passed'], verified
    assert handover['steps'][:len(receipt['steps'])] == receipt['steps']
    for field in ('packet', 'instance', 'plan'):
        assert read(package / f'{field}.json') == hero_run[field]
        assert handover[f'{field}_sha256'] == receipt[f'{field}_sha256']
    assert handover['checker']['passed'] is True


def test_hero_run_has_authenticated_confirmed_configured_reading(hero_run):
    receipt = hero_run['receipt']
    confirmation = hero_run['confirmation']
    # Retained v3 evidence stays byte-identical; v4 regeneration records the
    # plain demo disclosure as its source before the runner signs it.
    assert confirmation['source'] in {
        'user_authorized_batch:package_regeneration', BATCH_CONFIRMATION_SOURCE,
    }
    rules = confirmed_instruction_rows(receipt['rules'], confirmation, receipt['steps'],
        reading=hero_run.get('reading'), runner_receipt=receipt, packet=hero_run['packet'])
    assert rules and all(row['confirmed'] for row in rules)
    assert all(row['confirmation_source'] == confirmation['source'] for row in rules)
    configured = hero_run.get('fixture_provenance', {}).get('rule')
    if configured is not None:
        assert [row['reading'] for row in rules] == [configured]
    else:
        assert any(row['reading']['constraint']['type'] == 'dg_keep_away' for row in rules)
    assert [row['sentence'] for row in rules] == [
        row['text'] for row in hero_run['packet']['voyage_sentences']]
    approval = next(row for row in receipt['steps'] if row['stage'] == 'confirmation')
    notes = json.loads(approval['notes'])
    assert notes['confirmed_count'] == len(rules)
    assert notes['confirmation'] == confirmation
    assert approval['source_sha256'] == sha256_file(HERE.parent / 'runner/core.py')


def test_frozen_hero_uses_adopted_bay_nine_reading_and_pipeline_hash(hero_run):
    pipeline = hero_run.get('pipeline_sha256')
    if not pipeline:
        pytest.skip('historical pre-freeze DG package; leader will regenerate bay-nine hero after freezing')
    selection = read(HERE.parent / 'radio/hero-selection.json')
    source = hero_run['fixture_provenance']
    assert source['hero_sentence'] == selection['hero_sentence']
    assert source['rule'] == selection['canonical_rule']
    assert source['rule']['constraint'] == {'type': 'close_bay', 'bay': 9}
    assert source['reader']['adapter_sha256'] == selection['reader']['adapter_sha256']
    assert source['reader']['decoder_sha256'] == selection['reader']['decoder_sha256']
    assert source['pipeline_sha256'] == pipeline
    # The original runner receipt is preserved; its API has no pipeline-hash input.
    assert hero_run['receipt']['pipeline_sha256'] in {None, pipeline}
    assert read(RUN / 'package/receipt.json')['pipeline_sha256'] == pipeline
    assert read(RUN / 'package/package.json')['pipeline_sha256'] == pipeline
    assert [row['reading'] for row in hero_run['receipt']['rules']] == [selection['canonical_rule']]
    assert [row['text'] for row in hero_run['packet']['voyage_sentences']] == [selection['hero_sentence']]
    reader_stages = [row for row in hero_run['receipt']['steps'] if row['stage'] == 'reader' and row['adapter_sha256']]
    assert len(reader_stages) == 1
    assert reader_stages[0]['adapter_sha256'] == selection['reader']['adapter_sha256']
    from experiments.mvp.pipeline.package.hero_acceptance import verify_current_hero
    accepted = verify_current_hero(RUN, pipeline_sha256=pipeline)
    assert accepted['passed'], accepted['errors']


def test_real_hero_verifies_offline_without_regeneration_runtime(monkeypatch, request):
    import socket
    from experiments.mvp.pipeline.package import verify_package
    from experiments.mvp.pipeline.package.build_fixture import wait_for_admission
    def forbidden(*args, **kwargs):
        raise AssertionError('hero verification attempted network access')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    wait_for_admission()
    result = verify_package(RUN / 'package')
    assert result['passed'], {key: value for key, value in result.items() if key != 'checker'}
    assert result['checker']['full_checker_passed'] is True
    request.node.user_properties.extend([
        ('verify_api_wall_s', result['wall_s']), ('checker_wall_s', result['checker']['wall_s'])])


def test_hero_run_preserves_exact_lattice_candidate_and_input_hash(hero_run):
    candidate = read(RUN / 'stored-candidate.json')
    evidence = hero_run['evidence']
    assert evidence['stored_authenticated_sha256'] == sha256_json(candidate)
    assert candidate['plan'] == hero_run['plan']
    assert candidate['instance_sha256'] == sha256_json(hero_run['instance'])
    assert candidate['canonical_rules'] == [row['reading'] for row in hero_run['receipt']['rules']]
    assert candidate['engine_version'] == '0.13.0'
    assert candidate['engine_manifest_sha256'] == hero_run['receipt']['engine_manifest_sha256']
    engine = next(row for row in hero_run['receipt']['steps'] if row['stage'] == 'engine')
    assert engine['mode'] == 'stored'
    assert json.loads(engine['notes'])['engine_manifest_sha256'] == candidate['engine_manifest_sha256']
    selection = read(HERE.parent / 'lattice/hero.json')
    source = next(row for name, row in selection.items()
                  if name in {'hero', 'backup'} and row['instance_sha256'] == candidate['instance_sha256'])
    packet_source = hero_run['packet']['base_instance']
    assert Path(packet_source['path']).resolve() == Path(source['path']).resolve()
    assert packet_source['sha256'] == source['file_sha256'] == sha256_file(source['path'])
    frames = read(RUN / 'native-frames.json')
    assert frames['complete'] is True and frames['passed'] is True
    assert len(frames['frames']) == len(candidate['plan']['moves']) + 1
    assert frames['instance_sha256'] == candidate['instance_sha256']
    assert frames['plan_sha256'] == sha256_json(candidate['plan'])
    payload = read(RUN / 'package/provenance.json')
    assert payload['native_frames_sha256'] == sha256_file(RUN / 'native-frames.json')


@pytest.mark.parametrize('alteration', ['runner_source', 'approval', 'plan'])
def test_hero_evidence_tampering_cannot_pass_as_run_packet(hero_run, alteration):
    changed = deepcopy(hero_run)
    if alteration == 'runner_source':
        next(row for row in changed['receipt']['steps'] if row['stage'] == 'engine')['source_sha256'] = '0' * 64
    elif alteration == 'approval':
        changed['confirmation']['source'] = 'unrecorded approval'
    else:
        changed['plan']['moves'] = []
    result = hero_provenance(changed)
    assert not result['eligible']
    assert result['errors']

"""New hero release gate, tested with fake signed evidence and no model."""
from copy import deepcopy
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from experiments.mvp.pipeline.contract.schema import GENESIS, sha256_file, sha256_json
from experiments.mvp.pipeline.package import hero_acceptance as gate
from experiments.mvp.pipeline.package.receipt import sign_receipt

HASH = 'a' * 64


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


@pytest.fixture
def fake_hero(tmp_path, monkeypatch):
    """Mock only the independent runner origin audit; exercise real signatures."""
    from experiments.mvp.pipeline.package import receipt as signatures
    selection = gate._read(gate.SELECTION)
    reader = gate._identity(gate._read(gate.TIMING)['identity'])
    sentence, rule = selection['hero_sentence'], selection['canonical_rule']
    root, package = tmp_path / 'hero-run', tmp_path / 'hero-run/package'
    packet = {'standing': '', 'voyage_sentences': [{'text': sentence, 'origin': 'typed'}]}
    instance, plan = {'synthetic_fixture': True}, {'moves': []}
    row = dict(sentence=sentence, reading=rule, parser=reader,
               adapter_sha256=reader['adapter_sha256'], confirmed=True, recheck={'passed': True})
    runner = dict(engine_version='0.13.0', rules=[row],
                  steps=[dict(stage='reader', adapter_sha256=reader['adapter_sha256'])])
    outcome = dict(status='checked_plan', passed=True, packet=packet, instance=instance,
                   plan=plan, receipt=runner, confirmation={'source': 'test confirmation'})
    proof = {'eligible': True, 'errors': [], 'test': 'mock origin audit, not a real hero'}
    monkeypatch.setattr(gate, 'hero_provenance', lambda value: deepcopy(proof))
    frames = dict(complete=True, passed=True, instance_sha256=sha256_json(instance),
                  plan_sha256=sha256_json(plan), frames=[{}])
    write(root / 'native-frames.json', frames)
    provenance = dict(reader=reader, hero_sentence=sentence, rule=rule, pipeline_sha256=HASH,
                      native_frames_sha256=sha256_file(root / 'native-frames.json'))
    for name, value in [('packet', packet), ('instance', instance), ('plan', plan),
                        ('runner-receipt', runner), ('hero-provenance', proof), ('provenance', provenance)]:
        write(package / (name + '.json'), value)
    write(root / 'outcome.json', outcome)
    metadata = dict(presentation='hero', status='checked_plan', pipeline_sha256=HASH)
    receipt = dict(schema_version='stowlm.pipeline-receipt/1', pipeline_sha256=HASH,
                   engine_version='0.13.0', engine_manifest_sha256='b' * 64,
                   packet_sha256=sha256_json(packet), instance_sha256=sha256_json(instance),
                   plan_sha256=sha256_json(plan), steps=[], rules=[row], label='stored', synthetic=True,
                   checker=dict(passed=True, hero_provenance=proof, chain_head=GENESIS,
                       hero_handover=dict(reader=reader, hero_sentence=sentence, rule=rule, pipeline_sha256=HASH)))
    signing_dir = tmp_path / 'keys'
    key = Ed25519PrivateKey.generate()
    signing_dir.mkdir()
    (signing_dir / 'application-ed25519.pem').write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = tmp_path / 'published-public-key.pem'
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setattr(signatures, 'PUBLIC_KEY_PATH', public)

    def publish():
        files = {p.name: sha256_file(p) for p in package.iterdir()
                 if p.name not in {'package.json', 'receipt.json'}}
        receipt['checker']['package_files'] = files
        receipt['checker']['package_index'] = metadata
        write(package / 'receipt.json', sign_receipt(receipt, key_dir=signing_dir))
        write(package / 'package.json', {**metadata, 'files': {**files, 'receipt.json': sha256_file(package / 'receipt.json')}})
    publish()
    return dict(root=root, package=package, receipt=receipt, runner=runner, outcome=outcome,
                provenance=provenance, frames=frames, metadata=metadata, publish=publish,
                public=public, proof=proof)


def check(fake):
    return gate.verify_current_hero(fake['root'], pipeline_sha256=HASH)


def test_new_hero_acceptance_is_offline_with_fake_manifest(fake_hero, monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('hero handover attempted network')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    result = check(fake_hero)
    assert result['passed'], result
    assert gate.verify_current_hero(fake_hero['package'], pipeline_sha256=HASH)['passed']


@pytest.mark.parametrize('expected', ['', None, 'not frozen yet', 'z' * 64])
def test_hero_acceptance_requires_manifest_sha256(tmp_path, expected):
    result = gate.verify_current_hero(tmp_path, pipeline_sha256=expected)
    assert not result['passed']
    assert 'pipeline_sha256' in result['errors'][0]


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('hero_sentence', 'Keep class 2.2 away.', 'hero_sentence'),
    ('rule', {'constraint': {'type': 'dg_keep_away'}}, 'rule'),
    ('pipeline_sha256', 'f' * 64, 'pipeline_sha256'),
    ('reader', {'name': 'SparkParser'}, 'reader'),
])
def test_resigned_old_or_wrong_hero_handover_fails(fake_hero, field, value, message):
    fake_hero['receipt']['checker']['hero_handover'][field] = value
    fake_hero['publish']()
    result = check(fake_hero)
    assert not result['passed']
    assert any(message in error for error in result['errors']), result


def test_resigned_package_by_unknown_key_fails_named(fake_hero, tmp_path):
    receipt = fake_hero['receipt']
    write(fake_hero['package'] / 'receipt.json', sign_receipt(receipt, key_dir=tmp_path / 'forger'))
    index = gate._read(fake_hero['package'] / 'package.json')
    index['files']['receipt.json'] = sha256_file(fake_hero['package'] / 'receipt.json')
    write(fake_hero['package'] / 'package.json', index)
    result = check(fake_hero)
    assert not result['passed']
    assert any('untrusted public key' in error for error in result['errors'])


def test_confirmed_rule_must_match_packet(fake_hero):
    fake_hero['runner']['rules'][0]['sentence'] = 'Old instruction'
    write(fake_hero['package'] / 'runner-receipt.json', fake_hero['runner'])
    write(fake_hero['root'] / 'outcome.json', fake_hero['outcome'])
    fake_hero['publish']()
    result = check(fake_hero)
    assert not result['passed']
    assert any('exact selected close_bay' in error for error in result['errors'])


def test_confirmation_cannot_be_omitted(fake_hero):
    fake_hero['runner']['rules'][0]['confirmed'] = False
    write(fake_hero['package'] / 'runner-receipt.json', fake_hero['runner'])
    write(fake_hero['root'] / 'outcome.json', fake_hero['outcome'])
    fake_hero['publish']()
    result = check(fake_hero)
    assert not result['passed']
    assert any('confirmation:' in error for error in result['errors'])


def test_claimed_reader_cannot_replace_runner_identity(fake_hero):
    fake_hero['runner']['rules'][0]['parser']['decoder_sha256'] = '0' * 64
    write(fake_hero['package'] / 'runner-receipt.json', fake_hero['runner'])
    write(fake_hero['root'] / 'outcome.json', fake_hero['outcome'])
    fake_hero['publish']()
    result = check(fake_hero)
    assert not result['passed']
    assert any('original runner instruction has different adapter or decoder' in error for error in result['errors'])


def test_empty_standing_stage_does_not_claim_a_model_adapter(fake_hero):
    fake_hero['runner']['steps'].insert(0, {'stage': 'reader', 'adapter_sha256': None})
    write(fake_hero['package'] / 'runner-receipt.json', fake_hero['runner'])
    write(fake_hero['root'] / 'outcome.json', fake_hero['outcome'])
    fake_hero['publish']()
    result = check(fake_hero)
    assert result['passed'], result


def test_wrong_reader_stage_adapter_fails_named(fake_hero):
    fake_hero['runner']['steps'][0]['adapter_sha256'] = '0' * 64
    write(fake_hero['package'] / 'runner-receipt.json', fake_hero['runner'])
    write(fake_hero['root'] / 'outcome.json', fake_hero['outcome'])
    fake_hero['publish']()
    result = check(fake_hero)
    assert not result['passed']
    assert any('reader stage:' in error for error in result['errors'])


def test_failed_run_packet_origin_audit_fails(fake_hero, monkeypatch):
    monkeypatch.setattr(gate, 'hero_provenance', lambda value: {'eligible': False, 'errors': ['invented runner stages']})
    result = check(fake_hero)
    assert not result['passed']
    assert 'run_packet: invented runner stages' in result['errors']


def test_native_frames_must_cover_selected_plan(fake_hero):
    fake_hero['frames']['frames'] = []
    write(fake_hero['root'] / 'native-frames.json', fake_hero['frames'])
    result = check(fake_hero)
    assert not result['passed']
    assert any('native frames' in error for error in result['errors'])


def test_handover_artifact_tamper_is_named(fake_hero):
    write(fake_hero['package'] / 'plan.json', {'moves': [{'tampered': True}]})
    result = check(fake_hero)
    assert not result['passed']
    assert 'file hashes: mismatch plan.json' in result['errors']


def test_fresh_writer_normalized_real_runner_rows_pass_hero_acceptance(tmp_path, monkeypatch):
    """Exercise the real writer and origin audit with original missing flags.

    Only departure physics is replayed from the stored report; application
    signing uses a fresh temporary test key. No model, planner or publication.
    """
    import base64
    import socket
    import experiments.mvp.pipeline.package as package_module
    from experiments.mvp.pipeline.package import receipt as signatures
    from stowage.model import CheckReport

    source = Path(__file__).resolve().parent / 'fixtures/hero-run'
    original_bytes = (source / 'outcome.json').read_bytes()
    original_runner_bytes = (source / 'package/runner-receipt.json').read_bytes()
    outcome = json.loads(original_bytes)
    original_runner = deepcopy(outcome['receipt'])
    assert all('confirmed' not in row for row in original_runner['rules'])

    def forbidden(*args, **kwargs):
        raise AssertionError('fresh hero handover attempted network access')

    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    report = CheckReport.model_validate(gate._read(source / 'package/checker-report.json'))
    monkeypatch.setattr(package_module, '_checker', lambda *args: report)
    original_sign = signatures.sign_receipt
    monkeypatch.setattr(package_module, 'sign_receipt', lambda receipt, **kwargs:
                        original_sign(receipt, key_dir=tmp_path / 'temporary-test-key'))
    root = tmp_path / 'new-hero'
    root.mkdir()
    (root / 'native-frames.json').write_bytes((source / 'native-frames.json').read_bytes())
    write(root / 'outcome.json', outcome)
    package_module.write_package(outcome, root / 'package')
    signed = gate._read(root / 'package/receipt.json')
    public = tmp_path / 'temporary-public-key'
    public.write_bytes(base64.b64decode(signed['public_key']))
    monkeypatch.setattr(signatures, 'PUBLIC_KEY_PATH', public)

    assert all(row['confirmed'] is True for row in signed['rules'])
    assert signed['rules'] != original_runner['rules']
    assert gate._read(root / 'package/runner-receipt.json') == original_runner
    assert outcome['receipt'] == original_runner
    assert (source / 'outcome.json').read_bytes() == original_bytes
    assert (source / 'package/runner-receipt.json').read_bytes() == original_runner_bytes
    verified = package_module.verify_package(root / 'package')
    assert verified['passed'], verified
    accepted = gate.verify_current_hero(root, pipeline_sha256=outcome['pipeline_sha256'])
    assert accepted['passed'], accepted['errors']

"""Receipt integrity tests use temporary demo keys and make no paid calls."""
from __future__ import annotations

import base64
from copy import deepcopy
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from experiments.mvp.pipeline.contract.schema import Receipt, StageRecord, chain, sha256_json
from experiments.mvp.pipeline.package.receipt import (
    KEY_NAME, PUBLIC_KEY_PATH, sign_receipt, verify_receipt,
)


def _receipt():
    return Receipt(
        engine_version='0.13.0', engine_manifest_sha256='e' * 64,
        packet_sha256='a' * 64, instance_sha256='b' * 64, plan_sha256='c' * 64,
        steps=chain([StageRecord(
            stage=name, mode='recorded', input_sha256=str(index) * 64,
            output_sha256=str(index + 1) * 64, wall_s=0.25,
            model='synthetic-recorded-model' if index == 0 else None,
        ) for index, name in enumerate(['reader', 'checker'])]),
        rules=[], checker={'passed': True, 'report_sha256': 'd' * 64,
            'level': 0, 'broken_rules': [], 'boxes_ashore': [],
            'package_files': {'plan.json': 'c' * 64, 'package.json': 'f' * 64}},
        lifts=190, label='recorded',
    ).model_dump(mode='json')


def test_sign_verify_and_reuse_runtime_demo_key(tmp_path):
    receipt = _receipt()
    original = deepcopy(receipt)
    signed = sign_receipt(receipt, key_dir=tmp_path / '.keys')
    assert receipt == original
    assert verify_receipt(signed, expected_public_key=signed['public_key']) == {
        'chain': {'passed': True, 'errors': []},
        'signature': {'passed': True, 'errors': []},
    }
    assert signed == sign_receipt(receipt, key_dir=tmp_path / '.keys')
    assert signed['steps'][1]['prev_sha256'] == sha256_json(signed['steps'][0])
    assert stat.S_IMODE((tmp_path / '.keys' / KEY_NAME).stat().st_mode) == 0o600
    assert 'DEMO KEY' in (tmp_path / '.keys' / 'README.txt').read_text()


def test_edited_row_breaks_chain_and_signature(tmp_path):
    signed = sign_receipt(_receipt(), key_dir=tmp_path)
    signed['steps'][0]['model'] = 'edited-model'
    result = verify_receipt(signed, expected_public_key=signed['public_key'])
    assert not result['chain']['passed']
    assert result['chain']['errors'][0]['step'] == 1
    assert result['chain']['errors'][0]['stage'] == 'checker'
    assert not result['signature']['passed']


def test_signing_does_not_repair_a_broken_chain(tmp_path):
    receipt = _receipt()
    receipt['steps'][0]['model'] = 'changed after chaining'
    with pytest.raises(ValueError, match='broken stage hash chain'):
        sign_receipt(receipt, key_dir=tmp_path)


def test_last_row_is_bound_by_signature(tmp_path):
    signed = sign_receipt(_receipt(), key_dir=tmp_path)
    signed['steps'][-1]['output_sha256'] = '9' * 64
    result = verify_receipt(signed, expected_public_key=signed['public_key'])
    assert not result['chain']['passed']
    assert result['chain']['errors'][0]['error'] == 'chain_head mismatch'
    assert not result['signature']['passed']


@pytest.mark.parametrize('field', ['plan_sha256', 'public_key'])
def test_top_level_hash_and_public_key_are_signed(tmp_path, field):
    signed = sign_receipt(_receipt(), key_dir=tmp_path)
    expected = signed['public_key']
    signed[field] = base64.b64encode(b'x' * 32).decode() if field == 'public_key' else '0' * 64
    result = verify_receipt(signed, expected_public_key=expected)
    assert result['chain']['passed']
    assert not result['signature']['passed']


def test_package_index_hash_is_signed(tmp_path):
    signed = sign_receipt(_receipt(), key_dir=tmp_path)
    signed['checker']['package_files']['package.json'] = '0' * 64
    assert not verify_receipt(signed, expected_public_key=signed['public_key'])['signature']['passed']


@pytest.mark.parametrize('field,value', [
    ('signature', None), ('signature', '!'), ('signature', 'eA=='),
    ('public_key', None), ('public_key', '!'), ('public_key', 'eA=='),
])
def test_malformed_signature_is_a_named_failure(tmp_path, field, value):
    signed = sign_receipt(_receipt(), key_dir=tmp_path)
    signed[field] = value
    result = verify_receipt(signed, expected_public_key=signed['public_key'])
    assert result['chain']['passed']
    assert not result['signature']['passed']
    assert field in result['signature']['errors'][0]['error']


def test_invalid_shared_schema_is_reported_separately(tmp_path):
    signed = sign_receipt(_receipt(), key_dir=tmp_path)
    signed['steps'][0]['invented_field'] = True
    result = verify_receipt(signed, expected_public_key=signed['public_key'])
    assert not result['chain']['passed']
    assert 'receipt schema' in result['chain']['errors'][0]['error']
    assert not result['signature']['passed']


def test_truncation_and_missing_chain_head_are_named(tmp_path):
    signed = sign_receipt(_receipt(), key_dir=tmp_path)
    truncated = deepcopy(signed)
    truncated['steps'].pop()
    result = verify_receipt(truncated, expected_public_key=signed['public_key'])
    assert result['chain']['errors'][0]['error'] == 'chain_head mismatch'
    del signed['checker']['chain_head']
    result = verify_receipt(signed, expected_public_key=signed['public_key'])
    assert result['chain']['errors'][0]['error'] == 'chain_head missing'


def test_published_key_is_public_only_and_trusts_recorded_fixture():
    import json
    assert b'PRIVATE' not in PUBLIC_KEY_PATH.read_bytes()
    signed = json.loads((Path(__file__).parent / 'fixtures/complete/receipt.json').read_text())
    assert verify_receipt(signed)['signature']['passed']


def test_valid_resigned_forgery_fails_published_and_explicit_trust(tmp_path):
    original = sign_receipt(_receipt(), key_dir=tmp_path / 'trusted')
    altered = deepcopy(original)
    altered['plan_sha256'] = 'f' * 64
    forged = sign_receipt(altered, key_dir=tmp_path / 'attacker')
    assert verify_receipt(forged, expected_public_key=forged['public_key'])['signature']['passed']
    for expected in (None, original['public_key']):
        result = verify_receipt(forged, expected_public_key=expected)
        assert result['chain']['passed']
        assert result['signature'] == {
            'passed': False, 'errors': [{'error': 'untrusted public key'}],
        }


@pytest.mark.parametrize('kind', ['base64', 'raw', 'pem', 'pem_text', 'path', 'path_text'])
def test_explicit_public_key_encodings(tmp_path, kind):
    signed = sign_receipt(_receipt(), key_dir=tmp_path / 'signer')
    raw = base64.b64decode(signed['public_key'])
    pem = serialization.load_pem_private_key(
        (tmp_path / 'signer' / KEY_NAME).read_bytes(), password=None,
    ).public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    path = tmp_path / 'trusted.pem'
    path.write_bytes(pem)
    expected = {
        'base64': signed['public_key'], 'raw': raw, 'pem': pem,
        'pem_text': pem.decode(), 'path': path, 'path_text': str(path),
    }[kind]
    assert verify_receipt(signed, expected_public_key=expected)['signature']['passed']


def test_untrusted_key_does_not_hide_invalid_signature(tmp_path):
    signed = sign_receipt(_receipt(), key_dir=tmp_path)
    signed['signature'] = base64.b64encode(b'0' * 64).decode()
    result = verify_receipt(signed)
    assert result['signature']['errors'] == [
        {'error': 'untrusted public key'}, {'error': 'Ed25519 signature mismatch'},
    ]

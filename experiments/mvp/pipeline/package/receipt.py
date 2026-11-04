"""Local demo signatures over the shared receipt contract. No network or model calls."""
from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
import tempfile
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import ValidationError

from experiments.mvp.pipeline.contract.schema import (
    GENESIS, Receipt, canonical_json, chain_head, chain_intact, sha256_json,
)

KEY_DIR = Path(__file__).parent / '.keys'
KEY_NAME = 'application-ed25519.pem'
PUBLIC_KEY_PATH = Path(__file__).parent / 'published-public-key.pem'
KEY_NOTE = (
    'DEMO KEY: local application Ed25519 signing key, generated at runtime.\n'
    'This is our own signature, not approval by a carrier or other authority.\n'
    'Do not distribute or commit the private key. Verify against the published public key.\n'
)


def _private_key(key_dir: Path) -> Ed25519PrivateKey:
    key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = key_dir / KEY_NAME
    if not path.exists():
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        # Publish a complete key atomically, without replacing another writer's key.
        with tempfile.NamedTemporaryFile(dir=key_dir, prefix='.key-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(pem)
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink()
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Application signing key is not Ed25519')
    (key_dir / 'README.txt').write_text(KEY_NOTE, encoding='utf-8')
    return key


def _signed_bytes(receipt: dict) -> bytes:
    return canonical_json({key: value for key, value in receipt.items() if key != 'signature'}).encode('utf-8')


def sign_receipt(receipt: dict, *, key_dir: str | Path | None = None) -> dict:
    """Return a signed shared Receipt without changing the caller's input or chain.

    The public key is covered by the signature. Verification also checks it
    against the published application key, independently of the receipt.
    The writer binds payload hashes and index metadata in ``checker`` before
    calling this function. The writer must explicitly chain its stage records.
    """
    validated = Receipt.model_validate(receipt)
    if not chain_intact(validated.steps):
        raise ValueError('Cannot sign receipt with a broken stage hash chain')
    result = validated.model_dump(mode='json')
    result['checker']['chain_head'] = chain_head(validated.steps)
    key = _private_key(Path(key_dir) if key_dir is not None else KEY_DIR)
    public_key = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    result['public_key'] = base64.b64encode(public_key).decode('ascii')
    result['signature'] = base64.b64encode(key.sign(_signed_bytes(result))).decode('ascii')
    return result


def _decode(value: Any, size: int, field: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f'{field} is missing or is not a base64 string')
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f'{field} is not valid base64') from exc
    if len(decoded) != size:
        raise ValueError(f'{field} has invalid Ed25519 length')
    return decoded


def _expected_key(value: Any) -> bytes:
    """Read a caller's trust anchor, never one supplied by the receipt."""
    if value is None:
        value = PUBLIC_KEY_PATH
    if isinstance(value, os.PathLike):
        value = Path(value).read_bytes()
    elif isinstance(value, str):
        if '-----BEGIN' in value:
            value = value.encode('ascii')
        else:
            try:
                path = Path(value)
                if path.is_file():
                    value = path.read_bytes()
            except OSError:
                pass
    if isinstance(value, bytes):
        if value.startswith(b'-----BEGIN'):
            key = serialization.load_pem_public_key(value)
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError('expected_public_key is not Ed25519')
            return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if len(value) == 32:
            return value
        value = value.decode('ascii')
    return _decode(value, 32, 'expected_public_key')


def verify_receipt(receipt: dict, *, expected_public_key: Any = None) -> dict:
    """Check chain and signature offline, pinning the signer to an independent key.

    The default trust anchor is ``published-public-key.pem``. An explicit key
    may be a PEM/path, raw public bytes or the base64 representation used in
    receipts. A valid signature by another key is an ``untrusted public key``
    failure; the included key alone never establishes trust.
    """
    chain_errors = []
    try:
        validated = Receipt.model_validate(receipt)
        previous = GENESIS
        for index, row in enumerate(validated.steps):
            if row.prev_sha256 != previous:
                chain_errors.append({
                    'step': index, 'stage': row.stage, 'error': 'prev_sha256 mismatch',
                    'expected': previous, 'actual': row.prev_sha256,
                })
            previous = sha256_json(row.model_dump(mode='json'))
        head = validated.checker.get('chain_head')
        if head != previous:
            chain_errors.append({
                'error': 'chain_head mismatch' if head is not None else 'chain_head missing',
                'expected': head, 'actual': previous,
            })
    except (ValidationError, ValueError, TypeError) as exc:
        chain_errors.append({'error': f'receipt schema: {exc}'})

    signature_errors = []
    try:
        public = _decode(receipt.get('public_key'), 32, 'public_key')
        signature = _decode(receipt.get('signature'), 64, 'signature')
        if public != _expected_key(expected_public_key):
            signature_errors.append({'error': 'untrusted public key'})
        Ed25519PublicKey.from_public_bytes(public).verify(signature, _signed_bytes(receipt))
    except InvalidSignature:
        signature_errors.append({'error': 'Ed25519 signature mismatch'})
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        signature_errors.append({'error': str(exc)})
    return {
        'chain': {'passed': not chain_errors, 'errors': chain_errors},
        'signature': {'passed': not signature_errors, 'errors': signature_errors},
    }

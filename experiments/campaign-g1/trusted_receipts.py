"""Gate-executed receipts, signed by a local producer; only public trust travels.

The private signing key is never an input artifact and must never be uploaded.
Verifiers use the independently configured public anchor, never a key supplied
by the receipt. A hash-consistent caller-written checker report is not a receipt.
"""
from __future__ import annotations
import base64
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from eval.snapshot_io import read_snapshot

HERE=Path(__file__).resolve().parent
PRIVATE_KEY=HERE/'.admission-private'/'ed25519.pem'
TRUST_STORE=HERE/'admission-trust.json'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _atomic(path,data,mode=0o600):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.'+path.name+'.'+uuid4().hex)
    fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,mode)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data)
        os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)


def initialize_signer():
    """Explicit local setup before canaries; never auto-trust a receipt's key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    if PRIVATE_KEY.exists() or TRUST_STORE.exists():
        if not PRIVATE_KEY.exists() or not TRUST_STORE.exists():raise ValueError('partial admission signer setup; refuse replacement')
        key=serialization.load_pem_private_key(read_snapshot(PRIVATE_KEY).data,password=None)
        anchor=read_snapshot(TRUST_STORE).json()
        raw=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        if anchor.get('public_key')!=base64.b64encode(raw).decode():raise ValueError('admission private key differs from public trust anchor')
        return anchor
    key=Ed25519PrivateKey.generate()
    raw=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    anchor=dict(schema_version=1,algorithm='Ed25519',key_id=hashlib.sha256(raw).hexdigest(),public_key=base64.b64encode(raw).decode())
    PRIVATE_KEY.parent.mkdir(parents=True,exist_ok=True);PRIVATE_KEY.parent.chmod(0o700)
    _atomic(PRIVATE_KEY,key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    _atomic(TRUST_STORE,canonical(anchor)+b'\n',mode=0o644)
    return anchor


def trust_pin():
    snapshot=read_snapshot(TRUST_STORE);value=snapshot.json()
    if value.get('schema_version')!=1 or value.get('algorithm')!='Ed25519':raise ValueError('unsupported admission trust anchor')
    raw=base64.b64decode(value['public_key'],validate=True)
    if len(raw)!=32 or hashlib.sha256(raw).hexdigest()!=value.get('key_id'):raise ValueError('admission public key fingerprint differs')
    return snapshot,value


def sign_execution(kind,payload,artifact_pins,output,*,context=None):
    """Called only by the controlled gate worker after live checker/mask work."""
    from cryptography.hazmat.primitives import serialization
    anchor_snapshot,anchor=trust_pin()
    if PRIVATE_KEY.stat().st_mode&0o077:raise ValueError('admission private key must have mode 0600')
    private=read_snapshot(PRIVATE_KEY)
    key=serialization.load_pem_private_key(private.data,password=None)
    raw=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    if base64.b64encode(raw).decode()!=anchor['public_key']:raise ValueError('signer is not the configured public trust anchor')
    pins={str(Path(name).resolve()):digest for name,digest in artifact_pins.items()}
    artifacts=[read_snapshot(name,expected_sha256=digest) for name,digest in pins.items()]
    body=dict(schema_version=1,kind=kind,key_id=anchor['key_id'],context=context or {},payload=payload,
        artifacts=pins,created_at=datetime.now(timezone.utc).isoformat())
    signed={**body,'signature':base64.b64encode(key.sign(canonical(body))).decode()}
    anchor_snapshot.require_fresh();private.require_fresh()
    data=canonical(signed)+b'\n'
    _atomic(output,data,mode=0o644)
    published=read_snapshot(output,expected_sha256=hashlib.sha256(data).hexdigest())
    for snapshot in artifacts:snapshot.require_fresh()
    anchor_snapshot.require_fresh();private.require_fresh()
    return dict(path=str(Path(output).resolve()),sha256=published.sha256,
        key_id=anchor['key_id'],trust_sha256=anchor_snapshot.sha256)


def verify_execution(receipt,kind,context=None,*,verify_artifacts=True,allow_diagnostic=False):
    """Verify origin and exact context; remote consumers may use pinned artifacts.

    Artifact-free verification is for transporting an already-admitted dataset:
    its own content hashes and loaded component versions must still be checked.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    snapshot=None
    if isinstance(receipt,dict) and 'data_base64' in receipt:
        data=base64.b64decode(receipt['data_base64'],validate=True)
        if hashlib.sha256(data).hexdigest()!=receipt.get('sha256'):
            raise ValueError('transported execution receipt checksum differs')
        record=json.loads(data)
    else:
        if isinstance(receipt,dict):snapshot=read_snapshot(receipt['path'],expected_sha256=receipt['sha256'])
        else:snapshot=read_snapshot(receipt)
        record=snapshot.json()
    anchor_snapshot,anchor=trust_pin()
    signature=base64.b64decode(record.pop('signature'),validate=True)
    if record.get('schema_version')!=1 or record.get('kind')!=kind or record.get('key_id')!=anchor['key_id']:
        raise ValueError('receipt kind/key/schema is not trusted')
    Ed25519PublicKey.from_public_bytes(base64.b64decode(anchor['public_key'])).verify(signature,canonical(record))
    observed=record.get('context')
    if not isinstance(observed,dict) or any(observed.get(key)!=value for key,value in (context or {}).items()):
        raise ValueError('receipt execution context differs')
    if not allow_diagnostic and (observed.get('diagnostic') is not False or observed.get('evidence_eligible') is not True):
        raise ValueError('diagnostic or ineligible execution receipt')
    pins=record.get('artifacts')
    if not isinstance(pins,dict):raise ValueError('execution receipt lacks artifact inventory')
    if verify_artifacts:
        artifacts=[read_snapshot(name,expected_sha256=digest) for name,digest in pins.items()]
        for artifact in artifacts:artifact.require_fresh()
    anchor_snapshot.require_fresh()
    if snapshot is not None:snapshot.require_fresh()
    return record['payload']


def portable_execution(receipt):
    """Ship only the signed public record; no private key or absolute path needed."""
    snapshot=read_snapshot(receipt['path'],expected_sha256=receipt['sha256'])
    return dict(data_base64=base64.b64encode(snapshot.data).decode(),sha256=snapshot.sha256)

"""Explicit host confirmation of an exact authenticated reader interpretation.

This capability is process-local (and inherited by fork), like ReadBrief.
The reader's checkpoint API transports readings only, never confirmation.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets

from experiments.mvp.reader import public_brief


_SCHEMA = 'stowlm.binding-confirmation/1'
_PROCESS_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class ConfirmedBrief(public_brief.ReadBrief):
    """Reader-compatible inspection plus a separately authenticated host event."""
    source: str
    confirmation_authentication: str


def _source(source):
    if not isinstance(source, str) or not source.strip():
        raise ValueError('Confirmation source must be a nonempty string naming the host confirmation event')
    return source


def _message(receipt, source):
    return json.dumps(dict(schema_version=_SCHEMA, reading_payload=receipt.payload,
        reading_authentication=receipt.authentication, source=source),
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _mac(receipt, source):
    return hmac.new(_PROCESS_KEY, _message(receipt, source), hashlib.sha256).hexdigest()


def confirm_brief(receipt, source):
    """Seal a host's explicit approval; never register this as a model tool.

    ``source`` identifies the real host action or labelled batch policy. It is
    an application attestation, not independent evidence of human identity.
    Confirming an unresolved reading does not resolve it or permit execution.
    """
    source = _source(source)
    public_brief._open(receipt)
    return ConfirmedBrief(receipt.payload, receipt.authentication, source, _mac(receipt, source))


def verify_confirmation(receipt):
    """Authenticate approval before the caller verifies the input/context."""
    if not isinstance(receipt, ConfirmedBrief):
        # Preserve the useful reader-authentication error for tampered readings.
        if isinstance(receipt, public_brief.ReadBrief):
            public_brief._open(receipt)
        raise ValueError('Unconfirmed brief: call confirm_brief(receipt, source) after explicit host approval before execute_brief')
    source = _source(receipt.source)
    if (not isinstance(receipt.payload, str) or not isinstance(receipt.authentication, str)
            or not isinstance(receipt.confirmation_authentication, str)
            or not re.fullmatch(r'[0-9a-f]{64}', receipt.confirmation_authentication)
            or not hmac.compare_digest(_mac(receipt, source), receipt.confirmation_authentication)):
        raise ValueError('Invalid authenticated brief confirmation')
    public_brief._open(receipt)
    return dict(schema_version=_SCHEMA, source=source,
        receipt_sha256=hashlib.sha256(receipt.payload.encode()).hexdigest(),
        confirmation_sha256=hashlib.sha256(_message(receipt, source)).hexdigest())

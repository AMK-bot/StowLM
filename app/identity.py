"""Content identities shared by artifact imports and the local demo registry.

Names are labels. Canonical model digests match checker.instance.sha256 tokens;
input_sha256 in evaluation records instead hashes the original file bytes.
Neither identity is a safety verdict or authentication.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from stowage.model import Instance
from stowage.versions import SCHEMA_VERSION


_SHA256 = re.compile(r'[0-9a-f]{64}')
_PREFIX = 'checker.instance.sha256:'


def instance_content_digest(instance: Instance) -> str:
    return hashlib.sha256(json.dumps(instance.model_dump(mode='json'), sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def instance_binding_matches(instance: Instance, digest: str, source: dict | None = None) -> bool:
    """Verify a current or original legacy input binding without erasing fields.

    Same-major legacy readers add model defaults (1.9 adds crane height, side,
    lane and direct-delivery fields). An old checker hashed the original model
    representation. Its exact raw hash remains usable as input identity only
    when that source validates and normalizes to this exact current world.
    The original payload is never projected, stripped, or used as safety proof.
    """
    if instance_content_digest(instance) == digest:
        return True
    if not isinstance(source, dict):
        return False
    try:
        version = tuple(map(int, source.get('schema_version', '').split('.')))
        current = tuple(map(int, SCHEMA_VERSION.split('.')))
        if len(version) != 2 or version[0] != current[0] or version >= current:
            return False
        original = hashlib.sha256(json.dumps(source, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        if original != digest:
            return False
        normalized = Instance.model_validate(deepcopy(source))
        return instance_content_digest(normalized) == instance_content_digest(instance)
    except (TypeError, ValueError, AttributeError):
        return False


def record_instance_digest(record: dict) -> str | None:
    """Read direct report bindings, never inherited planner/instance lineage."""
    values = set()
    for key in ('report', 'check_report'):
        report = record.get(key)
        if report is None:
            continue
        if not isinstance(report, dict):
            raise ValueError('Malformed checker report: expected an object')
        provenance = report.get('provenance', {})
        if not isinstance(provenance, dict):
            raise ValueError('Malformed checker report provenance: expected an object')
        parents = provenance.get('parents', [])
        if not isinstance(parents, (list, tuple)) or any(not isinstance(parent, str) for parent in parents):
            raise ValueError('Malformed checker report provenance parents: expected a list of strings')
        for parent in parents:
            if isinstance(parent, str) and parent.startswith(_PREFIX):
                digest = parent.removeprefix(_PREFIX)
                if not _SHA256.fullmatch(digest):
                    raise ValueError('Malformed checker instance content binding')
                values.add(digest)
    if len(values) > 1:
        raise ValueError('Conflicting checker instance content bindings')
    return next(iter(values), None)


def record_file_digest(record: dict) -> str | None:
    digest = record.get('input_sha256')
    if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
        raise ValueError('Malformed original instance input_sha256 checksum')
    return digest

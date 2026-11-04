"""Hash-map proofs for excluded deadline measurement files; no planning policy migration."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json

HERE = Path(__file__).resolve().parent
BINDING = HERE.parent / 'binding'
AUDIT = HERE / 'audits/deadline-measurement-package/audit.json'
AUDIT_SHA256 = 'e064cdd00c5873862242d04a88ff85e22b6b81176ab43ab847738a803c503a33'
CANONICAL_SHA256 = '9fbd6d4bb006bdb8e4f9e04c25b0eb2990324cd431dc2f60088826dcc0743fec'
OBSERVATIONS = HERE / 'audits/binding-observations'
_SHA = re.compile('[0-9a-f]{64}')


def _current_audit(binding_root):
    raw = AUDIT.read_bytes()
    if hashlib.sha256(raw).hexdigest() != AUDIT_SHA256:
        raise ValueError('deadline import audit changed')
    audit = json.loads(raw)
    canonical = audit['canonical_runtime_files']
    if sha256_json(canonical) != CANONICAL_SHA256 or audit.get('runtime_imports_deadline') is not False:
        raise ValueError('deadline import audit is invalid')
    from .binding_sources import runtime_source_files
    root = Path(binding_root)
    if set(runtime_source_files(root)) != set(canonical) or any(sha256_file(root / name) != digest
                                                              for name, digest in canonical.items()):
        raise ValueError('planning runtime differs from the audited import closure')
    return audit


def _observed_alias(value, audit):
    if not isinstance(value, str) or not _SHA.fullmatch(value) or value == CANONICAL_SHA256:
        return None
    files = audit['expanded_source_maps'].get(value)
    observation_sha256 = None
    if files is None:
        path = OBSERVATIONS / (value + '.json')
        raw = path.read_bytes()
        record = json.loads(raw)
        files = record['source_files']
        if record.get('fingerprint') != value or record.get('stable_double_read') is not True or \
                record.get('runtime_imports_deadline') is not False or \
                record.get('canonical_fingerprint') != CANONICAL_SHA256 or \
                record.get('canonical_runtime_files') != audit['canonical_runtime_files']:
            return None
        observation_sha256 = hashlib.sha256(raw).hexdigest()
    if not isinstance(files, dict) or sha256_json(files) != value:
        return None
    canonical = audit['canonical_runtime_files']
    if any(files.get(name) != digest for name, digest in canonical.items()):
        return None
    extras = {name: digest for name, digest in files.items() if name not in canonical}
    if not extras or any(not isinstance(name, str) or not name.startswith('deadline/') or
                         '..' in Path(name).parts or Path(name).suffix != '.py' or
                         not isinstance(digest, str) or not _SHA.fullmatch(digest)
                         for name, digest in extras.items()):
        return None
    return dict(expanded_fingerprint=value, canonical_fingerprint=CANONICAL_SHA256,
                full_source_files=files, excluded_measurement_files=extras,
                observation_sha256=observation_sha256)


def normalize_row(row, *, binding_root=BINDING):
    """Normalize only cryptographically reconstructed, observed deadline aliases."""
    copied = deepcopy(row)
    try:
        audit = _current_audit(binding_root)
        aliases = {}
        for name in ('binding_source_sha256', 'binding_source_after_sha256'):
            value = row.get(name)
            if value == CANONICAL_SHA256 or value is None:
                continue
            try:
                proof = _observed_alias(value, audit)
            except (OSError, ValueError, TypeError, KeyError):
                proof = None
            if proof is not None:
                aliases[name] = proof
                copied[name] = CANONICAL_SHA256
        if not aliases:
            return copied, None
        proof = dict(policy='audited-unloaded-deadline-measurement-package/1', audit_sha256=AUDIT_SHA256,
                     historical_binding_source_sha256=row.get('binding_source_sha256'),
                     historical_binding_source_after_sha256=row.get('binding_source_after_sha256'),
                     current_binding_source_sha256=CANONICAL_SHA256, aliases=aliases,
                     historical_runtime_files=audit['canonical_runtime_files'],
                     source_screen_runtime_sha256=CANONICAL_SHA256,
                     planning_origin_binding_source_sha256=row.get('binding_source_sha256'),
                     native_checker_and_all_frame_regating_required=True)
        return copied, proof
    except (OSError, ValueError, TypeError, KeyError):
        return copied, None


def measurement_compatibility(row, *, binding_root=BINDING):
    normalized, proof = normalize_row(row, binding_root=binding_root)
    if proof is None or normalized.get('binding_source_sha256') != CANONICAL_SHA256 or \
            normalized.get('binding_source_after_sha256', CANONICAL_SHA256) != CANONICAL_SHA256:
        return None
    return proof

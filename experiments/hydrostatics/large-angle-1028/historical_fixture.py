"""Exact-byte historical input identity; this module performs no solves."""
from __future__ import annotations

import hashlib
from pathlib import Path

HISTORICAL_INSTANCE = 'experiments/mask/c2-on-current/random-tiny-1.instance.json'


def read_historical_fixture(root, instance_type, source_path=HISTORICAL_INSTANCE):
    """Hash and parse one immutable byte string, before any geometry solve."""
    source = Path(source_path)
    payload = (Path(root) / source).read_bytes()
    identity = {'source_path': source.as_posix(),
                'source_instance_sha256': hashlib.sha256(payload).hexdigest()}
    return instance_type.model_validate_json(payload), identity


def verify_historical_identity(root, identity):
    """Read the actual fixture; admit the two archived digest field names."""
    # The original historical result stores its identity in this nested recipe.
    record = identity.get('representative_condition', identity)
    paths = [p for p in (record.get('source_path'), identity.get('source_path')) if p is not None]
    if len(set(paths)) > 1:
        raise ValueError('Contradictory historical fixture paths')
    source_path = paths[0] if paths else HISTORICAL_INSTANCE
    digests = [d for d in (record.get('source_sha256'), record.get('source_instance_sha256'),
                           identity.get('source_instance_sha256')) if d is not None]
    if not digests or len(set(digests)) != 1:
        raise ValueError('Missing or contradictory historical fixture digests')
    actual = hashlib.sha256((Path(root) / source_path).read_bytes()).hexdigest()
    if actual != digests[0]:
        raise RuntimeError(f'Historical fixture changed: {source_path}')
    return actual


def require_fixture_hash(hashes, identity):
    """Require the runtime source map to include the exact parsed byte digest."""
    if hashes.get(identity['source_path']) != identity['source_instance_sha256']:
        raise RuntimeError('Historical fixture source map disagrees with parsed input')

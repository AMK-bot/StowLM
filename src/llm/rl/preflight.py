"""Read-only admission of cache bytes prepared by an explicit CPU warm-up.

The receipt is an integrity snapshot, not a replacement physics certificate.
Its producer must first finish the owner's full cache warm-up successfully on
this source snapshot. GPU admission only verifies bytes; it never builds tables.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

RECEIPT_NAME = 'grpo-preflight.json'


def _json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _assets(root):
    root = Path(root)
    if not root.is_dir():
        raise RuntimeError('physics cache directory missing')
    assets = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('physics cache symlinks are not admitted')
        if not path.is_file() or path == root / RECEIPT_NAME:
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        assets[path.relative_to(root).as_posix()] = {
            'size': path.stat().st_size, 'sha256': digest.hexdigest()}
    if not assets:
        raise RuntimeError('physics cache is empty')
    return assets


def write_cache_receipt(instance_json, manifest, cache_root, *, warmup_summary):
    """Called only after successful explicit cache_warm; no implicit warming.

    ``warmup_summary`` is the freshly returned owner command's summary, never a
    historical report used to infer readiness after source changes.
    """
    if (warmup_summary.get('complete') is not True or not warmup_summary.get('ships')
            or any(row.get('complete') is not True for row in warmup_summary['ships'])):
        raise RuntimeError('physics cache warm-up incomplete')
    receipt = {'schema': 1, 'manifest': asdict(manifest),
               'instance_sha256': _json_digest(instance_json),
               'assets': _assets(cache_root)}
    path = Path(cache_root) / RECEIPT_NAME
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(receipt, sort_keys=True) + '\n')
    temporary.replace(path)
    return receipt


def preflight_physics_cache(instance_json, manifest, cache_root):
    """Fail before GPU reservation on absent, stale, or altered prepared assets."""
    try:
        receipt = json.loads((Path(cache_root) / RECEIPT_NAME).read_text())
        if (receipt['schema'] != 1 or receipt['manifest'] != asdict(manifest)
                or receipt['instance_sha256'] != _json_digest(instance_json)
                or receipt['assets'] != _assets(cache_root)):
            raise RuntimeError('physics cache receipt differs from pinned inputs or cache bytes')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('physics cache receipt missing or malformed') from exc
    return receipt

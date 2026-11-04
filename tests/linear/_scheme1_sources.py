"""Resolve historical pins against exact archived bytes, never live source."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ARCHIVE=ROOT/'experiments/linear-model/epoch_requalification/hash-scheme-1028-review/scheme1-history'
MANIFEST_SHA256='f9f05ae43c4ec0b676c991f7cab8451d6a26d35d0eda07b4eb6f99e69d4f5819'


def bind_original_sources(promoter,*,archive=ARCHIVE):
    content=(archive/'source-tree-manifest.json').read_bytes()
    if hashlib.sha256(content).hexdigest()!=MANIFEST_SHA256:
        raise ValueError('scheme-1 source manifest checksum mismatch')
    metadata=json.loads(content)
    if metadata['historical_hash_scheme_version']!='1' or metadata['production_usable'] is not False:
        raise ValueError('historical source identity changed')
    tree=archive/'source-tree'
    for name,expected in metadata['files'].items():
        path=(tree/name).resolve()
        if not path.is_relative_to(tree.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('scheme-1 original source checksum mismatch')
    # Only the isolated research validator resolves original relative source
    # pins here. Production source paths, hashes and admission stay untouched.
    promoter.ROOT=tree
    return promoter

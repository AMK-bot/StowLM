"""Serialization-independent held-out exclusion for all ExIt training inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS = ROOT / 'experiments/mvp/corpus'
_FILE_CACHE = {}


def canonical_digest(instance):
    if isinstance(instance, dict) and 'vessel' in instance and 'containers' in instance:
        from stowage.model import Instance
        instance = Instance.model_validate(instance)
    value = instance.model_dump(mode='json') if hasattr(instance, 'model_dump') else instance
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


class IdentityGuard:
    """Index raw/canonical hashes, immutable IDs and root seeds, including streams.

    Manifest entries alone are insufficient: val instances arrive before manifest
    refresh. Read only JSON instance objects from the held-out directories; audit
    sidecars supply immutable root-seed/raw-hash identity during publication.
    """
    def __init__(self, corpus_root=DEFAULT_CORPUS):
        self.root = Path(corpus_root).resolve()
        self.identities = {split: set() for split in ('test', 'showcase', 'val')}
        manifest = self.root / 'manifest.json'
        if manifest.exists():
            for entry in json.loads(manifest.read_text()).get('files', []):
                if entry.get('split') in self.identities:
                    target = self.identities[entry['split']]
                    for key in ('sha256', 'seed', 'instance_id'):
                        if entry.get(key) is not None:
                            target.add((key, str(entry[key])))
        for split, target in self.identities.items():
            for path in (self.root / split).rglob('*.json'):
                cache_key=(str(path),path.stat().st_mtime_ns,path.stat().st_size)
                if cache_key in _FILE_CACHE:
                    target |= _FILE_CACHE[cache_key]
                    continue
                file_tokens=set()
                value = json.loads(path.read_text())
                if not isinstance(value, dict):
                    continue
                if path.name.endswith('.audit.json'):
                    if value.get('instance_sha256'):
                        file_tokens.add(('sha256', value['instance_sha256']))
                    seed = value.get('config', {}).get('seed')
                    if seed is not None:
                        file_tokens.add(('seed', str(seed)))
                elif 'vessel' in value and 'containers' in value:
                    from stowage.model import Instance
                    instance = Instance.model_validate(value)
                    file_tokens |= self.tokens(instance, hashlib.sha256(path.read_bytes()).hexdigest())
                target |= file_tokens
                _FILE_CACHE[cache_key]=file_tokens

    @staticmethod
    def tokens(instance, raw_sha=None):
        get = instance.get if isinstance(instance, dict) else lambda key: getattr(instance, key)
        result = {('canonical_sha256', canonical_digest(instance))}
        for key, label in (('id','instance_id'),('seed','seed')):
            if get(key) is not None:
                result.add((label, str(get(key))))
        if raw_sha:
            result.add(('sha256', raw_sha))
        return result

    def require_allowed(self, instance, *, role='train', raw_sha=None, input_path=None):
        if role not in ('train', 'loss_val', 'audit'):
            raise ValueError('unknown ExIt identity admission role')
        blocked = ('test', 'showcase', 'val') if role == 'train' else ('test', 'showcase')
        tokens = self.tokens(instance, raw_sha)
        for split in blocked:
            if tokens & self.identities[split]:
                raise ValueError(f'held-out {split} identity/seed/hash cannot enter ExIt {role}')
            if input_path is not None and Path(input_path).resolve().is_relative_to(self.root / split):
                raise ValueError(f'held-out {split} path cannot enter ExIt {role}')
        return canonical_digest(instance)


_CACHE = {}


def identity_guard(corpus_root=DEFAULT_CORPUS):
    root = Path(corpus_root).resolve()
    paths = [root/'manifest.json'] + [p for split in ('test','showcase','val')
                                     for p in (root/split).rglob('*.json')]
    signature = tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in sorted(paths) if p.exists())
    cached = _CACHE.get(root)
    if cached is None or cached[0] != signature:
        disk=ROOT/'experiments/mvp/exit/identity-cache.json'
        if root==DEFAULT_CORPUS.resolve() and disk.exists():
            saved=json.loads(disk.read_text())
            for row in saved.get('files',[]):
                _FILE_CACHE.setdefault(tuple(row['key']),{tuple(t) for t in row['tokens']})
        cached = (signature,IdentityGuard(root))
        _CACHE[root] = cached
        if root==DEFAULT_CORPUS.resolve():
            value={'files':[dict(key=list(k),tokens=sorted(v)) for k,v in _FILE_CACHE.items()
                            if str(k[0]).startswith(str(root))]}
            disk.parent.mkdir(parents=True,exist_ok=True)
            temporary=disk.with_name(f'.identity-cache-{__import__("os").getpid()}.json')
            temporary.write_text(json.dumps(value))
            temporary.replace(disk)
    return cached[1]

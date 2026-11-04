"""Bind the standalone lab adapter's cache identity to its engine dependencies."""
import hashlib
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
MARKER = '# Engine bundle SHA256: '
PATTERN = re.compile(r'# Engine bundle SHA256: [a-f0-9]{64}')


def source_files():
    runtime = [HERE/'tiers.toml', HERE/'approved-maps.json',
               HERE/'peel_ladder/copied-manifest.json',
               HERE/'peel_ladder/assignment-frozen-hashes.json']
    return [path for path in sorted([*HERE.rglob('*.py'), *runtime])
            if 'results' not in path.relative_to(HERE).parts]


def source_digest():
    digest = hashlib.sha256()
    for path in source_files():
        if 'results' in path.relative_to(HERE).parts:
            continue
        text = path.read_text()
        if path == HERE / 'lab_method.py':
            text = PATTERN.sub(MARKER + '0'*64, text)
        digest.update(str(path.relative_to(HERE)).encode())
        digest.update(text.encode())
    return digest.hexdigest()


def stamp_adapter():
    """Refresh before invoking score.py, whose key hashes only the adapter file."""
    path = HERE / 'lab_method.py'
    text = path.read_text()
    if not PATTERN.search(text):
        raise ValueError('lab_method.py lacks the engine bundle marker')
    updated = PATTERN.sub(MARKER + source_digest(), text)
    if updated != text:
        path.write_text(updated)


if __name__ == '__main__':
    stamp_adapter()
    print(source_digest())

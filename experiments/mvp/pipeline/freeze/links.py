"""Pin replay-data leaf links without admitting arbitrary referenced files."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .evidence import private_path
from .paths import reject_symlinks

def _public_name(root, path):
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError(f'symlink target outside repository: {path}') from None
    if '..' in relative.parts:
        raise ValueError(f'noncanonical symlink path: {path}')
    if (relative.parts[:3] == ('experiments', 'mvp', 'testv3')
            or relative.parts[:4] == ('experiments', 'mvp', 'corpus', 'test-v3')
            or 'sealed' in relative.parts and relative.parts[:4] != ('experiments', 'mvp', 'pipeline', 'sealed')):
        raise ValueError(f'sealed symlink path excluded: {relative}')
    if private_path(relative):
        raise ValueError(f'private symlink path excluded: {relative}')
    return relative.as_posix()


def inspect(root, name, pinned, hashes=None, *, import_closure=()):
    """Return link identity; target must already have its own regular-file pin.

    ``pinned`` is an iterable or mapping of repository-relative regular files.
    Optional ``hashes`` validates the independently pinned target's digest as
    well. The target is always hashed here; cached digest values never replace
    the read. Links in the import closure and directory aliases are rejected;
    target-link chains may resolve to an independently pinned regular file.
    """
    root = Path(root).resolve()
    path = Path(name)
    path = path if path.is_absolute() else root / path
    relative = _public_name(root, path)
    if relative in import_closure:
        raise ValueError(f'import symlink excluded: {relative}')
    reject_symlinks(path.parent, root)
    if not path.is_symlink():
        raise ValueError(f'expected data symlink: {relative}')
    text = os.readlink(path)
    target_text = Path(text)
    lexical = target_text if target_text.is_absolute() else path.parent / target_text
    lexical = Path(os.path.abspath(lexical))
    _public_name(root, lexical)
    target = lexical.resolve(strict=True)
    target_name = _public_name(root, target)
    if not target.is_file():
        raise ValueError(f'symlink target must be a regular file: {relative}')
    if target_name not in pinned:
        raise ValueError(f'symlink target is not independently pinned: {relative}: {target_name}')
    digest = hashlib.sha256()
    with target.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    value = digest.hexdigest()
    if hashes is not None and hashes.get(target_name) != value:
        raise ValueError(f'symlink target changed from independent pin: {relative}: {target_name}')
    # Catch replacement of the link or target while its bytes were read.
    if not path.is_symlink() or os.readlink(path) != text or path.resolve(strict=True) != target:
        raise ValueError(f'symlink changed during inventory: {relative}')
    return {'link_text': text, 'resolved_target': target_name, 'target_sha256': value}

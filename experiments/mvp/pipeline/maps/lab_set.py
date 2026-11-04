"""Fail-closed dev provenance adapter for the unchanged Frontier lab loader.

Use ``with approved_dev(): catalog.load_set(HERE / 'dev.txt')`` or run
``python lab_set.py score.py --set experiments/mvp/pipeline/maps/dev.txt ...``.
``score_ladder.py`` is also accepted: its only runner change is provenance,
which this adapter restricts to the manifest-pinned development cohort.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LAB = ROOT / 'experiments/mvp/frontier/lab'
_WORKER_MARKER = 'STOWAGE_PIPELINE_DEV_ADAPTER'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _approved():
    """Read public manifest metadata only; never read sealed map bytes."""
    path = HERE / 'MANIFEST.json'
    if not path.exists():
        path = HERE / 'DEV-MANIFEST.json'
    manifest = json.loads(path.read_text())
    approved = {}
    for row in manifest['maps']:
        raw = Path(row['path'])
        path = (ROOT / raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            path.relative_to(HERE / 'dev')
        except ValueError:
            continue
        if row.get('split', 'dev') != 'dev' or path.suffix != '.json':
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in approved:
            raise ValueError(f'Duplicate dev manifest entry: {rel}')
        approved[rel] = row['sha256']
    if len(approved) != 32:
        raise ValueError(f'Expected 32 pinned development maps, got {len(approved)}')
    return approved


@contextmanager
def approved_dev():
    """Temporarily allow only hash-pinned dev paths through the actual loader.

    Neither catalog's validation allowlist nor its TRAIN allowlist is called.
    Every candidate is checked for resolved dev provenance before any byte read.
    """
    if str(LAB) not in sys.path:
        sys.path.insert(0, str(LAB))
    import catalog
    approved = _approved()
    previous = catalog.ensure_safe, catalog.assert_allowed
    old_marker = os.environ.get(_WORKER_MARKER)

    def ensure_safe(path):
        raw = Path(path)
        absolute = (ROOT / raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            rel = absolute.relative_to(ROOT).as_posix()
        except ValueError as error:
            raise ValueError(f'Input outside repository: {path}') from error
        if rel not in approved:
            raise ValueError(f'Input is outside the manifest-pinned dev cohort: {rel}')
        if _sha(absolute) != approved[rel]:
            raise ValueError(f'Development map hash mismatch: {rel}')
        return rel

    catalog.ensure_safe = catalog.assert_allowed = ensure_safe
    os.environ[_WORKER_MARKER] = str(HERE)
    try:
        yield catalog
    finally:
        catalog.ensure_safe, catalog.assert_allowed = previous
        if old_marker is None:
            os.environ.pop(_WORKER_MARKER, None)
        else:
            os.environ[_WORKER_MARKER] = old_marker


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or Path(args[0]).name not in ('score.py', 'score_ladder.py'):
        raise SystemExit('Usage: lab_set.py score.py|score_ladder.py --set dev.txt [runner options]')
    args.pop(0)
    # Both original entry points call score.main; the ladder changes only its
    # provenance allowlist. Avoid installing its broader historical approvals.
    with approved_dev():
        import score
        return score.main(args)


# The lab uses spawn with a fresh interpreter per map. Its imported score.one
# consults this worker's catalog, so install the same bounded context there.
if __name__ == '__mp_main__' and os.environ.get(_WORKER_MARKER) == str(HERE):
    _worker_context = approved_dev()
    _worker_context.__enter__()

if __name__ == '__main__':
    raise SystemExit(main())

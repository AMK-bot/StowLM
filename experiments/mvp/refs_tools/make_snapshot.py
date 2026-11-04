"""Pin local source bytes while other fleet executives edit the working tree."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
DEST = Path(__file__).resolve().parent / 'frozen-runtime'


def source_files():
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / 'src').rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def main():
    global DEST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEST)
    args = parser.parse_args()
    DEST = args.output.resolve()
    if not DEST.is_relative_to(Path(__file__).resolve().parent):
        raise ValueError('Snapshot output must remain inside refs_tools')
    if DEST.exists():
        raise RuntimeError(f'Snapshot already exists: {DEST}; do not replace campaign source')
    before = source_files()
    for relative in before:
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    after = source_files()
    copied = {relative: hashlib.sha256((DEST / relative).read_bytes()).hexdigest() for relative in before}
    assert before == after == copied, 'Sources changed during snapshot; snapshot is not admitted'
    # The numerical caches validate content identities themselves. The snapshot
    # uses the existing shared cache, preserving the unmodified production route.
    cache = DEST / 'experiments' / 'cache'
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.symlink_to(ROOT / 'experiments' / 'cache', target_is_directory=True)
    evidence_routes = {}
    for name in ('linear-model', 'stability', 'campaign-g1'):
        original = ROOT / 'experiments' / name
        if original.exists():
            (DEST / 'experiments' / name).symlink_to(original, target_is_directory=True)
            evidence_routes[name] = str(original)
    record = dict(created_at=datetime.now(timezone.utc).isoformat(), source_files=before,
                  cache_route=str(cache.resolve()), shared_params=str(ROOT / 'params'),
                  shared_evidence_routes=evidence_routes,
                  cold_linear_boundary_policy_note='Unverified: absolute primitive-source path checks can differ under source relocation; C2 remains off, warm-cache tiny acceptance is measured by full checker.',
                  purpose='Stable classical-reference runtime during concurrent source edits')
    (DEST / 'snapshot.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    print(f'Pinned {len(before)} source/data files to {DEST.relative_to(ROOT)}')


if __name__ == '__main__':
    main()

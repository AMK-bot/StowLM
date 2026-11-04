"""Read-only production snapshot for D132; no corpus or model files are read."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[4]
WORK = Path('/private/tmp/stowlm-d132-assembly-20261030')


def manifest(root: Path, subtree: str = 'src/stowage') -> dict:
    result = {}
    for path in sorted((root / subtree).rglob('*')):
        if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        if path.is_symlink():
            raise RuntimeError(f'Source symlink is not qualified: {path}')
        if path.is_file():
            result[str(path.relative_to(root))] = {
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'mode': stat.S_IMODE(path.stat().st_mode),
            }
    return result


def main():
    if WORK.exists():
        raise SystemExit(f'Refusing existing scratch destination: {WORK}')
    before = manifest(ROOT)
    baseline = WORK / 'baseline'
    baseline.mkdir(parents=True)
    for name in ('src', 'tests', 'params', 'app'):
        shutil.copytree(ROOT / name, baseline / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
    for name in ('pyproject.toml', 'uv.lock'):
        shutil.copy2(ROOT / name, baseline / name)
    # Public numerical input only. No test/evaluation bodies are traversed.
    for name in ('kcs.fix', 'MANIFEST'):
        target = baseline / 'data/raw/kcs' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'data/raw/kcs' / name, target)
    after = manifest(ROOT)
    copied = manifest(baseline)
    if before != after or copied != before:
        raise RuntimeError('Production changed during baseline copy; discard scratch and retry explicitly')
    shutil.copytree(baseline, WORK / 'candidate')
    receipt = {'created_utc': datetime.now(timezone.utc).isoformat(),
               'baseline': str(baseline), 'candidate': str(WORK / 'candidate'),
               'production_root': str(ROOT), 'src_stowage': before,
               'data_scope': ['data/raw/kcs/kcs.fix', 'data/raw/kcs/MANIFEST'],
               'no_test_inputs_read': True}
    output = ROOT / 'experiments/mvp/batch-d132/assembly/baseline-manifest.json'
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'baseline': str(baseline), 'candidate': str(WORK / 'candidate'),
                      'source_files': len(before), 'manifest': str(output)}))


if __name__ == '__main__':
    main()

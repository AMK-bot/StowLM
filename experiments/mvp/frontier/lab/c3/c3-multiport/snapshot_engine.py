"""Freeze the in-progress engine under an isolated package name.

Run once before measuring. The manifest records both original and rewritten
bytes; no source engine or native solver file is changed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / 'src/stowage').is_dir())
SOURCE = ROOT / 'experiments/mvp/frontier/engine'
DEST = HERE / 'frozen_engine'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    originals = {}
    for path in sorted(SOURCE.rglob('*.py')):
        rel = path.relative_to(SOURCE)
        if any(part in {'bench', 'results', '__pycache__'} for part in rel.parts):
            continue
        if path.name.startswith('test_') or path.name.endswith('_test.py'):
            continue
        if path.name in {'conftest.py', 'provenance.py'}:
            continue
        originals[path] = path.read_bytes()
    native = ROOT / 'experiments/mvp/frontier/planner.py'
    originals[native] = native.read_bytes()
    rows = []
    for path, data in originals.items():
        transformations = []
        rel = Path('frontier09.py') if path == native else path.relative_to(SOURCE)
        content = data.decode()
        if path != native:
            changed = re.sub(r'(?m)^(\s*from )engine(?=\b)', r'\1frozen_engine', content)
            if changed != content:
                transformations.append('engine imports renamed to isolated frozen_engine namespace')
                content = changed
        if rel == Path('planner.py'):
            content = content.replace('ROOT = HERE.parents[3]',
                                      'from ._paths import ROOT, FRONTIER')
            content = content.replace("(ROOT / 'src', HERE.parent, HERE.parent / 'lab')",
                                      "(ROOT / 'src', FRONTIER, FRONTIER / 'lab')")
            transformations.append('repository bootstrap uses ancestor discovery instead of original depth')
        if rel == Path('frozen.py'):
            content = content.replace("Path(__file__).resolve().parent.parent / 'planner.py'",
                                      "Path(__file__).resolve().parent / 'frontier09.py'")
            content = content.replace("'_frontier_frozen_planner09'", "__package__ + '._planner09'")
            transformations.append('original Frontier planner loaded from frozen local copy under unique module name')
        target = DEST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        rows.append(dict(source=str(path.relative_to(ROOT)), copied=str(target.relative_to(ROOT)),
                         source_sha256=sha(data), copied_sha256=sha(target.read_bytes()),
                         transformations=transformations))
    for path, data in originals.items():
        if path.read_bytes() != data:
            raise RuntimeError(f'Source changed during snapshot: {path}; rerun before measurement')
    generated = DEST / '_paths.py'
    generated.write_text('''"""Locate unchanged native dependencies without importing the live engine."""
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'src/stowage').is_dir())
FRONTIER = ROOT / 'experiments/mvp/frontier'
''')
    native_paths = sorted((ROOT / 'src/stowage').rglob('*.py'))
    native_paths += sorted((ROOT / 'params').rglob('*'))
    native_paths += sorted((ROOT / 'experiments/mvp/frontier').glob('*.py'))
    dependencies = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in native_paths if p.is_file()}
    manifest = dict(description='Immutable engine copy for c3-multiport; no live engine imports',
                    files=rows, generated={str(generated.relative_to(ROOT)): sha(generated.read_bytes())},
                    unchanged_native_dependencies=dependencies)
    manifest['snapshot_sha256'] = sha(json.dumps(rows, sort_keys=True).encode())
    (HERE / 'engine-snapshot.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(files=len(rows), snapshot_sha256=manifest['snapshot_sha256'])))


if __name__ == '__main__':
    main()

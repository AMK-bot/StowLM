"""Verify copied engine and shared native source bindings after measurement."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((HERE / 'engine-snapshot.json').read_text())
    changes = []
    for row in manifest['files']:
        path = ROOT / row['copied']
        if digest(path) != row['copied_sha256']:
            changes.append(dict(path=row['copied'], kind='engine copy changed'))
    for name, expected in manifest['unchanged_native_dependencies'].items():
        if digest(ROOT / name) != expected:
            changes.append(dict(path=name, kind='shared native dependency changed'))
    scripts = sorted(HERE.rglob('*.py'))
    for path in scripts:
        ast.parse(path.read_text(), filename=str(path))
    output = dict(engine_snapshot_sha256=manifest['snapshot_sha256'], changes=changes,
                  copied_files=len(manifest['files']),
                  native_files=len(manifest['unchanged_native_dependencies']),
                  python_files_parsed=len(scripts), passed=not changes,
                  experiment_sources={str(p.relative_to(HERE)): digest(p) for p in scripts})
    (HERE / 'source-audit.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({k: v for k, v in output.items() if k != 'experiment_sources'}))
    assert not changes


if __name__ == '__main__':
    main()

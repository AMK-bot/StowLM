"""Copy all checker dependency code/data bytes for a reproducible shared-tree run.

Public raw data and disposable qualified tables stay shared; existing production
checksums and cache dependency verification still validate them on access.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
args = parser.parse_args()
output = args.output.resolve()
if output.exists():
    raise SystemExit('Refusing to change an existing source epoch')
paths = sorted({*(path for path in (ROOT/'src/stowage').rglob('*')
                  if path.is_file() and path.suffix in {'.py', '.toml', '.json'}),
                *(path for path in (ROOT/'params').rglob('*') if path.is_file()),
                *(path for path in (ROOT/'data/fixtures').rglob('*') if path.is_file()),
                ROOT/'pyproject.toml'})
payloads = {str(path.relative_to(ROOT)): path.read_bytes() for path in paths}
for relative, payload in payloads.items():
    destination = output/relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
for relative, target in [('data/raw', ROOT/'data/raw'),
                         ('experiments/cache/stability', ROOT/'experiments/cache/stability'),
                         ('experiments/cache/vessels', ROOT/'experiments/cache/vessels')]:
    destination = output/relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(target, target_is_directory=True)
changed = [relative for relative, payload in payloads.items()
           if (ROOT/relative).read_bytes() != payload]
if changed:
    raise SystemExit(f'Concurrent source changes while copying: {changed}')
manifest = dict(original_root=str(ROOT), files={relative: hashlib.sha256(payload).hexdigest()
                for relative, payload in payloads.items()},
                note='Exact code/parameter/bundled-data bytes; raw public data and verified qualified caches shared.')
(output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print(json.dumps(dict(output=str(output), files=len(payloads), verified=True)))

"""Emergency rollback for an observed old/new legal-set or plan difference.

Only invoke after detecting a behavioral mismatch. Does not touch git state.
"""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / 'experiments/mvp/maskspeed'
verification = json.loads((HERE / 'out/baseline2-verification.json').read_text())
for row in verification['files']:
    source = ROOT / row['pre_change_source']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == row['pre_change_sha256']
# Undo only this task's optional D115 additions if its application was recorded.
marker = HERE / 'out/d1152-applied.json'
if marker.exists():
    applied = json.loads(marker.read_text())
    for path, backup in applied['preexisting_backups'].items():
        shutil.copyfile(ROOT / backup, ROOT / path)
    for path in applied['created_files']:
        (ROOT / path).unlink(missing_ok=True)
# Restore pre-speedup bytes last, including the securing file D115 also edits.
for row in verification['files']:
    shutil.copyfile(ROOT / row['pre_change_source'], ROOT / row['production'])
restored = {}
for row in verification['files']:
    actual = hashlib.sha256((ROOT / row['production']).read_bytes()).hexdigest()
    assert actual == row['pre_change_sha256'], row['production']
    restored[row['production']] = actual
(HERE / 'out/revert2-verification.json').write_text(json.dumps(restored, indent=2) + '\n')
print(json.dumps(restored, indent=2))

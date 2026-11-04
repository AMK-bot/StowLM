"""Read-only source/input pins and git apply --check; never apply the patch."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
record = json.loads((HERE / "source-pins.json").read_text())
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert all(digest(ROOT / path) == pin["before_sha256"]
           for path, pin in record["source_files"].items()), "live target source changed"
assert all(digest(ROOT / path) == pin
           for path, pin in record["input_files"].items()), "preparation inputs changed"
patch = HERE.parent / "manual-c2.patch"
assert digest(patch) == record["patch_sha256"], "patch changed"
result = subprocess.run(["git", "apply", "--check", str(patch.relative_to(ROOT))],
                        cwd=ROOT, text=True, capture_output=True)
print(json.dumps(dict(pins_match=True, patch_sha256=record["patch_sha256"],
    apply_check_returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)))
raise SystemExit(result.returncode)

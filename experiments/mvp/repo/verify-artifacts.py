"""Offline, one-core consistency checks for the publication proposal only."""
from pathlib import Path
import hashlib
import re

folder = Path(__file__).resolve().parent
required = (
    "REPO-PLAN.md", "LICENSE.draft", "NOTICE.draft", "gitignore.additions",
    "commit-list.txt", "licence-audit.md", "sensitive-audit.md", "inventory-audit.md",
)
for name in required:
    path = folder / name
    assert path.is_file() and path.stat().st_size, f"Missing/empty: {name}"
plan = (folder / "REPO-PLAN.md").read_text()
assert len(re.findall(r"^## [1-4]\. ", plan, re.M)) == 4
for target in re.findall(r"\]\(([^)]+)\)", plan):
    if "://" not in target:
        assert (folder / target.split("#")[0]).is_file(), target
licence = (folder / "LICENSE.draft").read_text()
assert "Apache License" in licence and "Version 2.0" in licence
assert "END OF TERMS AND CONDITIONS" in licence
assert hashlib.sha256((folder / "LICENSE.draft").read_bytes()).hexdigest() == "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
entries = []
for row in (folder / "commit-list.txt").read_text().splitlines():
    match = re.fullmatch(r"(\d+)\t(.+)", row)
    if match:
        size, name = match.groups()
        path = folder.parents[2] / name
        assert path.is_file(), f"Missing proposed path: {name}"
        assert not name.startswith(("models/", "data/raw/", "experiments/mvp/repo/", ".claude/")), name
        assert not any("pytest" in part or part in {".venv", "__pycache__", "node_modules"} for part in Path(name).parts), name
        assert not name.endswith((".safetensors", ".gguf", "/provider.json")), name
        entries.append((int(size), name))
assert entries, "No explicit commit paths"
assert len(entries) == len({name for _, name in entries}), "Duplicate commit paths"
print(f"PASS: {len(required)} nonempty artifacts; four plan sections; local links; Apache licence text")
print(f"PASS: {len(entries)} existing unique proposed/conditional paths; excluded raw/weights/caches/provider envelopes")

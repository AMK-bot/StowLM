"""Check hull-download provenance, payload hashes, and inert data formats.

uv run --no-project --python 3.12 experiments/data-fetch/verify_hull_sources.py
This checks downloaded artifacts, not correctness of a future hull implementation.
"""
import ast
import csv
import gzip
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
count = 0
for source in ("kcs", "dtc", "series60"):
    folder = ROOT / "data/raw" / source
    if not folder.exists():
        assert source == "series60", f"Required source absent: {source}"
        print("series60: optional source skipped")
        continue
    assert (folder / "NOTES.md").stat().st_size > 100
    rows = json.loads((folder / "MANIFEST").read_text())
    if isinstance(rows, dict):
        rows = rows["files"]
    names = set()
    checks = []
    for row in rows:
        name = row["file"]
        rel = Path(name)
        assert not rel.is_absolute() and ".." not in rel.parts
        assert name not in names
        names.add(name)
        path = folder / rel
        assert path.stat().st_size > 0
        assert row["url"].startswith(("https://", "http://"))
        assert row.get("licence") or row.get("license")
        assert row.get("retrieved") or row.get("date")
        digest = row["sha256"]
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        checks.append(f"{digest}  {name}\n")
        assert path.suffix not in {".py", ".sh", ".js", ".exe"}, path
        if path.suffix == ".pdf":
            assert path.read_bytes().startswith(b"%PDF-")
            assert path.with_suffix(".txt").stat().st_size > 100
        elif path.suffix == ".gz":
            with gzip.open(path, "rb") as stream:
                assert stream.read(100)
        elif path.suffix == ".csv":
            with path.open(newline="") as stream:
                table = list(csv.reader(stream))
            assert len(table) > 2 and len(table[0]) > 1, path
    actual = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}
    assert actual - {"MANIFEST", "NOTES.md", "SHA256SUMS"} == names, (source, actual - names, names - actual)
    checked = subprocess.run(["shasum", "-a", "256", "-c", "-"], input="".join(checks),
                             text=True, capture_output=True, cwd=folder)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    count += len(rows)
    print(f"{source}: {len(rows)} payloads passed shasum -a 256 -c - and format checks")
for path in (ROOT / "experiments/data-fetch").glob("*.py"):
    ast.parse(path.read_text(), filename=str(path))
print(f"PASS: {count} hull-source payloads; provenance, hashes, inert formats, and script syntax")

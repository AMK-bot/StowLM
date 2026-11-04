"""Verify this task's source manifests and every payload with system shasum.

Run with uv run --no-project --python 3.12 experiments/data-fetch/verify_sources.py.
Only this batch's eight folders are inspected; unrelated raw downloads are ignored.
"""
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("larsen-pacino", "ecfr-172-101", "reference-ships", "imdg-42-24", "is-code", "iacs", "irs", "iso-668")
# The reference particulars in docs/data-sources.md §6, including the Annabella
# accident report underlying its stack limits. Keep this
# independent of MANIFEST so dropping a required download and its row fails.
REQUIRED_REFERENCE_FILES = {
    "arctic-trader.pdf", "dina-trader.pdf", "vega-tau.pdf",
    "bangkokmax-1700.html", "keh-bangkok-max.pdf", "bardu.pdf", "annabella.pdf",
}
REQUIRED_ISO_FILES = {"iso-668-2020-amd-1-2022.pdf", "iso-668-2020-amd-1-2022.txt"}

count = 0
for source in SOURCES:
    folder = ROOT / "data/raw" / source
    assert (folder / "NOTES.md").stat().st_size > 100, source
    rows = json.loads((folder / "MANIFEST").read_text())
    if isinstance(rows, dict):
        rows = rows["files"]
    names = set()
    checks = []
    for row in rows:
        name = row["file"]
        assert name not in names, (source, name)
        names.add(name)
        assert row["url"].startswith("https://"), (source, name)
        assert row.get("licence") or row.get("license"), (source, name)
        assert row.get("retrieved") or row.get("date") or row.get("retrieved_utc"), (source, name)
        digest = row["sha256"]
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef"), (source, name)
        path = folder / name
        assert path.is_relative_to(folder) and path.stat().st_size > 0, (source, name)
        checks.append(f"{digest}  {name}\n")
        if path.suffix == ".pdf":
            assert path.read_bytes().startswith(b"%PDF-"), (source, name)
            assert path.with_suffix(".txt").stat().st_size > 100, (source, name)
    if source == "reference-ships":
        missing = REQUIRED_REFERENCE_FILES - names
        assert not missing, (source, "missing required reference sources", sorted(missing))
    if source == "iso-668":
        missing = REQUIRED_ISO_FILES - names
        assert not missing, (source, "missing required ISO preview payloads", sorted(missing))
    actual = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}
    assert actual - {"MANIFEST", "NOTES.md", "SHA256SUMS"} == names, (source, actual - names, names - actual)
    result = subprocess.run(["shasum", "-a", "256", "-c", "-"], input="".join(checks), text=True,
                            cwd=folder, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    count += len(rows)
    print(f"{source}: {len(rows)} payloads verified with shasum -a 256 -c -")
print(f"PASS: {count} payloads in {len(SOURCES)} source folders; PDFs have text; no unmanifested payloads")

benchmark = ROOT / "data/raw/larsen-pacino"
assert len(list((benchmark / "vessel_data").glob("*.txt"))) == 3
loadlists = list((benchmark / "container_instances").rglob("*.txt"))
assert len(loadlists) == 27
containers = sum(int(p.read_text().splitlines()[1].split()[1]) for p in loadlists)
assert containers == 123431, containers
hmt = ET.parse(ROOT / "data/raw/ecfr-172-101/172.101.xml").getroot()
assert hmt.attrib["N"] == "172.101"
assert len(list(hmt.iter("TR"))) > 1000
hmt_text = (ROOT / "data/raw/ecfr-172-101/172.101.txt").read_text()
assert "UN1203" in hmt_text and "UN3480" in hmt_text
print("PASS: 3 vessels, 27 loadlists, 123431 containers; HMT XML/table sanity")

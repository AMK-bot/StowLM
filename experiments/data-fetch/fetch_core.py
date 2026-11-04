"""Fetch public benchmark, HMT and ship sheets; never execute downloaded content.

Run: uv run --no-project --python 3.12 --with pypdf experiments/data-fetch/fetch_core.py GROUP
GROUP is benchmark, ecfr, or references. Each is a small single-core download job.
"""
import hashlib
import json
from datetime import date
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]


def fetch(folder, name, url, licence):
    return _fetch_at(ROOT, folder, name, url, licence)


def _fetch_at(root, folder, name, url, licence):
    dest = root / "data/raw" / folder / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["curl", "--compressed", "-fLsS", "--max-time", "45", "--retry", "1", url, "-o", str(dest)], check=True)
    row = dict(file=name, url=url, retrieved=date.today().isoformat(), licence=licence,
               sha256=hashlib.sha256(dest.read_bytes()).hexdigest())
    print(name, dest.stat().st_size, flush=True)
    return row


def manifest(folder, rows):
    (ROOT / "data/raw" / folder / "MANIFEST").write_text(json.dumps(rows, indent=2) + "\n")


def extract(folder, rows):
    _extract_at(ROOT, folder, rows)


def _extract_at(root, folder, rows):
    for row in list(rows):
        path = root / "data/raw" / folder / row["file"]
        if path.suffix != ".pdf":
            continue
        reader = PdfReader(path)
        target = path.with_suffix(".txt")
        target.write_text("\n".join(f"\n--- PDF page {i} ---\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages, 1)))
        rows.append(dict(file=target.name, url=row["url"], retrieved=date.today().isoformat(), licence=row["licence"],
                         derived_from=path.name, extraction="pypdf, page numbered", sha256=hashlib.sha256(target.read_bytes()).hexdigest()))


def benchmark():
    folder = "larsen-pacino"
    api = "https://api.github.com/repos/maritimelab/Stowage-Planning-Benckmark/git/trees/master?recursive=1"
    rows = [fetch(folder, "github-tree.json", api, "GitHub API metadata; benchmark CC0-1.0")]
    tree = json.loads((ROOT / "data/raw" / folder / "github-tree.json").read_text())
    sha = tree["sha"]
    for item in tree["tree"]:
        if item["type"] != "blob":
            continue
        name = item["path"]
        # Download data and licensing only; do not import any repository code.
        if name in ("README.md", "LICENSE") or name.endswith(".txt"):
            url = f"https://raw.githubusercontent.com/maritimelab/Stowage-Planning-Benckmark/{sha}/{name}"
            rows.append(fetch(folder, name, url, "CC0-1.0; see LICENSE"))
    manifest(folder, rows)


def ecfr():
    folder = "ecfr-172-101"
    rows = [fetch(folder, "titles.json", "https://www.ecfr.gov/api/versioner/v1/titles.json", "US federal metadata; public domain (17 USC 105)")]
    titles = json.loads((ROOT / "data/raw" / folder / "titles.json").read_text())
    title = next(t for t in titles["titles"] if t["number"] == 49)
    asof = title["up_to_date_as_of"]
    url = f"https://www.ecfr.gov/api/versioner/v1/full/{asof}/title-49.xml?part=172&section=172.101"
    rows.append(fetch(folder, "172.101.xml", url, "US federal regulation; public domain (17 USC 105)"))
    path = ROOT / "data/raw" / folder / "172.101.xml"
    root = ET.fromstring(path.read_bytes())
    assert any("172.101" in e.attrib.values() for e in root.iter())
    target = path.with_suffix(".txt")
    target.write_text("\n".join(t.strip() for t in root.itertext() if t.strip()))
    rows.append(dict(file=target.name, url=url, retrieved=date.today().isoformat(), licence=rows[0]["licence"],
                     derived_from=path.name, sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    manifest(folder, rows)


REFERENCE_URLS = {
        "arctic-trader.pdf": "https://mmred.no/images/filer/Arctic-Trader-Brochure-Rev-0-170226.pdf",
        "dina-trader.pdf": "https://mmred.no/images/Dina_Trader_Brochure-2022.pdf",
        "vega-tau.pdf": "https://www.vega-reederei.de/pdf/tc_VEGA_TAU.pdf",
        "bangkokmax-1700.html": "https://www.eshiptrading.com/SS94778.html",
        "keh-bangkok-max.pdf": "https://knudehansen.com/wp-content/uploads/2017/10/Product_Sheet_2000_TEU_Bangkok_Max_Container_Feeder_Vessel_11019.pdf",
        "bardu.pdf": "https://na.swireshipping.com/documents/pdfs/MV%20Bardu%20Specifications.pdf?rvn=27",
        "annabella.pdf": "https://assets.publishing.service.gov.uk/media/547c7032e5274a429000007d/AnnabellaReport.pdf",
}

REFERENCE_LICENCES = {
    "annabella.pdf": "MAIB Report 21/2007, Annabella; Crown copyright, Open Government Licence v3.0 (OGLv3); contains public sector information licensed under the Open Government Licence v3.0.",
}


def references():
    folder = "reference-ships"
    raw = ROOT / "data/raw"
    raw.mkdir(parents=True, exist_ok=True)
    # All seven sources are required. Failed acquisition or extraction leaves the
    # published batch untouched; the manifest is replaced only after completion.
    with tempfile.TemporaryDirectory(prefix=".reference-ships-", dir=raw) as temporary:
        staging_root = Path(temporary)
        rows = [
            _fetch_at(staging_root, folder, name, url, REFERENCE_LICENCES.get(name, "No reuse licence stated; copyrighted owner/broker/designer sheet. Local reference only; use numerical facts, do not redistribute."))
            for name, url in REFERENCE_URLS.items()
        ]
        _extract_at(staging_root, folder, rows)
        staged = staging_root / "data/raw" / folder
        staged_manifest = staged / "MANIFEST"
        staged_manifest.write_text(json.dumps(rows, indent=2) + "\n")
        destination = raw / folder
        destination.mkdir(parents=True, exist_ok=True)
        for row in rows:
            (staged / row["file"]).replace(destination / row["file"])
        staged_manifest.replace(destination / "MANIFEST")


if __name__ == "__main__":
    {"benchmark": benchmark, "ecfr": ecfr, "references": references}[sys.argv[1]]()

"""Re-extract plain text and hash already downloaded DTC source payloads.

uv run --no-project --python 3.12 --with pypdf python experiments/data-fetch/dtc_prepare.py
Run dtc_geometry_audit.py first if the geometry audit needs rebuilding.
"""
from pathlib import Path
import hashlib
import json
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/dtc"
COMMIT = "9e356d8a16b975a4c71f3877981f8e34b83661be"
BASE = f"https://raw.githubusercontent.com/OpenFOAM/OpenFOAM-dev/{COMMIT}/"
RETRIEVED = "2026-10-27"
GPL = "GPL-3.0-or-later (OpenFOAM repository COPYING; geometry file has no separate licence header)"
PAPER = "unstated; public author/institution PDF, no open reuse licence identified"
entries = []


def add(filename, url, licence, derived_from=None):
    entries.append({
        "file": filename, "url": url, "retrieved": RETRIEVED,
        "licence": licence,
        "sha256": hashlib.sha256((RAW / filename).read_bytes()).hexdigest(),
        "derived_from": derived_from,
    })


for filename, remote in [
    ("DTC-scaled.stl.gz", "tutorials/resources/geometry/DTC-scaled.stl.gz"),
    ("OpenFOAM-COPYING.txt", "COPYING"),
    ("OpenFOAM-dynamicMeshDict.txt", "tutorials/incompressibleVoF/DTCHullMoving/constant/dynamicMeshDict"),
    ("OpenFOAM-functions.txt", "tutorials/incompressibleVoF/DTCHull/system/functions"),
    ("OpenFOAM-README.txt", "tutorials/incompressibleVoF/DTCHull/README"),
    ("OpenFOAM-hRef.txt", "tutorials/incompressibleVoF/DTCHull/constant/hRef"),
]:
    add(filename, BASE + remote, GPL)

for name, url in [
    ("el-moctar-shigunov-zorn-2012", "https://www.uni-due.de/imperia/md/content/ist/dtc_str_vol50no3.pdf"),
    ("piehl-dissertation-coordinate-reference", "https://duepublico2.uni-due.de/servlets/MCRFileNodeServlet/duepublico_derivate_00043372/Piehl_Diss.pdf"),
]:
    reader = PdfReader(RAW / (name + ".pdf"))
    extracted = "\n".join(f"\n--- PDF page {i+1} ---\n" + (page.extract_text() or "") for i, page in enumerate(reader.pages))
    (RAW / (name + ".txt")).write_text(extracted.replace("\x00", ""))
    add(name + ".pdf", url, PAPER)
    add(name + ".txt", url, PAPER, name + ".pdf")

add("geometry_audit.json", BASE + "tutorials/resources/geometry/DTC-scaled.stl.gz", GPL, "DTC-scaled.stl.gz; experiments/data-fetch/dtc_geometry_audit.py")
(RAW / "MANIFEST").write_text(json.dumps(entries, indent=2) + "\n")
print(f"Manifested {len(entries)} DTC payloads.")

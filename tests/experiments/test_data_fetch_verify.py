"""Offline regression cases for the public-source verifier."""

import hashlib
import json
from pathlib import Path
import runpy
import shutil

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "experiments/data-fetch/verify_sources.py"
REFERENCE_FILES = (
    "arctic-trader.pdf", "dina-trader.pdf", "vega-tau.pdf",
    "bangkokmax-1700.html", "keh-bangkok-max.pdf", "bardu.pdf", "annabella.pdf",
)
SOURCES = (
    "larsen-pacino", "ecfr-172-101", "reference-ships", "imdg-42-24",
    "is-code", "iacs", "irs", "iso-668",
)
ISO_PDF = "iso-668-2020-amd-1-2022.pdf"
ISO_TEXT = "iso-668-2020-amd-1-2022.txt"


@pytest.fixture
def source_tree(tmp_path):
    script = tmp_path / "experiments/data-fetch/verify_sources.py"
    script.parent.mkdir(parents=True)
    script.write_text(SCRIPT.read_text())
    for source in SOURCES:
        folder = tmp_path / "data/raw" / source
        folder.mkdir(parents=True)
        (folder / "NOTES.md").write_text("Source documentation. " * 10)
        payloads = {"payload.txt": "Public reference. " * 10}
        if source == "reference-ships":
            payloads = {}
            for name in REFERENCE_FILES:
                payloads[name] = "%PDF-fixture" if name.endswith(".pdf") else "HTML fixture"
                if name.endswith(".pdf"):
                    payloads[str(Path(name).with_suffix(".txt"))] = "Extracted text. " * 10
        elif source == "larsen-pacino":
            payloads = {f"vessel_data/{n}.txt": "vessel" for n in range(3)}
            payloads.update({
                f"container_instances/{n}.txt": f"header\ncontainers {123405 if n == 0 else 1}\n"
                for n in range(27)
            })
        elif source == "ecfr-172-101":
            payloads = {
                "172.101.xml": '<TABLE N="172.101">' + "<TR/>" * 1001 + "</TABLE>",
                "172.101.txt": "UN1203 UN3480",
            }
        elif source == "iso-668":
            payloads = {ISO_PDF: "%PDF-fixture", ISO_TEXT: "Extracted text. " * 10}
        rows = []
        for name, content in payloads.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            rows.append({
                "file": name, "url": "https://example.org/public", "retrieved": "2026-10-27",
                "licence": "fixture", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        (folder / "MANIFEST").write_text(json.dumps(rows))
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "compute.md").write_text("2026-10-27")
    (docs / "hackathon.md").write_text(
        "2026-10-27\n## Submission checklist\n" +
        "open — user to confirm from the participant guide\n" * 3
    )
    return tmp_path, script


@pytest.mark.parametrize("missing", REFERENCE_FILES)
def test_required_reference_cannot_be_removed_from_manifest_and_disk(source_tree, missing):
    root, script = source_tree
    folder = root / "data/raw/reference-ships"
    removed = {missing}
    if missing.endswith(".pdf"):
        removed.add(str(Path(missing).with_suffix(".txt")))
    manifest = folder / "MANIFEST"
    rows = json.loads(manifest.read_text())
    manifest.write_text(json.dumps([row for row in rows if row["file"] not in removed]))
    for name in removed:
        (folder / name).unlink()
    with pytest.raises(AssertionError, match=missing):
        runpy.run_path(str(script), run_name="__main__")


def test_data_verification_is_independent_of_admin_documents(source_tree):
    root, script = source_tree
    for path in (root / "docs").iterdir():
        path.unlink()
    runpy.run_path(str(script), run_name="__main__")


def test_complete_public_source_tree_passes(source_tree):
    _, script = source_tree
    runpy.run_path(str(script), run_name="__main__")


def test_iso_source_folder_is_required(source_tree):
    root, script = source_tree
    shutil.rmtree(root / "data/raw/iso-668")
    with pytest.raises((AssertionError, FileNotFoundError), match="iso-668"):
        runpy.run_path(str(script), run_name="__main__")


@pytest.mark.parametrize("removed", [(ISO_PDF, ISO_TEXT), (ISO_PDF,), (ISO_TEXT,)])
def test_iso_payload_inventory_is_independent_of_manifest(source_tree, removed):
    root, script = source_tree
    folder = root / "data/raw/iso-668"
    manifest = folder / "MANIFEST"
    rows = json.loads(manifest.read_text())
    manifest.write_text(json.dumps([row for row in rows if row["file"] not in removed]))
    for name in removed:
        (folder / name).unlink()
    with pytest.raises((AssertionError, FileNotFoundError), match="iso-668"):
        runpy.run_path(str(script), run_name="__main__")

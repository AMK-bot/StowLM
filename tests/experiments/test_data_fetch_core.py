"""Offline acquisition regressions: curl and PDF parsing never use the network."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def core(monkeypatch, tmp_path):
    # PDF extraction is isolated from the optional fetch-script dependency.
    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=lambda path: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "public sheet text")])))
    path = Path(__file__).resolve().parents[2] / "experiments/data-fetch/fetch_core.py"
    spec = importlib.util.spec_from_file_location("data_fetch_core", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "date", SimpleNamespace(today=lambda: SimpleNamespace(isoformat=lambda: "2031-04-09")), raising=False)
    return module


def fake_curl(args, **kwargs):
    Path(args[-1]).write_bytes(b"%PDF-offline fixture")


def test_fetch_records_actual_retrieval_date(core, monkeypatch):
    monkeypatch.setattr(core.subprocess, "run", fake_curl)
    row = core.fetch("example", "sheet.pdf", "https://example.org/sheet.pdf", "public")
    assert row["retrieved"] == "2031-04-09"


def test_pdf_extraction_records_actual_generation_date(core, monkeypatch):
    monkeypatch.setattr(core.subprocess, "run", fake_curl)
    rows = [core.fetch("example", "sheet.pdf", "https://example.org/sheet.pdf", "public")]
    rows[0]["retrieved"] = "2001-01-01"
    core.extract("example", rows)
    assert rows[-1]["retrieved"] == "2031-04-09"
    assert rows[-1]["derived_from"] == "sheet.pdf"


def test_ecfr_extraction_records_actual_generation_date(core, monkeypatch):
    def curl(args, **kwargs):
        path = Path(args[-1])
        path.write_text(json.dumps({"titles": [{"number": 49, "up_to_date_as_of": "2031-04-08"}]}) if path.suffix == ".json" else '<DIV N="172.101">UN1203</DIV>')
    monkeypatch.setattr(core.subprocess, "run", curl)
    core.ecfr()
    rows = json.loads((core.ROOT / "data/raw/ecfr-172-101/MANIFEST").read_text())
    assert {row["retrieved"] for row in rows} == {"2031-04-09"}


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failed_source", ["Bardu", "Annabella"])
def test_reference_failure_aborts_without_publishing_partial_batch(core, monkeypatch, existing, failed_source):
    folder = core.ROOT / "data/raw/reference-ships"
    folder.mkdir(parents=True)
    if existing:
        (folder / "MANIFEST").write_text("old manifest")
        (folder / "arctic-trader.pdf").write_bytes(b"old payload")
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    def curl(args, **kwargs):
        if failed_source in args[-3]:
            raise subprocess.CalledProcessError(22, args)
        fake_curl(args, **kwargs)
    monkeypatch.setattr(core.subprocess, "run", curl)
    with pytest.raises(subprocess.CalledProcessError):
        core.references()
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_reference_extraction_failure_keeps_previous_batch(core, monkeypatch):
    folder = core.ROOT / "data/raw/reference-ships"
    folder.mkdir(parents=True)
    (folder / "MANIFEST").write_text("old manifest")
    monkeypatch.setattr(core.subprocess, "run", fake_curl)
    def bad_pdf(path):
        raise ValueError("invalid downloaded PDF")
    monkeypatch.setattr(core, "PdfReader", bad_pdf)
    with pytest.raises(ValueError):
        core.references()
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == {"MANIFEST": b"old manifest"}


def test_reference_success_publishes_complete_manifest(core, monkeypatch):
    monkeypatch.setattr(core.subprocess, "run", fake_curl)
    core.references()
    folder = core.ROOT / "data/raw/reference-ships"
    rows = json.loads((folder / "MANIFEST").read_text())
    expected = {"arctic-trader.pdf", "dina-trader.pdf", "vega-tau.pdf", "bangkokmax-1700.html", "keh-bangkok-max.pdf", "bardu.pdf", "annabella.pdf"}
    assert {row["file"] for row in rows if "derived_from" not in row} == expected
    assert {row["file"] for row in rows} == {p.name for p in folder.iterdir()} - {"MANIFEST"}


def test_annabella_has_maib_attribution_and_ogl_licence(core, monkeypatch):
    monkeypatch.setattr(core.subprocess, "run", fake_curl)
    core.references()
    rows = json.loads((core.ROOT / "data/raw/reference-ships/MANIFEST").read_text())
    annabella = [row for row in rows if row["file"] in {"annabella.pdf", "annabella.txt"}]
    assert len(annabella) == 2
    for row in annabella:
        assert row["url"] == "https://assets.publishing.service.gov.uk/media/547c7032e5274a429000007d/AnnabellaReport.pdf"
        assert "MAIB" in row["licence"] and "21/2007" in row["licence"]
        assert "OGLv3" in row["licence"]

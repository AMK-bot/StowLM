"""Evidence must join actual stack contents, and a public run cannot silently skip."""
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _verifier():
    from stowage.bench.evidence import verify_zhu_source_identity
    return verify_zhu_source_identity


def _archives(tmp_path, *, changed=False):
    local, source = tmp_path / "local.zip", tmp_path / "source.zip"
    with ZipFile(local, "w") as z:
        z.writestr("3-3-6/1.txt", "3 6\n2 1 6\n1 4\n3 3 2 5\n")
    with ZipFile(source, "w") as z:
        # Same name/dimensions, different stack contents must not establish identity.
        row = "2 6 1" if changed else "2 1 6"
        z.writestr("data3-3-6-1.txt", f"3 3 6\n{row}\n1 4\n3 3 2 5\n")
    return local, source


def test_source_identity_compares_canonical_stacks(tmp_path):
    local, source = _archives(tmp_path)
    result = _verifier()(local, source)
    assert result["compared_instances"] == 1
    assert result["mismatches"] == []
    assert len(result["canonical_sha256"]) == 64


def test_source_identity_rejects_same_name_different_contents(tmp_path):
    local, source = _archives(tmp_path, changed=True)
    with pytest.raises(ValueError, match="stack contents differ"):
        _verifier()(local, source)


@pytest.mark.parametrize("option", ["--archive", "--source-archive"])
def test_evidence_workflow_requires_public_archives(tmp_path, monkeypatch, capsys, option):
    spec = importlib.util.spec_from_file_location("benchmark_evidence_run", ROOT / "experiments/benchmarks/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", ["run.py", option, str(tmp_path / "absent.zip"),
        "--limit", "1", "--output", str(tmp_path / "results.json")])
    with pytest.raises(SystemExit, match="2"):
        module.main()
    assert "required public evidence unavailable" in capsys.readouterr().err
    assert not (tmp_path / "results.json").exists()


def test_evidence_workflow_reports_selected_public_checks(tmp_path, monkeypatch):
    for path in ("data/raw/move-benchmarks-tanaka/Zhu_dataset.zip",
                 "data/raw/move-benchmarks-bacci/group6.zip"):
        if not (ROOT / path).is_file():
            pytest.skip("portable quick suite: public archives unavailable; evidence workflow requires them")
    spec = importlib.util.spec_from_file_location("benchmark_evidence_run", ROOT / "experiments/benchmarks/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", ["run.py", "--limit", "1", "--output", str(tmp_path / "results.json")])
    module.main()
    result = json.loads((tmp_path / "results.json").read_text())
    assert result["source_identity"]["compared_instances"] == 12500
    assert result["public_checks"] == {
        "archive_availability": "passed", "source_identity": "passed",
        "published_solution_replay": "passed", "bound_comparison": "passed",
    }
    assert result["scan_complete"] is False
    assert result["published_solution_replay"]["relocations"] == 3
    assert result["published_solution_replay"]["gate_moves"] == 6

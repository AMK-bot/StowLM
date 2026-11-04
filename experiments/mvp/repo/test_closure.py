"""Small synthetic filesystem checks; no project corpus or network."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("repo_closure", Path(__file__).with_name("closure.py"))
closure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(closure)


def write(root, name, text=""):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def policy():
    p = json.loads(Path(__file__).with_name("closure-policy.json").read_text())
    p["entry_modules"] = ["app.__main__"]
    p["explicit_assets"] = ["experiments/mvp/rules.json"]
    return p


def fixture(root):
    files = {
        "app/__init__.py": "",
        "app/__main__.py": "from .service import go\n",
        "app/service.py": "from llm.securing import wrap\nD='experiments/mvp/rules.json'\nREGISTRY='stowage.solver.twostep:Planner'\n",
        "src/llm/__init__.py": "",
        "src/llm/securing/__init__.py": "from .mask_filter import wrap\n",
        "src/llm/securing/mask_filter.py": "def wrap(): pass\n",
        "src/stowage/__init__.py": "",
        "src/stowage/solver/__init__.py": "",
        "src/stowage/solver/twostep.py": "class Planner: pass\n",
        "tests/test_something.py": "from app.service import go\n",
        "app/static/stage.js": "/* synthetic */",
        "experiments/mvp/rules.json": "{}",
        "params/fleet/feeder.toml": "count=1",
        "conftest.py": "",
        "pyproject.toml": "",
        "uv.lock": "",
        ".gitignore": "",
        "data/raw/kcs/kcs.fix": "private downloaded data",
        "models/model.safetensors": "weights",
        "experiments/.pytest-copy/private/key.pem": "do not copy",
    }
    for path, text in files.items():
        write(root, path, text)


def test_path_list_ignores_proposal_subtotals_and_accepts_plain_list(tmp_path):
    table = tmp_path / "table.txt"
    table.write_text("# scope\n[app]\n12\tapp/service.py\nBASE TOTAL: 1 file; 12 bytes\n")
    assert closure.read_list(table) == ["app/service.py"]
    table.write_text("# reviewed bundle\ndemo/showcase-small/instance.json\n")
    assert closure.read_list(table) == ["demo/showcase-small/instance.json"]


def test_import_closure_relative_lazy_registry_and_assets(tmp_path):
    fixture(tmp_path)
    c = closure.Closure(tmp_path, policy())
    c.build([], [])
    assert not c.errors
    assert set(c.files) >= {"app/service.py", "src/llm/securing/mask_filter.py", "src/stowage/solver/twostep.py", "experiments/mvp/rules.json", "app/static/stage.js", "params/fleet/feeder.toml", "tests/test_something.py"}
    assert not c.unresolved


@pytest.mark.parametrize("path", ["data/raw/kcs/kcs.fix", "./data/raw/kcs/kcs.fix", "data//raw/kcs/kcs.fix", "./models/tokenizer.json", "./experiments/mvp/corpus/test/small/case.json", "models/model.safetensors", "experiments/.pytest-copy/private/key.pem", "../outside.json", "/tmp/absolute.json", "docs/expert/replies.md", ".env", "experiments/mvp/corpus/test/small/case.json"])
def test_audited_exclusions_override_explicit_addition(tmp_path, path):
    fixture(tmp_path)
    c = closure.Closure(tmp_path, policy())
    c.build([], [path])
    assert path not in c.files
    assert path in c.excluded
    assert any(e.get("path") == path for e in c.errors)


def test_namespace_imports_and_required_data_are_reported(tmp_path):
    fixture(tmp_path)
    write(tmp_path, "tests/helpers/utility.py", "value=1")
    c = closure.Closure(tmp_path, policy())
    c.index()
    c.module("tests.helpers", "synthetic namespace import")
    assert not c.unresolved
    assert any(e.get("namespace") for e in c.import_edges)
    (tmp_path / "experiments/mvp/rules.json").unlink()
    c.build([], [])
    assert any(e.get("path") == "experiments/mvp/rules.json" for e in c.errors)


def test_file_based_and_imported_experiment_helpers_enter_closure(tmp_path):
    fixture(tmp_path)
    write(tmp_path, "tests/test_script.py", "HELPER='experiments/mvp/tool/run.py'\nfrom experiments.mvp.tool import support\n")
    write(tmp_path, "experiments/mvp/tool/run.py", "from llm.securing import wrap\n")
    write(tmp_path, "experiments/mvp/tool/support.py", "pass\n")
    c = closure.Closure(tmp_path, policy())
    c.build([], [])
    assert {"experiments/mvp/tool/run.py", "experiments/mvp/tool/support.py"} <= set(c.files)
    assert not c.unresolved


def test_unreferenced_historical_snapshot_is_retained_without_expanding_it(tmp_path):
    fixture(tmp_path)
    write(tmp_path, "experiments/archive/candidate.py", "from .absent_snapshot_module import value\n")
    c = closure.Closure(tmp_path, policy())
    c.build(["experiments/archive/candidate.py"], [])
    assert "experiments/archive/candidate.py" in c.files
    assert not c.errors


def test_missing_entry_fails_and_copy_refuses_changed_bytes(tmp_path):
    fixture(tmp_path)
    p = policy()
    p["entry_modules"] += ["app.missing"]
    c = closure.Closure(tmp_path, p)
    c.build([], [])
    assert any(e.get("entry_module") == "app.missing" for e in c.errors)
    row = c.files["app/service.py"]
    write(tmp_path, "app/service.py", "changed=True")
    ignore = tmp_path / "additions"
    ignore.write_text("*.pyc\n")
    with pytest.raises(RuntimeError, match="Source changed"):
        closure.copy_snapshot(tmp_path, tmp_path.parent / (tmp_path.name + "-copy"), [row], ignore)


def test_copy_is_plain_hashed_fresh_and_applies_ignore(tmp_path):
    root = tmp_path / "source"
    fixture(root)
    c = closure.Closure(root, policy())
    c.build([], [])
    additions = tmp_path / "additions"
    additions.write_text("*.pyc\n")
    target = tmp_path / "scratch"
    result = closure.copy_snapshot(root, target, list(c.files.values()), additions)
    assert result["no_git"] and result["copied"] == len(c.files)
    assert "*.pyc" in (target / ".gitignore").read_text()
    assert (target / "app/service.py").read_bytes() == (root / "app/service.py").read_bytes()
    with pytest.raises(ValueError, match="must not exist"):
        closure.copy_snapshot(root, target, [], additions)
    with pytest.raises(ValueError, match="outside"):
        closure.copy_snapshot(root, root / "inside", [], additions)

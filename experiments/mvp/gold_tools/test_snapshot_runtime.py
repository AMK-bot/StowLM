import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location("gold_snapshot", Path(__file__).with_name("snapshot_runtime.py"))
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


def fixture_tree(tmp_path):
    root = tmp_path / "live"
    for name, content in {
        "src/stowage/__init__.py": "VALUE = 1\n",
        "params/fleet/test.toml": "value = 1\n",
        "pyproject.toml": "[project]\nname = 'test'\n",
        "models/glimmer-30b-4bit/tokenizer.json": "{}",
        "experiments/evidence.json": "{\"measured\": true}",
        "experiments/cache/linear/test.npz": "private warm bytes",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    evidence = root / "experiments/evidence.json"
    bank = root / "src/stowage/bank.json"
    cache = root / "experiments/cache/linear/test.npz"
    bank.write_text(json.dumps({"evidence": [{"path": str(evidence), "sha256": snapshot.digest(evidence)},
                                              {"path": str(cache), "sha256": snapshot.digest(cache)}]}))
    return root


def test_epoch_seals_resources_and_keeps_private_cache_bytes(tmp_path):
    root = fixture_tree(tmp_path)
    record = snapshot.create(root, tmp_path / "gold")
    epoch = Path(record["runtime_root"])
    manifest = json.loads((epoch / "MANIFEST.json").read_text())
    resources = json.loads((epoch / "RESOURCE-MANIFEST.json").read_text())["files"]
    assert manifest["files"]["RESOURCE-MANIFEST.json"] == snapshot.digest(epoch / "RESOURCE-MANIFEST.json")
    for name, pin in resources.items():
        stat = (epoch / name).stat()
        assert (pin["size"], pin["mtime_ns"], pin["ctime_ns"]) == (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        assert pin["sha256"] == snapshot.digest(epoch / name)
    # Absolute records remain byte-identical; the runtime adapter relocates reads.
    assert (root / "src/stowage/bank.json").read_bytes() == (epoch / "src/stowage/bank.json").read_bytes()
    cache = root / "experiments/cache/linear/test.npz"
    cache.write_text("live replacement")
    assert (epoch / cache.relative_to(root)).read_text() == "private warm bytes"
    assert not (epoch / cache.relative_to(root)).is_symlink()
    (root / "src/stowage/__init__.py").write_text("VALUE = 2\n")
    assert (epoch / "src/stowage/__init__.py").read_text() == "VALUE = 1\n"
    with pytest.raises(FileExistsError, match="no automatic resnapshot"):
        snapshot.create(root, tmp_path / "gold")


def test_capture_retries_when_live_bytes_change_during_copy(tmp_path, monkeypatch):
    root = fixture_tree(tmp_path)
    original = snapshot.inventory
    calls = 0
    def inventory(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "src/stowage/__init__.py").write_text("VALUE = 3\n")
        return original(path)
    monkeypatch.setattr(snapshot, "inventory", inventory)
    record = snapshot.create(root, tmp_path / "gold", warm_caches=False)
    epoch = Path(record["runtime_root"])
    assert json.loads((epoch / "MANIFEST.json").read_text())["attempts"] == 2
    assert (epoch / "src/stowage/__init__.py").read_text() == "VALUE = 3\n"


def test_resource_reads_survive_live_edits_but_reject_epoch_mutation(tmp_path):
    root = fixture_tree(tmp_path)
    package = root / "src/stowage/linear"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "margin_policy.py").write_text(
        "from pathlib import Path\n_ROOT = Path(__file__).resolve().parents[3]\n")
    record = snapshot.create(root, tmp_path / "gold", warm_caches=False)
    driver_path = Path(__file__).with_name("driver.py").resolve()
    code = '''
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("gold_epoch_driver", sys.argv[1])
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
epoch, live = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[4])
driver.LIVE_ROOT = live
driver.configure_runtime(epoch, sys.argv[3])
from stowage.linear import margin_policy
original = (epoch / "experiments/evidence.json").read_bytes()
live_evidence = live / "experiments/evidence.json"
live_evidence.write_text("changed outside epoch")
assert margin_policy.Path(live_evidence).read_bytes() == original
assert (margin_policy._ROOT / live_evidence).read_bytes() == original
assert margin_policy._ROOT.joinpath(live_evidence).read_bytes() == original
assert driver.Path is pathlib.Path
driver.source_pin()
sealed = epoch / "experiments/evidence.json"
sealed.chmod(0o644)
sealed.write_text("changed inside epoch")
try:
    driver.source_pin()
except ValueError as exc:
    assert "sealed fingerprint changed" in str(exc)
else:
    raise AssertionError("modified epoch evidence was accepted")
'''
    subprocess.run([sys.executable, "-c", code, str(driver_path), record["runtime_root"],
                    record["runtime_epoch_sha256"], str(root)], check=True, timeout=15)


def test_explicit_refresh_pins_archived_producer_without_changing_live_tree(tmp_path):
    root = fixture_tree(tmp_path)
    output = tmp_path / "gold"
    first = snapshot.create(root, output, warm_caches=False)
    (root / "src/stowage/__init__.py").write_text("VALUE = 44\n")
    archived = tmp_path / "archived-v43.py"
    archived.write_text("VALUE = 43\n")
    second = snapshot.create(root, output, warm_caches=False, new_epoch=True,
        overlays={"src/stowage/__init__.py": archived})
    assert first["runtime_epoch_sha256"] != second["runtime_epoch_sha256"]
    assert (Path(first["runtime_root"]) / "src/stowage/__init__.py").read_text() == "VALUE = 1\n"
    assert (Path(second["runtime_root"]) / "src/stowage/__init__.py").read_text() == "VALUE = 43\n"
    assert (root / "src/stowage/__init__.py").read_text() == "VALUE = 44\n"
    assert (output / "epoch-pointers" / f"{first['runtime_epoch_sha256']}.json").is_file()
    manifest = json.loads((Path(second["runtime_root"]) / "MANIFEST.json").read_text())
    assert "SOURCE-OVERLAYS.json" in manifest["files"]


def test_real_deadline_spawn_keeps_epoch_resource_bridge(tmp_path):
    root = fixture_tree(tmp_path)
    linear = root / "src/stowage/linear"
    linear.mkdir()
    (linear / "__init__.py").write_text("")
    (linear / "margin_policy.py").write_text(
        "from pathlib import Path\n_ROOT = Path(__file__).resolve().parents[3]\n")
    solver = root / "src/stowage/solver"
    solver.mkdir()
    (solver / "__init__.py").write_text("")
    project = Path(__file__).resolve().parents[3]
    (solver / "ballast_deadline.py").write_bytes(
        (project / "src/stowage/solver/ballast_deadline.py").read_bytes())
    (solver / "epoch_probe.py").write_text('''
def read_evidence(name):
    from stowage.linear import margin_policy
    path = margin_policy._ROOT / name
    return dict(value=path.read_text(), path=str(path),
                bridge=type(margin_policy.Path()).__name__,
                origin=margin_policy.__file__)
''')
    record = snapshot.create(root, tmp_path / "gold", warm_caches=False)
    code = '''
import pathlib, sys, time
sys.path.insert(0, sys.argv[1])
import driver
driver.LIVE_ROOT = pathlib.Path(sys.argv[4])
driver.configure_runtime(sys.argv[2], sys.argv[3])
# Repeated activation must not replace the saved original with the wrapper.
driver.configure_runtime(sys.argv[2], sys.argv[3])
from stowage.solver import ballast_deadline, epoch_probe
name = str(pathlib.Path(sys.argv[4]) / "experiments/evidence.json")
pathlib.Path(name).write_text("changed outside epoch")
try:
    result = ballast_deadline.run_with_deadline(epoch_probe.read_evidence,
        (name,), deadline=time.monotonic()+10)
finally:
    ballast_deadline.shutdown_worker()
assert result["value"] == '{"measured": true}', result
assert result["bridge"] == "EpochResourcePath", result
assert pathlib.Path(result["path"]).is_relative_to(sys.argv[2]), result
assert pathlib.Path(result["origin"]).is_relative_to(sys.argv[2]), result
'''
    subprocess.run([sys.executable, "-c", code, str(Path(__file__).parent.resolve()),
                    record["runtime_root"], record["runtime_epoch_sha256"], str(root)],
                   check=True, timeout=20)

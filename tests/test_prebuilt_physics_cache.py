"""Inert byte packaging guards; no numerical grids are loaded or constructed."""
from hashlib import sha256
import json
import os

import pytest

from stowage import prebuilt_physics_cache as cache
from stowage.vessel import hull_offsets

H = "a" * 64
GRID = f"liquid-{H}-{'1' * 16}.npz"
QUALIFIED = f"liquid-{H}-{'1' * 16}.qualified.json"
VESSEL = f"liquid-vessel-{H}.json"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # APPROXIMATION: inert synthetic files test packaging only, not numerical
    # qualification. The real raw-byte checksum reader remains unmodified.
    root = tmp_path / "project"
    for relative in ("src/stowage/checker", "src/stowage/vessel", "src/stowage/model", "params/fleet"):
        (root / relative).mkdir(parents=True)
    (root / "src/stowage/checker/__init__.py").write_bytes(b"")
    source = root / "src/stowage/checker/identity.py"
    source.write_bytes(b"# synthetic source epoch one\n")
    raw = b"inert synthetic raw offsets\n"
    asset = root / "asset.fix"
    asset.write_bytes(raw)
    params = {}
    for name in cache.PRODUCTION_SHIPS:
        value = (f'id = "{name}"\n[hull_form]\nfamily = "stowlm_offsets_v1"\n'
                 f'offsets_path = "asset.fix"\noffsets_sha256 = "{sha256(raw).hexdigest()}"\n').encode()
        params[name] = value
        (root / f"params/fleet/{name}.toml").write_bytes(value)
    monkeypatch.setattr(cache, "repo_root", lambda: root)
    monkeypatch.setattr(hull_offsets, "repo_root", lambda: root)
    monkeypatch.setattr(cache, "_parameter_bytes", lambda name: (root / f"params/fleet/{name}.toml").read_bytes())
    runtime = {"synthetic-runtime": "one"}
    monkeypatch.setattr(cache, "runtime_identity", lambda: dict(runtime))
    source_cache = tmp_path / "native"
    source_cache.mkdir()
    members = {GRID: b"inert bytes, deliberately not loaded as npz", QUALIFIED: b'{"qualification":"native only"}', VESSEL: b'{"metadata":"synthetic"}'}
    for index, (name, payload) in enumerate(members.items()):
        path = source_cache / name
        path.write_bytes(payload)
        os.utime(path, ns=(1700000000000000000 + index, 1700000000000000000 + index))
    return dict(root=root, source=source, asset=asset, raw=raw, runtime=runtime,
                source_cache=source_cache, members=members, tmp=tmp_path)


def bundle(setup):
    output = setup["tmp"] / "bundle"
    manifest = cache.export(source_cache=setup["source_cache"], output=output)
    return output, manifest


def write_manifest(output, manifest):
    (output / cache.MANIFEST).write_text(json.dumps(manifest))


def test_export_install_preserves_exact_bytes_mtime_and_leaves_native_checks(setup, monkeypatch):
    import numpy as np
    monkeypatch.setattr(np, "load", lambda *a, **kw: pytest.fail("packaging must not load arrays"))
    output, manifest = bundle(setup)
    target = setup["tmp"] / "installed"
    installed = cache.install(bundle=output, target_cache=target)
    assert installed == manifest
    assert "native certificates" in manifest["qualification"]
    assert len(manifest["entries"]) == 3
    for entry in manifest["entries"]:
        name = entry["filename"]
        assert (target / name).read_bytes() == setup["members"][name]
        assert (target / name).stat().st_mtime_ns == (setup["source_cache"] / name).stat().st_mtime_ns == entry["mtime_ns"]
    assert not (target / cache.MANIFEST).exists()


def test_export_excludes_subdirectories_partial_temp_unknown_json_and_executables(setup):
    native = setup["source_cache"]
    (native / "warmup").mkdir()
    (native / "warmup" / GRID).write_bytes(b"not root")
    for name in ("data.partial.npz", f"{H}.tmp.npz", "arbitrary.json", "run.py", "run.pkl", "manifest.json", "random.npz"):
        (native / name).write_bytes(b"not selected")
    output, manifest = bundle(setup)
    assert {entry["filename"] for entry in manifest["entries"]} == set(setup["members"])
    assert {path.name for path in output.iterdir()} == set(setup["members"]) | {cache.MANIFEST}


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_raw_asset_damage_fails_even_with_populated_target(setup, damage):
    output, manifest = bundle(setup)
    target = setup["tmp"] / "installed"
    cache.install(bundle=output, target_cache=target)
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    if damage == "missing":
        setup["asset"].unlink()
    else:
        setup["asset"].write_bytes(b"tampered")
    with pytest.raises((ValueError, OSError)):
        cache.install(bundle=output, target_cache=target)
    with pytest.raises((ValueError, OSError)):
        cache.export(source_cache=setup["source_cache"], output=setup["tmp"] / "another")
    assert before == {path.name: path.read_bytes() for path in target.iterdir()}


@pytest.mark.parametrize("damage", ["source", "params", "runtime"])
def test_source_input_runtime_changes_reject_stale_bundle(setup, damage):
    output, manifest = bundle(setup)
    if damage == "source":
        setup["source"].write_bytes(b"# epoch two\n")
    elif damage == "params":
        path = setup["root"] / "params/fleet/feeder_500.toml"
        path.write_bytes(path.read_bytes() + b"\n# new input\n")
    else:
        setup["runtime"]["synthetic-runtime"] = "two"
    target = setup["tmp"] / "installed"
    with pytest.raises(cache.PhysicsCacheError, match="stale"):
        cache.install(bundle=output, target_cache=target)
    assert not target.exists()


@pytest.mark.parametrize("damage", ["bytes", "missing", "size", "total", "traversal", "duplicate", "digest", "mtime", "bool-size", "format"])
def test_bad_bundle_never_changes_existing_good_targets(setup, monkeypatch, damage):
    output, manifest = bundle(setup)
    target = setup["tmp"] / "installed"
    cache.install(bundle=output, target_cache=target)
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    entry = manifest["entries"][-1]
    if damage == "bytes":
        (output / entry["filename"]).write_bytes(b"changed")
    elif damage == "missing":
        (output / entry["filename"]).unlink()
    elif damage == "size":
        entry["size_bytes"] = cache.MAX_FILE_BYTES + 1
    elif damage == "total":
        monkeypatch.setattr(cache, "MAX_TOTAL_BYTES", 1)
    elif damage == "traversal":
        entry["filename"] = "../" + GRID
    elif damage == "duplicate":
        manifest["entries"][-1] = manifest["entries"][0]
    elif damage == "digest":
        entry["sha256"] = "0" * 64
    elif damage == "mtime":
        entry["mtime_ns"] = -1
    elif damage == "bool-size":
        entry["size_bytes"] = True
    else:
        manifest["format"] = "not supported"
    write_manifest(output, manifest)
    with pytest.raises(cache.PhysicsCacheError):
        cache.install(bundle=output, target_cache=target)
    assert before == {path.name: path.read_bytes() for path in target.iterdir()}


@pytest.mark.parametrize("part", ["source", "bundle", "target", "manifest", "fifo"])
def test_nonregular_members_and_symlinks_fail_closed(setup, part):
    output, manifest = bundle(setup)
    target = setup["tmp"] / "installed"
    location = setup["source_cache"] if part == "source" else target if part == "target" else output
    location.mkdir(exist_ok=True)
    path = location / (cache.MANIFEST if part == "manifest" else GRID)
    if path.exists():
        path.unlink()
    if part == "fifo":
        os.mkfifo(path)
    else:
        path.symlink_to(setup["asset"])
    with pytest.raises(cache.PhysicsCacheError, match="regular|symlink"):
        if part == "source":
            cache.export(source_cache=setup["source_cache"], output=setup["tmp"] / "bad-output")
        else:
            cache.install(bundle=output, target_cache=target)
    assert setup["asset"].read_bytes() == setup["raw"]


def test_mutation_while_staging_cannot_change_first_good_target(setup, monkeypatch):
    output, manifest = bundle(setup)
    target = setup["tmp"] / "installed"
    target.mkdir()
    first = manifest["entries"][0]["filename"]
    (target / first).write_bytes(b"existing good first file")
    original = cache._stream
    copies = []
    def changed(path, **kwargs):
        result = original(path, **kwargs)
        if kwargs.get("destination") is not None:
            copies.append(path.name)
            if len(copies) == 1:
                last = output / manifest["entries"][-1]["filename"]
                last.write_bytes(b"changed after prevalidation")
        return result
    monkeypatch.setattr(cache, "_stream", changed)
    with pytest.raises(cache.PhysicsCacheError, match="changed"):
        cache.install(bundle=output, target_cache=target)
    assert (target / first).read_bytes() == b"existing good first file"
    assert [path.name for path in target.iterdir()] == [first]


def test_streaming_is_chunked_and_export_is_repeatable(setup, monkeypatch):
    monkeypatch.setattr(cache, "CHUNK_BYTES", 3)
    output, manifest = bundle(setup)
    assert cache.export(source_cache=setup["source_cache"], output=output) == manifest
    assert sum(entry["size_bytes"] for entry in manifest["entries"]) == sum(map(len, setup["members"].values()))


def test_transport_can_reset_bundle_timestamps_but_install_restores_selection_times(setup):
    output, manifest = bundle(setup)
    for entry in manifest["entries"]:
        os.utime(output / entry["filename"], ns=(1800000000000000000, 1800000000000000000))
    target = setup["tmp"] / "installed"
    cache.install(bundle=output, target_cache=target)
    for entry in manifest["entries"]:
        assert (target / entry["filename"]).stat().st_mtime_ns == entry["mtime_ns"]


def test_directory_destination_is_rejected_before_any_other_target_changes(setup):
    output, manifest = bundle(setup)
    target = setup["tmp"] / "installed"
    target.mkdir()
    first, last = (manifest["entries"][index]["filename"] for index in (0, -1))
    (target / first).write_bytes(b"existing good bytes")
    (target / last).mkdir()
    with pytest.raises(cache.PhysicsCacheError, match="destination"):
        cache.install(bundle=output, target_cache=target)
    assert (target / first).read_bytes() == b"existing good bytes"


def test_no_supported_members_and_nested_outputs_rejected(setup):
    with pytest.raises(cache.PhysicsCacheError, match="separate"):
        cache.export(source_cache=setup["source_cache"], output=setup["source_cache"] / "nested")
    empty = setup["tmp"] / "empty"
    empty.mkdir()
    with pytest.raises(cache.PhysicsCacheError, match="no supported"):
        cache.export(source_cache=empty, output=setup["tmp"] / "bundle")


def test_cli_reports_integrity_without_claiming_numerical_qualification(setup, capsys):
    output = setup["tmp"] / "bundle"
    assert cache.main(["export", "--source-cache", str(setup["source_cache"]), "--output", str(output)]) == 0
    assert cache.main(["install", "--bundle", str(output), "--target-cache", str(setup["tmp"] / "installed")]) == 0
    assert '"numerical_qualification": "native checker required"' in capsys.readouterr().out
    assert cache.main(["install", "--bundle", str(setup["tmp"] / "absent"), "--target-cache", str(setup["tmp"] / "never")]) == 2
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["export", "install"])
def test_cli_checksum_mismatch_returns_clear_configuration_error(setup, capsys, command):
    output, manifest = bundle(setup)
    setup["asset"].write_bytes(b"tampered real checksum input")
    if command == "export":
        args = ["export", "--source-cache", str(setup["source_cache"]), "--output", str(setup["tmp"] / "another")]
    else:
        args = ["install", "--bundle", str(output), "--target-cache", str(setup["tmp"] / "installed")]
    assert cache.main(args) == 2
    captured = capsys.readouterr()
    assert "checksum mismatch" in captured.err
    assert "Traceback" not in captured.err
    assert not captured.out


def test_physics_cli_caps_native_threads_before_first_numpy_import():
    import subprocess
    import sys
    script = '''
import builtins, os, runpy, sys
keys = ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS',
        'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')
for key in keys: os.environ[key] = '8'
original = builtins.__import__
seen = []
def checked(name, *args, **kwargs):
    if name == 'numpy' or name.startswith('numpy.'):
        assert all(os.environ[key] == '1' for key in keys), name
        seen.append(name)
    return original(name, *args, **kwargs)
builtins.__import__ = checked
sys.argv = ['prebuilt-physics-cache', '--help']
try: runpy.run_module('stowage.prebuilt_physics_cache', run_name='__main__')
except SystemExit as error: assert error.code == 0
assert seen, 'guard did not observe a numerical import'
'''
    completed = subprocess.run([sys.executable, "-c", script],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr

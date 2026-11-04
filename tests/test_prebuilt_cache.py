"""Local JSON cache integrity; numerical builders use synthetic unit fixtures."""
import json
from hashlib import sha256

import pytest

from stowage import prebuilt_cache as cache
from stowage.model.fixtures import build_instance
from stowage.vessel import fleet, fleet_cache, hull_offsets


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # APPROXIMATION: synthetic model fixture replaces numerical assembly only in
    # unit tests. Production cache/asset readers and public load_vessel stay real.
    raw = b"synthetic inert offset bytes for cache contract tests\n"
    asset = tmp_path / "asset.fix"
    asset.write_bytes(raw)
    monkeypatch.setattr(hull_offsets, "repo_root", lambda: tmp_path)
    params = {}
    for name in cache.PRODUCTION_SHIPS:
        params[name] = (f'id = "{name}"\n[hull_form]\nfamily = "stowlm_offsets_v1"\n'
                        f'offsets_path = "asset.fix"\noffsets_sha256 = "{sha256(raw).hexdigest()}"\n').encode()
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda name: params[name])
    monkeypatch.setattr(cache, "_parameter_bytes", lambda name: params[name])
    monkeypatch.setattr(fleet, "list_fleet", lambda: list(cache.PRODUCTION_SHIPS))
    monkeypatch.setattr(cache, "list_fleet", lambda: list(cache.PRODUCTION_SHIPS))
    monkeypatch.setattr(cache, "rule_freeze_v1_enabled", lambda: False)
    epoch = ["source-v1"]
    def key(name, slice_name, with_hull, payload):
        return sha256(repr((name, slice_name, with_hull, epoch[0])).encode() + payload).hexdigest()
    monkeypatch.setattr(fleet_cache, "_cache_key", key)
    identity = {"test-runtime": "v1"}
    monkeypatch.setattr(cache, "runtime_identity", lambda: dict(identity))
    base = build_instance().vessel
    calls = []
    def builder(payload, name, with_hull):
        assert with_hull
        calls.append(name)
        return base.model_copy(deep=True, update={"id": name, "slice_of": None})
    monkeypatch.setattr(fleet, "_build_vessel", builder)
    fleet_cache.clear_vessel_memo()
    yield dict(root=tmp_path, asset=asset, raw=raw, params=params, epoch=epoch,
               calls=calls, identity=identity, base=base)
    fleet_cache.clear_vessel_memo()


def make_bundle(setup, ships=("feeder_500",)):
    bundle = setup["root"] / "bundle"
    work = setup["root"] / "build-cache"
    manifest = cache.prepare(ships, output=bundle, cache_dir=work)
    return bundle, manifest


def write_manifest(bundle, manifest):
    (bundle / cache.MANIFEST).write_text(json.dumps(manifest))


def test_prepare_install_reuses_public_builder_json_and_preserves_providers(setup, monkeypatch):
    providers = (fleet.load_vessel, fleet_cache.cached_vessel, fleet_cache._cache_key)
    bundle, manifest = make_bundle(setup)
    entry = manifest["entries"][0]
    expected = (bundle / entry["filename"]).read_bytes()
    target = setup["root"] / "installed"
    cache.install(bundle=bundle, cache_dir=target)
    assert (target / entry["filename"]).read_bytes() == expected
    assert providers == (fleet.load_vessel, fleet_cache.cached_vessel, fleet_cache._cache_key)
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE_DIR", str(target))
    first = fleet.load_vessel("feeder_500")
    assert first.model_dump_json().encode() == expected
    first.cells[0].reefer_plug = not first.cells[0].reefer_plug
    assert fleet.load_vessel("feeder_500").model_dump_json().encode() == expected
    fleet_cache.clear_vessel_memo()  # Same state as a fresh process for disk lookup.
    assert fleet.load_vessel("feeder_500").model_dump_json().encode() == expected
    assert setup["calls"] == ["feeder_500"]


def test_prepare_deduplicates_and_does_not_eagerly_build_fleet(setup):
    bundle, manifest = make_bundle(setup, ("feeder_800", "feeder_800"))
    assert setup["calls"] == ["feeder_800"]
    assert len(manifest["entries"]) == 1
    again = cache.prepare(["feeder_800"], output=bundle, cache_dir=setup["root"] / "build-cache")
    assert again == manifest
    assert setup["calls"] == ["feeder_800"]


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_asset_damage_raises_before_install_even_with_existing_cache(setup, damage):
    bundle, manifest = make_bundle(setup)
    target = setup["root"] / "installed"
    cache.install(bundle=bundle, cache_dir=target)
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    if damage == "missing":
        setup["asset"].unlink()
    else:
        setup["asset"].write_bytes(b"tampered")
    with pytest.raises((OSError, ValueError)):
        cache.install(bundle=bundle, cache_dir=target)
    with pytest.raises((OSError, ValueError)):
        cache.prepare(["feeder_500"], output=bundle, cache_dir=target)
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}


@pytest.mark.parametrize("damage", ["payload", "truncated", "checksum", "size", "traversal", "duplicate", "unknown", "runtime", "extra-field", "bool-size", "int-hull"])
def test_bad_bundle_never_changes_good_targets(setup, damage):
    bundle, manifest = make_bundle(setup, ("feeder_500", "feeder_800"))
    target = setup["root"] / "installed"
    cache.install(bundle=bundle, cache_dir=target)
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    entry = manifest["entries"][-1]
    payload = bundle / entry["filename"]
    if damage == "payload":
        obj = json.loads(payload.read_bytes())
        obj["name"] = "well formed changed model"
        payload.write_text(json.dumps(obj))
    elif damage == "truncated":
        payload.write_text("{")
    elif damage == "checksum":
        entry["sha256"] = "0" * 64
    elif damage == "size":
        entry["size_bytes"] = cache.MAX_PAYLOAD_BYTES + 1
    elif damage == "traversal":
        entry["filename"] = "../arbitrary.json"
    elif damage == "duplicate":
        manifest["entries"][-1] = manifest["entries"][0]
    elif damage == "unknown":
        entry["ship"] = "../unknown"
    elif damage == "runtime":
        manifest["runtime"] = {"test-runtime": "different"}
    elif damage == "bool-size":
        entry["size_bytes"] = True
    elif damage == "int-hull":
        entry["with_hull"] = 1
    else:
        entry["ignored-field"] = "not accepted"
    write_manifest(bundle, manifest)
    with pytest.raises(cache.PrebuiltCacheError):
        cache.install(bundle=bundle, cache_dir=target)
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}


@pytest.mark.parametrize("part", ["parameter", "source"])
def test_stale_bundle_rejected_then_prepare_generates_new_key(setup, part):
    bundle, manifest = make_bundle(setup)
    if part == "parameter":
        setup["params"]["feeder_500"] += b"\n# changed input\n"
    else:
        setup["epoch"][0] = "source-v2"
    target = setup["root"] / "installed"
    with pytest.raises(cache.PrebuiltCacheError, match="stale"):
        cache.install(bundle=bundle, cache_dir=target)
    assert not target.exists()
    fresh = cache.prepare(["feeder_500"], output=bundle, cache_dir=setup["root"] / "build-cache")
    assert fresh["entries"][0]["construction_key"] != manifest["entries"][0]["construction_key"]
    cache.install(bundle=bundle, cache_dir=target)
    assert setup["calls"] == ["feeder_500", "feeder_500"]


def test_identity_change_during_build_publishes_no_manifest(setup, monkeypatch):
    original = fleet._build_vessel
    def changed(*args):
        vessel = original(*args)
        setup["epoch"][0] = "edited while building"
        return vessel
    monkeypatch.setattr(fleet, "_build_vessel", changed)
    with pytest.raises(cache.PrebuiltCacheError, match="changed"):
        make_bundle(setup)
    assert not (setup["root"] / "bundle").exists()


@pytest.mark.parametrize("member", ["manifest", "payload", "target"])
def test_symlinks_rejected(setup, member):
    bundle, manifest = make_bundle(setup)
    name = manifest["entries"][0]["filename"]
    target = setup["root"] / "installed"
    path = bundle / (cache.MANIFEST if member == "manifest" else name)
    if member == "target":
        target.mkdir()
        path = target / name
        alternate = setup["root"] / "untouched.json"
        alternate.write_bytes(b"existing target")
    else:
        alternate = setup["root"] / "alternate.json"
        path.rename(alternate)
    path.symlink_to(alternate)
    before = alternate.read_bytes()
    with pytest.raises(cache.PrebuiltCacheError, match="symlink|regular"):
        cache.install(bundle=bundle, cache_dir=target)
    assert alternate.read_bytes() == before


def test_prepare_restores_environment_and_clears_memo(setup, monkeypatch):
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE_DIR", "original")
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE", "0")
    make_bundle(setup)
    import os
    assert os.environ["STOWAGE_VESSEL_CACHE_DIR"] == "original"
    assert os.environ["STOWAGE_VESSEL_CACHE"] == "0"
    assert not fleet_cache._MEMO


def test_special_bundle_member_is_rejected_without_opening_fifo(setup):
    import os
    bundle, manifest = make_bundle(setup)
    path = bundle / manifest["entries"][0]["filename"]
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(cache.PrebuiltCacheError, match="regular"):
        cache.install(bundle=bundle, cache_dir=setup["root"] / "install")


def test_invalid_model_with_updated_integrity_hash_is_rejected(setup):
    bundle, manifest = make_bundle(setup)
    entry = manifest["entries"][0]
    payload = b'{"id":"feeder_500"}'
    (bundle / entry["filename"]).write_bytes(payload)
    entry.update(size_bytes=len(payload), sha256=sha256(payload).hexdigest())
    write_manifest(bundle, manifest)
    with pytest.raises(cache.PrebuiltCacheError, match="invalid vessel JSON"):
        cache.install(bundle=bundle, cache_dir=setup["root"] / "install")


def test_valid_model_for_wrong_ship_with_updated_hash_is_rejected(setup):
    bundle, manifest = make_bundle(setup)
    entry = manifest["entries"][0]
    vessel = setup["base"].model_copy(deep=True, update={"id": "feeder_800", "slice_of": None})
    payload = vessel.model_dump_json().encode()
    (bundle / entry["filename"]).write_bytes(payload)
    entry.update(size_bytes=len(payload), sha256=sha256(payload).hexdigest())
    write_manifest(bundle, manifest)
    with pytest.raises(cache.PrebuiltCacheError, match="declared full"):
        cache.install(bundle=bundle, cache_dir=setup["root"] / "install")


def test_cli_requires_explicit_paths_and_reports_errors(setup, capsys):
    with pytest.raises(SystemExit):
        cache.main(["prepare", "--ships", "feeder_500"])
    assert cache.main(["install", "--bundle", str(setup["root"] / "absent"),
                       "--cache-dir", str(setup["root"] / "install")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_cli_prepare_install_uses_only_requested_ship(setup, capsys):
    bundle = setup["root"] / "bundle"
    assert cache.main(["prepare", "--ships", "feeder_500", "--output", str(bundle),
                       "--cache-dir", str(setup["root"] / "cache")]) == 0
    assert cache.main(["install", "--bundle", str(bundle),
                       "--cache-dir", str(setup["root"] / "install")]) == 0
    assert setup["calls"] == ["feeder_500"]
    responses = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(response["integrity_verified"] for response in responses)
    assert all("qualified" not in response for response in responses)
    assert all(response["numerical_qualification"] == "unchanged full checker required" for response in responses)


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_complete_destination_preflight_preserves_first_good_entry(setup, kind):
    bundle, manifest = make_bundle(setup, ("feeder_500", "feeder_800"))
    target = setup["root"] / "installed"
    target.mkdir()
    first, last = [entry["filename"] for entry in manifest["entries"]]
    (target / first).write_bytes(b"existing good first target")
    if kind == "directory":
        (target / last).mkdir()
    else:
        import os
        os.mkfifo(target / last)
    with pytest.raises(cache.PrebuiltCacheError, match="destination"):
        cache.install(bundle=bundle, cache_dir=target)
    assert (target / first).read_bytes() == b"existing good first target"


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_destination_root_must_be_nonsymlink_directory(setup, kind):
    bundle, manifest = make_bundle(setup)
    target = setup["root"] / "installed"
    if kind == "file":
        target.write_bytes(b"not a directory")
    else:
        real = setup["root"] / "real-cache"
        real.mkdir()
        target.symlink_to(real, target_is_directory=True)
    with pytest.raises(cache.PrebuiltCacheError, match="nonsymlink directory"):
        cache.install(bundle=bundle, cache_dir=target)
    if kind == "file":
        assert target.read_bytes() == b"not a directory"
    else:
        assert not list(real.iterdir())


def test_prepare_preflights_manifest_destination_before_replacing_payload(setup):
    bundle, manifest = make_bundle(setup)
    first = bundle / manifest["entries"][0]["filename"]
    first.write_bytes(b"existing output untouched on configuration failure")
    (bundle / cache.MANIFEST).unlink()
    (bundle / cache.MANIFEST).mkdir()
    with pytest.raises(cache.PrebuiltCacheError, match="destination"):
        cache.prepare(["feeder_500"], output=bundle, cache_dir=setup["root"] / "build-cache")
    assert first.read_bytes() == b"existing output untouched on configuration failure"


@pytest.mark.parametrize("command", ["prepare", "install"])
def test_cli_checksum_mismatch_is_concise_configuration_error(setup, capsys, command):
    bundle, manifest = make_bundle(setup)
    setup["asset"].write_bytes(b"tampered real checksum input")
    if command == "prepare":
        args = ["prepare", "--ships", "feeder_500", "--output", str(setup["root"] / "another"),
                "--cache-dir", str(setup["root"] / "cache")]
    else:
        args = ["install", "--bundle", str(bundle), "--cache-dir", str(setup["root"] / "cache")]
    assert cache.main(args) == 2
    captured = capsys.readouterr()
    assert "checksum mismatch" in captured.err
    assert "Traceback" not in captured.err
    assert not captured.out


def test_real_runtime_identity_is_json_roundtrippable():
    identity = cache.runtime_identity()
    assert json.loads(json.dumps(identity)) == identity
    assert identity["machine"]
    assert identity["dependencies"]["numpy"]


def test_cli_caps_threads_before_first_numpy_import():
    import os
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
sys.argv = ['prebuilt-cache', '--help']
try: runpy.run_module('stowage.prebuilt_cache', run_name='__main__')
except SystemExit as error: assert error.code == 0
assert seen, 'guard did not observe a numerical import'
'''
    completed = subprocess.run([sys.executable, '-c', script], env=dict(os.environ,
        PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr

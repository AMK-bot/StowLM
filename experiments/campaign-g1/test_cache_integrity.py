"""Numerical cache bytes are immutable evidence inputs, never just filenames."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent


def driver(name):
    spec = importlib.util.spec_from_file_location('cache_integrity_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def set_pin_environment(monkeypatch, pin, output):
    for key, value in driver('pin_transport').pin_environment(pin, output, {}).items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def cache_tree(tmp_path, monkeypatch):
    freeze, adapter = driver('freeze'), driver('cache_adapter')
    # This fixture isolates numerical hashing, not rule-migration admission.
    monkeypatch.setattr(freeze,'migration_gate',lambda *a,**k:dict(
        passed=True,component_epoch={},canaries={'numerical_cache_sha256':
            freeze.snapshot_numerical_caches(a[0])['sha256']},fixture_only=True))
    root = tmp_path / 'source'
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'sample.py').write_text('VERSION = 1\n')
    seed_rule_source(root)
    for name in ('stability', 'vessels', 'linear'):
        folder = root / 'experiments/cache' / name
        folder.mkdir(parents=True)
        (folder / 'payload.json').write_text(json.dumps({'max_mass_kg': 30000}))
    monkeypatch.delenv('STOWAGE_VESSEL_CACHE_DIR', raising=False)
    monkeypatch.delenv('CAMPAIGN_NUMERICAL_CACHE_PIN', raising=False)
    monkeypatch.delenv('CAMPAIGN_NUMERICAL_CACHE_PIN_PATH', raising=False)
    monkeypatch.delenv('CAMPAIGN_NUMERICAL_CACHE_PIN_SHA256', raising=False)
    monkeypatch.setattr(adapter, 'ROOT', root)
    monkeypatch.setattr(adapter, 'HERE', tmp_path)
    return freeze, adapter, root, tmp_path / 'run'


@pytest.mark.parametrize("mutation", ["change", "delete", "add"])
def test_freeze_rejects_mutated_numerical_payload(cache_tree, mutation):
    freeze, _, root, _ = cache_tree
    manifest = freeze.create_manifest(root, ['src'])
    path = root / 'freeze.json'
    freeze.atomic_json(path, manifest)
    payload = root / 'experiments/cache/vessels/payload.json'
    if mutation == "change":
        payload.write_text(json.dumps({'max_mass_kg': 60000}))
    elif mutation == "delete":
        payload.unlink()
    else:
        (payload.parent / 'new.json').write_text('{}')
    with pytest.raises(RuntimeError, match='numerical cache.*vessels/'):
        freeze.check_manifest(path)


def test_freeze_pins_environment_selected_vessel_cache(cache_tree, monkeypatch):
    freeze, _, root, output = cache_tree
    external = output.parent / 'external-vessels'
    external.mkdir()
    (external / 'payload.json').write_text('{"max_mass_kg":30000}')
    monkeypatch.setenv('STOWAGE_VESSEL_CACHE_DIR', str(external))
    path = root / 'freeze.json'
    freeze.atomic_json(path, freeze.create_manifest(root, ['src']))
    (external / 'payload.json').write_text('{"max_mass_kg":60000}')
    with pytest.raises(RuntimeError, match='numerical cache.*vessels/payload.json'):
        freeze.check_manifest(path)


def test_existing_snapshot_does_not_trust_marker_alone(cache_tree, monkeypatch):
    freeze, adapter, root, output = cache_tree
    frozen = freeze.create_manifest(root, ['src'])
    set_pin_environment(monkeypatch, frozen['numerical_caches'], output)
    adapter.prepare_cache(output)
    (output / 'cache/vessels/payload.json').write_text('{"max_mass_kg":60000}')
    with pytest.raises(RuntimeError, match='cache.*vessels/payload.json'):
        adapter.prepare_cache(output)


def test_generated_additions_are_never_promoted_for_reuse(cache_tree, monkeypatch):
    freeze, adapter, root, output = cache_tree
    frozen = freeze.create_manifest(root, ['src'])
    set_pin_environment(monkeypatch, frozen['numerical_caches'], output)
    first = adapter.prepare_cache(output)
    (first / 'vessels/unverified-generated.json').write_text('{"max_mass_kg":60000}')
    second = adapter.prepare_cache(output)
    assert second != first
    assert not (second / 'vessels/unverified-generated.json').exists()


def test_cache_reuse_requires_frozen_pin(cache_tree):
    _, adapter, _, output = cache_tree
    with pytest.raises(ValueError, match='numerical cache pin'):
        adapter.prepare_cache(output)


def test_new_cache_content_requires_new_epoch(cache_tree):
    freeze, _, root, _ = cache_tree
    first = freeze.create_manifest(root, ['src'])
    (root / 'experiments/cache/vessels/payload.json').write_text('{"max_mass_kg":60000}')
    second = freeze.create_manifest(root, ['src'])
    assert first['source_sha256'] == second['source_sha256']
    assert first['epoch_id'] != second['epoch_id']
    assert first['freeze_sha256'] != second['freeze_sha256']


def test_unpinned_legacy_snapshot_cannot_be_reused(cache_tree, monkeypatch):
    freeze, adapter, root, output = cache_tree
    frozen = freeze.create_manifest(root, ['src'])
    set_pin_environment(monkeypatch, frozen['numerical_caches'], output)
    (output / 'cache').mkdir(parents=True)
    (output / 'cache/snapshot.json').write_text('{"sources":[],"copy_wall_s":0}')
    with pytest.raises(ValueError, match='differs from frozen pin'):
        adapter.prepare_cache(output)


def seed_rule_source(root):
    source = HERE.parents[1] / 'src/stowage'
    for relative in ('versions.py', 'rules/catalogue.toml'):
        destination = root / 'src/stowage' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / relative).read_bytes())


def test_migration_freeze_preserves_shared_numerical_pin_contract(cache_tree):
    # src/eval owns this consumer. The richer rule epoch must remain compatible
    # with its established source+numerical fingerprint, without editing eval.
    from eval.splits_epoch import validate_numerical_cache_snapshot
    freeze,_,root,_=cache_tree
    frozen=freeze.create_manifest(root,['src'])
    assert validate_numerical_cache_snapshot(frozen)==frozen['numerical_caches']['sha256']
    assert frozen['rule_epoch_sha256']!=frozen['freeze_sha256']

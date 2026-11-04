"""Official harness evaluation reads only the numerical routes frozen for it."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eval import harness, splits_epoch


def test_official_harness_rejects_unfrozen_vessel_cache_route(tmp_path, monkeypatch):
    from stowage.vessel import fleet_cache
    expected = fleet_cache._cache_dir().resolve()
    alternate = tmp_path / 'unfrozen-vessels'
    alternate.mkdir()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'freeze_manifest': {'numerical_caches': {
        'sources': {'vessels': str(expected)}}}}))
    # Isolate active-route admission: content admission is independently covered
    # by epoch cache integrity tests. No numerical calculation is performed.
    monkeypatch.setattr(splits_epoch, 'validate_numerical_cache_snapshot', lambda freeze: None)
    monkeypatch.setattr(harness, 'require_clean', lambda *args, **kwargs: None)
    monkeypatch.setattr(splits_epoch, 'instance_matches_spec', lambda *args: True)
    monkeypatch.setenv('STOWAGE_VESSEL_CACHE_DIR', str(alternate))
    path = Path(__file__).resolve().parents[2] / 'data/fixtures/empty_ship.json'
    row = {'path': str(path), 'input_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
           'spec': {}, 'regen_manifest_path': str(manifest),
           'regen_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='numerical cache route.*vessels'):
        harness._load_selected(row, tiny_run=False)


def active_pin():
    from stowage.vessel import fleet_cache
    from stowage.checker import stability_tables
    from stowage.linear import cache
    return {'numerical_caches': {'sources': {
        'vessels': str(fleet_cache._cache_dir().resolve()),
        'stability': str(stability_tables.CACHE_DIR.resolve()),
        'linear': str(cache._DEFAULT_ROOT.resolve())}}}


def test_matching_routes_clear_previous_numerical_memos(monkeypatch):
    from functools import lru_cache
    from stowage.vessel import fleet_cache
    from stowage.checker import stability_liquid
    @lru_cache(maxsize=1)
    def stale_payload():
        return 'payload from a previous route'
    stale_payload.__module__ = stability_liquid.__name__
    monkeypatch.setattr(stability_liquid, '_route_probe', stale_payload, raising=False)
    stale_payload()
    monkeypatch.setitem(fleet_cache._MEMO, 'old-route', 'old-payload')
    splits_epoch.validate_active_numerical_cache_routes(active_pin())
    assert not fleet_cache._MEMO
    assert stale_payload.cache_info().currsize == 0


@pytest.mark.parametrize('reader, attribute', [
    ('stowage.checker.stability_tables', 'CACHE_DIR'),
    ('stowage.checker.stability_sounding', 'CACHE_DIRECTORY'),
    ('stowage.checker.stability_liquid_tables', 'CACHE_DIRECTORY'),
    ('stowage.checker.stability_liquid', 'CACHE_DIRECTORY'),
    ('stowage.linear.cache', '_DEFAULT_ROOT'),
])
def test_module_route_mismatch_refuses_admission(tmp_path, monkeypatch, reader, attribute):
    import importlib
    pin = active_pin()
    monkeypatch.setattr(importlib.import_module(reader), attribute, tmp_path / 'unfrozen')
    with pytest.raises(ValueError, match='numerical cache route differs'):
        splits_epoch.validate_active_numerical_cache_routes(pin)


def test_old_bound_default_route_cannot_hide_behind_correct_global(tmp_path, monkeypatch):
    from stowage.checker import stability_tables
    pin = active_pin()
    monkeypatch.setattr(stability_tables.cached_geometry, '__kwdefaults__',
                        {'cache_dir': tmp_path / 'unfrozen'})
    with pytest.raises(ValueError, match='numerical cache route differs.*cache_dir'):
        splits_epoch.validate_active_numerical_cache_routes(pin)

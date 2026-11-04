"""One refreshed content identity owns checker metadata, maps and provenance."""

from hashlib import sha256
from pathlib import Path
import tomllib

import pytest

from stowage.checker import rules
from stowage.model import Placement
from stowage.model.fixtures import build_instance
from stowage.rules import catalogue as module
from stowage.versions import component_versions


def _e2_case():
    instance = build_instance()
    boxes = [box for box in instance.containers if box.length_ft == 20][:2]
    for box, mass in zip(boxes, (9000, 15000)):
        box.gross_mass_kg, box.dg, box.special_stowage = mass, None, []
    ship = instance.onboard.model_copy(deep=True)
    ship.placements = [Placement(container_id=box.id, slot=slot)
                       for box, slot in zip(boxes, ('0010182', '0010184'))]
    return instance, ship


def test_warm_permissive_checker_refreshes_both_caches_and_recorded_identity(tmp_path, monkeypatch):
    strict = module.DEFAULT_CATALOGUE_PATH.read_bytes()
    permissive = strict.replace(b'"tolerance" = { "value" = 0,',
                                b'"tolerance" = { "value" = 1,', 1)
    assert strict != permissive
    path = tmp_path / 'catalogue.toml'
    path.write_bytes(permissive)
    monkeypatch.setattr(module, 'DEFAULT_CATALOGUE_PATH', path)
    # Existing cache API's imported loader has a bound default path; the new
    # snapshot reader resolves DEFAULT_CATALOGUE_PATH dynamically instead.
    monkeypatch.setattr(rules, 'load_catalogue', lambda: module.load_catalogue(path))
    rules._catalogue.cache_clear()
    rules._catalogue_data.cache_clear()
    instance, ship = _e2_case()

    def count():
        report = rules.check_static_rules(instance, ship, call_index=0)
        return sum(v.rule_id == 'expert.e2.weight_order' for v in report.violations)

    try:
        assert count() == 0  # Warm both process caches with tolerance 1.
        path.write_bytes(strict)
        assert count() == 1  # No manual clearing between the two checks.
        snapshot = module.catalogue_snapshot()
        assert snapshot.content_sha256 == 'sha256:' + sha256(strict).hexdigest()
        assert rules._catalogue_data() is snapshot.catalogue
        assert rules._catalogue() is snapshot.rules
        assert component_versions()['rule_catalogue'] == snapshot.content_sha256
    finally:
        rules._catalogue.cache_clear()
        rules._catalogue_data.cache_clear()


def test_unchanged_snapshot_uses_stat_without_rereading_or_reparsing(tmp_path, monkeypatch):
    path = tmp_path / 'catalogue.toml'
    path.write_bytes(module.DEFAULT_CATALOGUE_PATH.read_bytes())
    original = module.catalogue_snapshot(path)
    original_read = Path.read_bytes
    reads = []

    def observed_read(value):
        reads.append(value)
        return original_read(value)

    monkeypatch.setattr(Path, 'read_bytes', observed_read)
    assert module.catalogue_snapshot(path) is original
    assert reads == []


def test_snapshot_loader_does_not_share_mutable_parameter_lists(tmp_path):
    path = tmp_path / 'catalogue.toml'
    path.write_bytes(module.DEFAULT_CATALOGUE_PATH.read_bytes())
    loaded = module.load_catalogue(path)
    loaded.weight_classes.parameters['labels'].value.append('edited')
    assert module.load_catalogue(path).weight_classes.parameters['labels'].value == ['L', 'M', 'H']


def test_invalid_replacement_does_not_keep_enforcing_old_snapshot(tmp_path, monkeypatch):
    path = tmp_path / 'catalogue.toml'
    path.write_bytes(module.DEFAULT_CATALOGUE_PATH.read_bytes())
    monkeypatch.setattr(module, 'DEFAULT_CATALOGUE_PATH', path)
    module.catalogue_snapshot()
    path.write_text('[[invalid')
    with pytest.raises(tomllib.TOMLDecodeError):
        rules._catalogue()

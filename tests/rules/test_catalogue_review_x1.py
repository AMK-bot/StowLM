"""Review regressions for staged E6 metadata and the shared rule-freeze switch."""

from hashlib import sha256
import re

import pytest

from stowage.rules import catalogue as module
from stowage.versions import component_versions


E6 = "expert.e6.heavy_under_deck"


def test_pending_e6_metric_names_its_independent_threshold():
    rule = module.load_catalogue()[E6]
    definition = rule.pending_contract.parameters["metric"].provenance.assumption
    assert "gross mass >= heavy_on_deck_threshold_kg" in definition
    assert "pending" in definition.lower()
    assert rule.pending_contract.penalty.metric == "heavy_on_deck_count"


def test_divergent_legacy_and_pending_thresholds_include_20500kg_in_new_metric():
    data = module.load_catalogue().model_dump()
    data["weight_classes"]["parameters"]["heavy_min_kg"]["value"] = 21_000
    rule = module.Catalogue.model_validate(data)[E6]
    assert rule.parameters["heavy_threshold"].value == 21_000
    assert rule.pending_contract.parameters["heavy_on_deck_threshold_kg"].value == 20_000
    definition = rule.pending_contract.parameters["metric"].provenance.assumption
    threshold_name = re.search(r"gross mass >= ([a-z_]+)", definition).group(1)
    declared_threshold = rule.pending_contract.parameters[threshold_name].value
    assert 20_500 >= declared_threshold, "The declared pending metric must include this on-deck box"
    assert 20_500 < rule.parameters["heavy_threshold"].value


def test_rule_freeze_v1_defaults_off_in_distributed_catalogue():
    assert module.rule_freeze_v1_enabled() is False
    assert module.load_catalogue().activation.rule_freeze_v1 is False


def test_rule_freeze_override_nests_and_restores_on_exception():
    assert module.rule_freeze_v1_enabled() is False
    with pytest.raises(RuntimeError, match="deliberate exit"):
        with module.rule_freeze_v1(enabled=True):
            assert module.rule_freeze_v1_enabled() is True
            with module.rule_freeze_v1(enabled=False):
                assert module.rule_freeze_v1_enabled() is False
            assert module.rule_freeze_v1_enabled() is True
            raise RuntimeError("deliberate exit")
    assert module.rule_freeze_v1_enabled() is False


def test_rule_freeze_override_changes_effective_snapshot_identity_without_file_edits():
    original_bytes = module.DEFAULT_CATALOGUE_PATH.read_bytes()
    base = module.catalogue_snapshot()
    file_identity = "sha256:" + sha256(original_bytes).hexdigest()
    assert base.content_sha256 == file_identity
    assert component_versions()["rule_catalogue"] == file_identity
    with module.rule_freeze_v1(enabled=True):
        enabled = module.catalogue_snapshot()
        assert enabled.content_sha256 != file_identity
        assert component_versions()["rule_catalogue"] == enabled.content_sha256
        assert module.rule_freeze_v1_enabled() is True
        assert module.load_catalogue().activation.rule_freeze_v1 is True
        assert enabled.catalogue.activation.rule_freeze_v1 is True
        assert enabled.rules[E6] == enabled.catalogue[E6]
        assert module.DEFAULT_CATALOGUE_PATH.read_bytes() == original_bytes
        with module.rule_freeze_v1(enabled=False):
            disabled = module.catalogue_snapshot()
            assert disabled.content_sha256 != enabled.content_sha256
            assert component_versions()["rule_catalogue"] == disabled.content_sha256
            assert module.rule_freeze_v1_enabled() is False
            assert module.load_catalogue().activation.rule_freeze_v1 is False
        assert module.catalogue_snapshot().content_sha256 == enabled.content_sha256
        assert component_versions()["rule_catalogue"] == enabled.content_sha256
    assert module.catalogue_snapshot().content_sha256 == file_identity
    assert component_versions()["rule_catalogue"] == file_identity
    assert module.DEFAULT_CATALOGUE_PATH.read_bytes() == original_bytes


def test_explicit_file_snapshot_binds_activation_rules_and_content_identity(tmp_path):
    original_bytes = module.DEFAULT_CATALOGUE_PATH.read_bytes()
    if b"rule_freeze_v1 = false" in original_bytes:
        enabled_bytes = original_bytes.replace(b"rule_freeze_v1 = false", b"rule_freeze_v1 = true", 1)
    else:
        enabled_bytes = original_bytes + b"\n[activation]\nrule_freeze_v1 = true\n"
    path = tmp_path / "catalogue.toml"
    path.write_bytes(enabled_bytes)
    snapshot = module.catalogue_snapshot(path)
    assert snapshot.content_sha256 == "sha256:" + sha256(enabled_bytes).hexdigest()
    assert snapshot.catalogue.activation.rule_freeze_v1 is True
    assert snapshot.rules[E6] == snapshot.catalogue[E6]
    assert module.rule_freeze_v1_enabled(catalogue=snapshot.catalogue) is True
    assert module.rule_freeze_v1_enabled() is False
    assert module.load_catalogue(path) == snapshot.catalogue

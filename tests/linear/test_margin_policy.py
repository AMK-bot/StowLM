"""Directional default budgets bind measured evidence without global inflation."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from stowage.linear import margin_policy as policy


@pytest.fixture
def context(monkeypatch, tmp_path):
    import stowage.linear.cache as cache
    monkeypatch.setattr(cache, "response_calibration_identity", lambda family: "response", raising=False)
    monkeypatch.setattr(policy, "_BANK_PATH", tmp_path/"bank.json")
    model = NS(station_x_m=np.array([0., 5.]), water_density_kg_m3=1025.)
    sides = {side: {metric: [1., 2.] if metric in ("sf_n", "bm_nm") else .01
                    for metric in policy._METRICS} for side in policy._SIDES}
    diagnostics = dict(signed_errors=sides, family_domain={"kg_min_m": 0., "kg_max_m": 20.},
        reference_inputs={"method": 1, "files": {"a": "hash"}},
        reference_identity={"method": 1, "direct": False, "dispatch": "numeric-table-hash"},
        family_calibration_cache_key="calibration", response_calibration_identity="response")
    model.measurement_diagnostics = diagnostics
    return NS(template=model), model, diagnostics


def bank_for(context, tmp_path):
    _, model, diagnostics = context
    physical = deepcopy(diagnostics["signed_errors"])
    # Reproduce the observed >3.1x list underfit without inflating trim.
    physical["upper_unsafe"]["list_rad"] = .03108911
    transfer = policy._zeros(len(model.station_x_m))
    transfer["lower_unsafe"]["gm_m"] = .00002
    transfer["lower_unsafe"]["sf_n"] = [2.464512e-9, 0.]
    path = tmp_path/"observed.json"
    path.write_text("measured physical observations")
    return {"format": 1, "entries": {"response": dict(
        station_x_m=model.station_x_m.tolist(), physical_unsafe=physical,
        reference_transfer=transfer, domain=diagnostics["family_domain"],
        reference_identity=diagnostics["reference_identity"], equivalence={"fresh_numeric_equal": True},
        evidence=[{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}])}}


def test_fresh_family_has_explicit_empirical_default(context):
    family, model, diagnostics = context
    certificate = policy.qualify_policy(family, None, diagnostics)
    diagnostics["margin_policy"] = certificate
    result = policy.default_reserves(model, direction="conservative")
    assert result["lower_unsafe"]["gm_m"] == pytest.approx(.011)
    assert certificate["mandatory_exact_checker"] is True
    assert certificate["bank_enriched"] is False
    assert certificate["builder_version"] == policy.LINEAR_MARGIN_POLICY_VERSION
    assert len(certificate["inputs_hash"]) == 64
    assert certificate["qualified_at"].endswith("Z")


def test_physical_and_reference_evidence_default_is_directional(context, tmp_path):
    family, model, diagnostics = context
    diagnostics["margin_policy"] = policy.qualify_policy(family, None, diagnostics, bank=bank_for(context, tmp_path))
    result = policy.default_reserves(model, direction="conservative")
    assert result["upper_unsafe"]["list_rad"] == pytest.approx(1.1*.03108911)
    assert result["lower_unsafe"]["list_rad"] == pytest.approx(.011)
    assert result["upper_unsafe"]["trim_m"] == pytest.approx(.011)
    assert result["lower_unsafe"]["gm_m"] == pytest.approx(1.1*(.01+.00002))
    assert result["lower_unsafe"]["sf_n"][0] == pytest.approx(1.1*(1.+2.464512e-9))


def test_zero_baseline_reference_closure_gets_absolute_reserve(context, tmp_path):
    family, model, diagnostics = context
    bank = bank_for(context, tmp_path)
    diagnostics["signed_errors"]["lower_unsafe"]["sf_n"] = [0., 0.]
    bank["entries"]["response"]["physical_unsafe"]["lower_unsafe"]["sf_n"] = [0., 0.]
    diagnostics["margin_policy"] = policy.qualify_policy(family, None, diagnostics, bank=bank)
    assert policy.default_reserves(model, direction="conservative")["lower_unsafe"]["sf_n"][0] == pytest.approx(1.1*2.464512e-9)


def test_known_reference_change_requires_explicit_nonproduction_qualification(context, tmp_path):
    family, model, diagnostics = context
    bank = bank_for(context, tmp_path)
    bank["entries"]["response"]["reference_identity"] = {"method": 2}
    with pytest.raises(ValueError, match="epoch is stale"):
        policy.qualify_policy(family, None, diagnostics, bank=bank)
    diagnostics["margin_policy"] = policy.qualify_policy(
        family, None, diagnostics, bank=bank, allow_reference_qualification=True)
    with pytest.raises(ValueError, match="invalid default margin-policy"):
        policy.default_reserves(model, direction="conservative")


@pytest.mark.parametrize("field,value", [("domain", {}), ("station_x_m", [0., 6.]),
                                         ("equivalence", None)])
def test_bank_domain_station_and_equivalence_mismatch_fail_closed(context, tmp_path, field, value):
    family, _, diagnostics = context
    bank = bank_for(context, tmp_path)
    bank["entries"]["response"][field] = value
    with pytest.raises(ValueError, match="margin bank"):
        policy.qualify_policy(family, None, diagnostics, bank=bank)


def test_evidence_changed_after_measurement_fails_closed(context, tmp_path):
    family, _, diagnostics = context
    bank = bank_for(context, tmp_path)
    (tmp_path/"observed.json").write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        policy.qualify_policy(family, None, diagnostics, bank=bank)


def test_certificate_mutation_stale_identity_and_bank_updates_fail_closed(context):
    family, model, diagnostics = context
    diagnostics["margin_policy"] = policy.qualify_policy(family, None, diagnostics)
    diagnostics["margin_policy"]["reserves"]["lower_unsafe"]["gm_m"] = 0.
    with pytest.raises(ValueError, match="invalid default"):
        policy.default_reserves(model, direction="conservative")
    diagnostics["margin_policy"] = policy.qualify_policy(family, None, diagnostics)
    diagnostics["response_calibration_identity"] = "other-response"
    with pytest.raises(ValueError, match="response_identity"):
        policy.default_reserves(model, direction="conservative")
    diagnostics["response_calibration_identity"] = "response"
    policy._BANK_PATH.write_text(json.dumps({"format": 1, "entries": {}}))
    with pytest.raises(ValueError, match="bank changed"):
        policy.default_reserves(model, direction="conservative")


def test_audit_only_reference_changes_preserve_semantic_certificate(context):
    family, model, diagnostics = context
    diagnostics["margin_policy"] = policy.qualify_policy(family, None, diagnostics)
    diagnostics["reference_inputs"] = {"files": {"renewed-cert": "new-audit-bytes"}}
    assert policy.default_reserves(model, direction="conservative")["lower_unsafe"]["gm_m"] == pytest.approx(.011)


def test_family_without_policy_is_rejected_but_hand_model_is_legacy(context):
    _, model, _ = context
    with pytest.raises(ValueError, match="lacks default"):
        policy.default_reserves(model, direction="conservative")
    model.measurement_diagnostics = {}
    assert policy.default_reserves(model, direction="conservative") is None


@pytest.mark.parametrize("field,value", [("builder_version", "obsolete"), ("inputs_hash", "0"*64),
                                         ("qualified_at", "2026-01-01T00:00:00+07:00"),
                                         ("qualified_at", "2999-01-01T00:00:00Z")])
def test_builder_inputs_and_utc_date_are_validated_even_with_valid_checksum(context, field, value):
    family, model, diagnostics = context
    certificate = policy.qualify_policy(family, None, diagnostics)
    certificate[field] = value
    certificate["sha256"] = policy._digest({key: value for key, value in certificate.items() if key != "sha256"})
    diagnostics["margin_policy"] = certificate
    with pytest.raises(ValueError, match="margin-policy"):
        policy.default_reserves(model, direction="conservative")


def test_qualification_date_is_audit_only_not_a_cache_or_inputs_dependency(context):
    family, model, diagnostics = context
    certificate = policy.qualify_policy(family, None, diagnostics)
    inputs = policy.policy_inputs()
    identity = certificate["inputs_hash"]
    certificate["qualified_at"] = "2020-01-01T00:00:00Z"
    certificate["sha256"] = policy._digest({key: value for key, value in certificate.items() if key != "sha256"})
    diagnostics["margin_policy"] = certificate
    assert policy.policy_inputs() == inputs
    assert certificate["inputs_hash"] == identity
    assert policy.default_reserves(model, direction="conservative")["lower_unsafe"]["gm_m"] == pytest.approx(.011)


def test_optional_bank_holdout_failure_refuses_policy_certificate(context, tmp_path):
    family, model, diagnostics = context
    bank = bank_for(context, tmp_path)
    record = bank["entries"]["response"]
    record["holdout_qualification"] = dict(format=1, independent_holdout_passed=True,
        calibration_seed=1, holdout_seed=2, calibration_samples=128, holdout_samples=64,
        holdout_unsafe=policy._zeros(len(model.station_x_m)))
    certificate = policy.qualify_policy(family, None, diagnostics, bank=bank)
    assert "holdout_qualification" in certificate
    diagnostics["margin_policy"] = certificate
    policy.default_reserves(model, direction="conservative")
    record["holdout_qualification"]["holdout_unsafe"]["upper_unsafe"]["sf_n"][1] = 9999.
    with pytest.raises(ValueError, match="upper_unsafe/sf_n@5"):
        policy.qualify_policy(family, None, diagnostics, bank=bank)

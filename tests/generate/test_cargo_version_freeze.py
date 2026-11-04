"""The effective cargo epoch is pinned independently of the leader registry."""
from copy import deepcopy

import pytest

from stowage.generate import cargo
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1


def _class_three_profile():
    params = deepcopy(load_param_file('cargo', 'feeder_export'))
    equipment = next(row for row in params['equipment'] if row['id'] == 'dry_20')
    params['equipment'] = [equipment]
    equipment['empty_share']['value'] = 0
    params['dg']['share_of_eligible_loaded_boxes']['value'] = 1
    params['dg']['classes'] = [row for row in params['dg']['classes'] if row['id'] == '3']
    return params


@pytest.mark.parametrize('enabled,expected', [(False, '7.0.0'), (True, '8.0.0')])
def test_effective_cargo_version_is_pinned_despite_registry_drift(monkeypatch, enabled, expected):
    monkeypatch.setattr(cargo, 'CARGO_GENERATOR_VERSION', '9.0.0')
    with rule_freeze_v1(enabled=enabled):
        assert cargo.effective_cargo_generator_version() == expected


@pytest.mark.parametrize('enabled,expected', [(False, '7.0.0'), (True, '8.0.0')])
def test_audit_and_shipment_use_effective_epoch_despite_registry_drift(monkeypatch, enabled, expected):
    monkeypatch.setattr(cargo, 'CARGO_GENERATOR_VERSION', '9.0.0')
    audit = cargo.CargoGenerationReport()
    with rule_freeze_v1(enabled=enabled):
        boxes = cargo.generate_load_list('straits', 0, 2, seed=71,
                                         params=_class_three_profile(), report=audit)
    assert audit.generator_version == expected
    assert len(boxes) == 2
    assert all(box.dg.shipment_provenance.versions['cargo_generator'] == expected for box in boxes)

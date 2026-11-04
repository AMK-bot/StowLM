"""Addendum 1/D66: activated derived markers and unknown next-call crane limits."""

import pytest
from pydantic import ValidationError

from stowage.model import Instance, PortCall, PortProfile
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


def _payload(primary="1.4S", *, flag=True, classes=("1",), exemptions=()):
    payload = build_instance().model_dump(mode="json")
    payload["port"]["port_rules"] = [dict(rule_id="port.dg.direct_delivery", params=dict(
        classes=list(classes), exempt_divisions=list(exemptions)))]
    box = payload["containers"][0]
    box["dg"] = dict(un_number="0004", imdg_class=primary, stowage_category="01")
    box["direct_delivery"] = flag
    if flag:
        payload["yard"]["stacks"] = [s for s in payload["yard"]["stacks"] if box["id"] not in s["boxes"]]
    return payload, box


@pytest.mark.parametrize("primary,flag", [("1.4S", False), ("3", True)])
def test_activated_direct_delivery_rejects_both_marker_mismatches(primary, flag):
    payload, _ = _payload(primary, flag=flag)
    with rule_freeze_v1(enabled=True):
        with pytest.raises(ValidationError, match="direct_delivery must match current-port primary DG class"):
            Instance.model_validate(payload)


@pytest.mark.parametrize("primary", ["1", "1.1D", "1.4S"])
def test_primary_class_one_includes_all_divisions_by_default(primary):
    payload, box = _payload(primary)
    with rule_freeze_v1(enabled=True):
        instance = Instance.model_validate(payload)
        assert next(c for c in instance.containers if c.id == box["id"]).direct_delivery
        assert Instance.model_validate_json(instance.model_dump_json()) == instance


def test_explicit_division_exemption_removes_direct_delivery_requirement():
    payload, box = _payload(flag=False, exemptions=("1.4S",))
    with rule_freeze_v1(enabled=True):
        assert not Instance.model_validate(payload).containers[0].direct_delivery
        box["direct_delivery"] = True
        payload["yard"]["stacks"] = [s for s in payload["yard"]["stacks"] if box["id"] not in s["boxes"]]
        with pytest.raises(ValidationError, match="direct_delivery must match current-port primary DG class"):
            Instance.model_validate(payload)


@pytest.mark.parametrize("secondary_kind", ["subsidiary", "additional"])
def test_secondary_class_one_never_triggers_direct_delivery(secondary_kind):
    payload, box = _payload("3", flag=False)
    if secondary_kind == "subsidiary":
        box["dg"]["subsidiary_risks"] = ["1"]
    else:
        box["dg"]["additional"] = [dict(un_number="0004", imdg_class="1.4S", stowage_category="01")]
    with rule_freeze_v1(enabled=True):
        assert not Instance.model_validate(payload).containers[0].direct_delivery


def test_flag_cannot_be_enabled_without_current_port_direct_delivery_rule():
    payload, _ = _payload()
    payload["port"]["port_rules"] = []
    with rule_freeze_v1(enabled=True):
        with pytest.raises(ValidationError, match="direct_delivery must match current-port primary DG class"):
            Instance.model_validate(payload)


@pytest.mark.parametrize("primary,flag", [("1.4S", False), ("3", True)])
def test_activation_off_keeps_legacy_marker_behavior(primary, flag):
    payload, box = _payload(primary, flag=flag)
    with rule_freeze_v1(enabled=False):
        instance = Instance.model_validate(payload)
    assert next(c for c in instance.containers if c.id == box["id"]).direct_delivery is flag


def _environment_payload():
    return dict(locode="VNSGN", archetype="cat_lai_like", profile_kind="next_call_environment",
                yard=dict(id="synthetic_environment", blocks=[]), equipment=[], water_density_kg_m3=1000)


def test_environment_profile_has_unknown_crane_fields_and_round_trips():
    port = PortProfile.model_validate(_environment_payload())
    assert port.max_crane_height_tiers is None
    assert port.quay_crane is None
    assert port.yard_max_tiers is None
    assert PortProfile.model_validate_json(port.model_dump_json()) == port


@pytest.mark.parametrize("field,value", [
    ("max_crane_height_tiers", 1), ("yard_max_tiers", 1),
    ("quay_crane", dict(count=1, max_on_deck_tiers=1)),
])
def test_environment_profile_cannot_declare_placeholder_crane_capacity(field, value):
    payload = _environment_payload() | {field: value}
    with pytest.raises(ValidationError, match="environment-only profiles must have unknown crane and yard limits"):
        PortProfile.model_validate(payload)


@pytest.mark.parametrize("field", ["quay_crane", "yard_max_tiers"])
def test_operating_profile_still_requires_legacy_crane_and_yard_limits(field):
    payload = build_instance().port.model_dump(mode="json")
    payload[field] = None
    with pytest.raises(ValidationError, match="operational profile requires quay_crane and yard_max_tiers"):
        PortProfile.model_validate(payload)


def test_later_call_archetype_is_explicit_and_crane_limit_unknown():
    call = PortCall(locode="VNSGN", index=1, archetype="cat_lai_like")
    assert call.max_on_deck_tiers is None
    assert PortCall.model_validate_json(call.model_dump_json()) == call

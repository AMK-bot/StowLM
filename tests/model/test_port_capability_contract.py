"""Review regressions: arrival density evidence cannot declare operating capability."""

import pytest
from pydantic import ValidationError

from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model.port import PortCall, PortProfile
from stowage.ports import load_port


ENVIRONMENTS = ["cat_lai_like", "hong_kong_like", "port_klang_like"]


@pytest.mark.parametrize("archetype", ENVIRONMENTS)
def test_density_only_profile_kind_survives_json_roundtrip(archetype):
    profile = load_port(archetype)
    assert profile.profile_kind == "next_call_environment"
    restored = PortProfile.model_validate_json(profile.model_dump_json())
    assert restored.profile_kind == "next_call_environment"
    assert restored == profile


@pytest.mark.parametrize("archetype", ENVIRONMENTS)
def test_density_only_profile_has_no_governing_crane_height(archetype):
    assert load_port(archetype).max_crane_height_tiers is None


@pytest.mark.parametrize("archetype", ENVIRONMENTS)
def test_density_only_profile_cannot_be_the_current_operational_port(archetype):
    payload = build_instance().model_dump(mode="json")
    payload["stage"] = "B"
    payload["containers"] = payload["containers"][1:3]
    for box in payload["containers"]:
        box["dg"] = None
        box["special_stowage"] = []
    profile = load_port(archetype)
    payload["port"] = profile.model_dump(mode="json")
    payload["rotation"]["calls"][0]["locode"] = profile.locode
    payload["yard"] = {"yard_id": profile.yard.id, "stacks": []}
    payload["load_list"] = []
    payload["onboard"]["placements"] = [
        {"container_id": box["id"], "slot": slot}
        for box, slot in zip(payload["containers"], ["0010182", "0010184"])
    ]
    with pytest.raises(ValidationError, match="current port must be an operational profile"):
        Instance.model_validate(payload)


@pytest.mark.parametrize("height", [None, 1, 0, -1])
def test_later_call_height_is_unknown_or_positive(height):
    if height is None or height >= 1:
        assert PortCall(locode="SGSIN", index=1, max_on_deck_tiers=height).max_on_deck_tiers == height
    else:
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            PortCall(locode="SGSIN", index=1, max_on_deck_tiers=height)

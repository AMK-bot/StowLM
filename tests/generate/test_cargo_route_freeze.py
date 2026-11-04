"""A3: generated rotations carry resolvable profiles only after activation."""
from copy import deepcopy
from random import Random

import pytest

from stowage.generate.cargo import _params, _route
from stowage.model.port import PortCall, Rotation
from stowage.ports import crane_height_at_call, resolve_port_call_profile
from stowage.rules.catalogue import rule_freeze_v1


@pytest.mark.parametrize("row", _params(None)["rotations"], ids=lambda row: row["id"])
def test_active_route_populates_all_call_archetypes_without_inventing_crane_caps(row):
    with rule_freeze_v1(enabled=True):
        rotation, selected = _route(row["id"], _params(None), Random(18))
        assert selected["id"] == row["id"]
        for call in rotation.calls:
            assert call.archetype is not None
            profile = resolve_port_call_profile(call)
            assert call.archetype == profile.archetype
            assert call.max_on_deck_tiers is None
            if profile.profile_kind == "next_call_environment":
                assert profile.max_crane_height_tiers is None
                assert profile.quay_crane is None
                assert crane_height_at_call(call) is None


@pytest.mark.parametrize("seed", [0, 7, 1201])
def test_off_route_preserves_seeded_legacy_serialization_and_random_state(seed):
    params = _params(None)
    expected_rng = Random(seed)
    rows = params["rotations"]
    row = expected_rng.choices(rows, weights=[float(r["share"]["value"]) for r in rows], k=1)[0]
    expected = Rotation(service=row["id"], calls=[
        PortCall(locode=locode, index=index) for index, locode in enumerate(row["port_calls"])
    ])
    actual_rng = Random(seed)
    with rule_freeze_v1(enabled=False):
        actual, _ = _route(None, params, actual_rng)
    assert actual.model_dump_json() == expected.model_dump_json()
    assert actual_rng.getstate() == expected_rng.getstate()


def test_active_route_copies_caller_rotation_and_preserves_explicit_archetypes_and_caps():
    params = _params(None)
    row = params["rotations"][0]
    original = Rotation(service=row["id"], calls=[
        PortCall(locode=locode, index=index) for index, locode in enumerate(row["port_calls"])
    ])
    original.calls[1].archetype = "singapore_like"
    original.calls[1].max_on_deck_tiers = 3
    original.calls[1].eta_s = 1000
    before = original.model_dump_json()
    with rule_freeze_v1(enabled=True):
        actual, _ = _route(original, params, Random(18))
    assert original.model_dump_json() == before
    assert actual is not original
    assert all(call.archetype is not None for call in actual.calls)
    assert actual.calls[1] == original.calls[1]


def test_active_route_rejects_unresolvable_call():
    params = deepcopy(_params(None))
    params["rotations"][0]["port_calls"][1] = "ZZUNK"
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError, match="ZZUNK"):
        _route(params["rotations"][0]["id"], params, Random(18))


def test_active_route_validates_explicit_archetype_without_mutating_caller():
    row = _params(None)["rotations"][0]
    original = Rotation(service=row["id"], calls=[
        PortCall(locode=locode, index=index) for index, locode in enumerate(row["port_calls"])
    ])
    original.calls[1].archetype = "laem_chabang_like"
    before = original.model_dump_json()
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError, match="LOCODE"):
        _route(original, _params(None), Random(18))
    assert original.model_dump_json() == before


def test_off_route_preserves_caller_rotation_object_and_explicit_data():
    row = _params(None)["rotations"][0]
    original = Rotation(service=row["id"], calls=[
        PortCall(locode=locode, index=index) for index, locode in enumerate(row["port_calls"])
    ])
    original.calls[1].archetype = "singapore_like"
    original.calls[1].max_on_deck_tiers = 3
    before = original.model_dump_json()
    with rule_freeze_v1(enabled=False):
        actual, _ = _route(original, _params(None), Random(18))
    assert actual is original
    assert actual.model_dump_json() == before


def test_active_route_environment_call_cap_does_not_become_a_crane_rule():
    row = _params(None)["rotations"][0]
    original = Rotation(service=row["id"], calls=[
        PortCall(locode=locode, index=index) for index, locode in enumerate(row["port_calls"])
    ])
    original.calls[2].max_on_deck_tiers = 9
    with rule_freeze_v1(enabled=True):
        actual, _ = _route(original, _params(None), Random(18))
        assert actual.calls[2].archetype == "port_klang_like"
        assert actual.calls[2].max_on_deck_tiers == 9
        assert crane_height_at_call(actual.calls[2]) is None

"""A1-A3: parameter activation and one typed later-call capability lookup."""

import pytest

from stowage import ports
from stowage.model.port import PortCall, PortProfile, QuayCraneSpec
from stowage.model.yard import Yard
from stowage.ports import profiles
from stowage.rules.catalogue import rule_freeze_v1


def _laem_limits():
    profile = ports.load_port("laem_chabang_like")
    simulator = ports.load_simulator_params("laem_chabang_like")
    return (profile.max_crane_height_tiers, profile.yard_max_tiers,
            profile.equipment[0].stack_tiers, simulator.equipment_clearance_height_tiers,
            profile.quay_crane.max_on_deck_tiers, simulator.equipment_stacking_notation)


@pytest.mark.parametrize("enabled,expected", [
    (False, (None, 5, 5, 6, 7, "synthetic RTG; yard height 5")),
    (True, (8, 5, 5, 6, 8, "synthetic RTG; yard height 5")),
])
def test_laem_profile_and_simulator_follow_effective_activation(enabled, expected):
    with rule_freeze_v1(enabled=enabled):
        assert _laem_limits() == expected
        rules = {ref.rule_id for ref in ports.load_port("laem_chabang_like").port_rules}
        assert ("port.dg.refused_class" in rules) is enabled
        assert ("port.dg.direct_delivery" in rules) is enabled


def test_activation_toggle_refreshes_cached_profiles_and_simulator_in_one_process():
    with rule_freeze_v1(enabled=False):
        before = _laem_limits()
        assert before == (None, 5, 5, 6, 7, "synthetic RTG; yard height 5")
    with rule_freeze_v1(enabled=True):
        assert _laem_limits() == (8, 5, 5, 6, 8, "synthetic RTG; yard height 5")
    with rule_freeze_v1(enabled=False):
        assert _laem_limits() == before


@pytest.mark.parametrize("archetype,locode", [
    ("cat_lai_like", "VNSGN"), ("hong_kong_like", "HKHKG"), ("port_klang_like", "MYPKG"),
])
def test_resolved_density_environment_has_no_crane_limits(archetype, locode):
    call = PortCall(locode=locode, index=1, archetype=archetype)
    profile = ports.resolve_port_call_profile(call)
    assert profile.profile_kind == "next_call_environment"
    assert profile.max_crane_height_tiers is profile.quay_crane is profile.yard_max_tiers is None
    assert ports.crane_height_at_call(call) is None
    assert not ports.crane_limits_known_at_call(call)


@pytest.mark.parametrize("enabled,expected", [(False, 7), (True, 8)])
def test_resolver_uses_current_activation_for_later_laem_calls(enabled, expected):
    call = PortCall(locode="THLCH", index=1, archetype="laem_chabang_like")
    with rule_freeze_v1(enabled=enabled):
        profile = ports.resolve_port_call_profile(call)
        assert profile.quay_crane.max_on_deck_tiers == expected
        assert ports.crane_height_at_call(call) == expected
        assert ports.crane_limits_known_at_call(call)


def test_resolver_preserves_legacy_locode_density_lookup_and_unknowns():
    assert ports.resolve_port_call_profile(PortCall(locode="VNSGN", index=1)).water_density_kg_m3 == 1000
    unknown = PortCall(locode="ZZUNK", index=1)
    assert ports.resolve_port_call_profile(unknown) is None
    assert ports.crane_height_at_call(unknown) is None
    assert not ports.crane_limits_known_at_call(unknown)


def test_resolver_rejects_explicit_archetype_locode_mismatch():
    with pytest.raises(ValueError, match="LOCODE"):
        ports.resolve_port_call_profile(PortCall(locode="SGSIN", index=1, archetype="laem_chabang_like"))


def test_resolver_retains_declared_current_profile_instead_of_reloading_its_limits():
    current = ports.load_port("small_feeder")
    current.max_crane_height_tiers = None
    current.quay_crane.max_on_deck_tiers = 2
    call = PortCall(locode=current.locode, index=0)
    resolved = ports.resolve_port_call_profile(call, current_port=current)
    assert resolved == current
    assert ports.crane_height_at_call(call, current_port=current) == 2


def test_explicit_later_call_limit_is_retained_when_operating_limits_are_known():
    call = PortCall(locode="SGSIN", index=1, archetype="singapore_like", max_on_deck_tiers=3)
    assert ports.crane_height_at_call(call) == 3


def test_explicit_call_cap_cannot_raise_a_resolved_ports_governing_height(monkeypatch):
    profile = PortProfile(locode="THLCH", archetype="test.height", yard=Yard(id="test.yard", blocks=[]),
                          equipment=[], yard_max_tiers=8, max_crane_height_tiers=8,
                          quay_crane=QuayCraneSpec(count=1, max_on_deck_tiers=8))
    monkeypatch.setattr(profiles, "resolve_port_call_profile", lambda *args, **kwargs: profile)
    assert ports.crane_height_at_call(PortCall(locode="THLCH", index=1, max_on_deck_tiers=9)) == 8

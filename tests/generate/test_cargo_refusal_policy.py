"""D66 refusal screens actual handling calls, never untouched transit calls."""

import pytest

from stowage.generate import cargo_shipments
from stowage.model.port import PortCall, PortRuleRef, Rotation
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1


def rotation(*archetypes):
    return Rotation(service="refusal_policy_test", calls=[
        PortCall(index=index, locode=load_port(archetype).locode, archetype=archetype)
        for index, archetype in enumerate(archetypes)
    ])


@pytest.mark.parametrize("archetypes,expected", [
    (("laem_chabang_like", "singapore_like"), [0]),
    (("singapore_like", "laem_chabang_like"), [1]),
    (("laem_chabang_like", "laem_chabang_like"), [0, 1]),
    (("singapore_like", "laem_chabang_like", "cat_lai_like"), []),
])
def test_primary_seven_refused_at_pol_or_pod_but_not_untouched_through_calls(archetypes, expected):
    with rule_freeze_v1(enabled=True):
        route = rotation(*archetypes)
        assert cargo_shipments.refused_handling_calls(route, 0, route.calls[-1].locode, "7") == expected


@pytest.mark.parametrize("primary", [None, "1.4S", "3", "6.1"])
def test_only_primary_refused_class_is_considered(primary):
    with rule_freeze_v1(enabled=True):
        route = rotation("laem_chabang_like", "laem_chabang_like")
        assert cargo_shipments.refused_handling_calls(route, 0, route.calls[-1].locode, primary) == []


def test_onboard_origin_is_pol_and_current_untouched_call_is_not_handling():
    with rule_freeze_v1(enabled=True):
        route = rotation("singapore_like", "laem_chabang_like", "cat_lai_like")
        assert cargo_shipments.refused_handling_calls(route, 1, "VNSGN", "7", onboard=True) == []
        origin_refused = rotation("laem_chabang_like", "singapore_like", "cat_lai_like")
        assert cargo_shipments.refused_handling_calls(origin_refused, 1, "VNSGN", "7", onboard=True) == [0]


def test_pod_is_first_future_occurrence_not_an_earlier_or_later_repeat(monkeypatch):
    with rule_freeze_v1(enabled=True):
        route = rotation("laem_chabang_like", "singapore_like", "laem_chabang_like", "laem_chabang_like")
        from stowage.ports import resolve_port_call_profile

        visited = []

        def recording_resolver(call, **kwargs):
            visited.append(call.index)
            return resolve_port_call_profile(call, **kwargs)

        monkeypatch.setattr(cargo_shipments, "resolve_port_call_profile", recording_resolver)
        assert cargo_shipments.refused_handling_calls(route, 1, "THLCH", "7") == [2]
        assert visited == [1, 2]


@pytest.mark.parametrize("primary,expected", [("1.4S", [0]), ("1.1D", [0]), ("3", [])])
def test_legacy_call_can_use_current_profile_and_parent_class_matching(primary, expected):
    with rule_freeze_v1(enabled=True):
        port = load_port("laem_chabang_like")
        port.port_rules = [PortRuleRef(rule_id="port.dg.refused_class", params={"classes": ["1"]})]
        route = rotation("laem_chabang_like", "singapore_like")
        route.calls[0].archetype = None
        assert cargo_shipments.refused_handling_calls(
            route, 0, "SGSIN", primary, current_port=port,
        ) == expected


def test_unknown_handling_profile_fails_closed():
    route = Rotation(service="unknown", calls=[
        PortCall(index=0, locode="ZZZZZ"), PortCall(index=1, locode="SGSIN", archetype="singapore_like"),
    ])
    with pytest.raises(ValueError, match="Cannot resolve handling port"):
        cargo_shipments.refused_handling_calls(route, 0, "SGSIN", "7")


@pytest.mark.parametrize("classes", [None, "7", [True], [None]])
def test_malformed_refusal_policy_fails_closed(classes):
    with rule_freeze_v1(enabled=True):
        port = load_port("laem_chabang_like")
        port.port_rules = [PortRuleRef(rule_id="port.dg.refused_class", params={"classes": classes})]
        route = rotation("laem_chabang_like", "singapore_like")
        route.calls[0].archetype = None
        with pytest.raises(ValueError, match="requires classes"):
            cargo_shipments.refused_handling_calls(route, 0, "SGSIN", "7", current_port=port)


def test_missing_future_pod_fails_explicitly():
    route = rotation("singapore_like", "cat_lai_like")
    with pytest.raises(ValueError, match="POD must resolve to a future call"):
        cargo_shipments.refused_handling_calls(route, 0, "THLCH", "7")

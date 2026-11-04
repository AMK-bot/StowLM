"""A4/A6 cargo behavior uses port policy and exact masses only after activation."""
from copy import deepcopy

import pytest

from stowage.generate import cargo
from stowage.model.cargo import DangerousGoods
from stowage.model.fixtures import build_instance
from stowage.model.port import PortRuleRef
from stowage.params import load_param_file
from stowage.ports import profiles
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1
from stowage.rules.imdg.dgl import entries_for_un


def _profile(*, dg=False):
    params = deepcopy(load_param_file("cargo", "feeder_export"))
    equipment = deepcopy(next(row for row in params["equipment"] if row["id"] == "dry_20"))
    equipment["share"]["value"] = 1
    equipment["empty_share"]["value"] = 0
    params["equipment"] = [equipment]
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = int(dg)
    params["oog"]["share_of_loaded_compatible_boxes"]["value"] = 0
    params["special_stowage"]["share_of_loaded_boxes"]["value"] = 0
    # One supported POD isolates category and current-port policy behavior.
    params["rotations"][0]["pods"] = params["rotations"][0]["pods"][:1]
    return params


@pytest.mark.parametrize("enabled", [False, True])
def test_categories_distinguish_exact_mass_only_when_activated(monkeypatch, enabled):
    masses = (15000.0, 15001.0, 15000.0)
    draws = iter(masses)
    monkeypatch.setattr(cargo, "_loaded_gross_mass", lambda *args: next(draws))
    with rule_freeze_v1(enabled=enabled):
        boxes = cargo.generate_load_list("straits", 0, 3, seed=1729, params=_profile())
        labels = [load_catalogue().weight_classes.classify(mass) for mass in masses]
    assert [box.gross_mass_kg for box in boxes] == list(masses)
    assert [box.weight_class for box in boxes] == labels
    assert labels[0] == labels[1]
    assert boxes[0].category == boxes[2].category
    assert not any(box.direct_delivery for box in boxes)
    if enabled:
        assert boxes[0].category != boxes[1].category
    else:
        assert boxes[0].category == boxes[1].category
        assert boxes[0].category == f"{boxes[0].pod}:22G1:{labels[0]}:loaded"


def _fixed_dg(un, subsidiary_risks=()):
    entry = entries_for_un(un)[0]
    return DangerousGoods(
        un_number=f"{un:04d}",
        imdg_class=entry.hazard_class + (entry.compatibility_group or ""),
        subsidiary_risks=list(subsidiary_risks), packing_group=entry.packing_group,
        stowage_category=entry.stowage_category,
    )


def _port_delivery_policy(monkeypatch, classes, exemptions):
    original_load = profiles.load_port

    def load(archetype):
        profile = original_load(archetype).model_copy(deep=True)
        # Refusal and placement are separate tests; retain only delivery policy.
        profile.port_rules = []
        if profile.locode == "THLCH" and classes is not None:
            profile.port_rules = [PortRuleRef(rule_id="port.dg.direct_delivery", params={
                "classes": classes, "exempt_divisions": exemptions,
            })]
        return profile

    monkeypatch.setattr(profiles, "load_port", load)


@pytest.mark.parametrize("un,subsidiary,classes,exemptions,expected", [
    (12, (), ["1"], [], True),
    (12, (), ["1"], ["1.4S"], False),
    (12, (), ["1"], ["1.4"], False),
    (1263, ("1.4S",), ["1"], [], False),
    (1263, (), ["3"], [], True),
    (12, (), None, [], False),
], ids=["class-one-includes-1.4S", "explicit-1.4S-exemption", "division-exemption",
        "subsidiary-does-not-trigger", "primary-class-three", "no-policy"])
@pytest.mark.parametrize("enabled", [False, True])
def test_direct_delivery_matches_current_port_primary_class_policy(
    monkeypatch, enabled, un, subsidiary, classes, exemptions, expected,
):
    _port_delivery_policy(monkeypatch, classes, exemptions)
    dg = _fixed_dg(un, subsidiary)
    monkeypatch.setattr(cargo, "_dg", lambda *args, **kwargs: dg.model_copy(deep=True))
    with rule_freeze_v1(enabled=enabled):
        box, = cargo.generate_load_list("straits", 0, 1, seed=1729, params=_profile(dg=True))
        current = profiles.load_port("laem_chabang_like")
        assert current.requires_direct_delivery(box.dg.imdg_class) is expected
    assert box.direct_delivery is (enabled and expected)
    assert box.dg.imdg_class == dg.imdg_class
    # This generator returns bookings without any placement; yard inventory is
    # separately constructed by L3/L4, which must omit direct-delivery exports.
    assert not any("position" in field or field.startswith("yard") for field in box.model_dump())


def test_direct_delivery_uses_current_call_instead_of_the_loading_port(monkeypatch):
    vessel = build_instance().vessel
    _port_delivery_policy(monkeypatch, ["1"], [])
    dg = _fixed_dg(12)
    monkeypatch.setattr(cargo, "_dg", lambda *args, **kwargs: dg.model_copy(deep=True))
    params = _profile(dg=True)
    params["rotations"][0]["pods"] = [deepcopy(load_param_file("cargo", "feeder_export")["rotations"][0]["pods"][1])]
    with rule_freeze_v1(enabled=True):
        # Current SGSIN has no policy, despite earlier POL THLCH having one.
        box, = cargo.generate_onboard_cargo(vessel, "straits", 1, n_boxes=1, seed=1729, params=params)
    assert box.pol == "THLCH"
    assert box.direct_delivery is False

"""C3/C4 activation, physical-cell kills, and the represented guided holds."""
import pytest

from stowage.checker.rules_special import killed_slots, special_violations
from stowage.model import Placement, ShipState
from stowage.model.cargo import OutOfGauge, SpecialStowage
from stowage.rules.catalogue import rule_freeze_v1
from tests.checker.test_rules_fixtures import build_instance


def scenario(oog=None, slot="0010182", neighbour=None, *, long=False):
    instance = build_instance()
    for box in instance.containers:
        box.oog, box.special_stowage, box.dg = None, [], None
    box = instance.containers[0 if long else 1]
    box.oog = oog
    other = instance.containers[2]
    placements = [Placement(container_id=box.id, slot=slot)]
    if neighbour:
        placements.append(Placement(container_id=other.id, slot=neighbour))
    ship = ShipState(vessel_id=instance.vessel.id, placements=placements)
    return instance, ship, box, other


def violations(instance, ship, **kwargs):
    return special_violations(instance, ship, call_index=0, **kwargs)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("deck", [False, True])
def test_height_allowed_in_hold_or_deck_and_kills_directly_above(enabled, deck):
    tier, above = (82, 84) if deck else (2, 4)
    slot, neighbour = f"00101{tier:02}", f"00101{above:02}"
    instance, ship, _, _ = scenario(OutOfGauge(over_height_m=.01), slot)
    with rule_freeze_v1(enabled=enabled):
        assert not violations(instance, ship)
        assert killed_slots(instance, ship) == ({neighbour} if enabled else set())
        ship.placements.append(Placement(container_id=instance.containers[2].id, slot=neighbour))
        found = [v for v in violations(instance, ship) if v.rule_id == "expert.e9.over_height"]
        assert found and all(v.severity == "hard" for v in found)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("oog,slot,neighbour", [
    (OutOfGauge(over_width_port_m=.01), "0010182", "0010282"),
    (OutOfGauge(over_width_starboard_m=.01), "0010282", "0010182"),
    (OutOfGauge(over_length_fore_m=.01), "0010182", "0030182"),
    (OutOfGauge(over_length_aft_m=.01), "0030182", "0010182"),
])
def test_adjacent_kill_is_binding_even_when_small_overhang_does_not_intersect(enabled, oog, slot, neighbour):
    instance, ship, _, candidate = scenario(oog, slot, neighbour)
    with rule_freeze_v1(enabled=enabled):
        found = [v for v in violations(instance, ship) if v.rule_id == "expert.e9.over_width_length"]
        assert bool(found) == enabled
        expected = {neighbour, neighbour[:5] + "84"}
        assert killed_slots(instance, ship) == (expected if enabled else set())
        focused = violations(instance, ship, focus_container_id=candidate.id)
        assert any(v.rule_id == "expert.e9.over_width_length" for v in focused) == enabled


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("oog", [OutOfGauge(over_width_port_m=.01), OutOfGauge(over_length_fore_m=.01)])
def test_guided_hold_rejects_horizontal_oog_even_with_empty_neighbours(enabled, oog):
    instance, ship, _, _ = scenario(oog, "0010102")
    with rule_freeze_v1(enabled=enabled):
        found = [v for v in violations(instance, ship) if v.rule_id == "expert.e9.over_width_length"]
        assert bool(found) == enabled
        if enabled:
            assert "cell guides" in found[0].message


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("oog,expected", [
    (OutOfGauge(over_height_m=.1), {"0010184", "0030184"}),
    (OutOfGauge(over_width_port_m=.01), {"0010282", "0030282", "0010284", "0030284"}),
])
def test_long_box_kills_both_physical_support_neighbours(enabled, oog, expected):
    instance, ship, _, _ = scenario(oog, "0020182", long=True)
    with rule_freeze_v1(enabled=enabled):
        assert not violations(instance, ship)
        assert killed_slots(instance, ship) == (expected if enabled else set())


@pytest.mark.parametrize("enabled", [False, True])
def test_short_box_kill_blocks_long_neighbour(enabled):
    instance, ship, _, other = scenario(OutOfGauge(over_width_port_m=.01))
    other.length_ft = 40
    ship.placements.append(Placement(container_id=other.id, slot="0020282"))
    with rule_freeze_v1(enabled=enabled):
        found = [v for v in violations(instance, ship) if v.rule_id == "expert.e9.over_width_length"]
        assert bool(found) == enabled


def test_killed_physical_cells_are_counted_once_across_owners():
    instance, ship, first, other = scenario(OutOfGauge(over_width_port_m=.01), "0010184", "0010282")
    other.oog = OutOfGauge(over_height_m=.01)
    with rule_freeze_v1():
        assert killed_slots(instance, ship) == {"0010284"}


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("booking,slot,neighbour,rule", [
    ("under_deck", "0010182", None, "expert.e8.under_deck"),
    ("on_deck", "0010102", None, "expert.e8.on_deck"),
    ("top_stowage", "0010182", "0010184", "expert.e8.top_stowage"),
    ("away_from_heat", "0010102", None, "expert.e8.away_from_heat"),
])
def test_special_stowage_stays_binding_and_never_kills_slots(enabled, booking, slot, neighbour, rule):
    instance, ship, box, _ = scenario(slot=slot, neighbour=neighbour)
    box.special_stowage = [SpecialStowage(booking)]
    if booking == "away_from_heat":
        instance.rule_overrides[rule] = {"minimum_clearance": 1000}
    with rule_freeze_v1(enabled=enabled):
        found = [v for v in violations(instance, ship) if v.rule_id == rule]
        assert found and all(v.severity == "hard" for v in found)
        assert killed_slots(instance, ship) == set()


def test_large_overhang_keeps_geometric_collision_checks_beyond_adjacent_slot():
    instance, ship, _, _ = scenario(OutOfGauge(over_width_port_m=10), "0010182", "0030282")
    # A diagonal neighbour is beyond the immediate row/bay neighbours, but its
    # envelope still intersects OOG extending in both horizontal axes.
    instance.containers[1].oog.over_length_fore_m = 10
    with rule_freeze_v1():
        assert "0030282" in killed_slots(instance, ship)
        assert any(v.rule_id == "expert.e9.over_width_length" and len(v.containers) == 2
                   for v in violations(instance, ship))


@pytest.mark.parametrize("enabled", [False, True])
def test_report_exposes_soft_counts_only_when_activated(enabled):
    from stowage.checker.rules import check_static_rules

    instance, ship, box, _ = scenario(OutOfGauge(over_height_m=.1), "0020182", long=True)
    box.gross_mass_kg = 20000
    with rule_freeze_v1(enabled=enabled):
        report = check_static_rules(instance, ship, call_index=0)
    if enabled:
        assert report.metrics['killed_slots'] == 2.0
        assert report.metrics['heavy_on_deck_count'] == 1.0
    else:
        assert 'killed_slots' not in report.metrics
        assert 'heavy_on_deck_count' not in report.metrics

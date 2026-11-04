"""Analytic D64 departure reserve and reference-screen attribution, no campaign."""
import math
from types import SimpleNamespace as S

import pytest

from stowage.generate import random_export
from stowage.rules.catalogue import rule_freeze_v1


def _vessel():
    tanks = [S(id=identifier, adjustable=True, kind="ballast", capacity_m3=capacity,
               content_density_kg_m3=1000.) for identifier, capacity in
             (("custom-port", 50.), ("custom-starboard", 50.),
              ("forward-port", 200.), ("forward-starboard", 200.))]
    tanks.append(S(id="actual-fuel", adjustable=False, kind="fuel_oil",
                   capacity_m3=50., content_density_kg_m3=1000.))
    return S(tanks=tanks, sources=[], tank_pairs=[S(role="anti_heeling",
        port_tank="custom-port", starboard_tank="custom-starboard")])


def _policy(**kwargs):
    return random_export.ExportPolicy(100, 1_000_000., 300_000., 48_500., 0.,
        500_000., 1., 2500., **kwargs)


def test_departure_budget_reserves_the_pair_and_excludes_its_unused_capacity():
    policy = _policy(mandatory_ballast_kg=50_000., mandatory_ballast_capacity_kg=100_000.)
    assert policy.ballast_kg(1_000_000.) == 50_000.
    assert policy.ballast_kg(0.) == 450_000.
    audit = policy.audit(200_000., 80.)
    assert audit["mandatory_ballast_reserve_kg"] == 50_000.
    assert audit["operating_ballast_capacity_kg"] == 450_000.
    assert audit["departure_displacement_kg"] == pytest.approx(930_000.)


def test_full_booking_preserves_explicit_pair_half_and_fills_only_other_ballast(monkeypatch):
    vessel = _vessel()
    policy = _policy(mandatory_ballast_kg=50_000., mandatory_ballast_capacity_kg=100_000.)
    monkeypatch.setattr(random_export, "full_export_policy", lambda *args: policy)
    boxes = [S(gross_mass_kg=2500.) for _ in range(80)]
    assert policy.is_export_count(len(boxes))
    with rule_freeze_v1(enabled=True):
        masses = random_export.booking_tank_masses(vessel, boxes, {})
    assert masses["custom-port"] == masses["custom-starboard"] == 25_000.
    assert masses["actual-fuel"] == 48_500.
    assert masses["forward-port"] == masses["forward-starboard"]
    assert sum(masses[tank.id] for tank in vessel.tanks if tank.adjustable) == pytest.approx(
        policy.ballast_kg(200_000.))
    assert masses["forward-port"] == pytest.approx(165_750.)


def test_off_keeps_the_original_common_fraction_including_pair_members(monkeypatch):
    vessel = _vessel()
    policy = _policy()
    monkeypatch.setattr(random_export, "full_export_policy", lambda *args: policy)
    boxes = [S(gross_mass_kg=2500.) for _ in range(80)]
    with rule_freeze_v1(enabled=False):
        masses = random_export.booking_tank_masses(vessel, boxes, {})
    fraction = policy.ballast_kg(200_000.) / policy.ballast_capacity_kg
    assert all(masses[tank.id] == fraction * tank.capacity_m3 * tank.content_density_kg_m3
               for tank in vessel.tanks if tank.adjustable)


def test_reference_screen_reports_policy_gaps_without_claiming_actual_ship_overload(monkeypatch):
    policy = random_export.ExportPolicy(100, 10_000_000., 3_000_000., 500_000., 0.,
                                       3_000_000., 1., 50_000.)
    parent, region = S(cells=[None] * 100), S(cells=[None] * 10)
    monkeypatch.setattr(random_export, "full_export_policy", lambda *args: policy)
    # Eight stub boxes keep the reference TEU fraction (0.8) inside its band,
    # so only the cargo-mass component fails; the mass is the same 600 t.
    boxes = [S(length_ft=20, gross_mass_kg=75_000.) for _ in range(8)]
    audit = random_export.export_reference_audit(region, boxes, {}, full_vessel=parent)
    assert audit["reference_scale"] == .1
    assert audit["actual_booking_cargo_mass_kg"] == 600_000.
    assert audit["cargo_mass_kg"] == 6_000_000.
    assert audit["design_displacement_fraction"] < 1.
    assert audit["cargo_mass_fraction"] > .9
    assert audit["failure_components"] == ["reference_cargo_mass_fraction"]
    assert audit["cargo_mass_upper_gap_kg"] > 0
    assert audit["actual_ship_displacement_checked"] is False
    assert random_export.export_rejections(region, boxes, {}, full_vessel=parent) == [
        "iscode.loading.displacement"]


# D77: booking target two SD inside the [0.75, 0.90] cargo band; C-condition ballast floor.

def test_d77_target_aims_inside_the_band_and_defaults_keep_d37():
    # Mass-limited stub: 10 t boxes, so the TEU term (85) never binds.
    legacy = random_export.ExportPolicy(100, 1_000_000., 300_000., 48_500., 0., 500_000., 1., 10_000.)
    d77 = random_export.ExportPolicy(100, 1_000_000., 300_000., 48_500., 0., 500_000., 1., 10_000.,
                                     target_cargo_fraction=.815)
    teu = math.floor(.85 * 100 / 1.)
    budget = legacy.cargo_budget_kg(teu * 10_000.)
    assert legacy.target_count == min(teu, math.floor(.90 * budget / 10_000.))

    assert d77.target_count == min(teu, math.floor(.815 * budget / 10_000.))
    assert d77.target_count < legacy.target_count

    def own(policy, count):
        return count * 1e4 / policy.cargo_budget_kg(count * 1e4)
    # D37 sat near the band's top edge; D77 sits inside it, below the edge.
    assert own(legacy, legacy.target_count) > own(d77, d77.target_count) >= .75


def test_d77_departure_ballast_never_below_the_c_condition_floor():
    policy = _policy(mandatory_ballast_kg=50_000., mandatory_ballast_capacity_kg=100_000.,
                     minimum_departure_ballast_kg=300_000.)
    # Heavy booking: without the floor only the HEEL half would remain.
    assert policy.ballast_kg(1_000_000.) == 300_000.
    assert policy.audit(1_000_000., 80.)["minimum_departure_ballast_kg"] == 300_000.
    # Never beyond operating capacity.
    capped = _policy(mandatory_ballast_kg=50_000., mandatory_ballast_capacity_kg=100_000.,
                     minimum_departure_ballast_kg=10_000_000.)
    assert capped.ballast_kg(1_000_000.) == capped.operating_ballast_capacity_kg


@pytest.mark.parametrize("ship", ["feeder_500", "feeder_1700"])
def test_d77_real_full_policy_reads_lightest_c_condition_on_and_nothing_off(ship):
    import tomllib
    from stowage.params import load_param_file
    from stowage.vessel.fleet import _parameter_bytes, load_vessel
    from stowage.vessel.fleet_manual import standard_manual_conditions

    params = load_param_file("cargo", "dg_stress")
    with rule_freeze_v1(enabled=True):
        vessel = load_vessel(ship)
        policy = random_export.full_export_policy(vessel, params)
        adjustable = {tank.id for tank in vessel.tanks if tank.adjustable}
        floor = min(sum(m for tid, m in recipe["departure_tank_masses_kg"].items() if tid in adjustable)
                    for recipe in standard_manual_conditions(vessel, tomllib.loads(_parameter_bytes(ship).decode())))
    assert policy.minimum_departure_ballast_kg == pytest.approx(floor) and floor > 0
    assert policy.target_cargo_fraction == random_export.D77_TARGET_CARGO_FRACTION
    mass = policy.target_count * policy.mean_mass_kg
    assert policy.ballast_kg(mass) >= floor
    own = policy.audit(mass, policy.target_count * policy.mean_teu)["cargo_mass_fraction"]
    assert .75 <= own < .90
    with rule_freeze_v1(enabled=False):
        off = random_export.full_export_policy(load_vessel(ship), params)
    assert off.target_cargo_fraction is None and off.minimum_departure_ballast_kg == 0.

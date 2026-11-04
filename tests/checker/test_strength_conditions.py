"""Checker behavior on fixture-builder variants; fleet geometry is synthetic."""
from __future__ import annotations

import pytest

from stowage.checker.strength import check_strength
from stowage.model import Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.tanks import build_tanks
from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id


@pytest.fixture(scope="module")
def feeder():
    from stowage.checker.strength_limits import permissible_stations

    return load_vessel("feeder_500", with_hull=True)


def _condition(feeder):
    instance = build_instance()
    instance.vessel = feeder.model_copy(deep=True)
    instance.onboard = ShipState(vessel_id=feeder.id)
    return instance


def test_in_port_loading_fsm_approximation_is_explicit():
    import inspect
    from stowage.checker import stability_loading

    # Audit the required disclosure without changing the existing FSM physics.
    source = inspect.getsource(stability_loading)
    assert 'APPROXIMATION: in-port FSM' in source
    assert 'consumables use maximum FSM; ballast uses actual-fill FSM' in source
    assert 'tanks being worked' in source


def test_missing_hull_and_stations_are_hard_failures():
    instance = build_instance()
    instance.vessel.hull = None
    report = check_strength(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id == "checker.missing_vessel_data"
    instance = build_instance()
    instance.vessel.hull.stations = []
    report = check_strength(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert report.violations[0].severity == "hard"


def test_complete_fleet_condition_runs_deterministically_and_closes(feeder):
    instance = _condition(feeder)
    report = check_strength(instance, instance.onboard, call_index=0)
    assert report.ran, report.error
    assert not report.violations
    assert report == check_strength(instance, instance.onboard, call_index=0)
    assert "max_sf_ratio" in report.metrics
    assert "max_bm_ratio" in report.metrics
    assert report.margins["sf_ratio"] == pytest.approx(1-report.metrics["max_sf_ratio"])
    assert report.margins["bm_ratio"] == pytest.approx(1-report.metrics["max_bm_ratio"])
    assert report.metrics["buoyant_x_min_m"] < 0
    assert report.metrics["buoyant_x_max_m"] > feeder.hull.lpp_m
    assert abs(report.metrics["end_shear_n"]) < 1e-4
    assert abs(report.metrics["end_bending_nm"]) < .01
    # AP/FP are interior cuts of the KCS hull, not free ends.
    assert abs(report.metrics["sf_n@0"]) > 1


def test_midship_overload_fails_sagging_limit(feeder):
    instance = _condition(feeder)
    # APPROXIMATION: intentionally artificial proof condition: a single very
    # heavy box at midship, with generous shear/hogging but a tight sagging limit.
    cell = min(instance.vessel.cells, key=lambda c: abs(c.lcg_m-feeder.hull.lpp_m/2))
    box = instance.containers[0]
    box.length_ft = 20
    box.gross_mass_kg = 2_000_000
    instance.onboard.placements = [Placement(slot=cell.id, container_id=box.id)]
    for station in instance.vessel.hull.stations:
        station.max_shear_n = 1e12
        station.min_shear_n = -1e12
        station.max_bending_nm = 1e12
        station.min_bending_nm = -1e5
    report = check_strength(instance, instance.onboard, call_index=2)
    assert report.ran, report.error
    assert report.metrics["max_bm_ratio"] > 1
    assert any(v.rule_id == "iacs.s11a.longitudinal_strength" and
               v.call_index == 2 and v.measured < 0 and v.limit < 0
               for v in report.violations)


def test_tank_weight_and_trim_are_included(feeder):
    instance = _condition(feeder)
    tank = instance.vessel.tanks[0]
    added = tank.capacity_m3*tank.content_density_kg_m3*.4
    instance.onboard.tank_masses_kg = {tank.id: added}
    report = check_strength(instance, instance.onboard, call_index=0)
    assert report.ran, report.error
    fixed = sum(load[3] for load in instance.vessel.fixed_loads)
    assert report.metrics["displacement_kg"] == pytest.approx(feeder.hull.lightship_mass_kg+fixed+added)
    assert report.metrics["closure_force_fraction"] < 1e-6
    assert report.metrics["end_shear_n"] == pytest.approx(0, abs=1e-4)
    assert report.metrics["end_bending_nm"] == pytest.approx(0, abs=.01)


def test_hull_following_liquid_loads_run_in_production_condition(feeder):
    instance = _condition(feeder)
    params = load_ship_params("feeder_500")
    shaped_ids = {t["id"] for t in params["tanks"]
                  if t["geometry"].get("shape") in ("hull_sections", "hull_intersection")}
    assert shaped_ids, "production regression must exercise hull-following tanks"
    tank = next(t for t in feeder.tanks if t.id in shaped_ids)
    added = .4*tank.capacity_m3*tank.content_density_kg_m3
    instance.onboard.tank_masses_kg = {tank.id: added}
    report = check_strength(instance, instance.onboard, call_index=0)
    assert report.ran, report.error
    fixed = sum(load[3] for load in feeder.fixed_loads)
    assert report.metrics["displacement_kg"] == pytest.approx(feeder.hull.lightship_mass_kg+fixed+added)
    assert report.metrics["closure_force_fraction"] < 1e-6
    assert report.metrics["closure_moment_fraction"] < 1e-6
    assert report.metrics["end_shear_n"] == pytest.approx(0, abs=1e-4)
    assert report.metrics["end_bending_nm"] == pytest.approx(0, abs=.01)


def test_full_homogeneous_load_reports_physical_utilization_margins(feeder):
    instance = _condition(feeder)
    empty = check_strength(instance, instance.onboard, call_index=0)
    template = instance.containers[1]
    boxes, placements = [], []
    for serial, cell in enumerate((c for c in feeder.cells if c.accepts_20), 200000):
        cid = make_container_id(SYNTHETIC_OWNER_CODES[0], serial)
        boxes.append(template.model_copy(update=dict(id=cid, gross_mass_kg=10_000,
            length_ft=20, height_m=2.591, dg=None, special_stowage=[], oog=None)))
        placements.append(Placement(slot=cell.id, container_id=cid))
    instance.containers = boxes
    instance.onboard.placements = placements
    full = check_strength(instance, instance.onboard, call_index=0)
    assert full.ran, full.error
    # Limits are design-derived, with no required utilization or minimum
    # reward sensitivity. Verify the physical report and margin identities.
    for kind,metric in (("sf","sf_n"),("bm","bm_nm")):
        assert full.margins[f"{kind}_ratio"] == pytest.approx(1-full.metrics[f"max_{kind}_ratio"])
        assert any(abs(full.metrics[f"{metric}@{s.x_m:.12g}"]-
                       empty.metrics[f"{metric}@{s.x_m:.12g}"]) > 1.
                   for s in feeder.hull.stations)
    assert not any("closure" in v.message.lower() for v in full.violations)

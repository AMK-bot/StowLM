"""Physical changes must arise from the cargo moments, never UI constants."""
import json
import math

import pytest

from stowage.model import CheckReport, PartReport, Placement, Violation
from stowage.model.fixtures import build_instance
from experiments.mvp.pipeline.sandbox import physics


def _instance():
    instance = build_instance()
    hull = instance.vessel.hull
    # Synthetic table broad enough to include this fixture's actual mass.
    rows = [row.model_copy(update={"displacement_kg": mass})
            for row, mass in zip(hull.hydrostatics, (2_000_000., 6_000_000., 10_000_000.))]
    hull = hull.model_copy(update={"hydrostatics": rows})
    instance = instance.model_copy(update={"vessel": instance.vessel.model_copy(update={"hull": hull})})
    return instance


def _ship(instance, slot):
    return instance.onboard.model_copy(update={"tank_masses_kg": {}, "placements": [
        Placement(container_id=instance.containers[1].id, slot=slot)]})


def test_moving_high_changes_computed_gm_and_no_mass():
    instance = _instance()
    low = physics._rough_frame(instance, _ship(instance, "0010102"))
    high = physics._rough_frame(instance, _ship(instance, "0010182"))
    assert high["gm_m"] < low["gm_m"]
    assert high["displacement_t"] == low["displacement_t"]
    assert low["stability_passed"] is None
    assert high["strength_passed"] is None


def test_moving_across_ship_changes_list_direction():
    instance = _instance()
    port = physics._rough_frame(instance, _ship(instance, "0010102"))
    starboard = physics._rough_frame(instance, _ship(instance, "0010202"))
    assert port["heel_deg"] < 0 < starboard["heel_deg"]
    assert starboard["gm_m"] == pytest.approx(port["gm_m"])


def test_fast_screen_keeps_static_checker_evidence(monkeypatch):
    instance = _instance()
    violation = Violation(rule_id="expert.stack.weight", severity="hard", call_index=0,
        message="Synthetic stack mass test", measured=61000., limit=60000., unit="kg",
        containers=[instance.containers[1].id])
    monkeypatch.setattr(physics, "check_static_rules", lambda *a, **k: PartReport(name="rules", violations=[violation]))
    monkeypatch.setattr(physics, "_loading_policy", lambda i: (-100., 100., 1., 100_000_000.))
    broken, gauges = physics.fast_check(instance, _ship(instance, "0010102"))
    assert broken[0]["measured"] == 61000.
    assert broken[0]["limit"] == 60000.
    assert broken[0]["boxes"] == violation.containers
    assert "approximation" not in broken[0]
    assert gauges["label"] == "Fast screen, full check running"
    assert gauges["approximation"] is True
    assert gauges["frames"][0]["error"] is None
    json.dumps(gauges, allow_nan=False)


def test_out_of_table_is_unavailable_instead_of_clamped():
    instance = build_instance()
    with pytest.raises(ValueError, match="outside declared table coverage"):
        physics._rough_frame(instance, _ship(instance, "0010102"))


def test_full_gauges_use_departure_metrics_and_preserve_overall_failure():
    instance = _instance()
    departure = PartReport(name="stability", metrics={
        "gm_m": .71, "list_rad": math.radians(1.2), "displacement_kg": 5_000_000.,
        "trim_m": .2, "condition.phase: departure": 1.})
    aggregate = departure.model_copy(deep=True)
    aggregate.violations.append(Violation(rule_id="iscode.loading.gm", severity="hard", call_index=1,
        message="Synthetic arrival failure", measured=.1, limit=.15))
    strength = PartReport(name="strength", metrics={"max_sf_ratio": .3, "max_bm_ratio": .4,
        "sf_n@0": 123., "bm_nm@0": 456., "condition.phase: departure": 1.})
    report = CheckReport(passed=False, parts={"stability": aggregate, "strength": strength,
        "condition:departure:stability": departure, "condition:departure:strength": strength})
    gauges = physics.full_gauges(instance, _ship(instance, "0010102"), report)
    frame = gauges["frames"][0]
    assert gauges["checker_passed"] is False
    assert frame["stability_passed"] is True
    assert frame["gm_m"] == .71
    assert frame["heel_deg"] == pytest.approx(1.2)
    assert frame["sf_n"][0] == 123.
    assert frame["bm_nm"][0] == 456.
    assert frame["displacement_t"] == 5000.
    assert frame["gz"] == []
    assert frame["gz_error"]
    json.dumps(gauges, allow_nan=False)


def test_absent_parts_never_pass():
    instance = _instance()
    frame = physics.full_gauges(instance, _ship(instance, "0010102"), CheckReport(passed=False))["frames"][0]
    assert frame["stability_passed"] is False
    assert frame["strength_passed"] is False
    assert frame["error"]

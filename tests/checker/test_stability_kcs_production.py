"""Production KCS departures exercise real fleet loading, without fake moments."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from stowage.checker.stability_loading import loading_condition
from tests.checker.test_stability_policy_fixtures import supply_loading_policy

_SCRIPT = Path(__file__).resolve().parents[2] / "experiments/stability/kcs_departures.py"
_SPEC = importlib.util.spec_from_file_location("stability_kcs_departures", _SCRIPT)
_departures = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_departures)


@pytest.mark.parametrize("name", _departures.FLEET)
def test_production_kcs_homogeneous_departure_runs_and_selects_d21(name, monkeypatch):
    original_builder = _departures.build_departure
    def declared_departure(*args, **kwargs):
        instance, summary = original_builder(*args, **kwargs)
        return supply_loading_policy(instance), summary
    # The historical experiment predates mandatory independent loading policy.
    # Supply explicit synthetic test policy without editing its production data.
    monkeypatch.setattr(_departures, 'build_departure', declared_departure)
    result = _departures.run_departure(name)
    assert result["ran"], result["error"]
    m = result["metrics"]
    assert m["criteria_part_b_2_3_active"] == 1.
    assert m["criteria_part_a_2_2_1_to_3_active"] == 0.
    assert m["criteria_initial_gm_active"] == m["criteria_weather_active"] == 1.
    assert m["equilibrium_residual"] < 2e-6
    assert m["angle_flooding_rad"] < 1.5707963267948966
    assert m["curve_max_heel_rad"] < 1.570621793869697
    assert m["flooding_openings_declared"] == 1.
    # B 2.3 total positive area can stop at vanishing stability, but the
    # separate 0–40 and 30–40 integrals still need min(40 degrees, phi_f).
    assert m["curve_max_heel_rad"] >= min(.6981317007977318, m["angle_flooding_rad"])-1e-10
    assert len(result["criteria"]) == 11
    assert all(isinstance(criterion["passes"], bool)
               for criterion in result["criteria"].values())
    assert not any(rule.startswith("checker.") for rule in result["violation_ids"])
    # Passing all rules is deliberately not asserted: this condition must be
    # reported honestly even when it fails table applicability or a GZ minimum.


@pytest.mark.parametrize("name", _departures.FLEET)
def test_production_departure_preserves_mass_moments_without_cargo_balancing(name):
    instance, summary = _departures.build_departure(name)
    assert instance.vessel.hull is not None
    assert len({b.gross_mass_kg for b in instance.containers}) == 1
    assert all(mass == 0 for mass in instance.onboard.tank_masses_kg.values())
    assert set(instance.onboard.tank_masses_kg) == {t.id for t in instance.vessel.tanks}
    assert instance.vessel.fixed_loads == summary["production_fixed_loads"]
    selected = {cid for stack in instance.vessel.stacks
                for cid in (stack.cell_ids[:2] if stack.on_deck else stack.cell_ids)}
    assert {placement.slot for placement in instance.onboard.placements} == selected
    condition = loading_condition(instance, instance.onboard)
    assert not condition.errors
    assert condition.mass_kg == pytest.approx(summary["target_displacement_kg"])
    assert condition.fsc_m == 0.

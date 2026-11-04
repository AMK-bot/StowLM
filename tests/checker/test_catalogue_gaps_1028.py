"""Catalogue outcome evidence using the actual stability/strength entry points.

The balanced fixed load is a declared synthetic test condition, not a public
loading manual/reference case. Each loading assertion targets its own rule ID;
other stability criteria may independently reject this proof condition.
"""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from stowage.checker.stability import check_stability
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.strength import check_strength
from stowage.model import ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel


@pytest.fixture(scope="module")
def balanced_condition():
    instance = build_instance()
    vessel = load_vessel("feeder_500")
    instance.vessel = vessel
    instance.onboard = ShipState(vessel_id=vessel.id)
    geometry = StabilityGeometry(load_ship_params("feeder_500"))
    draught = .9 * geometry.design_draught_m
    hydro = geometry.full_submerged(draught, draught)
    mass = hydro.displacement_kg - vessel.hull.lightship_mass_kg
    # Synthetic proof load: match upright displacement/LCG with a declared low
    # fixed load. No tank, cargo geometry or stability result is mocked.
    x = (hydro.displacement_kg * hydro.lcb_m
         - vessel.hull.lightship_mass_kg * vessel.hull.lightship_lcg_m) / mass
    vessel.fixed_loads = [(x, 0., 3., mass)]
    return instance


@pytest.fixture(scope="module")
def baseline_report(balanced_condition):
    report = check_stability(balanced_condition, balanced_condition.onboard, call_index=0)
    assert report.ran, report.error
    return report


@pytest.mark.parametrize("rule,margin_keys", [
    ("iscode.loading.displacement", ("displacement_minimum_kg", "displacement_maximum_kg")),
    ("iscode.loading.draft", ("draught_aft_m", "draught_fwd_m", "draught_fwd_min_m")),
    ("iscode.loading.gm", ("gm_m",)),
    ("iscode.loading.trim", ("trim_min_m", "trim_m")),
    ("iscode.loading.list", ("list_rad",)),
    ("port.call.draft", ("port_draught_m",)),
])
def test_loading_rule_accepts_condition_with_positive_margins(baseline_report, rule, margin_keys):
    assert rule not in {item.rule_id for item in baseline_report.violations}
    for key in margin_keys:
        assert baseline_report.margins[key] > 0, (rule, key, baseline_report.margins)


@pytest.mark.parametrize("rule,endpoint", [
    ("iscode.loading.displacement", "minimum"),
    ("iscode.loading.displacement", "maximum"),
    ("iscode.loading.draft", "maximum"),
    ("iscode.loading.draft", "forward_minimum"),
    ("iscode.loading.gm", "minimum"),
    ("iscode.loading.trim", "minimum"),
    ("iscode.loading.trim", "maximum"),
    ("iscode.loading.list", "maximum"),
    ("port.call.draft", "maximum"),
])
def test_loading_rule_rejects_violated_endpoint(balanced_condition, baseline_report, rule, endpoint):
    instance = balanced_condition.model_copy(deep=True)
    hull, metrics = instance.vessel.hull, baseline_report.metrics
    if rule == "iscode.loading.displacement":
        limit = metrics["displacement_kg"] + (1000. if endpoint == "minimum" else -1000.)
        instance.rule_overrides[rule] = {endpoint: limit}
        margin_key = "displacement_minimum_kg" if endpoint == "minimum" else "displacement_maximum_kg"
    elif rule == "iscode.loading.trim":
        limit = metrics["trim_m"] + (.1 if endpoint == "minimum" else -.1)
        instance.rule_overrides[rule] = {endpoint: limit}
        margin_key = "trim_min_m" if endpoint == "minimum" else "trim_m"
    elif rule == "iscode.loading.gm":
        instance.rule_overrides[rule] = {"minimum_gm": metrics["gm_m"] + .2}
        margin_key = "gm_m"
    elif rule == "iscode.loading.draft":
        if endpoint == "maximum":
            hull.max_draught_m = min(metrics["draught_aft_m"], metrics["draught_fwd_m"]) - .2
            margin_key = "draught_aft_m"
        else:
            hull.min_draught_fwd_m = metrics["draught_fwd_m"] + .2
            margin_key = "draught_fwd_min_m"
    elif rule == "port.call.draft":
        instance.port.max_draught_m = max(metrics["draught_aft_m"], metrics["draught_fwd_m"]) - .2
        margin_key = "port_draught_m"
    else:
        x, _, z, mass = instance.vessel.fixed_loads[0]
        instance.vessel.fixed_loads[0] = (x, .05, z, mass)
        hull.max_list_rad = .0001
        margin_key = "list_rad"
    report = check_stability(instance, instance.onboard, call_index=0)
    assert report.ran, report.error
    assert report.margins[margin_key] < 0
    failures = [item for item in report.violations if item.rule_id == rule]
    assert failures, (rule, report.violations)
    assert all(item.severity == "hard" and item.call_index == 0 for item in failures)


def test_free_surface_accepts_valid_tank_state(balanced_condition, baseline_report):
    # Explicit empty known tank is valid; numerical slack-tank correction is
    # independently covered in test_stability_loading's hand-worked prism.
    instance = balanced_condition.model_copy(deep=True)
    instance.onboard.tank_masses_kg = {instance.vessel.tanks[0].id: 0.}
    report = check_stability(instance, instance.onboard, call_index=0)
    assert report.ran, report.error
    assert report.metrics["fsc_m"] == pytest.approx(baseline_report.metrics["fsc_m"])
    assert "iscode.b3_1.free_surface" not in {item.rule_id for item in report.violations}


@pytest.mark.parametrize("invalid", ["unknown_tank", "over_capacity"])
def test_free_surface_rejects_invalid_tank_at_checker_entry(balanced_condition, invalid):
    instance = balanced_condition.model_copy(deep=True)
    tank = instance.vessel.tanks[0]
    if invalid == "unknown_tank":
        instance.onboard.tank_masses_kg = {"not-a-tank": 1000.}
        expected = "unknown tank not-a-tank"
    else:
        instance.onboard.tank_masses_kg = {tank.id: 1.01 * tank.capacity_m3 * tank.content_density_kg_m3}
        expected = f"tank {tank.id} exceeds capacity"
    report = check_stability(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert expected in report.error
    assert any(item.rule_id == "iscode.b3_1.free_surface" and item.severity == "hard"
               and item.call_index == 0 for item in report.violations)


@pytest.fixture(scope="module")
def strength_condition():
    instance = build_instance()
    instance.vessel = load_vessel("feeder_500")
    instance.onboard = ShipState(vessel_id=instance.vessel.id)
    return instance


def test_s1_complete_required_station_readouts_without_applicable_torsion(strength_condition):
    report = check_strength(strength_condition, strength_condition.onboard, call_index=0)
    assert report.ran, report.error
    assert not report.violations
    assert strength_condition.vessel.hull.beam_m <= 32.26
    for station in strength_condition.vessel.hull.stations:
        assert station.max_torsion_nm is None
        label = format(station.x_m, ".12g")
        assert math.isfinite(report.metrics[f"sf_n@{label}"])
        assert math.isfinite(report.metrics[f"bm_nm@{label}"])
    assert not any(key.startswith("torsion_nm@") for key in report.metrics)


def test_s1_missing_all_required_readouts_fails_closed(strength_condition):
    instance = strength_condition.model_copy(deep=True)
    instance.vessel.hull.stations = []
    report = check_strength(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert "read-out stations" in report.error
    assert any(item.severity == "hard" for item in report.violations)


def test_s1_torsion_applicable_above_breadth_limit_fails_closed(strength_condition):
    instance = strength_condition.model_copy(deep=True)
    instance.vessel.hull.beam_m = 32.27
    report = check_strength(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert "torsion" in report.error.lower()
    assert any(item.severity == "hard" for item in report.violations)


def test_s1_missing_one_required_readout_fails_closed(strength_condition, monkeypatch):
    import stowage.checker.strength as strength

    original = strength.integrate_strength
    required = strength_condition.vessel.hull.stations[-1]

    def truncated_readouts(*args, **kwargs):
        curve = original(*args, **kwargs)
        # Fault injection at the numerical-result boundary: retain the genuine
        # full beam/assessment and every declared required hull station, but
        # simulate a missing last-station sample and free-end sample.
        return replace(curve, x_m=curve.x_m[:-2], shear_n=curve.shear_n[:-2],
                       bending_nm=curve.bending_nm[:-2])

    monkeypatch.setattr(strength, "integrate_strength", truncated_readouts)
    report = check_strength(strength_condition, strength_condition.onboard, call_index=0)
    label = format(required.x_m, ".12g")
    assert not report.ran, (
        f"Required station x={label} omitted; sf/bm readouts present: "
        f"{f'sf_n@{label}' in report.metrics}/{f'bm_nm@{label}' in report.metrics}")
    assert report.error and 'readouts' in report.error
    assert any(item.rule_id == 'iacs.s1.loading_instrument' and item.severity == 'hard'
               and item.call_index == 0 for item in report.violations)


def test_s1_explicitly_required_torsion_readout_fails_closed(strength_condition):
    instance = strength_condition.model_copy(deep=True)
    station = instance.vessel.hull.stations[len(instance.vessel.hull.stations) // 2]
    station.max_torsion_nm = 1e6
    report = check_strength(instance, instance.onboard, call_index=0)
    assert not report.ran
    assert report.error and 'torsion is not modelled' in report.error
    assert any(item.rule_id == 'iacs.s1.loading_instrument' and item.severity == 'hard'
               and item.call_index == 0 for item in report.violations)


@pytest.mark.parametrize('damage', ['shear_only', 'bending_only', 'wrong_coordinates',
                                   'nonfinite_shear', 'nonfinite_bending', 'wrong_shape', 'empty',
                                   'masked_coordinates', 'masked_shear', 'masked_bending',
                                   'masked_free_bow'])
def test_s1_malformed_numerical_readouts_fail_closed(strength_condition, monkeypatch, damage):
    import numpy as np
    import stowage.checker.strength as strength

    original = strength.integrate_strength

    def malformed(*args, **kwargs):
        curve = original(*args, **kwargs)
        if damage == 'shear_only':
            return replace(curve, shear_n=curve.shear_n[:-1])
        if damage == 'bending_only':
            return replace(curve, bending_nm=curve.bending_nm[:-1])
        if damage == 'wrong_coordinates':
            return replace(curve, x_m=curve.x_m[::-1])
        if damage == 'wrong_shape':
            return replace(curve, shear_n=curve.shear_n[:, None])
        if damage == 'empty':
            return replace(curve, x_m=np.array([]), shear_n=np.array([]), bending_nm=np.array([]))
        if damage.startswith('masked_'):
            field = {'masked_coordinates': 'x_m', 'masked_shear': 'shear_n',
                     'masked_bending': 'bending_nm', 'masked_free_bow': 'shear_n'}[damage]
            values = np.ma.array(getattr(curve, field), mask=False)
            index = -1 if damage == 'masked_free_bow' else len(values) // 2
            values.mask[index] = True
            return replace(curve, **{field: values})
        field = 'shear_n' if damage == 'nonfinite_shear' else 'bending_nm'
        values = getattr(curve, field).copy()
        values[0] = np.nan if damage == 'nonfinite_shear' else np.inf
        return replace(curve, **{field: values})

    monkeypatch.setattr(strength, 'integrate_strength', malformed)
    report = check_strength(strength_condition, strength_condition.onboard, call_index=7)
    assert not report.ran
    assert report.error and 'readouts' in report.error
    assert any(item.rule_id == 'iacs.s1.loading_instrument' and item.severity == 'hard'
               and item.call_index == 7 for item in report.violations)


@pytest.mark.parametrize('representation', ['list', 'unmasked', 'array_only'])
def test_s1_reporting_consumes_validated_numeric_vectors(strength_condition, monkeypatch, representation):
    import numpy as np
    import stowage.checker.strength as strength

    expected = check_strength(strength_condition, strength_condition.onboard, call_index=7)
    assert expected.ran, expected.error
    original = strength.integrate_strength

    class ArrayOnly:
        def __init__(self, values):
            self.values = values

        def __array__(self, dtype=None, copy=None):
            return np.array(self.values, dtype=dtype, copy=copy) if copy is not None else np.asarray(self.values, dtype=dtype)

        def __getitem__(self, index):
            raise AssertionError('Reporting must use the validated numeric vector.')

    def valid_alternate_vectors(*args, **kwargs):
        curve = original(*args, **kwargs)
        def convert(values):
            if representation == 'list':
                return values.tolist()
            if representation == 'unmasked':
                return np.ma.array(values, mask=False)
            return ArrayOnly(values)
        return replace(curve, x_m=convert(curve.x_m), shear_n=convert(curve.shear_n),
                       bending_nm=convert(curve.bending_nm))

    monkeypatch.setattr(strength, 'integrate_strength', valid_alternate_vectors)
    report = check_strength(strength_condition, strength_condition.onboard, call_index=7)
    # JSON's round-trippable float encoding also distinguishes signed zero.
    assert report.model_dump_json() == expected.model_dump_json()

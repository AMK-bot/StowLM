"""Tolerance arithmetic for the independent KN interpolation validation."""
from pathlib import Path
import importlib.util
import json

import pytest

from stowage.conventions import canonical_vessel_id

_PATH = Path(__file__).resolve().parents[2]/"experiments/stability/interpolation/error_statistics.py"
_SPEC = importlib.util.spec_from_file_location("stability_error_statistics", _PATH)
_STATS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_STATS)


@pytest.mark.parametrize("quantity,reference,delta", [
    ("gz_m", .01, .01), ("gz_m", 2., .02),
    ("area_mrad", .001, .00024), ("area_mrad", .2, .002),
    ("gm_m", .2, .01), ("gm_m", 10., .02),
])
def test_interpolation_error_target_is_one_fifth_l5(quantity, reference, delta):
    stats = _STATS.error_statistics([reference], [reference+delta], quantity)
    assert stats["max_l5_fraction"] == pytest.approx(.2)
    assert stats["max_absolute"] == pytest.approx(delta)


@pytest.mark.parametrize("reference,interpolated", [([], []), ([1], [1, 2]), ([float("nan")], [1])])
def test_interpolation_error_never_hides_invalid_values(reference, interpolated):
    with pytest.raises(ValueError):
        _STATS.error_statistics(reference, interpolated, "gz_m")


def test_signed_unstable_curves_use_absolute_reference_magnitude():
    stats = _STATS.error_statistics([-2., 0., 2.], [-1.98, .01, 2.02], "gz_m")
    assert stats["count"] == 3
    assert stats["max_l5_fraction"] == pytest.approx(.2)
    assert stats["rms_absolute"] == pytest.approx((.0009/3)**.5)


def test_interpolation_error_summary_exposes_failure_of_target():
    stats = _STATS.error_statistics([.001], [.021], "gz_m")
    assert stats["max_l5_fraction"] == pytest.approx(.4)
    assert not stats["target_met"]


@pytest.mark.parametrize("vessel", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("density", [1000., 1025.])
def test_recorded_production_holdout_meets_one_fifth_l5(vessel, density):
    """The shipped evidence must cover the whole matrix without hidden failures.

    This audits the offline evidence; dynamic interpolation/solver regressions
    against direct hull integration live in test_stability_tables.py.
    """
    path = _PATH.parent/"results.json"
    rows = json.loads(path.read_text())["validation"]
    row = next(r for r in rows if canonical_vessel_id(r["vessel"]) == vessel and r["density_kg_m3"] == density)
    assert row["completed_samples"] == row["requested_samples"] == 16
    assert row["failures"] == []
    assert row["errors"]["gz_m"]["count"] == 352
    assert row["errors"]["area_mrad"]["count"] == 64
    assert row["errors"]["gm_m"]["count"] == 16
    for result in row["errors"].values():
        assert result["target_met"]
        assert result["max_l5_fraction"] <= .2
    broad = row["broad_geometric_validation"]
    assert broad["completed_samples"] == broad["requested_samples"] == 256
    assert broad["failures"] == []
    measured = _STATS.error_statistics(
        [c["gz_reference_m"] for c in broad["cases"]],
        [c["gz_interpolated_m"] for c in broad["cases"]], "gz_m")
    assert measured == broad["errors"]
    assert measured["target_met"]
    assert all(broad["minimum_mass_kg"] <= c["mass_kg"] <= broad["maximum_mass_kg"]
               for c in broad["cases"])

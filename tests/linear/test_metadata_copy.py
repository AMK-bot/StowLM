"""Safety and calibration provenance must survive standard dataclass copies."""
from dataclasses import replace

import numpy as np
import pytest

from tests.linear.test_operations import example


@pytest.mark.parametrize("name,value", [
    ("measurement_diagnostics", {"signed_errors": {"qualified": True}}),
    ("hull_length_m", 100.), ("hull_max_draught_m", 6.),
    ("hull_min_draught_fwd_m", 2.),
    ("_fixed_tank_masses", {"fuel": 20.}),
    ("_reference_params", {"reference": "exact"}),
    ("_geometry_fingerprint", "geometry"),
    ("_vessel_physics_fingerprint", "actual-vessel-limits"),
    ("_fixed_loads_fingerprint", "loads"),
    ("_cargo_mass_kg", 123.), ("_tank_mass_bounds_kg", {"low": (20., 40.)}),
    ("_nominal_height_m", 2.6), ("_manifest_ids", ("box",)),
    ("_manifest_fingerprint", "manifest"),
    ("_constraint_cell_vcg_m", np.array([12.])),
    *[("_family_" + name, np.array([1.])) for name in (
        "response_sf", "response_bm", "buoyancy_sf", "buoyancy_bm", "lightship_sf", "lightship_bm")],
    ("coefficient_certificate", {"inputs_hash": "coefficient-inputs"}),
])
def test_replace_preserves_builder_safety_and_provenance_metadata(name, value):
    model, _, _ = example()
    object.__setattr__(model, name, value)
    copied = replace(model, gm_min_m=.6)
    assert getattr(copied, name, None) is value


def test_replace_preserves_local_band_cargo_and_tank_domain():
    model, _, _ = example()
    windows = {"low": (20., 40.)}
    object.__setattr__(model.band, "_tank_mass_bounds_kg", windows)
    object.__setattr__(model.band, "_cargo_mass_kg", 123.)
    copied = replace(model.band, max_abs_trim_m=.8)
    assert getattr(copied, "_cargo_mass_kg", None) == 123.
    assert getattr(copied, "_tank_mass_bounds_kg", None) is windows

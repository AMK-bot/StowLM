"""Optional boundary LIST evidence is qualified separately from interior errors."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace as NS

import numpy as np
import pytest

from stowage.linear import margin_policy as policy
from stowage.linear.operations import constraints
from stowage.linear.model import ValidityBand


@pytest.fixture
def boundary_context(monkeypatch, tmp_path):
    import stowage.linear.cache as cache
    monkeypatch.setattr(cache, "response_calibration_identity", lambda family: "boundary-response")
    monkeypatch.setattr(policy, "_BANK_PATH", tmp_path/"bank.json")
    z = np.zeros(1)
    model = NS(station_x_m=z, water_density_kg_m3=1025.,
        band=ValidityBand(500., 2000., 0., 10., 1., np.deg2rad(2.)),
        max_list_rad=np.deg2rad(2.), gm_min_m=.5,
        max_trim_m=1., cell_ids=("cell",), tank_ids=(),
        cell_lcg_m=z, cell_tcg_m=np.ones(1), cell_vcg_m=np.array([10.]),
        tank_adjustable=np.zeros(0), tank_lcg_m=np.zeros(0), tank_tcg_m=np.zeros(0),
        tank_vcg_m=np.zeros(0), tank_fsm_max_kgm=np.zeros(0),
        km_line=(0., 10.), trim_line=(0., 0., 0.), draught_line=(0., .001),
        const_mass_kg=1000., const_moment_x_kgm=0., const_moment_y_kgm=0.,
        const_moment_z_kgm=9000., const_sf_n=z, const_bm_nm=z,
        sf_per_kg_cell=np.zeros((1, 1)), bm_per_kg_cell=np.zeros((1, 1)),
        sf_per_kg_tank=np.zeros((1, 0)), bm_per_kg_tank=np.zeros((1, 0)),
        sf_min_n=np.array([-1e8]), sf_max_n=np.array([1e8]),
        bm_min_nm=np.array([-1e8]), bm_max_nm=np.array([1e8]))
    signed = policy._zeros(1)
    for side in policy._SIDES:
        signed[side]["list_rad"] = np.deg2rad(1.9)
    diagnostics = dict(signed_errors=signed, family_domain={"kg_min_m": 0., "kg_max_m": 20.},
        reference_identity={"method": 1, "qualified": True, "dispatch": "current"},
        family_calibration_cache_key="calibration", response_calibration_identity="boundary-response")
    model.measurement_diagnostics = diagnostics
    evidence = tmp_path/"boundary-measurements.json"
    evidence.write_text("synthetic boundary calibration and independent passing holdouts")
    pins = [dict(path=str(evidence), sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())]
    boundary = dict(format=1, kind="boundary-moment-kgm", response_identity="boundary-response",
        reference_identity=deepcopy(diagnostics["reference_identity"]),
        domain=deepcopy(diagnostics["family_domain"]), station_x_m=[0.],
        water_density_kg_m3=1025., limit_rad=model.max_list_rad, gm_min_m=.5,
        measured_unsafe_kgm={"lower_unsafe": 1., "upper_unsafe": 2.},
        reference_transfer_kgm={"lower_unsafe": .1, "upper_unsafe": .2},
        qualification=dict(independent_holdout_passed=True, branch_monotonicity_checked=True,
            calibration_seed=1, holdout_seed=2,
            calibration_samples={side: 20 for side in policy._SIDES},
            holdout_samples={side: 10 for side in policy._SIDES},
            max_holdout_excess_kgm={side: 0. for side in policy._SIDES}), evidence=pins)
    from stowage.linear.boundary_calibration import BOUNDARY_CALIBRATION_VERSION
    from stowage.linear import boundary_calibration
    from pathlib import Path
    primitive = Path(boundary_calibration.__file__).resolve()
    boundary['evidence'] = [*pins, {'path':str(primitive),
        'sha256':hashlib.sha256(primitive.read_bytes()).hexdigest()}]
    boundary['builder_version'] = BOUNDARY_CALIBRATION_VERSION
    boundary['builder_inputs_hash'] = policy._boundary_builder_inputs_hash(boundary)
    record = dict(station_x_m=[0.], physical_unsafe=deepcopy(signed),
        reference_transfer=policy._zeros(1), domain=diagnostics["family_domain"],
        reference_identity=diagnostics["reference_identity"], evidence=pins,
        equivalence={"fresh_numeric_equal": True}, boundary_list=boundary)
    bank = dict(format=1, entries={"boundary-response": record})
    return NS(template=model), model, diagnostics, bank


def install(context):
    family, model, diagnostics, bank = context
    diagnostics["margin_policy"] = policy.qualify_policy(family, None, diagnostics, bank=bank)
    return model


def test_independent_moment_policy_preserves_nonempty_list_band(boundary_context):
    model = install(boundary_context)
    rows = constraints(model, 1., direction="conservative")
    assert "list_margin" not in rows.names
    assert "gm" in rows.names
    restoring = 1000.
    assert rows.const[rows.names.index("list_port")] == pytest.approx(
        -np.tan(model.max_list_rad)*restoring+1.1*2.2)
    assert rows.const[rows.names.index("list_starboard")] == pytest.approx(
        -np.tan(model.max_list_rad)*restoring+1.1*1.1)
    # Historical synthetic 1.8degree predictor remains safely inside this row.
    my = restoring*np.tan(np.deg2rad(1.8))
    assert my+rows.const[rows.names.index("list_port")] < 0


def test_absent_boundary_evidence_retains_exhausted_angular_policy(boundary_context):
    del boundary_context[3]["entries"]["boundary-response"]["boundary_list"]
    rows = constraints(install(boundary_context), 1., direction="conservative")
    assert "list_margin" in rows.names


def test_boundary_bad_case_rejected_with_direction_and_caller_scale(boundary_context):
    _, model, _, bank = boundary_context
    model.max_list_rad = np.arctan(.1)
    model.band = ValidityBand(500., 2000., 0., 10., 1., model.max_list_rad)
    boundary = bank["entries"]["boundary-response"]["boundary_list"]
    boundary["limit_rad"] = model.max_list_rad
    boundary["builder_inputs_hash"] = policy._boundary_builder_inputs_hash(boundary)
    boundary["measured_unsafe_kgm"]["upper_unsafe"] = 20.
    boundary["reference_transfer_kgm"]["upper_unsafe"] = 0.
    model.const_moment_y_kgm = 85.  # exact boundary capacity80; affine capacity100
    rows = constraints(install(boundary_context), 1., direction="conservative")
    assert rows.const[rows.names.index("list_port")] == pytest.approx(7.)
    assert rows.const[rows.names.index("list_port")] > 0.
    assert constraints(model, 2., direction="conservative").const[rows.names.index("list_port")] == pytest.approx(29.)


@pytest.mark.parametrize("field,value", [
    ("response_identity", "stale"), ("reference_identity", {}), ("domain", {}),
    ("station_x_m", [1.]), ("limit_rad", .01), ("gm_min_m", .15),
    ("water_density_kg_m3", 1000.), ("evidence", []),
    ("measured_unsafe_kgm", {"lower_unsafe": -1., "upper_unsafe": 0.}),
    ("reference_transfer_kgm", {"lower_unsafe": 0., "upper_unsafe": float("nan")}),
])
def test_mismatched_or_invalid_boundary_data_fails_closed(boundary_context, field, value):
    boundary_context[3]["entries"]["boundary-response"]["boundary_list"][field] = value
    with pytest.raises(ValueError):
        install(boundary_context)


@pytest.mark.parametrize("field,value", [
    ("independent_holdout_passed", False), ("branch_monotonicity_checked", False),
    ("holdout_seed", 1), ("holdout_samples", {"lower_unsafe": 0, "upper_unsafe": 2}),
    ("max_holdout_excess_kgm", {"lower_unsafe": 1e-12, "upper_unsafe": 0.}),
])
def test_failed_missing_or_nonindependent_holdout_fails_closed(boundary_context, field, value):
    boundary_context[3]["entries"]["boundary-response"]["boundary_list"]["qualification"][field] = value
    with pytest.raises(ValueError):
        install(boundary_context)


def test_certificate_binds_boundary_data_and_rechecks_domain(boundary_context):
    model = install(boundary_context)
    certificate = model.measurement_diagnostics["margin_policy"]
    original = certificate["inputs_hash"]
    certificate["boundary_list"]["measured_unsafe_kgm"]["lower_unsafe"] = 0.
    certificate["sha256"] = policy._digest({k: v for k, v in certificate.items() if k != "sha256"})
    with pytest.raises(ValueError, match="input hash"):
        policy.default_reserves(model, direction="conservative")
    assert policy._inputs_hash(certificate) != original
    model = install(boundary_context)
    model.band = ValidityBand(500., 2000., 0., 10., 1., .01)
    with pytest.raises(ValueError, match="domain/reference/geometry"):
        policy.default_reserves(model, direction="conservative")


@pytest.mark.parametrize('field,value', [('builder_version','stale'), ('builder_inputs_hash','stale')])
def test_boundary_builder_identity_tampering_fails_closed(boundary_context, field, value):
    boundary_context[3]['entries']['boundary-response']['boundary_list'][field] = value
    with pytest.raises(ValueError, match='builder identity'):
        install(boundary_context)


def test_boundary_primitive_pin_mismatch_fails_closed(boundary_context):
    boundary = boundary_context[3]['entries']['boundary-response']['boundary_list']
    boundary['evidence'][-1]['sha256'] = 'stale'
    with pytest.raises(ValueError, match='primitive source pin'):
        install(boundary_context)

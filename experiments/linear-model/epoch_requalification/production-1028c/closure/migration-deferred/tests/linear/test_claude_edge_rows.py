"""Public-row inclusion on independently captured physical edge equilibria.

Every station and heel side is checked separately. A failure of another exact
constraint cannot hide a false rejection. Recorded exact values are reused;
these tests do not rerun equilibrium roots or certify complete departures.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from stowage.linear import linear_constraints
from stowage.linear.margin_policy import optimistic_envelope, qualify_policy
from stowage.linear.operations import public_loading_policy
from stowage.linear.reusable import assemble_instance
from stowage.model import FixedLoad, ShipState

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT/"experiments/linear-model/epoch_requalification/production-1028c/seawater"
HISTORICAL = ROOT/"experiments/linear-model/fix-t3-linear"
# The historical ship ID remains a supported alias for this fresh campaign.
from stowage.conventions import VESSEL_ID_ALIASES
LEGACY_MIDDLE = next(old for old, new in VESSEL_ID_ALIASES.items() if new == "feeder_800")
SHIPS = ("feeder_500", LEGACY_MIDDLE, "feeder_1700")
REGRESSION_ID = "train-071-33446023d17a202b"


def read(path):
    return json.loads(path.read_text())


def pin(path):
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture(scope="module")
def driver():
    path = ROOT/"experiments/linear-model/epoch_requalification/driver.py"
    spec = importlib.util.spec_from_file_location("claude_edge_pytest_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def families(driver):
    cache = {}
    def prepare(ship):
        if ship not in cache:
            prepared = driver.prepare(ship, HERE/"ordinary")
            family, instance, errors, diagnostics, before = prepared
            cache[ship] = (*prepared, qualify_policy(family, instance, diagnostics))
        return cache[ship]
    return prepare


def reconstruct(prepared, observed):
    family, instance, errors, diagnostics, _, certificate = prepared
    tanks = sorted(instance.vessel.tanks, key=lambda tank: tank.id)
    assert len(observed["all_tank_masses_kg"]) == len(tanks)
    onboard = ShipState(vessel_id=instance.vessel.id, tank_masses_kg={tank.id: float(value)
        for tank, value in zip(tanks, observed["all_tank_masses_kg"], strict=True) if not tank.adjustable})
    state = instance.model_copy(update={"containers": [], "load_list": [], "discharge_list": [],
        "onboard": onboard, "vessel": instance.vessel.model_copy(update={
            "fixed_loads": [FixedLoad(**observed["fixed_point"])]})})
    model = assemble_instance(family, state, family.template.band, errors=errors,
        diagnostics=dict(diagnostics, margin_policy=certificate, loading_policy=public_loading_policy(state)))
    return model


def exact_passes(model, observed, names):
    def inside(value, low, high):
        return bool(np.all(np.asarray(value) >= np.asarray(low)-1e-7)
            and np.all(np.asarray(value) <= np.asarray(high)+1e-7))
    band, policy = model.band, model.measurement_diagnostics["loading_policy"]
    draught, trim, heel = (observed["exact_"+name] for name in ("draught_m", "trim_m", "list_rad"))
    angle = min(model.max_list_rad, band.max_abs_heel_rad)
    result = {
        "gm": inside(observed["exact_gm_m"], model.gm_min_m, np.inf),
        "trim": inside(trim, max(policy["min_trim_m"], -band.max_abs_trim_m), min(policy["max_trim_m"], band.max_abs_trim_m)),
        "list_port": inside(heel, -np.inf, angle), "list_starboard": inside(heel, -angle, np.inf),
        "draught": inside(draught, band.draught_min_m, band.draught_max_m),
        "draught_aft": inside(draught+trim/2, -np.inf, model.hull_max_draught_m),
        "draught_forward": inside(draught-trim/2, model.hull_min_draught_fwd_m, model.hull_max_draught_m),
        "displacement": inside(observed["mass_kg"], max(policy["min_displacement_kg"], band.displacement_min_kg),
                               min(policy["max_displacement_kg"], band.displacement_max_kg)),
    }
    if model._cargo_mass_kg is not None:
        result["cargo_mass"] = inside(sum(observed["cell_masses_kg"]), model._cargo_mass_kg, model._cargo_mass_kg)
    for key, (low, high) in model._tank_mass_bounds_kg.items():
        result[f"tank_mass@{key}"] = inside(observed["tank_masses_kg"][model.tank_ids.index(key)], low, high)
    # Public :g labels can collide. Each occurrence remains a distinct row;
    # neither a dictionary overwrite nor float parsing can identify a station.
    aligned = []
    position = {"sf": 0, "bm": 0}
    for name in names:
        if name.startswith(("sf@", "bm@")):
            kind = name[:2]
            index = position[kind]
            assert index < len(model.station_x_m)
            assert name == f"{kind}@{model.station_x_m[index]:g}"
            units = "n" if kind == "sf" else "nm"
            aligned.append(inside(observed[f"exact_{kind}_{units}"][index],
                getattr(model, f"{kind}_min_{units}")[index],
                getattr(model, f"{kind}_max_{units}")[index]))
            position[kind] += 1
        else:
            aligned.append(result[name])
    assert position == {"sf": len(model.station_x_m), "bm": len(model.station_x_m)}
    return aligned


def strict_centroid(observed, domain):
    """Match the public builder's exact comparisons, without sampler tolerance."""
    point = observed["fixed_point"]
    return (domain["fixed_x_min_m"] <= point["x_m"] <= domain["fixed_x_max_m"]
        and abs(point["y_m"]) <= domain["fixed_y_abs_max_m"]
        and domain["kg_min_m"] <= point["z_m"] <= domain["kg_max_m"])


@pytest.mark.parametrize("ship", SHIPS)
@pytest.mark.parametrize("phase", ("train", "holdout"))
@pytest.mark.parametrize("epoch", ("edges-v1", "edges-v2", "edges-v3"))
def test_each_exact_feasible_edge_row_survives_necessary_screen(driver, families, ship, phase, epoch, record_property):
    prepared = families(ship)
    family, _, _, diagnostics, before, certificate = prepared
    source = HERE/epoch/ship/f"necessary-edges-{phase}.json"
    record = read(source)
    assert record["complete"] is True
    assert record["physical_inputs_start"] == record["physical_inputs_end"] == before
    assert record["physical_inputs_unchanged"] is True
    assert record["calibration"]["response_calibration_identity"] == diagnostics["response_calibration_identity"]
    assert record["sampler_source"] == pin(ROOT/record["sampler_source"]["path"])
    # Earlier failed holds are retained calibration enrichment. Only the latest
    # untouched edge population supplies independent outer qualification.
    assert pin(source) in read(HERE/"claude-current-audit-inputs.json")["source_pins"]
    if epoch == "edges-v3":
        assert pin(source) in certificate["necessary"]["edge_evidence"]
    if epoch != "edges-v1":
        assert record["sampler_base_source"] == pin(ROOT/record["sampler_base_source"]["path"])
        assert record["sampler_config"]["sampler_base_source"] == record["sampler_base_source"]
    assert record["samples"] == len(record["list_row_samples"])
    if phase == "holdout":
        assert record["samples"] >= 64
        assert record["seed"] != read(source.with_name("necessary-edges-train.json"))["seed"]
    comparisons = 0
    exclusions = []
    for observed in record["list_row_samples"]:
        assert observed["admitted"] is observed["qualified_domain"] is True
        if epoch == "edges-v3" and observed["state_id"].startswith("v3-"):
            domain = diagnostics["family_domain"]
            expected_z = domain["kg_min_m"]+observed["request"]["dry_kg_fraction"]*(domain["kg_max_m"]-domain["kg_min_m"])
            assert observed["fixed_point"]["z_m"] == expected_z
        if not strict_centroid(observed, diagnostics["family_domain"]):
            # The sampler's explicit 1e-7 measurement tolerance may retain a
            # boundary point a few ULPs outside the builder's strict centroid
            # domain. Do not clip the data or relax the production boundary.
            # Residual qualification still charges these captured points.
            assert phase == "train", (ship, epoch, observed["state_id"], "strict-outside holdout")
            with pytest.raises(ValueError, match="positive fixed load centroid is outside"):
                reconstruct(prepared, observed)
            exclusions.append(observed["state_id"])
            continue
        model = reconstruct(prepared, observed)
        rows = linear_constraints(model, direction="necessary")
        values = rows.a_cell@np.asarray(observed["cell_masses_kg"])+rows.a_tank@np.asarray(observed["tank_masses_kg"])+rows.const
        exact = exact_passes(model, observed, rows.names)
        assert len(rows.names) == len(exact)
        passes = (values >= rows.lb-1e-7)&(values <= rows.ub+1e-7)
        for name, accepted, exact_accepted in zip(rows.names, passes, exact, strict=True):
            if exact_accepted:
                comparisons += 1
                assert accepted, (ship, phase, observed["state_id"], name)
    assert comparisons > 0
    # These are explicit rejection checks, not skipped tests or removed source
    # observations. Every independent holdout must support public row replay.
    expected_exclusions = {"feeder_500": 7, LEGACY_MIDDLE: 9, "feeder_1700": 6}
    if epoch == "edges-v3" and phase == "train":
        prior = read(HERE/"edges-v2"/ship/"necessary-edges-train.json")
        prior_exclusions = {row["state_id"] for row in prior["list_row_samples"]
                            if not strict_centroid(row, diagnostics["family_domain"])}
        assert prior_exclusions <= set(exclusions)
        assert len(prior_exclusions) == expected_exclusions[ship]
    else:
        assert len(exclusions) == (expected_exclusions[ship] if phase == "train" else 0)
    record_property("strict_centroid_rejections", json.dumps(exclusions))
    record_property("necessary_row_comparisons", comparisons)
    assert driver.snapshot(ship, family.parameters) == before


def test_valid_centroid_aft_regression_rejects_old_reserve_and_accepts_current(families):
    prepared = families("feeder_500")
    _, _, _, diagnostics, _, _ = prepared
    source = HERE/"edges-v1/feeder_500/necessary-edges-train.json"
    observed = next(row for row in read(source)["list_row_samples"] if row["state_id"] == REGRESSION_ID)
    model = reconstruct(prepared, observed)
    rows = linear_constraints(model, direction="necessary")
    index = rows.names.index("draught_aft")
    value = float(rows.a_cell[index]@np.asarray(observed["cell_masses_kg"])
        +rows.a_tank[index]@np.asarray(observed["tank_masses_kg"])+rows.const[index])
    baseline = read(HISTORICAL/"claude-baseline-bank.json")
    # The rejected reserve belongs to the archived epoch; today's fingerprint
    # deliberately differs even when the physical coefficients are identical.
    historical_raw = read(HISTORICAL/"epoch-claude/feeder_500/raw.json")
    baseline_identity = historical_raw["diagnostics"]["response_calibration_identity"]
    original = baseline["entries"][baseline_identity]["necessary"]
    archived_raw_optimistic = optimistic_envelope(
        historical_raw["diagnostics"]["signed_errors"], len(model.station_x_m))
    # Reconstruct the old public bound including its raw-family maximum. The
    # old physical-bank maximum alone understates the actual reserve used.
    old_reserve = 1.1*(max(archived_raw_optimistic["upper_unsafe"]["draught_aft_m"],
        original["physical_optimistic"]["upper_unsafe"]["draught_aft_m"])
        +original["reference_transfer"]["upper_unsafe"]["draught_aft_m"])
    assert exact_passes(model, observed, rows.names)[index] is True
    assert old_reserve == pytest.approx(.6775873854429051, abs=1e-12)
    assert value > model.hull_max_draught_m+old_reserve+1e-7
    assert value <= rows.ub[index]+1e-7


def test_root_audit_does_not_group_strength_stations_or_list_sides():
    from types import SimpleNamespace as NS
    path = HERE.parent/"verify.py"
    spec = importlib.util.spec_from_file_location("claude_root_audit_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module = module.load_gate("seawater", "rows")
    model = NS(max_list_rad=.1, band=NS(max_abs_heel_rad=.1), station_x_m=np.array([0., 1.]),
        sf_min_n=np.array([-1., -1.]), sf_max_n=np.array([1., 1.]),
        bm_min_nm=np.array([-1., -1.]), bm_max_nm=np.array([1., 1.]), _tank_mass_bounds_kg={})
    rows = NS(names=("sf@0", "bm@0", "sf@1", "bm@1", "list_port", "list_starboard"))
    result = module.per_row_exact(model, rows, {}, {"sf_n": [0., 2.], "bm_nm": [0., 2.], "list_rad": .2},
                                  {"sf": False, "bm": False, "list": False})
    assert result == [True, True, False, False, False, True]


def test_exact_flags_preserve_duplicate_formatted_station_names():
    from types import SimpleNamespace as NS
    band = NS(displacement_min_kg=1., displacement_max_kg=10., draught_min_m=0.,
        draught_max_m=5., max_abs_trim_m=1., max_abs_heel_rad=.2)
    model = NS(band=band, gm_min_m=.5, max_list_rad=.2, hull_max_draught_m=5.,
        hull_min_draught_fwd_m=0., _cargo_mass_kg=None, _tank_mass_bounds_kg={},
        station_x_m=np.array([1.0000001, 1.0000002]),
        sf_min_n=np.array([-1., -1.]), sf_max_n=np.array([1., 1.]),
        bm_min_nm=np.array([-1., -1.]), bm_max_nm=np.array([1., 1.]),
        measurement_diagnostics={"loading_policy": {"min_trim_m": -1., "max_trim_m": 1.,
            "min_displacement_kg": 1., "max_displacement_kg": 10.}})
    observed = dict(exact_draught_m=2., exact_trim_m=0., exact_list_rad=0.,
        exact_gm_m=1., mass_kg=5., exact_sf_n=[0., 2.], exact_bm_nm=[2., 0.])
    names = ("sf@1", "bm@1", "sf@1", "bm@1")
    assert exact_passes(model, observed, names) == [True, False, False, True]

"""Linear coefficients, conservative envelopes and executable ballast decisions."""
from dataclasses import replace
from pathlib import Path
import runpy

import numpy as np
import pytest

from stowage.conventions import cells_of_slot
from stowage.linear.model import (
    build_linear_model, evaluate_linear, linear_constraints, solve_ballast_lp,
)
from stowage.linear.physics import exact_condition, geometry_for
from stowage.model import Placement, ShipState

# Numerical calibration is deliberate integration work; algebra/cache tests stay quick.
pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def context():
    helper = runpy.run_path(str(Path(__file__).resolve().parents[2] /
                               "experiments/linear-model/validate.py"))
    instance = helper["fleet_instance"]("feeder_500")
    # Declared synthetic fixed cargo makes a laden departure physically feasible
    # without relaxing the fleet's minimum-forward-draught or trim limits.
    vessel = instance.vessel
    fixed = [*vessel.fixed_loads, (.7*vessel.hull.lpp_m, 0., 2., 400_000.)]
    instance = instance.model_copy(update={"vessel": vessel.model_copy(update={"fixed_loads": fixed})})
    return instance, build_linear_model(instance)


@pytest.fixture(scope="module")
def forward_cargo(context):
    instance, model = context
    cells = {cell.id: cell for cell in instance.vessel.cells}
    occupied, placements = set(), []
    boxes = sorted(instance.containers, key=lambda box: -box.length_ft)
    for box in boxes:
        choices = []
        for cell in cells.values():
            slot = (f"{int(cell.id[:3])+1:03d}"+cell.id[3:]) if box.length_ft > 20 else cell.id
            support = cells_of_slot(slot)
            if all(cid in cells and cid not in occupied for cid in support):
                x = np.mean([cells[cid].lcg_m for cid in support])
                y = np.mean([cells[cid].tcg_m for cid in support])
                z = np.mean([cells[cid].vcg_base_m for cid in support])
                choices.append(((x, -abs(y), -z), slot, support))
        _, slot, support = max(choices)
        occupied.update(support)
        placements.append(Placement(slot=slot, container_id=box.id))
    return ShipState(vessel_id=instance.vessel.id, placements=placements)


def test_shapes_errors_and_density(context):
    instance, model = context
    c, t, s = len(model.cell_ids), len(model.tank_ids), len(model.station_x_m)
    assert model.sf_per_kg_cell.shape == model.bm_per_kg_cell.shape == (s, c)
    assert model.sf_per_kg_tank.shape == model.bm_per_kg_tank.shape == (s, t)
    for value in (model.errors.gm_m, model.errors.trim_m, model.errors.list_rad,
                  model.errors.draught_m, *model.errors.sf_n, *model.errors.bm_nm):
        assert np.isfinite(value) and value >= 0
    different_water = instance.model_copy(update={
        "port": instance.port.model_copy(update={"water_density_kg_m3": 1000.0})})
    with pytest.raises(ValueError, match="density"):
        evaluate_linear(model, instance.onboard, different_water)
    with pytest.raises(ValueError, match="density"):
        solve_ballast_lp(model, instance.onboard, different_water, direction="conservative")
    with pytest.raises(ValueError):
        linear_constraints(model, margin_scale=0.99, direction="conservative")


def test_km_mass_lower_bound(context):
    instance, model = context
    geometry = geometry_for(instance)
    a, b = model.km_line
    # Independent dense hydrostatic scan checks the envelope between fit knots.
    from scipy.optimize import brentq
    for mass in np.linspace(model.band.displacement_min_kg, model.band.displacement_max_kg, 31):
        draught = brentq(lambda d: geometry.submerged(d, d, 0., model.water_density_kg_m3).displacement_kg-mass,
                         .01, geometry.max_z_m)
        row = geometry.hydrostatic_row(draught, model.water_density_kg_m3)
        assert a + b*mass <= row.kmt_m*mass + 1e-5*mass


def test_forty_foot_mass_split(context):
    instance, model = context
    # This isolates coefficient algebra for partial states, rather than testing
    # departure-manifest validity. Explicitly remove only the known-manifest
    # binding for this partial-state algebra fixture; ordinary copies retain it.
    model = replace(model, _manifest_ids=None, _manifest_fingerprint=None,
                    _cargo_mass_kg=None)
    box = next(box for box in instance.containers if box.length_ft == 40)
    # Pick an even bay backed by two physical cells of the same stack level.
    chosen = None
    ids = set(model.cell_ids)
    for cell in model.cell_ids:
        slot = f"{int(cell[:3])+1:03d}" + cell[3:]
        if all(cid in ids for cid in cells_of_slot(slot)):
            chosen = slot
            break
    assert chosen is not None
    ship = ShipState(vessel_id=instance.vessel.id,
                     placements=[Placement(slot=chosen, container_id=box.id)])
    empty = evaluate_linear(model, instance.onboard, instance)
    loaded = evaluate_linear(model, ship, instance)
    indices = [model.cell_ids.index(cid) for cid in cells_of_slot(chosen)]
    mass = box.gross_mass_kg
    assert loaded.displacement_kg-empty.displacement_kg == pytest.approx(mass)
    assert loaded.lcg_m*loaded.displacement_kg-empty.lcg_m*empty.displacement_kg == pytest.approx(
        mass*np.mean(model.cell_lcg_m[indices]))
    np.testing.assert_allclose(loaded.sf_n-empty.sf_n,
                              mass*np.mean(model.sf_per_kg_cell[:, indices], axis=1), atol=1e-6)


def test_constraints_and_feasible_lp_exact_with_margins(context, forward_cargo):
    instance, model = context
    constraints = linear_constraints(model, direction="conservative")
    assert constraints.a_cell.shape[1] == len(model.cell_ids)
    assert constraints.a_tank.shape[1] == len(model.tank_ids)
    result = solve_ballast_lp(model, forward_cargo, instance, direction="conservative")
    assert result.status == "optimal", result
    tanks = np.array([result.tank_masses_kg.get(tid, 0.0) for tid in model.tank_ids])
    assert np.all(tanks >= -1e-6)
    assert np.all(tanks <= model.tank_capacity_kg+1e-6)
    from stowage.linear.operations import mass_vectors
    cargo, _ = mass_vectors(model, forward_cargo, instance)
    values = constraints.a_cell@cargo + constraints.a_tank@tanks + constraints.const
    assert np.all(values >= constraints.lb-1e-4)
    assert np.all(values <= constraints.ub+1e-4)
    exact = exact_condition(model, instance, cargo, tanks)
    assert exact["gm_m"] >= model.gm_min_m-1e-6
    assert abs(exact["trim_m"]) <= model.max_trim_m+1e-6
    assert abs(exact["list_rad"]) <= model.max_list_rad+1e-6
    assert np.all(exact["sf_n"] >= model.sf_min_n-1e-3)
    assert np.all(exact["sf_n"] <= model.sf_max_n+1e-3)
    assert np.all(exact["bm_nm"] >= model.bm_min_nm-1e-3)
    assert np.all(exact["bm_nm"] <= model.bm_max_nm+1e-3)
    from stowage.checker.strength import check_strength
    selected = forward_cargo.model_copy(update={"tank_masses_kg": result.tank_masses_kg})
    reference = check_strength(instance, selected, call_index=instance.call_index)
    assert reference.ran, reference.error
    assert not reference.violations, reference.violations
    from stowage.checker.stability import check_stability
    # The current D40 builder supplies its independently declared manual; use
    # those actual bounds rather than any broader synthetic fixture override.
    stability = check_stability(instance, selected, call_index=instance.call_index)
    assert stability.ran, stability.error
    assert stability.metrics["gm_m"] >= model.gm_min_m-1e-6
    assert abs(stability.metrics["trim_m"]) <= model.max_trim_m+1e-6
    assert abs(stability.metrics["list_rad"]) <= model.max_list_rad+1e-6
    hull = instance.vessel.hull
    assert stability.metrics["draught_fwd_m"] >= hull.min_draught_fwd_m-1e-6
    assert stability.metrics["draught_fwd_m"] <= hull.max_draught_m+1e-6
    assert stability.metrics["draught_aft_m"] <= hull.max_draught_m+1e-6
    approximation = evaluate_linear(model, selected, instance)
    for metric, estimate, error in (("gm_m", approximation.gm_m, model.errors.gm_m),
                                    ("trim_m", approximation.trim_m, model.errors.trim_m),
                                    ("list_rad", approximation.list_rad, model.errors.list_rad),
                                    ("draught_mean_m", approximation.draught_mean_m, model.errors.draught_m)):
        assert abs(stability.metrics[metric]-estimate) <= 1.01*error+1e-6

    for i, x in enumerate(model.station_x_m):
        label = format(float(x), ".12g")
        assert abs(reference.metrics[f"sf_n@{label}"]-approximation.sf_n[i]) <= model.errors.sf_n[i]*1.01+1e-3
        assert abs(reference.metrics[f"bm_nm@{label}"]-approximation.bm_nm[i]) <= model.errors.bm_nm[i]*1.01+1e-2


def test_impossible_lp_reports_positive_violation(context, forward_cargo):
    instance, model = context
    # Exercise a valid stricter caller reserve. Altering the certified GM
    # criterion itself would correctly invalidate the LIST-boundary evidence.
    result = solve_ballast_lp(model, forward_cargo, instance, margin_scale=10000., direction="conservative")
    assert result.status == "infeasible"
    assert result.tank_masses_kg == {}
    assert result.min_violation is not None and result.min_violation > 0
    assert any("gm" in name for name in result.binding)


def test_copied_model_cannot_reuse_boundary_certificate_for_changed_gm_criterion(context, forward_cargo):
    instance, model = context
    with pytest.raises(ValueError,match='boundary LIST domain/reference/geometry mismatch'):
        solve_ballast_lp(replace(model,gm_min_m=1000.),forward_cargo,instance, direction="conservative")


def test_max_gm_margin_objective_feasible(context, forward_cargo):
    instance, model = context
    result = solve_ballast_lp(model, forward_cargo, instance, objective="max_gm_margin", direction="conservative")
    assert result.status == "optimal", result
    assert result.objective is not None and np.isfinite(result.objective)
    tank = np.array([result.tank_masses_kg.get(tid, 0.) for tid in model.tank_ids])
    constraints = linear_constraints(model, direction="conservative")
    from stowage.linear.operations import mass_vectors
    cargo, _ = mass_vectors(model, forward_cargo, instance)
    value = constraints.a_cell@cargo+constraints.a_tank@tank+constraints.const
    assert np.all(value >= constraints.lb-1e-3)
    assert np.all(value <= constraints.ub+1e-3)


def test_slack_fsm_uses_maximum_sounding(context):
    instance, model = context
    from stowage.vessel.tanks import TankGeometry
    params=model._reference_params
    records={record['id']:record for record in params['tanks']}
    tanks = {tank.id: tank for tank in instance.vessel.tanks}
    for i, tid in enumerate(model.tank_ids):
        tank = tanks[tid]
        solid=TankGeometry.from_params(records[tid],ship_params=params)
        density=tank.content_density_kg_m3
        if solid.is_rectangular:
            expected=solid.sounding(solid.capacity_m3/2).fsm_m4*density
            assert model.tank_fsm_max_kgm[i] == pytest.approx(expected)
        else:
            # Curved tanks need not peak at sounding quarters. Independently
            # clip a grid including both one-sided slack endpoint limits.
            for fraction in np.r_[1e-6,np.linspace(.02,.98,25),1-1e-6]:
                measured=solid.sounding(solid.capacity_m3*fraction).fsm_m4*density
                assert model.tank_fsm_max_kgm[i]>=measured-1e-6
            # Bound the supplied additive quadrature, rather than assuming
            # adjacent section polygons form an exact union. Current production
            # HEEL cuts overlap by one ULP; replacing them by a box/union would
            # silently change the geometry whose FSM we promise to reserve.
            origin=(solid.y_min_m+solid.y_max_m)/2
            raw_upper=sum(weight*sum(((polygon[:,0].max()-origin)**3-
                (polygon[:,0].min()-origin)**3)/3 for polygon in section)
                for weight,section in zip(solid.integration_weights_m,solid.sections_yz_m,strict=True))*density
            assert model.tank_fsm_max_kgm[i]<=raw_upper*(1+1e-8)


def test_cache_and_outside_band_are_explicit(context, forward_cargo):
    instance, model = context
    from stowage.linear.cache import model_cache_key
    from stowage.linear.physics import _params_for
    _, params = _params_for(instance)
    before = model_cache_key(instance, None, 64, params)
    fresh = build_linear_model(instance)
    again = build_linear_model(instance)
    after = model_cache_key(instance, None, 64, params)
    # Other executives may change component source while this slow fixture is
    # calibrating. A changed fingerprint must invalidate the old object.
    if before == after:
        assert again is fresh
    assert fresh.vessel_id == model.vessel_id
    low_band = replace(model.band, displacement_min_kg=model.const_mass_kg*2,
                       displacement_max_kg=model.const_mass_kg*3)
    assert not evaluate_linear(replace(model, band=low_band), forward_cargo, instance).in_band
    no_hull = instance.model_copy(update={"vessel": instance.vessel.model_copy(update={"hull": None})})
    with pytest.raises(ValueError, match="hull"):
        build_linear_model(no_hull)

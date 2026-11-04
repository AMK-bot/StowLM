"""Mass algebra and LP edge cases without numerical-hull construction."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.linear.model import (
    LinearErrors, LinearModel, ValidityBand, evaluate_linear,
    linear_constraints, solve_ballast_lp,
)
from stowage.model import ShipState


def example(*, adjustable=True):
    """Synthetic algebra: lightship GM=1m; a low tank improves GM most."""
    zeros = np.zeros(1)
    model = LinearModel(
        vessel_id="algebra", version="test", water_density_kg_m3=1025.,
        band=ValidityBand(500., 2500., 0., 10., 1., .2),
        errors=LinearErrors(0., 0., 0., 0., zeros, zeros),
        cell_ids=("0010102",), tank_ids=("low", "high"), station_x_m=zeros,
        cell_lcg_m=zeros, cell_tcg_m=np.array([1.]), cell_vcg_m=np.array([10.]),
        tank_lcg_m=np.zeros(2), tank_tcg_m=np.zeros(2), tank_vcg_m=np.array([0., 9.]),
        tank_capacity_kg=np.array([500., 500.]), tank_fsm_max_kgm=np.zeros(2),
        tank_adjustable=np.full(2, adjustable),
        sf_per_kg_cell=np.zeros((1, 1)), bm_per_kg_cell=np.zeros((1, 1)),
        sf_per_kg_tank=np.zeros((1, 2)), bm_per_kg_tank=np.zeros((1, 2)),
        const_mass_kg=1000., const_moment_x_kgm=0., const_moment_y_kgm=0.,
        const_moment_z_kgm=9000., const_sf_n=zeros, const_bm_nm=zeros,
        km_line=(0., 10.), trim_line=(0., 0., 0.), draught_line=(0., .001),
        gm_min_m=.5, max_trim_m=1., max_list_rad=.2,
        sf_max_n=np.ones(1)*1e8, sf_min_n=-np.ones(1)*1e8,
        bm_max_nm=np.ones(1)*1e8, bm_min_nm=-np.ones(1)*1e8,
    )
    ship = ShipState(vessel_id="algebra")
    instance = SimpleNamespace(vessel=SimpleNamespace(id="algebra"),
        port=SimpleNamespace(water_density_kg_m3=1025.), onboard=ship, containers=[], rule_overrides={})
    return model, ship, instance


def signed_example():
    model, ship, instance = example()
    model = replace(model, errors=LinearErrors(.9, .4, .15, .3, np.array([9.]), np.array([8.])))
    lower = dict(gm_m=.01, trim_m=.02, list_rad=.03, draught_m=.04, sf_n=[1.], bm_nm=[2.])
    upper = dict(gm_m=.8, trim_m=.05, list_rad=.06, draught_m=.07, sf_n=[3.], bm_nm=[4.])
    object.__setattr__(model, "measurement_diagnostics", {"signed_errors": {
        "lower_unsafe": lower, "upper_unsafe": upper}})
    return model, ship, instance


def test_signed_bound_margins_do_not_charge_safe_gm_bias():
    model, _, _ = signed_example()
    rows = linear_constraints(model, margin_scale=2., direction="conservative")
    index = rows.names.index
    assert rows.const[index("gm")] == pytest.approx(9000.+(.5+.02-10.)*1000.)
    assert rows.lb[index("trim")] == pytest.approx(-1.+.04)
    assert rows.ub[index("trim")] == pytest.approx(1.-.1)
    assert rows.lb[index("draught")] == pytest.approx(.08)
    assert rows.ub[index("draught")] == pytest.approx(10.-.14)
    assert rows.lb[index("sf@0")] == pytest.approx(-1e8+2.)
    assert rows.ub[index("sf@0")] == pytest.approx(1e8-6.)
    assert rows.lb[index("bm@0")] == pytest.approx(-1e8+4.)
    assert rows.ub[index("bm@0")] == pytest.approx(1e8-8.)
    restore = (10.-.02)*1000.-9000.
    assert rows.const[index("list_port")] == pytest.approx(-np.tan(.2-.12)*restore)
    assert rows.const[index("list_starboard")] == pytest.approx(-np.tan(.2-.06)*restore)


def test_signed_endpoint_margins_account_for_trim_direction_and_direct_errors():
    model, _, _ = signed_example()
    object.__setattr__(model, "hull_max_draught_m", 8.)
    object.__setattr__(model, "hull_min_draught_fwd_m", .5)
    rows = linear_constraints(model, direction="conservative")
    index = rows.names.index
    assert rows.ub[index("draught_aft")] == pytest.approx(8.-(.07+.5*.05))
    assert rows.ub[index("draught_forward")] == pytest.approx(8.-(.07+.5*.02))
    assert rows.lb[index("draught_forward")] == pytest.approx(.5+.04+.5*.05)
    signed = model.measurement_diagnostics["signed_errors"]
    signed["upper_unsafe"]["draught_aft_m"] = .001
    signed["lower_unsafe"]["draught_forward_m"] = .002
    rows = linear_constraints(model, direction="conservative")
    assert rows.ub[index("draught_aft")] == pytest.approx(7.999)
    assert rows.lb[index("draught_forward")] == pytest.approx(.502)


@pytest.mark.parametrize("bad", [-.1, float("nan"), [1., 2.], "invalid"])
def test_malformed_signed_envelope_fails_closed(bad):
    model, _, _ = signed_example()
    model.measurement_diagnostics["signed_errors"]["lower_unsafe"]["gm_m"] = bad
    with pytest.raises(ValueError, match="invalid lower_unsafe"):
        linear_constraints(model, direction="conservative")


def test_missing_signed_metric_fails_closed():
    model, _, _ = signed_example()
    del model.measurement_diagnostics["signed_errors"]["upper_unsafe"]["sf_n"]
    with pytest.raises(ValueError, match="missing upper_unsafe"):
        linear_constraints(model, direction="conservative")


def test_signed_solver_error_need_not_be_bounded_by_point_error():
    model, _, _ = signed_example()
    model.measurement_diagnostics["signed_errors"]["lower_unsafe"]["gm_m"] = 1.2
    rows = linear_constraints(model, direction="conservative")
    # Solver all-FSM residuals have a different reference from known-fill point
    # residuals; LinearErrors remains the latter's absolute summary.
    assert rows.const[rows.names.index("gm")] == pytest.approx(700.)


def test_private_height_arms_change_only_gm_and_list_solver_rows():
    model, ship, instance = example()
    before = linear_constraints(model, direction="conservative")
    from stowage.model import Placement
    from stowage.conventions import make_container_id, SYNTHETIC_OWNER_CODES
    box_id = make_container_id(SYNTHETIC_OWNER_CODES[0], 1)
    instance.containers = [SimpleNamespace(id=box_id, gross_mass_kg=10.)]
    ship = ship.model_copy(update={"placements": [Placement(slot="0010102", container_id=box_id)]})
    point = evaluate_linear(model, ship, instance)
    object.__setattr__(model, "_constraint_cell_vcg_m", np.array([12.]))
    after = linear_constraints(model, direction="conservative")
    assert evaluate_linear(model, ship, instance).gm_m == point.gm_m
    assert after.a_cell[after.names.index("gm"), 0]-before.a_cell[before.names.index("gm"), 0] == 2.
    for i, name in enumerate(after.names):
        if name in ("list_port", "list_starboard"):
            assert after.a_cell[i, 0]-before.a_cell[i, 0] == pytest.approx(2*np.tan(.2))
        elif name != "gm":
            np.testing.assert_array_equal(after.a_cell[i], before.a_cell[i])
        np.testing.assert_array_equal(after.a_tank[i], before.a_tank[i])
        assert after.const[i] == before.const[i]
        assert after.lb[i] == before.lb[i]
        assert after.ub[i] == before.ub[i]


@pytest.mark.parametrize("bad", [[], [1., 2.], [float("nan")], [float("inf")], "invalid"])
def test_malformed_private_height_arms_fail_closed(bad):
    model, ship, instance = example()
    object.__setattr__(model, "_constraint_cell_vcg_m", bad)
    with pytest.raises(ValueError, match="conservative cell VCG"):
        linear_constraints(model, direction="conservative")
    with pytest.raises(ValueError, match="conservative cell VCG"):
        solve_ballast_lp(model, ship, instance, direction="conservative")


@pytest.mark.parametrize("adjustable", [True, False])
def test_maximum_gm_objective_uses_private_height_arms(adjustable):
    model, ship, instance = example(adjustable=adjustable)
    from stowage.model import Placement
    from stowage.conventions import make_container_id, SYNTHETIC_OWNER_CODES
    box_id = make_container_id(SYNTHETIC_OWNER_CODES[0], 1)
    instance.containers = [SimpleNamespace(id=box_id, gross_mass_kg=10.)]
    ship = ship.model_copy(update={"placements": [Placement(slot="0010102", container_id=box_id)]})
    object.__setattr__(model, "_constraint_cell_vcg_m", np.array([12.]))
    result = solve_ballast_lp(model, ship, instance, objective="max_gm_margin", direction="conservative")
    assert result.status == "optimal"
    mass = model.const_mass_kg+10.+sum(result.tank_masses_kg.values())
    moment = model.const_moment_z_kgm+120.+sum(
        model.tank_vcg_m[model.tank_ids.index(key)]*value for key, value in result.tank_masses_kg.items())
    assert result.objective == pytest.approx((10.*mass-moment)/mass-model.gm_min_m)


def test_certificate_policy_is_default_for_constraints_and_lp(monkeypatch, tmp_path):
    from copy import deepcopy
    import hashlib
    import stowage.linear.cache as cache
    from stowage.linear.margin_policy import qualify_policy, _zeros
    monkeypatch.setattr(cache, "response_calibration_identity", lambda family: "response", raising=False)
    model, ship, instance = signed_example()
    diagnostics = model.measurement_diagnostics
    signed = diagnostics["signed_errors"]
    signed["upper_unsafe"]["list_rad"] = .006
    for side in ("lower_unsafe", "upper_unsafe"):
        signed[side].update(draught_aft_m=.02, draught_forward_m=.03)
    diagnostics.update(reference_identity={"method": 1}, family_domain={"kg_max_m": 20.},
                       family_calibration_cache_key="calibration", response_calibration_identity="response")
    physical = deepcopy(signed)
    physical["upper_unsafe"]["list_rad"] = .006*3.108911
    evidence = tmp_path/"evidence.json"
    evidence.write_text("measured directional observations")
    bank = {"format": 1, "entries": {"response": dict(station_x_m=[0.],
        domain=diagnostics["family_domain"], reference_identity=diagnostics["reference_identity"],
        physical_unsafe=physical, reference_transfer=_zeros(1), equivalence={"numeric_equal": True},
        evidence=[{"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}])}}
    diagnostics["margin_policy"] = qualify_policy(SimpleNamespace(template=model), instance, diagnostics, bank=bank)
    rows = linear_constraints(model, direction="conservative")
    reserve = .01*1.1
    restoring = (10.-reserve)*1000.-9000.
    assert rows.const[rows.names.index("gm")] == pytest.approx(9000.+(.5+reserve-10.)*1000.)
    assert rows.const[rows.names.index("list_port")] == pytest.approx(-np.tan(.2-.006*3.108911*1.1)*restoring)
    # An inflated list budget does not multiply trim by the same >3.1 factor.
    assert rows.lb[rows.names.index("trim")] == pytest.approx(-1.+.02*1.1)
    assert "list_margin" not in rows.names
    assert solve_ballast_lp(model, ship, instance, direction="conservative").status == "optimal"
    doubled = linear_constraints(model, margin_scale=2., direction="conservative")
    assert doubled.lb[doubled.names.index("trim")] == pytest.approx(-1.+.02*1.1*2.)


def test_no_adjustable_tanks_feasible_and_infeasible():
    model, ship, instance = example(adjustable=False)
    answer = solve_ballast_lp(model, ship, instance, direction="conservative")
    assert answer.status == "optimal"
    assert answer.tank_masses_kg == {}
    assert answer.objective == 0.
    answer = solve_ballast_lp(replace(model, gm_min_m=2.), ship, instance, direction="conservative")
    assert answer.status == "infeasible"
    assert answer.min_violation == pytest.approx(1000.)
    assert answer.binding == ("gm",)


def test_safety_overrides_fail_closed():
    model, ship, instance = example()
    instance.rule_overrides = {"iscode.loading.gm": {"minimum_gm": 20.}}
    with pytest.raises(ValueError, match="iscode.loading.gm"):
        solve_ballast_lp(model, ship, instance, direction="conservative")


def test_slack_lp_preserves_original_si_units():
    model, ship, instance = example()
    # At both capacities the stability moment is 6500 kg m at D=2000 kg:
    # requiring GM=1000 m is impossible by a wide margin.
    answer = solve_ballast_lp(replace(model, gm_min_m=1000.), ship, instance, direction="conservative")
    assert answer.status == "infeasible"
    assert answer.min_violation == pytest.approx(999000.)
    assert "gm" in answer.binding


def test_maximum_gm_uses_ratio_rather_than_stability_numerator():
    model, ship, instance = example()
    light = solve_ballast_lp(model, ship, instance, direction="conservative")
    stable = solve_ballast_lp(model, ship, instance, objective="max_gm_margin", direction="conservative")
    assert light.status == stable.status == "optimal"
    assert light.objective == pytest.approx(0.)
    assert stable.tank_masses_kg["low"] == pytest.approx(500.)
    assert stable.tank_masses_kg["high"] == pytest.approx(0.)
    assert stable.objective == pytest.approx(4.-model.gm_min_m)
    final = ship.model_copy(update={"tank_masses_kg": stable.tank_masses_kg})
    assert evaluate_linear(model, final, instance).gm_m == pytest.approx(4.)


def test_lp_ignores_current_adjustable_mass_but_evaluation_validates_it():
    model, ship, instance = example()
    invalid_current = ship.model_copy(update={"tank_masses_kg": {"low": 99999.}})
    assert solve_ballast_lp(model, invalid_current, instance, direction="conservative").status == "optimal"
    with pytest.raises(ValueError, match="capacity"):
        evaluate_linear(model, invalid_current, instance)


def test_affine_gm_and_list_rows_match_physical_moment_bounds():
    model, _, _ = example()
    model = replace(model, tank_fsm_max_kgm=np.array([30., 20.]),
                    const_moment_y_kgm=20., errors=replace(model.errors, gm_m=.1, list_rad=.02))
    rows = linear_constraints(model, margin_scale=1.5, direction="conservative")
    cargo, tanks = np.array([25.]), np.array([50., 10.])
    values = rows.a_cell@cargo + rows.a_tank@tanks + rows.const
    d = model.const_mass_kg+cargo.sum()+tanks.sum()
    mz = model.const_moment_z_kgm+model.cell_vcg_m@cargo+model.tank_vcg_m@tanks
    my = model.const_moment_y_kgm+model.cell_tcg_m@cargo
    gm_lower_numerator = 10*d-mz-50.-.15*d
    assert values[rows.names.index("gm")] == pytest.approx(.5*d-gm_lower_numerator)
    tangent = np.tan(.2-1.5*.02)
    assert values[rows.names.index("list_port")] == pytest.approx(my-tangent*gm_lower_numerator)
    assert values[rows.names.index("list_starboard")] == pytest.approx(-my-tangent*gm_lower_numerator)


def test_endpoint_draught_metadata_survives_replace():
    model, _, _ = example()
    object.__setattr__(model, "hull_max_draught_m", 1.1)
    object.__setattr__(model, "hull_min_draught_fwd_m", .4)
    rows = linear_constraints(model, direction="conservative")
    assert "draught_aft" in rows.names and "draught_forward" in rows.names
    copied = replace(model, gm_min_m=.6)
    copied_rows = linear_constraints(copied, direction="conservative")
    assert copied_rows.names == rows.names
    for name in ("draught_aft", "draught_forward"):
        i = rows.names.index(name)
        assert copied_rows.lb[i] == rows.lb[i]
        assert copied_rows.ub[i] == rows.ub[i]


def test_replaced_model_rejects_endpoint_violation_inside_mean_draught_band():
    model, ship, instance = example(adjustable=False)
    model = replace(model, trim_line=(.4, 0., 0.))
    object.__setattr__(model, "hull_max_draught_m", 1.1)
    object.__setattr__(model, "hull_min_draught_fwd_m", .4)
    # Mean draught = 1 m lies in [0, 10], but aft = 1 + .4/2 = 1.2 m.
    assert solve_ballast_lp(model, ship, instance, direction="conservative").status == "infeasible"
    copied = replace(model, gm_min_m=.6)
    result = solve_ballast_lp(copied, ship, instance, direction="conservative")
    assert result.status == "infeasible"
    assert "draught_aft" in result.binding


def test_stricter_port_draught_requires_model_rebuild():
    model, ship, instance = example()
    object.__setattr__(model, "hull_max_draught_m", 7.)
    instance.port.max_draught_m = 6.
    with pytest.raises(ValueError, match="rebuild"):
        evaluate_linear(model, ship, instance)
    with pytest.raises(ValueError, match="rebuild"):
        solve_ballast_lp(model, ship, instance, direction="conservative")
    instance.port.max_draught_m = 8.
    assert solve_ballast_lp(model, ship, instance, direction="conservative").status == "optimal"


def test_nonconvergent_fractional_optimizer_does_not_claim_optimal(monkeypatch):
    from stowage.linear import operations
    model, ship, instance = example()
    calls = 0

    def alternating_lp(*args):
        nonlocal calls
        calls += 1
        return SimpleNamespace(status=0, success=True,
                               x=np.array([500. if calls % 2 == 0 else 0., 0.]))

    monkeypatch.setattr(operations, "_highs", alternating_lp)
    answer = solve_ballast_lp(model, ship, instance, objective="max_gm_margin", direction="conservative")
    assert answer.status == "error"
    assert answer.tank_masses_kg == {}


def test_narrow_model_requires_the_calibrated_cargo_mass():
    model, ship, instance = example()
    object.__setattr__(model, "_cargo_mass_kg", 20.)
    rows = linear_constraints(model, direction="conservative")
    i = rows.names.index("cargo_mass")
    assert rows.lb[i] == rows.ub[i] == 20.
    assert not evaluate_linear(model, ship, instance).in_band
    result = solve_ballast_lp(model, ship, instance, direction="conservative")
    assert result.status == "infeasible"
    assert "cargo_mass" in result.binding
    assert result.min_violation == pytest.approx(20.)


def test_narrow_tank_windows_are_constraints_and_lp_bounds():
    model, ship, instance = example()
    object.__setattr__(model, "_tank_mass_bounds_kg", {"low": (100., 200.), "high": (0., 0.)})
    assert not evaluate_linear(model, ship, instance).in_band
    rows = linear_constraints(model, direction="conservative")
    assert "tank_mass@low" in rows.names
    result = solve_ballast_lp(model, ship, instance, direction="conservative")
    assert result.status == "optimal"
    assert result.tank_masses_kg == pytest.approx({"low": 100., "high": 0.})
    ship = ship.model_copy(update={"tank_masses_kg": result.tank_masses_kg})
    assert evaluate_linear(model, ship, instance).in_band


def test_known_fill_fsc_prediction_does_not_remove_the_affine_lp_reserve():
    from stowage.linear.operations import actual_fsm_kgm
    model, ship, instance = example()
    model = replace(model, tank_fsm_max_kgm=np.array([100., 200.]))
    instance.vessel.tanks = [SimpleNamespace(id="low", kind="ballast"),
                             SimpleNamespace(id="high", kind="fuel_oil")]
    assert actual_fsm_kgm(model, instance, np.array([0., 0.])) == 0.
    assert actual_fsm_kgm(model, instance, np.array([250., 0.])) == 100.
    assert actual_fsm_kgm(model, instance, np.array([490., 0.])) == 0.
    assert actual_fsm_kgm(model, instance, np.array([500., 0.])) == 0.
    assert actual_fsm_kgm(model, instance, np.array([0., 500.])) == 200.
    assert evaluate_linear(model, ship, instance).gm_m == pytest.approx(1.)
    rows = linear_constraints(model, direction="conservative")
    i = rows.names.index("gm")
    # The LP still reserves both tank maxima, even when the evaluated state is empty.
    assert rows.const[i] == pytest.approx(9000.+(.5-10.)*1000.+300.)


def test_carried_consumable_type_reserves_all_tanks_of_that_type():
    from stowage.linear.operations import actual_fsm_kgm
    model, _, instance = example()
    model = replace(model, tank_fsm_max_kgm=np.array([100., 200.]))
    instance.vessel.tanks = [SimpleNamespace(id="low", kind="fuel_oil"),
                             SimpleNamespace(id="high", kind="fuel_oil")]
    assert actual_fsm_kgm(model, instance, np.array([0., 500.])) == 300.


def manifest_example():
    from stowage.linear.operations import departure_manifest_identity
    from stowage.model import Placement
    from stowage.model.fixtures import build_instance
    model, _, instance = example()
    boxes = build_instance().containers
    original, substitute = boxes[1], boxes[2]
    assert original.gross_mass_kg == substitute.gross_mass_kg
    instance.containers = [original, substitute]
    instance.load_list = [original.id]
    instance.discharge_list = []
    ship = ShipState(vessel_id="algebra", placements=[
        Placement(slot="0010102", container_id=original.id)])
    ids, fingerprint = departure_manifest_identity(instance)
    object.__setattr__(model, "_manifest_ids", ids)
    object.__setattr__(model, "_manifest_fingerprint", fingerprint)
    return model, ship, instance, original, substitute


def test_matching_narrow_departure_manifest_is_accepted():
    model, ship, instance, original, _ = manifest_example()
    assert evaluate_linear(model, ship, instance).displacement_kg == pytest.approx(1000.+original.gross_mass_kg)
    # The deliberately tiny synthetic band cannot carry this box; identity
    # validation succeeds and the LP reports ordinary physical infeasibility.
    assert solve_ballast_lp(model, ship, instance, direction="conservative").status == "infeasible"


@pytest.mark.parametrize("operation", [evaluate_linear, solve_ballast_lp])
@pytest.mark.parametrize("change", ["height", "instance_id", "ship_swap", "ship_missing"])
def test_narrow_model_rejects_equal_mass_manifest_substitution(operation, change):
    from stowage.model import Placement
    model, ship, instance, original, substitute = manifest_example()
    if change == "height":
        instance.containers[0] = original.model_copy(update={"height_m": original.height_m+.305})
    elif change == "instance_id":
        instance.load_list = [substitute.id]
    elif change == "ship_swap":
        ship = ship.model_copy(update={"placements": [Placement(slot="0010102", container_id=substitute.id)]})
    else:
        ship = ShipState(vessel_id="algebra")
    with pytest.raises(ValueError, match="manifest"):
        operation(model, ship, instance, **({"direction": "conservative"} if operation is solve_ballast_lp else {}))

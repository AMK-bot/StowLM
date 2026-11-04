"""Cheap algebra/contract checks of constructive physics; no hull calibration."""
from types import SimpleNamespace

import numpy as np
from ortools.linear_solver import pywraplp
import pytest

from stowage.conventions import cells_of_slot
from stowage.linear import LinearConstraintSet
from stowage.model.fixtures import build_instance
from stowage.model.vessel import VesselFieldProvenance
from stowage.model.voyage import VoyageSpec, VoyageTankPolicy
from stowage.solver import construct_physics as physics


def _instance():
    instance = build_instance()
    instance.vessel = instance.vessel.model_copy(update={
        "voyage_policy": VoyageTankPolicy(groups=[dict(id="fuel", kind="fuel_oil", tank_ids=["FO-1"])],
                                         drawdown_order=["FO-1"]),
        "hull": instance.vessel.hull.model_copy(update={
            "max_gm_lashing_m": 8.,
            "max_gm_lashing_m_provenance": VesselFieldProvenance(assumption="Synthetic test design GM")}),
    })
    instance.voyage = VoyageSpec.model_validate(dict(
        departure=dict(id="dep", phase="departure", call_index=0, locode="THLCH",
                       environment_id="dep-water", density_id="dep-rho", water_density_kg_m3=1025.),
        arrival=dict(id="arr", phase="arrival", call_index=1, locode="THBKK",
                     environment_id="arr-water", density_id="arr-rho", water_density_kg_m3=1000.)))
    return instance


def _model(instance):
    cells = tuple(cell.id for cell in instance.vessel.cells)
    tanks = tuple(tank.id for tank in instance.vessel.tanks)
    return SimpleNamespace(cell_ids=cells, tank_ids=tanks, km_line=(0., 10.),
                           const_mass_kg=4e6, const_moment_z_kgm=24e6,
                           cell_vcg_m=np.array([cell.vcg_base_m+1.2955 for cell in instance.vessel.cells]),
                           tank_vcg_m=np.ones(len(tanks)), errors=SimpleNamespace(gm_m=.1))


def _fake_rows(model, *, direction):
    n, t = len(model.cell_ids), len(model.tank_ids)
    # A single two-sided row with nonidentical cell coefficients catches both
    # 40ft mass sharing and accidental averaging of arbitrary slot addresses.
    return LinearConstraintSet(("algebra",), np.arange(1, n+1, dtype=float)[None, :],
                               np.array([[.5 if tid == "WB-P" else -.25 if tid == "WB-S" else 0. for tid in model.tank_ids]]), np.array([11.]),
                               np.array([-1e12]), np.array([1e12]))


def test_three_state_source_rows_share_departure_ballast(monkeypatch):
    instance = _instance()
    seen = []
    def build(context):
        seen.append((context.port.locode, context.port.water_density_kg_m3,
                     context.onboard.tank_masses_kg["FO-1"], context.port.max_draught_m))
        return _model(context)
    monkeypatch.setattr(physics, "build_linear_model", build)
    calls = []
    def rows(model, *, direction):
        calls.append(direction)
        return _fake_rows(model, direction=direction)
    monkeypatch.setattr(physics, "linear_constraints", rows)
    solver = pywraplp.Solver.CreateSolver("SCIP")
    box = next(b for b in instance.containers if b.length_ft == 40)
    slot = "0020102"
    x = {(box.id, slot): solver.BoolVar("x")}
    result = physics.add_construct_physics(solver, instance, x, mode="conservative", securing_proxy=False)
    assert result.state_names == ("departure", "mid_voyage", "arrival")
    assert len(result.approximations) == 2
    assert not any("D56" in approximation for approximation in result.approximations)
    assert calls == ["conservative"]*3
    assert [row[1] for row in seen] == [1025., 1025., 1000.]
    assert [row[2] for row in seen] == pytest.approx([47500., 26125., 4750.])
    assert seen[1][3] is None and seen[2][3] is None
    model = _model(instance)
    coef = np.arange(1, len(model.cell_ids)+1)
    footprint = cells_of_slot(slot)
    for cell in footprint:
        mass = result.cargo_mass_vars[cell]
        definition = solver.LookupConstraint("construct_cargo_mass_definition@"+cell)
        assert definition.GetCoefficient(mass) == 1.
        assert definition.GetCoefficient(x[box.id, slot]) == -box.gross_mass_kg/len(footprint)
    for name in result.row_names:
        row = solver.LookupConstraint(name)
        assert row.GetCoefficient(x[box.id, slot]) == 0.
        for cell in footprint:
            assert row.GetCoefficient(result.cargo_mass_vars[cell]) == pytest.approx(
                coef[model.cell_ids.index(cell)]/1e12, abs=1e-18)
        assert row.GetCoefficient(result.ballast_vars["WB-P"]) == pytest.approx(.5/1e12, abs=1e-18)
        assert row.GetCoefficient(result.ballast_vars["WB-S"]) == pytest.approx(-.25/1e12, abs=1e-18)
    solver.Add(x[box.id, slot] == 1)
    solver.Add(result.ballast_vars["WB-P"] == 2500.)
    solver.Add(result.ballast_vars["WB-S"] == 5000.)
    assert solver.Solve() in (solver.OPTIMAL, solver.FEASIBLE)
    assert result.ballast_condition().tank_masses_kg == {"WB-P": 2500., "WB-S": 5000.}
    assert result.ballast_condition().source == "planner"
    for mass in result.cargo_mass_vars.values():
        assert mass.solution_value() == pytest.approx(box.gross_mass_kg/len(footprint))


def test_retained_cargo_is_constant_discharge_is_absent(monkeypatch):
    instance = _instance()
    # Choose existing fixture placements from the layover fixture, and an
    # empty variable map: the entire cargo contribution must enter RHS once.
    layover = build_instance(layover=True)
    instance.onboard = layover.onboard
    instance.containers = layover.containers
    instance.discharge_list = layover.discharge_list
    monkeypatch.setattr(physics, "build_linear_model", _model)
    monkeypatch.setattr(physics, "linear_constraints", _fake_rows)
    solver = pywraplp.Solver.CreateSolver("SCIP")
    result = physics.add_construct_physics(solver, instance, {}, securing_proxy=False)
    model = _model(instance)
    boxes = {box.id: box for box in instance.containers}
    coef = np.arange(1, len(model.cell_ids)+1)
    constant = 11.
    for p in instance.onboard.placements:
        if p.container_id not in instance.discharge_list:
            constant += boxes[p.container_id].gross_mass_kg*np.mean(
                [coef[model.cell_ids.index(c)] for c in cells_of_slot(p.slot)])
    for name in result.row_names:
        row = solver.LookupConstraint(name)
        assert row.ub() == pytest.approx((1e12-constant)/1e12)


def test_necessary_direction_omits_securing_and_never_authorizes_certificate(monkeypatch):
    instance = _instance()
    monkeypatch.setattr(physics, "build_linear_model", _model)
    directions = []
    def rows(model, *, direction):
        directions.append(direction)
        return _fake_rows(model, direction=direction)
    monkeypatch.setattr(physics, "linear_constraints", rows)
    monkeypatch.setattr(physics, "_upper_design_gm", lambda *a: pytest.fail("unsafe certificate restriction"))
    monkeypatch.setattr(physics, "_securing_envelopes", lambda *a: pytest.fail("unsafe certificate restriction"))
    result = physics.add_construct_physics(pywraplp.Solver.CreateSolver("SCIP"), instance, {})
    assert directions == ["necessary"]*3
    assert result.direction == "necessary"
    assert not any("D56" in approximation for approximation in result.approximations)
    assert not result.certificate_eligible
    assert any("empirical" in reason for reason in result.certificate_blockers)


def test_source_qualification_failure_is_not_silently_omitted(monkeypatch):
    monkeypatch.setattr(physics, "build_linear_model", lambda *a: (_ for _ in ()).throw(ValueError("stale policy")))
    with pytest.raises(ValueError, match="stale policy") as rejected:
        physics.add_construct_physics(pywraplp.Solver.CreateSolver("SCIP"), _instance(), {})
    assert "construct/departure density=1025.0" in str(rejected.value)
    assert "vessel=synthetic_feeder.slice_b01-b03" in str(rejected.value)
    assert isinstance(rejected.value.__cause__, ValueError)


def test_rejected_qualification_reports_existing_source_response_identity(monkeypatch):
    def rejected_source(*args):
        response_identity = "a"*64
        raise ValueError("production linear policy epoch is stale; requalify")
    monkeypatch.setattr(physics, "build_linear_model", rejected_source)
    with pytest.raises(ValueError, match="response_identity="+"a"*64):
        physics.add_construct_physics(pywraplp.Solver.CreateSolver("SCIP"), _instance(), {})


def test_missing_voyage_fails_closed():
    with pytest.raises(ValueError, match="D57 voyage"):
        physics.add_construct_physics(pywraplp.Solver.CreateSolver("SCIP"), build_instance(), {})


def test_upper_gm_has_opposite_fsm_conservatism_from_lower_gm(monkeypatch):
    instance = _instance()
    monkeypatch.setattr(physics, "build_linear_model", _model)
    monkeypatch.setattr(physics, "linear_constraints", _fake_rows)
    monkeypatch.setattr(physics, "_securing_envelopes", lambda *a: None)
    solver = pywraplp.Solver.CreateSolver("SCIP")
    cid = instance.load_list[0]
    slot = "0010102"
    x = {(cid, slot): solver.BoolVar("cargo")}
    result = physics.add_construct_physics(solver, instance, x, mode="conservative")
    row = solver.LookupConstraint("construct/departure/APPROXIMATION_lashing_design_gm_upper")
    # kgm numerator: a + (b-design+guard)*D - Mz <= 0.
    constant = (10.-8.+.1)*4e6-24e6
    scale = abs(constant)
    assert row.GetCoefficient(result.ballast_vars["WB-P"]) == pytest.approx((10.-8.+.1-1.)/scale)
    # There is no +FSM subtraction: adding a large maximum FSM must not loosen
    # this upper-GM restriction, unlike the required minimum-GM row.
    assert row.ub() == pytest.approx(-constant/scale)


def test_adjustable_consumable_default_drawdown_scales_one_shared_variable(monkeypatch):
    instance = _instance()
    instance.vessel.tanks = [tank.model_copy(update={"adjustable": True}) if tank.id == "FO-1" else tank
                             for tank in instance.vessel.tanks]
    monkeypatch.setattr(physics, "build_linear_model", _model)
    def rows(model, *, direction):
        result = _fake_rows(model, direction=direction)
        result.a_tank[0, model.tank_ids.index("FO-1")] = 2.
        return result
    monkeypatch.setattr(physics, "linear_constraints", rows)
    solver = pywraplp.Solver.CreateSolver("SCIP")
    result = physics.add_construct_physics(solver, instance, {}, securing_proxy=False)
    coefficients = [solver.LookupConstraint(name).GetCoefficient(result.ballast_vars["FO-1"])
                    for name in result.row_names]
    assert coefficients == pytest.approx([2e-12, 1.1e-12, .2e-12], abs=1e-18)


def test_expired_assembly_budget_stops_before_model_build(monkeypatch):
    monkeypatch.setattr(physics, "build_linear_model", lambda *a: pytest.fail("deadline ignored"))
    with pytest.raises(TimeoutError, match="wall budget"):
        physics.add_construct_physics(pywraplp.Solver.CreateSolver("SCIP"), _instance(), {}, deadline=0.)


def test_nearby_station_display_name_collisions_keep_both_rows(monkeypatch):
    monkeypatch.setattr(physics, "build_linear_model", _model)
    def rows(model, *, direction):
        original = _fake_rows(model, direction=direction)
        return LinearConstraintSet(("sf@10.78", "sf@10.78"),
            np.repeat(original.a_cell, 2, axis=0), np.repeat(original.a_tank, 2, axis=0),
            np.repeat(original.const, 2), np.repeat(original.lb, 2), np.repeat(original.ub, 2))
    monkeypatch.setattr(physics, "linear_constraints", rows)
    solver = pywraplp.Solver.CreateSolver("SCIP")
    result = physics.add_construct_physics(solver, _instance(), {}, securing_proxy=False)
    assert len(result.row_names) == len(set(result.row_names)) == 6
    assert solver.LookupConstraint("construct/departure/sf@10.78#row1") is not None


@pytest.mark.parametrize("count,mass", [(1, 3000.), (2, 3000.), (1, 6000.)])
def test_proxy_accepted_deck_chains_pass_exact_irs_spring_calculation(monkeypatch, count, mass):
    """The proxy is checked against the judge's separate elastic calculation."""
    from stowage.checker import lashing, visibility
    from stowage.model import Placement
    instance = _instance()
    instance.vessel.fixed_loads = []
    h = instance.vessel.hull
    # This unit isolates the securing equations. The known physical block
    # coefficient is used identically by the D54 envelope and exact IRS code;
    # it does not pretend to certify the fixture's placeholder hull.
    geometry = SimpleNamespace(submerged=lambda *a: SimpleNamespace(
        volume_m3=.7*h.lpp_m*h.beam_m*h.scantling_draught_m))
    monkeypatch.setattr("stowage.linear.physics.geometry_for", lambda *a: geometry)
    monkeypatch.setattr(visibility, "_loading_condition", lambda *a: SimpleNamespace(
        gm_m=h.max_gm_lashing_m, t_aft_m=h.scantling_draught_m/4,
        t_fwd_m=h.scantling_draught_m/4, geometry=geometry))
    monkeypatch.setattr(lashing, "derive_cell_guide_design", lambda *a: lashing.CellGuideDesign(
        .01, .01, h.max_gm_lashing_m, "", 0., 0., 0))
    boxes = [box.model_copy(update={"gross_mass_kg": mass, "oog": None})
             for box in instance.containers if box.length_ft == 20][:count]
    instance.containers = boxes
    instance.load_list = [box.id for box in boxes]
    slots = ["0010182", "0010184"][:count]
    solver = pywraplp.Solver.CreateSolver("SCIP")
    x = {(box.id, slot): solver.BoolVar(f"x{i}") for i, (box, slot) in enumerate(zip(boxes, slots))}
    for variable in x.values():
        solver.Add(variable == 1)
    result = physics.ConstructPhysics({}, "conservative", ("departure",))
    physics._securing_envelopes(solver, instance, x, [], result)
    assert solver.Solve() in (solver.OPTIMAL, solver.FEASIBLE)
    ship = instance.onboard.model_copy(update={"placements": [
        Placement(container_id=box.id, slot=slot) for box, slot in zip(boxes, slots)]})
    report = lashing._check_lashing(instance, ship, call_index=0)
    assert report.ran and not report.violations

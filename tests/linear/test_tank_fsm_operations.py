"""Known-fill section-tank FSM differs from the LP's maximum reserve."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.linear.model import (LinearErrors, LinearModel, ValidityBand,
                                  evaluate_linear, linear_constraints)
from stowage.linear.operations import actual_fsm_kgm
from stowage.model import ShipState
from stowage.vessel.tanks import TankGeometry


def example(*, rectangular=False, kind="ballast", adjustable=True):
    # A widening trapezoid: breadth 1 m at bottom, 4 m at top. Unlike a
    # rectangular prism, its upright free surface changes with sounding.
    polygon = [[-.5, 0.], [.5, 0.], [2., 2.], [-2., 2.]]
    record = dict(id="tank", shape="hull_sections", x_m=[2-2/np.sqrt(3), 2+2/np.sqrt(3)],
        integration_weights_m=[2., 2.], sections_yz_m=[[polygon], [polygon]],
        x_min_m=0., x_max_m=4., y_min_m=-2., y_max_m=2., z_min_m=0., z_max_m=2.)
    if rectangular:
        record["shape"] = "rectangular_prism"
    solid = TankGeometry.from_params(record)
    density = 1000.
    capacity = solid.capacity_m3*density
    maximum = (4.*4.**3/12)*density  # Exact supremum as the wedge approaches full.
    zero = np.zeros(1)
    model = LinearModel(vessel_id="toy", version="test", water_density_kg_m3=1025.,
        band=ValidityBand(1e6, 1.1e6, 0., 10., 1., .2),
        errors=LinearErrors(0., 0., 0., 0., zero, zero),
        cell_ids=(), tank_ids=("tank",), station_x_m=zero,
        cell_lcg_m=np.zeros(0), cell_tcg_m=np.zeros(0), cell_vcg_m=np.zeros(0),
        tank_lcg_m=zero, tank_tcg_m=zero, tank_vcg_m=np.ones(1),
        tank_capacity_kg=np.array([capacity]), tank_fsm_max_kgm=np.array([maximum]),
        tank_adjustable=np.array([adjustable]),
        sf_per_kg_cell=np.zeros((1, 0)), bm_per_kg_cell=np.zeros((1, 0)),
        sf_per_kg_tank=np.zeros((1, 1)), bm_per_kg_tank=np.zeros((1, 1)),
        const_mass_kg=1e6, const_moment_x_kgm=0., const_moment_y_kgm=0.,
        const_moment_z_kgm=5e6, const_sf_n=zero, const_bm_nm=zero,
        km_line=(0., 10.), trim_line=(0., 0., 0.), draught_line=(0., 1e-6),
        gm_min_m=.15, max_trim_m=1., max_list_rad=.2,
        sf_max_n=np.ones(1)*1e8, sf_min_n=-np.ones(1)*1e8,
        bm_max_nm=np.ones(1)*1e8, bm_min_nm=-np.ones(1)*1e8)
    object.__setattr__(model, "_reference_params", {"tanks": [record]})
    tank = SimpleNamespace(id="tank", kind=kind, content_density_kg_m3=density,
                           capacity_m3=solid.capacity_m3)
    ship = ShipState(vessel_id="toy")
    instance = SimpleNamespace(vessel=SimpleNamespace(id="toy", tanks=[tank]),
        onboard=ship, port=SimpleNamespace(water_density_kg_m3=1025.),
        containers=[], rule_overrides={})
    return model, instance, solid


@pytest.mark.parametrize("fraction", [0., .25, .97, .98, 1.])
def test_section_known_fill_matches_exact_sounding_and_public_slack_threshold(fraction):
    model, instance, solid = example()
    mass = model.tank_capacity_kg[0]*fraction
    volume = mass/1000.
    expected = solid.sounding(volume).fsm_m4*1000. if 0 < volume < .98*solid.capacity_m3 else 0.
    assert actual_fsm_kgm(model, instance, np.array([mass])) == pytest.approx(expected)
    ship = instance.onboard.model_copy(update={"tank_masses_kg": {"tank": mass}})
    condition = evaluate_linear(model, ship, instance)
    expected_gm = (10.*condition.displacement_kg-5e6-mass-expected)/condition.displacement_kg
    assert condition.gm_m == pytest.approx(expected_gm)


def test_section_point_estimates_vary_but_affine_constraint_keeps_true_maximum():
    model, instance, solid = example()
    quarter = actual_fsm_kgm(model, instance, model.tank_capacity_kg*.25)
    near_full = actual_fsm_kgm(model, instance, model.tank_capacity_kg*.97)
    assert quarter < near_full < model.tank_fsm_max_kgm[0]
    rows = linear_constraints(model, direction="conservative")
    gm = rows.names.index("gm")
    assert rows.const[gm] == pytest.approx(5e6+(.15-10.)*1e6+model.tank_fsm_max_kgm[0])


def test_box_retains_exact_constant_fsm_fast_path(monkeypatch):
    model, instance, _ = example(rectangular=True)
    def fail(*args, **kwargs):
        raise AssertionError("box point prediction should not reconstruct geometry")
    monkeypatch.setattr(TankGeometry, "from_params", fail)
    assert actual_fsm_kgm(model, instance, model.tank_capacity_kg*.25) == model.tank_fsm_max_kgm[0]


def test_fixed_carried_consumable_retains_maximum_across_fills():
    model, instance, solid = example(kind="fuel_oil", adjustable=False)
    mass = model.tank_capacity_kg[0]*.25
    instance.onboard = instance.onboard.model_copy(update={"tank_masses_kg": {"tank": mass}})
    assert solid.sounding(solid.capacity_m3*.25).fsm_m4*1000. < model.tank_fsm_max_kgm[0]
    assert actual_fsm_kgm(model, instance, np.zeros(1)) == model.tank_fsm_max_kgm[0]


def test_known_fill_uses_production_individual_small_tank_exemption():
    model, instance, _ = example(rectangular=True)
    moment=model.tank_fsm_max_kgm[0]
    instance.vessel.hull=SimpleNamespace(lightship_mass_kg=moment/.009)
    assert actual_fsm_kgm(model,instance,model.tank_capacity_kg*.25)==0.
    instance.vessel.hull.lightship_mass_kg=moment/.01
    assert actual_fsm_kgm(model,instance,model.tank_capacity_kg*.25)==moment


def test_exact_section_geometry_resolves_from_instance_when_model_params_absent(monkeypatch):
    from stowage.linear import physics
    model, instance, solid = example()
    params = model._reference_params
    object.__delattr__(model, "_reference_params")
    instance.vessel.model_dump = lambda: {}
    monkeypatch.setattr(physics, "_params_for", lambda instance: ("toy", params))
    assert actual_fsm_kgm(model, instance, model.tank_capacity_kg*.25) == pytest.approx(
        solid.sounding(solid.capacity_m3*.25).fsm_m4*1000.)


def test_explicit_params_missing_tank_do_not_substitute_a_box():
    model, instance, _ = example()
    object.__setattr__(model, "_reference_params", {"tanks": []})
    with pytest.raises(ValueError, match="exact tank geometry unavailable"):
        actual_fsm_kgm(model, instance, model.tank_capacity_kg*.25)

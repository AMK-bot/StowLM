"""Hand mass moments and exact rectangular-liquid transfer checks."""
import math

import numpy as np
import pytest

from stowage.checker.stability_loading import (
    curve_correction, liquid_centroid,
    liquid_cg, loading_condition,
)
from stowage.model import Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry, build_tanks


def _empty():
    instance = build_instance()
    state = ShipState(vessel_id=instance.vessel.id)
    return instance, state


def _tank_instance(kind='ballast', fraction=.5):
    instance, state = _empty()
    raw = dict(id='test', kind=kind, content_density_kg_m3=1000.,
               capacity_m3=80., x_aft_m=10., x_fore_m=20.,
               geometry=dict(x_min_m=10., x_max_m=20., y_min_m=-2.,
                             y_max_m=2., z_min_m=0., z_max_m=2.))
    params = {'tanks': [raw]}
    instance.vessel.tanks = build_tanks(params)
    state.tank_masses_kg = {'test': fraction*80000}
    return instance, state, params


def test_lightship_fixed_loads_and_closed_covers_not_double_counted():
    instance, state = _empty()
    result = loading_condition(instance, state)
    hull = instance.vessel.hull
    assert result.mass_kg == hull.lightship_mass_kg+200000
    assert result.lcg_m == pytest.approx((hull.lightship_mass_kg*60+200000*80)/result.mass_kg)
    assert result.kg_m == pytest.approx((hull.lightship_mass_kg*6+200000*4)/result.mass_kg)
    assert not result.errors


def test_actual_high_cube_heights_raise_upper_boxes():
    instance, state = _empty()
    lower, upper = instance.containers[:2]
    lower.height_m = 2.896
    upper.height_m = 2.591
    lower.length_ft = upper.length_ft = 40
    state.placements = [Placement(slot='0020182', container_id=lower.id),
                        Placement(slot='0020184', container_id=upper.id)]
    empty = loading_condition(instance, ShipState(vessel_id=instance.vessel.id))
    result = loading_condition(instance, state)
    expected = (empty.mass_kg*empty.kg_m + lower.gross_mass_kg*(9+lower.height_m/2)
                + upper.gross_mass_kg*(9+lower.height_m+upper.height_m/2))
    assert result.kg_m == pytest.approx(expected/result.mass_kg)
    assert result.mass_kg == empty.mass_kg+lower.gross_mass_kg+upper.gross_mass_kg


def test_removed_and_relocated_covers():
    instance, state = _empty()
    closed = loading_condition(instance, state)
    cover, target = instance.vessel.hatch_covers
    state.covers_off = {cover.id: 'quay'}
    removed = loading_condition(instance, state)
    assert removed.mass_kg == closed.mass_kg-cover.mass_kg
    assert removed.kg_m*removed.mass_kg == pytest.approx(closed.kg_m*closed.mass_kg-cover.mass_kg*cover.vcg_m)
    state.covers_off = {cover.id: target.id}
    moved = loading_condition(instance, state)
    assert moved.mass_kg == closed.mass_kg
    assert moved.tcg_m*moved.mass_kg == pytest.approx(
        closed.tcg_m*closed.mass_kg+cover.mass_kg*(target.tcg_m-cover.tcg_m))
    assert any('cover thickness' in text for text in moved.assumptions)


@pytest.mark.parametrize('fraction,corrected', [(0,False), (.001,True), (.5,True), (.979,True), (.98,False), (1,False)])
def test_fixed_ballast_fsm_endpoints_and_nominal_full(fraction, corrected):
    instance, state, params = _tank_instance(fraction=fraction)
    result = loading_condition(instance, state, ship_params=params)
    # Rectangular tank L b^3 / 12, independent of all slack fill levels.
    expected = 1000*10*4**3/12 if corrected else 0
    assert result.fsm_kgm == pytest.approx(expected)
    assert result.fsc_m == pytest.approx(expected/result.mass_kg)
    assert not result.errors
    assert any(text.startswith('ASSUMPTION: ballast is not filled')
               for text in result.assumptions)


def test_consumables_maximum_across_range_even_nominal_full():
    instance, state, params = _tank_instance(kind='fuel_oil', fraction=1)
    result = loading_condition(instance, state, ship_params=params)
    assert result.fsm_kgm == pytest.approx(1000*10*4**3/12)
    assert result.tanks[0].volume_m3 == 40
    assert result.tanks[0].variable
    assert any('filling limits' in text for text in result.assumptions)


def test_missing_geometry_and_overcapacity_return_errors():
    instance, state, params = _tank_instance()
    assert any('geometry unavailable' in e for e in loading_condition(instance, state).errors)
    state.tank_masses_kg['test'] = 80001
    assert any('capacity' in e for e in loading_condition(instance, state, ship_params=params).errors)


@pytest.mark.parametrize('fill', [.001,.1,.5,.9,.999])
@pytest.mark.parametrize('heel,trim', [(0.,0.),(.01,0.),(.4,0.),(-.6,.03),(.3,-.01),(0.,.01),(.01,1e-7)])
def test_analytic_liquid_centroid_matches_polyhedron_integrals(fill, heel, trim):
    solid = TankGeometry(10,30,-7,-3,1,4)
    volume = fill*solid.capacity_m3
    actual = liquid_centroid(solid, volume, heel, trim)
    expected = solid.liquid(volume, heel, trim).centroid_m
    assert actual == pytest.approx(expected, abs=2e-7)


@pytest.mark.parametrize('tank_id', ['DB-M-P', 'DB-F-P', 'FO-A-P'])
@pytest.mark.parametrize('degrees', [10., 30., 60.])
@pytest.mark.parametrize('offset', [-1e-9, -1e-12, -1e-14, -3e-16, 0.,
                                    3e-16, 1e-14, 1e-12, 1e-9])
def test_fleet_tank_exact_and_near_half_fill_remain_bracketed(tank_id, degrees, offset):
    params = load_ship_params('feeder_500')
    raw = next(tank for tank in params['tanks'] if tank['id'] == tank_id)
    solid = TankGeometry.from_params(raw)
    volume = solid.capacity_m3*(.5+offset)
    heel, trim = math.radians(degrees), .001
    # Actual section clipping verifies every near-half-fill centroid, including
    # the earlier ±1e-14 floating-point band; bounds are not a tank solid.
    expected = solid.liquid(volume, heel, trim).centroid_m
    assert liquid_centroid(solid, volume, heel, trim) == pytest.approx(expected, abs=2e-7)


@pytest.mark.parametrize('tank_id', ['DB-M-P', 'DB-F-P', 'FO-A-P'])
def test_real_half_capacity_mass_roundtrip_has_finite_liquid_transfer(tank_id):
    params = load_ship_params('feeder_500')
    raw = next(tank for tank in params['tanks'] if tank['id'] == tank_id)
    instance, state = _empty()
    instance.vessel.tanks = build_tanks({'tanks': [raw]})
    tank = instance.vessel.tanks[0]
    state.tank_masses_kg = {tank_id: .5*tank.capacity_m3*tank.content_density_kg_m3}
    condition = loading_condition(instance, state, ship_params=params)
    assert not condition.errors
    actual_tank = condition.actual_tanks[0]
    angles = np.radians([10., 30., 60.])
    assert np.all(np.isfinite(curve_correction(condition, angles, .001)))
    for angle in angles:
        expected = actual_tank.geometry.liquid(actual_tank.volume_m3, angle, .001).centroid_m
        assert liquid_centroid(actual_tank.geometry, actual_tank.volume_m3, angle, .001) == pytest.approx(expected, abs=2e-7)


@pytest.mark.parametrize('degrees', [10., 30., 60.])
@pytest.mark.parametrize('offset', [-1e-9, -1e-12, -1e-14, -3e-16, 0.,
                                    3e-16, 1e-14, 1e-12, 1e-9])
def test_rectangular_acceleration_near_half_fill_stays_bracketed(degrees, offset):
    # Preserve independent polyhedron-reference coverage of the analytic box
    # root after the production tanks migrated to actual hull sections.
    solid = TankGeometry(10.13, 23.87, -4.83, -1.11, .29, 3.47)
    mass = solid.capacity_m3*(.5+offset)*1025.
    volume = mass/1025.
    heel, trim = math.radians(degrees), .001
    expected = solid.liquid(volume, heel, trim).centroid_m
    assert liquid_centroid(solid, volume, heel, trim) == pytest.approx(expected, abs=2e-7)


def test_small_angle_liquid_shift_and_fsc_agree():
    instance, state, params = _tank_instance()
    result = loading_condition(instance, state, ship_params=params)
    angle = 1e-3
    correction = curve_correction(result, [angle])[0]
    assert correction/angle == pytest.approx(result.fsc_m, rel=1e-5)
    cg = liquid_cg(result, angle)
    assert cg[1] > result.tcg_m
    assert cg[2] > result.kg_m


def test_exact_large_angle_transfer_not_virtual_kg():
    instance, state, params = _tank_instance()
    result = loading_condition(instance, state, ship_params=params)
    angle = math.radians(50)
    solid = result.tanks[0].geometry
    centre = solid.liquid(40,angle).centroid_m
    expected = 40000/result.mass_kg*((centre[1])*math.cos(angle)+(centre[2]-.5)*math.sin(angle))
    actual = curve_correction(result, [angle])[0]
    assert actual == pytest.approx(expected, abs=1e-9)
    assert not np.isclose(actual, result.fsc_m*math.sin(angle), rtol=1e-3)


def test_variable_consumables_cover_worst_transverse_pair_and_centre_tank():
    instance, state, params = _tank_instance(kind='fuel_oil', fraction=1)
    base = params['tanks'][0]
    # Independent fixed-volume ranges allow all three tanks to be slack at once.
    pair = []
    for name, y in [('port', 4), ('starboard', -4)]:
        row = dict(base, id=name, geometry=dict(base['geometry'], y_min_m=y-1, y_max_m=y+1),
                   capacity_m3=40.)
        pair.append(row)
    params['tanks'] += pair
    instance.vessel.tanks = build_tanks(params)
    state.tank_masses_kg.update(port=40000, starboard=40000)
    condition = loading_condition(instance, state, ship_params=params)
    assert {tank.id for tank in condition.tanks} == {'test', 'port', 'starboard'}
    assert condition.fsm_kgm == pytest.approx(1000*(10*4**3/12+2*10*2**3/12))


def test_half_fill_envelopes_box_liquid_transfer_over_entire_fill_range():
    solid = TankGeometry(0,10,-2,2,0,2)
    angle = math.radians(50)
    def moment(fraction):
        volume = solid.capacity_m3*fraction
        centre = liquid_centroid(solid, volume, angle)
        upright = solid.sounding(volume)
        return volume*((centre[1]-upright.tcg_m)*math.cos(angle)
                       +(centre[2]-upright.vcg_m)*math.sin(angle))
    maximum = moment(.5)
    assert all(moment(fraction) <= maximum+1e-10 for fraction in np.linspace(.001,.999,101))


def test_angle_dependent_trim_and_actual_liquid_cg():
    instance, state, params = _tank_instance(fraction=.1)
    condition = loading_condition(instance, state, ship_params=params)
    angles, trims = [0.,.1,.4], [.02,.01,-.01]
    correction = curve_correction(condition, angles, trims)
    solid = condition.actual_tanks[0].geometry
    volume = 8
    reference = solid.sounding(volume)
    for index,(heel,trim) in enumerate(zip(angles,trims,strict=True)):
        moved = solid.liquid(volume,heel,trim).centroid_m
        normal = np.array([math.tan(trim)*math.cos(heel), -math.sin(heel), math.cos(heel)])
        normal /= np.linalg.norm(normal)
        longitudinal = np.array([1.,0.,-math.tan(trim)])
        longitudinal /= np.linalg.norm(longitudinal)
        transverse = np.cross(normal,longitudinal)
        shift = np.array(moved)-[reference.lcg_m,reference.tcg_m,reference.vcg_m]
        assert correction[index] == pytest.approx(8000/condition.mass_kg*(shift @ transverse), abs=1e-9)
        expected = np.array([condition.lcg_m,condition.tcg_m,condition.kg_m])+8000/condition.mass_kg*(np.array(moved)-[reference.lcg_m,reference.tcg_m,reference.vcg_m])
        assert liquid_cg(condition,heel,trim) == pytest.approx(expected,abs=1e-9)

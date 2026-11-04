"""Section-solid dispatch regressions (IS Code B3.1 actual liquid transfer)."""
from types import SimpleNamespace
import math

import numpy as np
import pytest

from stowage.checker.stability_loading import (
    CorrectionTank, LoadingCondition, curve_correction, liquid_centroid,
    liquid_cg, loading_condition,
)
from stowage.model import ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.tanks import TankGeometry, build_tanks


def tapered_solid_record():
    # Two different triangular sections: their weighted capacity is 18 m3,
    # whereas the enclosing prism is 64 m3. Port-only, asymmetric in y and x.
    return dict(shape='hull_sections', x_m=[1., 3.],
                integration_weights_m=[2., 2.],
                sections_yz_m=[[[[0., 0.], [2., 0.], [2., 3.]]],
                               [[[0., 0.], [4., 0.], [4., 3.]]]],
                x_min_m=0., x_max_m=4., y_min_m=0., y_max_m=4.,
                z_min_m=0., z_max_m=4.)


@pytest.mark.parametrize('fraction', [0., .05, .5, .95, 1.])
@pytest.mark.parametrize('heel,trim', [(0., 0.), (.45, .08), (-.3, -.04)])
def test_hull_solid_centroid_every_fill_uses_sections(fraction, heel, trim):
    solid = TankGeometry.from_params(tapered_solid_record())
    assert solid.capacity_m3 == pytest.approx(18.)
    volume = fraction*solid.capacity_m3
    expected = solid.liquid(volume, heel, trim).centroid_m
    np.testing.assert_allclose(liquid_centroid(solid, volume, heel, trim),
                               expected, atol=1e-12)
    if fraction == 1.:
        # Triangle centroids weighted by the section areas 3 and 6.
        assert expected == pytest.approx((7/3, 20/9, 1.))
        assert not np.allclose(expected, (2., 2., 2.))


@pytest.mark.parametrize('shape', [None, 'future_solid'])
def test_unknown_solid_never_defaults_to_rectangular_bounds(shape):
    solid = SimpleNamespace(shape=shape, **{
        f'{axis}_{edge}_m': float(edge == 'max')
        for axis in 'xyz' for edge in ('min', 'max')})
    with pytest.raises(ValueError, match='unsupported exact tank geometry'):
        liquid_centroid(solid, .5, .2)


def test_section_transfer_drives_actual_cg_and_fixed_fill_gz_correction():
    solid = TankGeometry.from_params(tapered_solid_record())
    volume, density, mass = solid.capacity_m3*.37, 1025., 1e6
    tank = CorrectionTank('section', density, volume, 0., solid)
    condition = LoadingCondition(mass, 20., .1, 5., 0., 0.,
                                 tanks=(tank,), actual_tanks=(tank,))
    heel, trim = .48, .06
    upright = solid.sounding(volume)
    reference = np.array([upright.lcg_m, upright.tcg_m, upright.vcg_m])
    moved = np.array(solid.liquid(volume, heel, trim).centroid_m)
    shift = density*volume/mass*(moved-reference)
    assert liquid_cg(condition, heel, trim) == pytest.approx(
        np.array([20., .1, 5.])+shift, abs=1e-13)
    normal = np.array([math.tan(trim)*math.cos(heel), -math.sin(heel), math.cos(heel)])
    normal /= np.linalg.norm(normal)
    longitudinal = np.array([1., 0., -math.tan(trim)])
    longitudinal /= np.linalg.norm(longitudinal)
    expected = shift @ np.cross(normal, longitudinal)
    assert curve_correction(condition, [heel], [trim])[0] == pytest.approx(expected, abs=1e-13)
    assert abs(expected) > 1e-4


def test_fixed_section_ballast_uses_actual_sounding_fsm_and_centres():
    solid = TankGeometry.from_params(tapered_solid_record())
    raw = dict(id='shape-aware', kind='ballast', content_density_kg_m3=1025.,
               capacity_m3=solid.capacity_m3, x_aft_m=0., x_fore_m=4.,
               geometry=tapered_solid_record())
    params = {'tanks': [raw]}
    instance = build_instance()
    instance.vessel.tanks = build_tanks(params)
    volume = solid.capacity_m3*.37
    state = ShipState(vessel_id=instance.vessel.id,
                      tank_masses_kg={'shape-aware': volume*1025.})
    result = loading_condition(instance, state, ship_params=params)
    assert not result.errors
    row = solid.sounding(volume)
    assert result.fsm_kgm == pytest.approx(row.fsm_m4*1025., rel=1e-12)
    box_fsm = 4.*4.**3/12*1025.
    assert result.fsm_kgm < box_fsm*.5
    assert result.tanks[0].volume_m3 == pytest.approx(volume)
    assert result.actual_tanks[0].geometry.shape == 'hull_sections'
    tank_mass = volume*1025.
    empty_state = ShipState(vessel_id=instance.vessel.id)
    empty = loading_condition(instance, empty_state, ship_params=params)
    for key, centre in [('lcg_m', row.lcg_m), ('tcg_m', row.tcg_m), ('kg_m', row.vcg_m)]:
        assert getattr(result, key)*result.mass_kg == pytest.approx(
            getattr(empty, key)*empty.mass_kg+tank_mass*centre)

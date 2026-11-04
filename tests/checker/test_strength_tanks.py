"""Independent polyhedron reference for longitudinal liquid integration."""
import math

import numpy as np
import pytest

from stowage.checker.strength import G, _integrate_segments, _tank_liquid, _tank_segments
from stowage.vessel.tanks import TankGeometry


@pytest.mark.parametrize('fill,heel,trim',[(.07,.13,-.08),(.5,.02,.005),(.93,-.15,.09),(.31,0.,.03)])
def test_tilted_tank_distribution_conserves_mass_and_first_moment(fill,heel,trim):
    tank = TankGeometry(12.,30.,-5.,2.,.5,4.)
    volume = fill*tank.capacity_m3
    reference = tank.liquid(volume,heel,trim)
    liquid = _tank_liquid(tank,volume,heel,trim)
    assert np.asarray(liquid.centroid_m) == pytest.approx(reference.centroid_m,abs=2e-8)
    assert liquid.plane_level_m == pytest.approx(reference.plane_level_m,abs=2e-9)
    rho = 1025.
    segments = _tank_segments(tank,liquid,heel,trim,rho)
    sf,bm = _integrate_segments([40.],segments,[])
    mass = volume*rho
    assert sf[0] == pytest.approx(-mass*G,rel=2e-10)
    assert bm[0] == pytest.approx(mass*G*(40-reference.centroid_m[0]),rel=2e-10)

"""Section-tank influences retain actual masses and longitudinal moments."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.linear.tank_weights import unit_tank_weight_integrals


def test_section_quadrature_keeps_gaps_and_unpaired_samples():
    tank=SimpleNamespace(is_rectangular=False,
        section_liquid_areas=lambda *args:(np.array([1.,3.,8.]),np.array([2.,1.,2.]),np.array([1.,2.,3.])))
    stations=np.array([0.,2.,4.,7.,9.])
    sf,bm=unit_tank_weight_integrals(tank,10.,stations)
    np.testing.assert_allclose(sf,[0.,.2,.4,.4,1.])
    np.testing.assert_allclose(bm,[0.,.2,.8,2.,3.4])
    # At9m: BM=9-LCG. The entire declared point mass and moment survive.
    assert bm[-1]==pytest.approx(9.-(.2*1.+.2*3.+.6*8.))


def test_rectangular_tank_retains_uniform_analytic_integrals():
    tank=SimpleNamespace(is_rectangular=True,x_min_m=2.,x_max_m=6.)
    sf,bm=unit_tank_weight_integrals(tank,1.,np.array([0.,2.,4.,6.,9.]))
    np.testing.assert_allclose(sf,[0.,0.,.5,1.,1.])
    np.testing.assert_allclose(bm,[0.,0.,.5,2.,5.])

"""Independent closed-form large-angle moments of a sealed rectangular prism.

The reference integrates vertical columns using antiderivatives, without any
production clipping, polygon moments, waterplane axes or equilibrium solver.
See experiments/hydrostatics/LARGE_ANGLE_REFERENCE.md for the derivation and
the distinction between numerical verification and external KCS validation.
"""
import math

import numpy as np
import pytest

from stowage.vessel.hydrostatics import HullGeometry


def rectangular_prism():
    """Supply an exact test solid through the documented section quadrature."""
    geometry=object.__new__(HullGeometry)
    geometry.lpp_m,geometry.beam_m,geometry.depth_m=100.,12.,8.
    geometry.design_draught_m=4.
    geometry.density_kg_m3=1025.
    geometry.max_z_m,geometry.openings=8.,[]
    geometry.x_m=np.array([50.-50./math.sqrt(3),50.+50./math.sqrt(3)])
    geometry.integration_weights_m=np.array([50.,50.])
    section=np.array([[-6.,0.],[6.,0.],[6.,8.],[-6.,8.]])
    geometry.sections=[[section.copy()],[section.copy()]]
    geometry._finish_geometry()
    return geometry


def closed_form_reference(beam,depth,fraction,phi):
    """Return centreline intercept, wet area and (y,z) centroid at zero trim.

    Wet column height is clip(d + tan(phi)*y, 0, depth). Its area and two
    first moments have the elementary positive-part antiderivatives below.
    A separate fixed-iteration bisection finds d at the requested wet fraction.
    """
    slope=math.tan(phi)

    def integrals(intercept):
        def primitives(t):
            positive=max(t,0.)
            above_deck=max(t-depth,0.)
            area=(positive**2-above_deck**2)/(2*slope)
            y_moment=(positive**3/3-intercept*positive**2/2
                      -above_deck**3/3-(depth-intercept)*above_deck**2/2)/slope**2
            z_moment=((positive**3-above_deck**3)/3-depth*above_deck**2)/(2*slope)
            return np.array([area,y_moment,z_moment])
        return primitives(intercept+slope*beam/2)-primitives(intercept-slope*beam/2)

    target=beam*depth*fraction
    lower,upper=-slope*beam/2,depth+slope*beam/2
    for _ in range(100):
        midpoint=(lower+upper)/2
        if integrals(midpoint)[0]<target:
            lower=midpoint
        else:
            upper=midpoint
    intercept=(lower+upper)/2
    area,y_moment,z_moment=integrals(intercept)
    return intercept,area,(y_moment/area,z_moment/area)


@pytest.mark.parametrize('fraction',[.2,.5,.8])
@pytest.mark.parametrize('degrees',[30.,45.,60.])
def test_large_angle_prism_against_independent_closed_form(fraction,degrees):
    geometry=rectangular_prism()
    phi=math.radians(degrees)
    intercept,area,(y,z)=closed_form_reference(12.,8.,fraction,phi)
    target_volume=100.*12.*8.*fraction
    assert area*100.==pytest.approx(target_volume,abs=1e-9)

    # First verify the public integral at an independently determined plane.
    submerged=geometry.submerged(intercept,intercept,phi)
    assert submerged.volume_m3==pytest.approx(target_volume,abs=1e-8)
    assert submerged.centroid_m==pytest.approx((50.,y,z),abs=2e-9)

    # Symmetry guarantees zero trim at midships LCG, independently of the
    # production solver. The earth-horizontal transverse projection is KN.
    expected_kn=y*math.cos(phi)+z*math.sin(phi)
    mass=target_volume*1025.
    actual_kn=geometry.kn(phi,mass,lcg_m=50.)
    assert actual_kn==pytest.approx(expected_kn,abs=2e-8)
    assert geometry.kn_curve([phi],mass,lcg_m=50.)[0]==pytest.approx(expected_kn,abs=2e-8)

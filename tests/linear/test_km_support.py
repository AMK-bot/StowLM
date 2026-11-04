"""Independent hull integrations validate the continuous polynomial support."""
import numpy as np
import pytest
from scipy.optimize import minimize_scalar

from stowage.linear.physics import _km_support_lower_intercept
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hydrostatics import HullGeometry


@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
@pytest.mark.parametrize('fractions', [(.3,.42),(.5,.62),(.8,.92)])
def test_fast_support_is_tight_and_below_dense_production_hydrostatics(name, fractions):
    hull=HullGeometry.from_params(load_ship_params(name))
    lo,hi=np.array(fractions)*hull.design_draught_m
    rho=hull.density_kg_m3
    endpoints=[hull.hydrostatic_row(float(t)) for t in (lo,hi)]
    slope=(endpoints[1].kmt_m*endpoints[1].displacement_kg-
           endpoints[0].kmt_m*endpoints[0].displacement_kg)/(endpoints[1].displacement_kg-endpoints[0].displacement_kg)
    intercept=_km_support_lower_intercept(hull,lo,hi,rho,slope)
    def reference(draught):
        wet=hull.submerged(float(draught),float(draught),0.,rho)
        return rho*(wet.volume_m3*wet.vcb_m+wet.transverse_inertia_m4-slope*wet.volume_m3)
    grid=np.linspace(lo,hi,41)
    exact=np.array([reference(t) for t in grid])
    assert np.all(intercept<=exact+1e-6)
    # Independently polish the lowest dense neighborhoods using original polygon
    # clipping; this does not reuse any polynomial construction from the helper.
    minima=[float(exact.min())]
    for j in np.argsort(exact)[:3]:
        left,right=grid[max(0,j-1)],grid[min(len(grid)-1,j+1)]
        opt=minimize_scalar(reference,bounds=(left,right),method='bounded',options={'xatol':1e-10})
        minima.append(float(opt.fun))
    expected=min(minima)
    assert 0 <= expected-intercept < .1  # kg m; roundoff guard is much smaller.


class _AsymmetricBox:
    """Analytic box: x length2, y in[1,3], z in[0,4]."""
    beam_m=4.
    sections=[[np.array([[1.,0.],[3.,0.],[3.,4.],[1.,4.]])]]
    integration_weights_m=np.array([2.])

    def submerged(self, ta, tf, heel, density):
        from types import SimpleNamespace
        height=float(np.clip(ta,0.,4.))
        return SimpleNamespace(volume_m3=4*height,vcb_m=height/2,
                               transverse_inertia_m4=4/3 if 0<ta<4 else 0.)


def test_asymmetric_waterplane_keeps_central_inertia_correction():
    intercept=_km_support_lower_intercept(_AsymmetricBox(),.5,3.5,1000.,2.)
    # F(T)=1000*(2*T²+4/3-8*T), minimum at T=2.
    exact=1000*(4/3-8)
    assert 0<=exact-intercept<.001


def test_actual_horizontal_roof_endpoint_is_included():
    intercept=_km_support_lower_intercept(_AsymmetricBox(),1.,4.,1000.,4.)
    # At the actual top plane the fully submerged polygon has no waterline;
    # the left-hand limit retains4/3 m^4 and would miss this minimum.
    assert 0<=-32000.-intercept<.001


def test_clockwise_section_is_rejected_explicitly():
    hull=_AsymmetricBox()
    hull.sections=[[hull.sections[0][0][::-1]]]
    with pytest.raises(ValueError,match='counter-clockwise'):
        _km_support_lower_intercept(hull,.5,3.5,1000.,2.)


def test_one_ulp_polygon_interval_uses_half_open_left_edge():
    from stowage.linear.physics import _polygon_upright_polynomials
    top=np.nextafter(1.,np.inf)
    polygon=np.array([[0.,0.],[1.,0.],[1.,1.],[2.,top],[0.,top]])
    knots,_,_,inertia,width,_,_,_=_polygon_upright_polynomials(polygon)
    span=knots[-1]-knots[-2]
    assert width[-1,0]==pytest.approx(1.)
    assert np.polynomial.polynomial.polyval(span,width[-1])==pytest.approx(2.)
    assert np.polynomial.polynomial.polyval(span,inertia[-1])==pytest.approx(8/3)


def test_one_ulp_support_interval_stays_below_original_clipping():
    from types import SimpleNamespace
    from stowage.vessel.hydrostatics import _moments,_clip,_water_segments
    top=np.nextafter(1.,np.inf)
    polygon=np.array([[0.,0.],[1.,0.],[1.,1.],[2.,top],[0.,top]])
    def submerged(ta,tf,heel,density):
        volume,_,moment=_moments(_clip(polygon,0.,1.,ta))
        segments=_water_segments(polygon,0.,1.,ta)
        area=sum(b[0]-a[0] for a,b in segments)
        first=sum((b[0]**2-a[0]**2)/2 for a,b in segments)
        raw=sum((b[0]**3-a[0]**3)/3 for a,b in segments)
        return SimpleNamespace(volume_m3=volume,vcb_m=moment/volume,
            transverse_inertia_m4=raw-first**2/area if area else 0.)
    hull=SimpleNamespace(sections=[[polygon]],integration_weights_m=np.array([1.]),
        beam_m=2.,submerged=submerged)
    intercept=_km_support_lower_intercept(hull,.5,top,1.,1.)
    for level in (.5,1.,top):
        wet=submerged(level,level,0.,1.)
        assert intercept<=wet.volume_m3*(wet.vcb_m-1.)+wet.transverse_inertia_m4+1e-10

"""The weather branch retains asymmetric actual-liquid transfer and free trim."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import stability_curve
from stowage.checker.stability_loading import LoadingCondition


class FreeTrimGeometry:
    lpp_m=100.
    _stability_table_bilateral=True
    openings=[dict(x_m=50.,y_m=10.,z_m=5.+10.*math.tan(math.radians(40.)))]

    def __init__(self):
        self.solved_angles=[]
        self.centres=[]

    def _axes(self,*args):
        return np.eye(3)[2],np.eye(3)[0],np.eye(3)[1]

    def free_trim_curve(self,angles,mass,lcg,kg,density,*,cg_callback=None,
                        initial_drafts=None,stop_when=None):
        arms=[];drafts=[]
        for phi in angles:
            phi=float(phi)
            centre=cg_callback(phi,0.) if cg_callback else (lcg,0.,kg)
            self.solved_angles.append(phi)
            self.centres.append(centre)
            # Different longitudinal/vertical liquid moments alter both the
            # free-trim solution and the raw lever. The direct negative solve
            # must therefore use its own callback, rather than mirror a value.
            ds=(5.+.02*(centre[0]-50.)+.1*(centre[2]-1.),
                5.-.02*(centre[0]-50.))
            raw=(2.+.3*(centre[0]-50.)+.2*(centre[2]-1.))*math.sin(phi)
            arms.append(raw);drafts.append(ds)
            if stop_when is not None and stop_when(phi,raw,ds):
                break
        return np.asarray(arms),np.asarray(drafts)


def liquid_condition():
    # The callback is patched: no tank integration is needed by this regression.
    return LoadingCondition(1e6,50.,0.,1.,.1,1e5,
                            actual_tanks=(SimpleNamespace(id='asymmetric-liquid'),))


def test_negative_weather_arm_uses_its_own_asymmetric_transfer(monkeypatch):
    monkeypatch.setattr(stability_curve,'liquid_cg',lambda *a:(50.,0.,1.))
    queried=[]
    def correction(condition,angles,trim):
        queried.extend(angles)
        return np.array([(.2 if phi>=0 else .05)*math.sin(phi) for phi in angles])
    geometry=FreeTrimGeometry()
    curve=stability_curve.build_stability_curve(
        geometry,liquid_condition(),1025.,correction_solver=correction)
    assert any(phi<0 for phi in queried)
    assert curve.gz==pytest.approx(1.8*np.sin(curve.angles),abs=1e-12)
    assert curve.negative_gz==pytest.approx(-1.95*np.sin(curve.angles),abs=1e-12)
    assert not np.allclose(curve.negative_gz,-curve.gz)


@pytest.mark.parametrize('axis',[0,2])
def test_asymmetric_longitudinal_or_vertical_cg_forces_negative_free_trim(monkeypatch,axis):
    def liquid_cg(condition,phi,trim):
        centre=[50.,0.,1.]
        centre[axis]+=.4*phi
        return tuple(centre)
    monkeypatch.setattr(stability_curve,'liquid_cg',liquid_cg)
    geometry=FreeTrimGeometry()
    curve=stability_curve.build_stability_curve(
        geometry,liquid_condition(),1025.,
        correction_solver=lambda c,a,t:np.zeros(len(a)))
    assert any(phi<0 for phi in geometry.solved_angles)
    coefficient=.3 if axis==0 else .2
    expected=-(2.-coefficient*.4*curve.angles)*np.sin(curve.angles)
    assert curve.negative_gz==pytest.approx(expected,abs=1e-12)


def test_matching_longitudinal_and_vertical_cg_reuses_hull_but_queries_negative_transfer(monkeypatch):
    # Opposite transverse shifts do not change the longitudinal free-trim
    # balance; the signed actual-transfer correction still needs both sides.
    monkeypatch.setattr(stability_curve,'liquid_cg',
                        lambda c,phi,trim:(50.,.2*math.sin(phi),1.))
    queried=[]
    def correction(c,a,t):
        queried.extend(a)
        return .1*np.sin(a)
    geometry=FreeTrimGeometry()
    curve=stability_curve.build_stability_curve(
        geometry,liquid_condition(),1025.,correction_solver=correction)
    assert any(phi<0 for phi in queried)
    assert not any(phi<0 for phi in geometry.solved_angles)
    assert curve.negative_gz==pytest.approx(-curve.gz,abs=1e-12)


def test_method2_keeps_virtual_rise_and_does_not_query_actual_liquid_cg(monkeypatch):
    def forbidden(*args):
        pytest.fail('method .2 must not query actual liquid CG')
    monkeypatch.setattr(stability_curve,'liquid_cg',forbidden)
    geometry=FreeTrimGeometry()
    curve=stability_curve.build_stability_curve(
        geometry,liquid_condition(),1025.,free_surface_method=2)
    assert not any(phi<0 for phi in geometry.solved_angles)
    assert curve.negative_gz==pytest.approx(-curve.gz,abs=1e-12)
    assert geometry.centres
    assert all(centre==pytest.approx((50.,0.,1.1)) for centre in geometry.centres)


def test_unqualified_hull_symmetry_cannot_skip_negative_free_trim(monkeypatch):
    monkeypatch.setattr(stability_curve,'liquid_cg',lambda *a:(50.,0.,1.))
    geometry=FreeTrimGeometry()
    geometry._stability_table_bilateral=False
    stability_curve.build_stability_curve(
        geometry,liquid_condition(),1025.,
        correction_solver=lambda c,a,t:np.zeros(len(a)))
    assert any(phi<0 for phi in geometry.solved_angles)

"""Regression: assessment endpoints never require an arbitrary 89.99° solve."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker.stability_curve import build_stability_curve


class EndpointGeometry:
    lpp_m=100.
    def __init__(self, *, flood=42., vanish=30., sealed=False):
        self.vanish=math.radians(vanish)
        self.calls=[]
        self.openings=[] if sealed else [dict(x_m=50.,y_m=7.,z_m=5.+7.*math.tan(math.radians(flood)))]
    def _axes(self,*args):
        phi=args[-1]
        return np.array([0.,-math.sin(phi),math.cos(phi)]),np.array([1.,0.,0.]),np.array([0.,math.cos(phi),math.sin(phi)])
    def free_trim_curve(self,angles,*args,cg_callback=None,initial_drafts=None,stop_when=None):
        arms=[];drafts=[]
        for phi in angles:
            self.calls.append(float(phi))
            if phi>math.radians(60.):
                raise ValueError('irrelevant high-angle solve requested')
            arm=math.sin(math.pi*phi/self.vanish)
            arms.append(arm);drafts.append((5.,5.))
            if stop_when is not None and stop_when(float(phi),arm,(5.,5.)):
                break
        return np.array(arms),np.array(drafts)


def condition():
    return SimpleNamespace(actual_tanks=(),mass_kg=1.,lcg_m=50.,kg_m=0.,tcg_m=0.)


def build(geometry):
    return build_stability_curve(geometry,condition(),1025.,
        correction_solver=lambda c,angles,trim:np.zeros(len(angles)))


def test_stop_at_vanishing_retains_independent_windward_coverage():
    g=EndpointGeometry()
    curve=build(g)
    assert math.degrees(curve.flooding_angle)==pytest.approx(42.,abs=1e-6)
    assert math.degrees(curve.angles[-1])==pytest.approx(40.)
    root=np.argmin(abs(curve.angles-math.radians(30.)))
    assert curve.gz[root]==pytest.approx(0.,abs=1e-9)
    assert max(g.calls)<math.radians(50.)


def test_early_leeward_flooding_does_not_truncate_windward_curve():
    curve=build(EndpointGeometry(flood=20.,vanish=60.))
    assert math.degrees(curve.flooding_angle)==pytest.approx(20.,abs=1e-6)
    assert math.degrees(curve.angles[-1])==pytest.approx(35.)
    assert curve.negative_gz[-1]<0


def test_sealed_envelope_with_vanishing_angle_never_solves_ninety():
    g=EndpointGeometry(sealed=True)
    curve=build(g)
    assert curve.flooding_angle==math.pi/2
    assert max(g.calls)<=math.radians(40.)


def test_closed_opening_is_excluded_from_downflooding():
    g=EndpointGeometry()
    g.openings.insert(0,dict(x_m=50.,y_m=7.,z_m=5.,closure='weathertight_closable',progressive_flooding=True))
    assert math.degrees(build(g).flooding_angle)==pytest.approx(42.,abs=1e-6)


def test_deck_edge_uses_deck_breadth_rather_than_maximum_submerged_breadth():
    from stowage.checker.stability import _deck_edge_angle
    # Tumblehome: maximum half breadth8m, true deck edge only4m from CL.
    polygon=np.array([[-8.,0.],[8.,0.],[4.,10.],[-4.,10.]])
    geometry=SimpleNamespace(depth_m=10.,lpp_m=100.,x_m=np.array([50.]),sections=[[polygon]])
    actual=_deck_edge_angle(geometry,np.radians([0.,30.,60.]),np.array([[6.,6.]]*3))
    assert actual==pytest.approx(math.atan(4./4.),abs=1e-9)


def test_vanishing_root_replaces_the_coarse_step_that_crossed_zero():
    curve=build(EndpointGeometry(flood=55.,vanish=40.4))
    assert math.degrees(curve.angles[-1])==pytest.approx(40.4,abs=1e-6)
    assert curve.gz[-1]==pytest.approx(0.,abs=1e-9)


def test_post_vanishing_work_is_retained_for_the_separate_forty_degree_areas():
    curve=build(EndpointGeometry(flood=36.,vanish=13.5))
    assert math.degrees(curve.angles[-1])==pytest.approx(36.,abs=1e-6)
    assert curve.flooding_angle==pytest.approx(curve.angles[-1],abs=1e-9)


class CoverageEndpointGeometry(EndpointGeometry):
    def __init__(self,*,flood=50.5,available=51.,error_type=None):
        from stowage.checker.stability_tables import OutsideTable
        super().__init__(flood=flood,vanish=60.)
        self.available=math.radians(available)
        self.error_type=error_type or OutsideTable
        self.attempts=[]
    def free_trim_curve(self,angles,*args,**kwargs):
        self.attempts.extend(map(float,angles))
        if any(phi>self.available for phi in angles):
            raise self.error_type('condition exceeds actual KN domain')
        return super().free_trim_curve(angles,*args,**kwargs)


def test_downflooding_refines_a_coarse_probe_outside_table():
    geometry=CoverageEndpointGeometry()
    curve=build(geometry)
    assert math.degrees(curve.flooding_angle)==pytest.approx(50.5,abs=1e-6)
    assert math.degrees(curve.angles[-1])==pytest.approx(50.5,abs=1e-6)
    assert math.radians(55.) in geometry.attempts
    assert any(math.radians(50.5)<phi<math.radians(51.) for phi in geometry.calls)
    # At the reported root the true opening clearance vanishes.
    opening=geometry.openings[0]
    assert opening['z_m']-(5.+7.*math.tan(curve.flooding_angle))==pytest.approx(0.,abs=1e-8)


def test_no_downflooding_before_domain_boundary_retains_outside_table():
    from stowage.checker.stability_tables import OutsideTable
    geometry=CoverageEndpointGeometry(flood=55.)
    with pytest.raises(OutsideTable,match='actual KN domain'):
        build(geometry)
    # Eleven successful 0..50deg solves, one failed55deg,24 bounded probes.
    assert len(geometry.attempts)<=36
    assert max(phi for phi in geometry.calls)<=math.radians(51.)


def test_downflooding_does_not_swallow_other_physics_failures():
    geometry=CoverageEndpointGeometry(error_type=ValueError)
    with pytest.raises(ValueError,match='actual KN domain'):
        build(geometry)
    assert len(geometry.attempts)==12


def test_downflooding_boundary_refinement_polls_shared_budget(monkeypatch):
    from stowage.checker import stability_curve
    from stowage.checker.stability_budget import StabilityTimeout
    count=0
    def check(label=''):
        nonlocal count
        if label=='flooding boundary refinement':
            count+=1
            if count==3: raise StabilityTimeout('test budget exhausted')
    monkeypatch.setattr(stability_curve,'check_budget',check)
    with pytest.raises(StabilityTimeout,match='budget exhausted'):
        build(CoverageEndpointGeometry(flood=55.))
    assert count==3

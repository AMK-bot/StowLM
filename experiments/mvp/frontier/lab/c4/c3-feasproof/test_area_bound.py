"""Small native geometry/liquid inclusion checks; no fleet maps or benchmarks."""
from fractions import Fraction
import math
from types import SimpleNamespace

import numpy as np

import area_bound as area
from stowage.vessel.hydrostatics import HullGeometry


class ToyHull(HullGeometry):
    """Native polygon integrator over a10x4x4 rectangular prism."""
    def __init__(self):
        self.lpp_m = 10.
        self.beam_m = 4.
        self.depth_m = 4.
        self.design_draught_m = 2.
        self.density_kg_m3 = 1000.
        self.max_z_m = 4.
        self.x_m = np.array([2.5,7.5])
        self.integration_weights_m = np.array([5.,5.])
        polygon = np.array([[-2.,0.],[2.,0.],[2.,4.],[-2.,4.]])
        self.sections = ((polygon.copy(),),(polygon.copy(),))


def _toy(with_tank=False):
    geometry = ToyHull()
    tanks = []
    raw_tanks = []
    if with_tank:
        tanks = [SimpleNamespace(id='tank',kind='ballast',capacity_m3=24.,content_density_kg_m3=1000.)]
        raw_tanks = [dict(id='tank',geometry=dict(shape='rectangular_prism',
            x_min_m=0.,x_max_m=6.,y_min_m=-1.,y_max_m=1.,z_min_m=0.,z_max_m=2.))]
    instance = SimpleNamespace(vessel=SimpleNamespace(tanks=tanks),
                               port=SimpleNamespace(water_density_kg_m3=1000.))
    params = dict(tanks=raw_tanks)
    oracle = area.AreaOracle(instance,geometry=geometry,params=params)
    return instance,geometry,oracle


def _assert_contains(interval,value,tolerance=1.e-8):
    assert interval[0]-tolerance <= value <= interval[1]+tolerance


def test_basic_interval_operations_enclose_exact_binary_arithmetic():
    cases = [((.1,.2),(.3,.4)),((-1.3,.7),(-.4,.9)),((-2.,-1.),(.3,1.7))]
    for a,b in cases:
        result = area._add(a,b)
        assert Fraction(result[0]) <= Fraction(a[0])+Fraction(b[0])
        assert Fraction(result[1]) >= Fraction(a[1])+Fraction(b[1])
        result = area._mul(a,b)
        products = [Fraction(x)*Fraction(y) for x in a for y in b]
        assert Fraction(result[0]) <= min(products)
        assert Fraction(result[1]) >= max(products)
    result = area._div((.1,.2),(.3,.4))
    quotients = [Fraction(x)/Fraction(y) for x in (.1,.2) for y in (.3,.4)]
    assert Fraction(result[0]) <= min(quotients)
    assert Fraction(result[1]) >= max(quotients)
    assert area._square((-2.,1.)) == (0.,4.)
    lo,hi = area._sqrt((2.,3.))
    assert Fraction(lo)**2 <= 2
    assert Fraction(hi)**2 >= 3


def test_rational_backend_has_fraction_binary_float_semantics():
    # This exact identity must hold for both optional mpq and Fraction paths.
    for value in (.1,-.3,math.nextafter(1.,math.inf),2.**-40):
        reference = Fraction(value)
        assert area.F(value) == area.F(reference.numerator,reference.denominator)
        assert area._down(area.F(value)) == value
        assert area._up(area.F(value)) == value


def test_direct_shell_encloses_native_submerged_integrals_at_random_poses():
    _,geometry,oracle = _toy()
    rng = np.random.default_rng(19660101)
    for repeat in range(24):
        heel = rng.uniform(.02,1.1)
        ta,tf = rng.uniform(.4,3.6,2)
        alpha = math.atan((tf-ta)/geometry.lpp_m)
        offset = math.cos(alpha)*math.cos(heel)*ta
        hp,ap,op = rng.uniform(.0001,.02),rng.uniform(.0001,.01),rng.uniform(.001,.05)
        result = oracle.direct_integral_bounds((heel-hp,heel+hp),(alpha-ap,alpha+ap),(offset-op,offset+op))
        exact = geometry.submerged(ta,tf,heel,1000.)
        expected = [exact.volume_m3,exact.volume_m3*exact.lcb_m,
                    exact.volume_m3*exact.tcb_m,exact.volume_m3*exact.vcb_m]
        for key,value in zip(('volume','mx','my','mz'),expected):
            _assert_contains(result[key],value)
        assert result['physical_outward']
        assert not result['checker_outward']


def test_rectangular_upright_volume_and_heave_contraction():
    _,geometry,oracle = _toy()
    result = oracle.direct_integral_bounds((0.,0.),(0.,0.),(1.37,1.37))
    _assert_contains(result['volume'],10*4*1.37,tolerance=1.e-11)
    mass = 1000*10*4*1.37
    contracted = oracle.contract_offset((0.,0.),(0.,0.),(0.,4.),mass_kg=(mass,mass),steps=10)
    lo,hi = contracted['offset_m']
    assert lo <= 1.37 <= hi
    assert hi-lo < .05
    # A root at a bisection boundary must remain: contraction need not make
    # progress when both half boxes include the residual tolerance band.
    middle = oracle.contract_offset((0.,0.),(0.,0.),(0.,4.),mass_kg=(80000.,80000.),steps=4)
    assert middle['offset_m'][0] <= 2. <= middle['offset_m'][1]


def test_classifier_retains_native_longitudinal_root_and_bounds_raw_gz():
    _,geometry,oracle = _toy()
    for heel,alpha in ((.2,.05),(.6,-.1),(1.0,.15)):
        ta = 2.
        tf = ta+geometry.lpp_m*math.tan(alpha)
        submerged = geometry.submerged(ta,tf,heel,1000.)
        kg = 2.5
        lcg = submerged.lcb_m+math.tan(alpha)*(submerged.vcb_m-kg)
        mass = 1000*submerged.volume_m3
        offset = math.cos(alpha)*math.cos(heel)*ta
        result = oracle.classify_pose((heel-1.e-9,heel+1.e-9),
            (alpha-1.e-9,alpha+1.e-9),(offset-1.e-8,offset+1.e-8),
            mass_kg=(mass-1.e-5,mass+1.e-5),kg_m=(kg-1.e-8,kg+1.e-8),
            lcg_m=(lcg-1.e-8,lcg+1.e-8))
        assert result['possible']
        across = geometry._axes(ta,tf,heel)[2]
        raw = float(np.dot(np.array([submerged.lcb_m-lcg,submerged.tcb_m,submerged.vcb_m-kg]),across))
        assert result['gz_upper_m'] >= raw-1.e-8
    impossible = oracle.classify_pose((0.,0.),(0.,0.),(0.,4.),
        mass_kg=(400000.,400001.),kg_m=(1.,2.),lcg_m=(4.,6.))
    assert not impossible['possible']
    assert impossible['reason'] == 'mass residual interval disjoint'
    assert not impossible['checker_outward']


def test_signed_direct_tank_correction_encloses_actual_negative_transfer():
    from stowage.checker.stability_loading import liquid_centroid
    from stowage.vessel.tanks import TankGeometry
    _,geometry,oracle = _toy(with_tank=True)
    solid = TankGeometry(x_min_m=0.,x_max_m=6.,y_min_m=-1.,y_max_m=1.,z_min_m=0.,z_max_m=2.)
    volume = 12.
    reference = solid.sounding(volume)
    ref = np.array([reference.lcg_m,reference.tcg_m,reference.vcg_m])
    found_negative = False
    for heel,alpha in ((.01,.5),(.01,-.5),(.3,.1),(.5,0.)):
        axes = area.pose_axes((heel-1.e-9,heel+1.e-9),(alpha-1.e-9,alpha+1.e-9))
        bounds = oracle.tank_envelopes({'tank':(12000.,12000.)},(120000.,120000.),axes)
        centre = liquid_centroid(solid,volume,heel,-alpha)
        across = geometry._axes(2.,2.+geometry.lpp_m*math.tan(alpha),heel)[2]
        correction = .1*float((centre-ref)@across)
        found_negative |= correction < -1.e-8
        assert bounds['correction_lower_m'] <= correction+1.e-8
        assert 0. <= (centre-ref)[2]+1.e-8
    assert found_negative


def test_heel_coverage_detection_requires_a_complete_continuous_cover():
    assert area._covers_heel_domain([(0.,math.pi/2)])
    assert area._covers_heel_domain([(1.,math.pi/2),(0.,1.)])
    assert area._covers_heel_domain([(0.,1.1),(1.,math.pi/2)])
    assert not area._covers_heel_domain([(0.,.5),(.6,math.pi/2)])
    assert not area._covers_heel_domain([(.01,math.pi/2)])


def test_full_custom_heel_cover_keeps_physical_scope_and_rejected_checker_flags():
    class FakeOracle:
        L = 10.
        rho = 1000.
        integral_queries = 0
        def offset_domain(self,phi,alpha):
            return (0.,1.)
        def classify_pose(self,*args,**kwargs):
            return dict(possible=False,gz_upper_m=-math.inf,checker_outward=False)
    result = area.build_area_envelope(object(),mass_kg=(1.,1.),kg_m=(1.,1.),lcg_m=(1.,1.),
        oracle=FakeOracle(),heel_intervals=[(0.,math.pi/2)],offset_contraction_steps=0,
        max_pose_boxes=8,max_depth=0)
    assert result['physical_outward']
    assert result['full_heel_domain']
    assert result['full_free_trim_domain']
    assert not result['checker_outward']  # uncertified rejections cannot vanish


def test_knot_area_assembly_charges_only_one_flood_gap_bonus(monkeypatch):
    def fake(instance,*,heel_intervals,**kwargs):
        rows = []
        for lo,hi in heel_intervals:
            point = hi-lo < 1.e-10
            rows.append(dict(heel_rad=[lo,hi],gz_upper_m=2. if point else 4.,checker_outward=False))
        return dict(slab_bounds=rows,phase_inputs={},direct_integral_queries=len(rows))
    monkeypatch.setattr(area,'build_area_envelope',fake)
    result = area.build_knot_area_envelope(object(),mass_kg=(1.,1.),kg_m=(1.,1.),
        lcg_m=(1.,1.),oracle=object(),max_pose_boxes=8,offset_contraction_steps=0)
    expected_base = 2*math.radians(89.9)
    expected_bonus = math.radians(2.)
    assert abs(result['base_positive_knot_area_upper_mrad']-expected_base) < 1.e-12
    assert abs(result['flood_endpoint_bonus_upper_mrad']-expected_bonus) < 1.e-12
    assert abs(result['area_upper_mrad']-(expected_base+expected_bonus+1.e-8)) < 1.e-11
    assert result['physical_outward']
    assert not result['checker_outward']

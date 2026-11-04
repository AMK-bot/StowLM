"""KCS free-trim numerical conditioning and curve continuation regressions."""
import numpy as np
import pytest

from stowage.checker.stability_geometry import StabilityGeometry
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hydrostatics import HullGeometry


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
def test_kcs_large_heel_equilibria_retain_physical_kg(name):
    params = load_ship_params(name)
    assert params["hull_form"]["family"] == "stowlm_offsets_v1"
    geometry = StabilityGeometry(params)
    # Design displacement/LCB comes from the production offsets, with a physical
    # KG of 7 m; setting KG to zero here would change the free-trim equations.
    upright = geometry.submerged(geometry.design_draught_m, geometry.design_draught_m)
    mass, lcg, kg, rho = upright.displacement_kg, upright.lcb_m, 7., 1025.
    angles = np.radians([0., 30., 60., 75., 85., 89.99])
    gz, drafts = geometry.free_trim_curve(angles, mass, lcg, kg, rho)
    reference = HullGeometry(params)
    for phi, lever, (ta, tf) in zip(angles, gz, drafts):
        # Independently integrate using the reference per-section clipping path.
        s = reference.submerged(ta, tf, phi, rho)
        _, along, transverse = reference._axes(ta, tf, phi)
        delta = np.asarray(s.centroid_m)-[lcg, 0., kg]
        residual = np.hypot((s.displacement_kg-mass)/mass,
                            np.dot(delta, along)/geometry.lpp_m)
        assert residual <= 2e-6
        assert lever == pytest.approx(np.dot(delta, transverse), abs=2e-9)


def test_free_trim_segments_continue_and_stop_before_unused_high_angles():
    geometry = StabilityGeometry(load_ship_params("feeder_500"))
    s = geometry.submerged(geometry.design_draught_m, geometry.design_draught_m)
    args = (s.displacement_kg, s.lcb_m, 7., 1025.)
    angles = np.radians([0., 10., 20., 30., 40.])
    whole, all_drafts = geometry.free_trim_curve(angles, *args)
    first, first_drafts = geometry.free_trim_curve(angles[:2], *args)
    later, later_drafts = geometry.free_trim_curve(angles[2:], *args,
                                                 initial_drafts=first_drafts[-1])
    assert np.r_[first, later] == pytest.approx(whole, abs=2e-8)
    assert later_drafts == pytest.approx(all_drafts[2:], abs=2e-7)
    visited = []
    def stop(phi, gz, drafts):
        visited.append((phi, gz, drafts))
        return phi >= angles[2]
    stopped, drafts = geometry.free_trim_curve(np.r_[angles, np.pi/2], *args,
                                              stop_when=stop)
    assert len(stopped) == len(visited) == 3
    assert stopped == pytest.approx(whole[:3], abs=2e-8)
    assert drafts == pytest.approx(all_drafts[:3], abs=2e-7)


def test_free_trim_validates_initial_drafts():
    geometry = StabilityGeometry(load_ship_params("feeder_500"))
    with pytest.raises(ValueError, match="initial_drafts"):
        geometry.free_trim_curve([], 1., 1., 1., 1025., initial_drafts=[float("nan"), 2.])


@pytest.mark.parametrize('name',['feeder_500','feeder_800','feeder_1700'])
def test_batched_edge_integrals_match_production_polygon_clipping_at_roundoff(name):
    geometry=StabilityGeometry(load_ship_params(name))
    rng=np.random.default_rng(31031)
    states=[(-geometry.depth_m,-geometry.depth_m,0.),
            (3*geometry.depth_m,3*geometry.depth_m,0.),
            (geometry.design_draught_m,geometry.design_draught_m,np.radians(89.99))]
    for _ in range(12):
        mean=rng.uniform(.3,1.15)*geometry.design_draught_m
        trim=rng.uniform(-.04,.04)*geometry.lpp_m
        states.append((mean+trim/2,mean-trim/2,rng.uniform(-1.3,1.3)))
    for ta,tf,phi in states:
        actual=geometry.submerged(ta,tf,phi,1000.)
        reference=HullGeometry.submerged(geometry,ta,tf,phi,1000.)
        assert actual.volume_m3==pytest.approx(reference.volume_m3,rel=3e-14,abs=2e-10)
        assert actual.centroid_m==pytest.approx(reference.centroid_m,rel=3e-14,abs=2e-11)
        assert actual.displacement_kg==pytest.approx(reference.displacement_kg,rel=3e-14,abs=2e-7)


@pytest.fixture
def box_offsets(tmp_path):
    import hashlib,json
    path=tmp_path/'box.json'
    path.write_text(json.dumps(dict(schema='stowlm_offsets_json_v1',stations=[
        dict(x_m=x,half_section_yz_m=[[0,0],[2,0],[2,1],[2,3]]) for x in (-1,0,5,10,11)])))
    return dict(hull=dict(lpp_m=10,beam_m=4,design_draught_m=1,depth_m=3,water_density_kg_m3=1000),
        hull_form=dict(family='stowlm_offsets_v1',offsets_path=str(path),
            offsets_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),offsets_format='stowlm_offsets_json_v1',
            reference_lpp_m=10,reference_beam_m=4,reference_draught_m=1,reference_depth_m=3),
        envelope=dict(deck_z_m=3,hatch_coamings=[],openings=[]))


@pytest.mark.parametrize('mean,trim,phi',[
    (-1.,0.,0.),(0.,0.,0.),(1.,0.,0.),(4.,0.,0.),
    (1.,.6,.45),(1.,-.6,-.45),(.1,.7,1.4)])
def test_batched_edge_integrals_match_box_clipping(box_offsets,mean,trim,phi):
    geometry=StabilityGeometry(box_offsets,nx=8,nz=8)
    ta,tf=mean+trim/2,mean-trim/2
    actual=geometry.submerged(ta,tf,phi)
    reference=HullGeometry.submerged(geometry,ta,tf,phi)
    assert actual.volume_m3==pytest.approx(reference.volume_m3,rel=1e-14,abs=1e-12)
    assert actual.centroid_m==pytest.approx(reference.centroid_m,rel=1e-14,abs=1e-12)
    if mean==1. and trim==0. and phi==0.:
        assert actual.volume_m3==pytest.approx(48.)
        assert actual.centroid_m==pytest.approx((5.,0.,.5))


def test_bilateral_contours_preserve_cycles_multiplicity_and_float_bits():
    from stowage.checker.stability_geometry import _bilateral_sections,_contour_cycle
    port=np.array([[0.,3.],[2.,3.],[2.,4.],[0.,4.]])
    starboard=port*[-1.,1.]
    assert _bilateral_sections([[port,np.roll(starboard[::-1],2,axis=0)]])
    assert not _bilateral_sections([[port,starboard,port]])
    changed=starboard.copy(); changed[1,0]=np.nextafter(changed[1,0],-np.inf)
    assert not _bilateral_sections([[port,changed]])
    assert _contour_cycle(port)==_contour_cycle(np.roll(port[::-1],1,axis=0))
    assert _contour_cycle(port)!=_contour_cycle(port[[0,2,1,3]])


def test_one_sided_buoyant_coaming_does_not_enable_reflection(box_offsets):
    box_offsets['envelope']['hatch_coamings']=[dict(x_min_m=2.,x_max_m=4.,
        y_min_m=0.,y_max_m=1.,z_min_m=3.,z_max_m=4.)]
    geometry=StabilityGeometry(box_offsets,nx=8,nz=8)
    assert geometry._stability_table_bilateral is False


@pytest.mark.parametrize('name',['feeder_500','feeder_800','feeder_1700'])
def test_direct_production_hulls_prove_exact_reflection_and_free_trim(name):
    geometry=StabilityGeometry(load_ship_params(name))
    assert geometry._stability_table_bilateral is True
    upright=geometry.submerged(geometry.design_draught_m+.2,geometry.design_draught_m-.2)
    kg=7.;slope=-.4/geometry.lpp_m
    lcg=upright.lcb_m+slope*(upright.vcb_m-kg)
    angles=np.radians([10.,35.,65.])
    args=(upright.displacement_kg,lcg,kg,1025.)
    positive,positive_drafts=geometry.free_trim_curve(angles,*args)
    negative,negative_drafts=geometry.free_trim_curve(-angles,*args)
    assert negative==pytest.approx(-positive,abs=3e-10)
    assert negative_drafts==pytest.approx(positive_drafts,abs=3e-9)

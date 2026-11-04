"""Exact multi-attitude integration and root-reuse performance regressions."""
import numpy as np
import pytest
from scipy.optimize import root

from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_budget import check_budget
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hydrostatics import HullGeometry, Submerged
from stowage.vessel.tanks import TankGeometry


@pytest.fixture(scope='module', params=['feeder_500', 'feeder_800', 'feeder_1700'])
def geometry(request):
    return StabilityGeometry(load_ship_params(request.param))


def test_multi_attitude_integrals_match_independent_polygon_clipping(geometry):
    d = geometry.design_draught_m
    pairs = np.array([[d, d], [d+.7, d-.4], [d-.6, d+.8], [-20., -20.], [50., 50.]])
    for heel in (-1.1, -.3, 0., .5, 1.2):
        actual = geometry._submerged_many(pairs, heel, 1000.)
        for (ta, tf), got in zip(pairs, actual):
            expected = HullGeometry.submerged(geometry, ta, tf, heel, 1000.)
            assert got.volume_m3 == pytest.approx(expected.volume_m3, rel=3e-14, abs=2e-10)
            assert got.centroid_m == pytest.approx(expected.centroid_m, rel=3e-14, abs=2e-11)


def test_free_trim_does_not_reintegrate_an_identical_state(monkeypatch, geometry):
    d = geometry.design_draught_m
    upright = geometry.submerged(d, d)
    original = geometry.submerged
    visited = []
    def tracked(ta, tf, phi=0., rho=None):
        visited.append((float(ta), float(tf), float(phi), rho))
        return original(ta, tf, phi, rho)
    monkeypatch.setattr(geometry, 'submerged', tracked)
    geometry.free_trim_curve([0., .1, .2], upright.displacement_kg, upright.lcb_m, 4., 1025.)
    assert visited
    assert len(visited) == len(set(visited))


@pytest.mark.parametrize('moving_liquid', [False, True])
def test_free_trim_batched_jacobian_matches_independent_root_and_clipping(geometry, moving_liquid):
    d, kg, rho = geometry.design_draught_m, 5., 1025.
    upright = HullGeometry.submerged(geometry, d+.2, d-.2, 0., rho)
    mass, lcg = upright.displacement_kg, upright.lcb_m
    # The callback varies liquid mass moments with both heel and physical trim;
    # these independent values must never leak between root or heel states.
    callback = (lambda phi, trim: [lcg+.4*np.sin(phi)+.7*trim, 0., kg+.1*np.sin(phi)**2]) if moving_liquid else None
    angles = np.radians([-35., -10., 0., 12., 40., 65.])
    got, drafts = geometry.free_trim_curve(angles, mass, lcg, kg, rho, cg_callback=callback)
    seed = np.array([d, d])
    for phi, lever, actual in zip(angles, got, drafts):
        def residual(pair):
            ta, tf = pair
            state = HullGeometry.submerged(geometry, ta, tf, phi, rho)
            along = geometry._axes(ta, tf, phi)[1]
            centre = callback(phi, np.arctan((ta-tf)/geometry.lpp_m)) if callback else [lcg, 0., kg]
            return [(state.displacement_kg-mass)/mass,
                    np.dot(np.asarray(state.centroid_m)-centre, along)/geometry.lpp_m]
        reference = root(residual, seed, tol=1e-9, options={'eps': 1e-8})
        assert np.linalg.norm(residual(reference.x)) < 2e-8
        seed = reference.x
        assert actual == pytest.approx(reference.x, abs=2e-7)
        independent = HullGeometry.submerged(geometry, *actual, phi, rho)
        across = geometry._axes(*actual, phi)[2]
        expected = np.dot(np.asarray(independent.centroid_m)-[lcg, 0., kg], across)
        assert lever == pytest.approx(expected, abs=2e-9)
        assert np.linalg.norm(residual(actual)) <= 2e-6


@pytest.mark.parametrize('moving_liquid', [False, True, 'exact_tank'])
def test_exact_old_vs_new_curve_sweep(geometry, moving_liquid):
    legacy = object.__new__(_LegacyGeometry)
    legacy.__dict__.update(geometry.__dict__)
    # monkeypatch restores an instance-bound method after instrumentation;
    # never copy that binding onto a different reference object.
    legacy.__dict__.pop('submerged',None)
    d, kg, rho = geometry.design_draught_m, 5., 1025.
    state = legacy.submerged(d+.35, d-.2, 0., rho)
    lcg = state.lcb_m
    callback = (lambda phi, trim: [lcg+.7*np.sin(phi)+1.3*trim, 0., kg+.2*np.cos(phi)]) if moving_liquid else None
    if moving_liquid == 'exact_tank':
        # Independent tilted-solid oracle, without qualified interpolation or
        # consumer liquid memoization: both heel and trim move the actual liquid.
        tank = TankGeometry(5.,25.,-5.,5.,0.,3.)
        volume = .37*tank.capacity_m3
        reference = np.asarray(tank.liquid(volume).centroid_m)
        fraction = 1025.*volume/state.displacement_kg
        callback = lambda phi, trim: np.asarray([lcg,0.,kg])+fraction*(
            np.asarray(tank.liquid(volume,phi,trim).centroid_m)-reference)
    angles = np.radians([-65., -30., -8., 0., 15., 45., 70.])
    arguments = angles,state.displacement_kg,lcg,kg,rho
    expected, old_drafts = legacy.free_trim_curve(*arguments,cg_callback=callback)
    actual, new_drafts = geometry.free_trim_curve(*arguments,cg_callback=callback)
    assert actual == pytest.approx(expected, abs=2e-8)
    assert new_drafts == pytest.approx(old_drafts, abs=2e-7)


def test_box_multi_attitude_integrals_and_curve_closed_form():
    geometry = object.__new__(StabilityGeometry)
    geometry.lpp_m,geometry.beam_m,geometry.depth_m = 80.,16.,12.
    geometry.design_draught_m,geometry.max_z_m,geometry.density_kg_m3 = 5.,12.,1025.
    geometry.x_m = np.array([40.-40./np.sqrt(3),40.+40./np.sqrt(3)])
    geometry.integration_weights_m = np.array([40.,40.])
    section = np.array([[-8.,0.],[8.,0.],[8.,12.],[-8.,12.]])
    geometry.sections = [[section.copy()],[section.copy()]]
    geometry._p = np.array([section.copy(),section.copy()])
    geometry._q = np.roll(geometry._p,-1,axis=1)
    geometry._px,geometry._pw = geometry.x_m,geometry.integration_weights_m
    geometry._prepare_edges()
    for got in geometry._submerged_many([[5.,5.],[5.3,4.7]],0.,1025.):
        assert got.volume_m3 == pytest.approx(80.*16.*5.,abs=1e-10)
    angles = np.radians([0.,.1,2.,8.,15.])
    kg,bm = 4.,16.**2/(12.*5.)
    gz,drafts = geometry.free_trim_curve(angles,80.*16.*5.*1025.,40.,kg,1025.)
    expected = (5./2+bm-kg+.5*bm*np.tan(angles)**2)*np.sin(angles)
    assert gz == pytest.approx(expected,abs=2e-8)
    assert drafts == pytest.approx(np.full((len(angles),2),5.),abs=1e-7)


@pytest.fixture
def isolated_geometry(geometry):
    isolated = object.__new__(StabilityGeometry)
    isolated.__dict__.update(geometry.__dict__)
    for name in ('submerged','full_submerged','_submerged_integrals'):
        isolated.__dict__.pop(name,None)
    isolated._prepare_edges()
    return isolated


@pytest.mark.parametrize('method', ['submerged', 'full_submerged', '_submerged_many'])
def test_exact_state_cache_reuses_geometry_without_exposing_mutable_arrays(monkeypatch, isolated_geometry, method):
    g = isolated_geometry
    def evaluate():
        if method == '_submerged_many':
            return g._submerged_many([[5.3,4.7],[5.3,4.7]],.1,1025.)[0]
        return getattr(g,method)(5.3,4.7,.1,1025.)
    initial = evaluate()
    originals = {name:getattr(initial,name).copy() for name in
                 ('x_m','sectional_areas_m2','integration_weights_m')}
    def forbidden(*args, **kwargs):
        raise AssertionError('an exact cached attitude must not be reintegrated')
    if method == 'full_submerged':
        monkeypatch.setattr(HullGeometry,'submerged',forbidden)
    else:
        monkeypatch.setattr(g,'_submerged_integrals',forbidden)
    repeated = evaluate()
    for name in originals:
        getattr(initial,name)[:] = -1000.
        getattr(repeated,name)[:] = -2000.
    repeated = evaluate()
    for name,original in originals.items():
        assert getattr(repeated,name) == pytest.approx(original)
    assert repeated.volume_m3 == initial.volume_m3


def test_exact_state_cache_distinguishes_density_angles_lpp_and_single_ulps(monkeypatch, isolated_geometry):
    g = isolated_geometry
    original = g._submerged_integrals
    calls = []
    def tracked(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(g,'_submerged_integrals',tracked)
    baseline = g.submerged(5.3,4.7,.1,1025.)
    fresh = g.submerged(5.3,4.7,.1,1000.)
    assert fresh.displacement_kg == pytest.approx(baseline.displacement_kg*1000./1025.)
    g.submerged(5.3,4.7,np.nextafter(.1,1.),1025.)
    g.submerged(np.nextafter(5.3,6.),4.7,.1,1025.)
    g.lpp_m += 1.
    g.submerged(5.3,4.7,.1,1025.)
    assert len(calls) == 5
    g.submerged(5.3,4.7,.1,1025.)
    assert len(calls) == 5


@pytest.mark.parametrize('full', [False, True])
def test_preparing_changed_edges_invalidates_exact_state_cache(isolated_geometry, full):
    g = isolated_geometry
    evaluate = g.full_submerged if full else g.submerged
    baseline = evaluate(5.3,4.7,.1,1025.)
    g.sections = [[polygon*np.array([1.1,1.]) for polygon in section] for section in g.sections]
    g._p = g._p*np.array([1.1,1.])
    g._q = np.roll(g._p,-1,axis=1)
    g._prepare_edges()
    changed = evaluate(5.3,4.7,.1,1025.)
    independent = HullGeometry.submerged(g,5.3,4.7,.1,1025.)
    assert changed.volume_m3 != baseline.volume_m3
    assert changed.volume_m3 == pytest.approx(independent.volume_m3,rel=3e-14)


@pytest.mark.parametrize('full', [False, True])
def test_exact_state_caches_are_bounded(monkeypatch, isolated_geometry, full):
    g = isolated_geometry
    monkeypatch.setattr(g,'_FULL_STATE_CACHE_LIMIT' if full else '_STATE_CACHE_LIMIT',3)
    evaluate = g.full_submerged if full else g.submerged
    for ta in (5.,5.1,5.2,5.3):
        evaluate(ta,5.,.1,1025.)
    assert len(g._full_state_cache if full else g._state_cache) == 3


def test_batch_larger_than_cache_retains_every_answer(monkeypatch, isolated_geometry):
    g = isolated_geometry
    monkeypatch.setattr(g,'_STATE_CACHE_LIMIT',3)
    pairs = [[5.+i*.1,5.] for i in range(5)]
    got = g._submerged_many(pairs,.1,1025.)
    assert len(got) == 5
    assert len(g._state_cache) == 3
    for (ta,tf),value in zip(pairs,got):
        reference = HullGeometry.submerged(g,ta,tf,.1,1025.)
        assert value.volume_m3 == pytest.approx(reference.volume_m3,rel=3e-14)


@pytest.mark.parametrize('method', ['submerged', 'full_submerged', '_submerged_many'])
def test_exact_state_cache_hit_still_checks_deadline(monkeypatch, isolated_geometry, method):
    from stowage.checker import stability_geometry
    from stowage.checker.stability_budget import StabilityTimeout
    g = isolated_geometry
    def evaluate():
        return g._submerged_many([[5.3,4.7]],.1,1025.) if method == '_submerged_many' else getattr(g,method)(5.3,4.7,.1,1025.)
    evaluate()
    def expired(*args):
        raise StabilityTimeout('expired cached geometry call')
    monkeypatch.setattr(stability_geometry,'check_budget',expired)
    with pytest.raises(StabilityTimeout,match='expired'):
        evaluate()


class _LegacyGeometry(StabilityGeometry):
    """Frozen pre-optimization integration/root routines; independent control.

    Kept verbatim so equivalence checks remain portable without temporary files.
    Constructor and original padded contour arrays are unchanged by this work.
    """
    def submerged(self, t_aft_m, t_fwd_m, heel_rad=0., density_kg_m3=None):
        check_budget("direct hull integration")
        rho = self.density_kg_m3 if density_kg_m3 is None else density_kg_m3
        if not np.isfinite(rho) or rho <= 0:
            raise ValueError('positive finite water density required')
        normal, _, _ = self._axes(t_aft_m, t_fwd_m, heel_rad)
        h = normal[2]*t_aft_m-normal[0]*self._px
        d = self._py*normal[1]+self._pz*normal[2]-h[:,None]
        dq = np.roll(d,-1,axis=1)
        inside, next_inside = d<=0, dq<=0
        crossing = inside != next_inside
        fraction = np.divide(d,d-dq,out=np.zeros_like(d),where=crossing)
        # Clip each original edge by its scalar interval [a,b]. Translating
        # the origin onto the waterline makes every closing waterline edge's
        # shoelace cross exactly zero mathematically. Therefore the unchanged
        # clipped-polygon area and first moments need no vertex compaction or
        # indirect successor gathering, even for concave/multiple contours.
        a = np.where(inside,0.,fraction)
        b = np.where(next_inside,1.,fraction)
        origin_scale = h/(normal[1]**2+normal[2]**2)
        oy,oz = origin_scale*normal[1],origin_scale*normal[2]
        cross = (b-a)*(self._edge_cross-oy[:,None]*self._dz+oz[:,None]*self._dy)
        area = cross.sum(axis=1)/2
        endpoint_sum = a+b
        my = (((2*(self._py-oy[:,None])+endpoint_sum*self._dy)*cross).sum(axis=1)/6
              +oy*area)
        mz = (((2*(self._pz-oz[:,None])+endpoint_sum*self._dz)*cross).sum(axis=1)/6
              +oz*area)
        px,pw=self._px,self._pw
        vol = float(area @ pw)
        cg = np.array([(area*px) @ pw, my @ pw, mz @ pw])/vol if vol > 1e-12 else np.zeros(3)
        # Solver residuals only need volume and centroid. Use the base implementation
        # explicitly for GM/waterplane data after convergence.
        return Submerged(vol, rho*vol, *map(float, cg), 0., self.lpp_m/2, 0., 0., self.x_m,
                         np.array([]), self.integration_weights_m)

    def free_trim_curve(self, angles, mass, lcg, kg, density, cg_callback=None,
                        initial_drafts=None, stop_when=None):
        """Continue free-trim equilibrium, retaining the physical longitudinal KG.

        Drafts are waterline intercepts and grow as 1/cos(heel) near 90 degrees.
        Solve instead for mean intercept times cos(heel), a finite immersion
        coordinate, and the aft-minus-forward draft difference. This is an
        algebraic coordinate change, not a hull or moment approximation. Roots
        meet task 2.1's scaled 2e-6 volume/longitudinal equilibrium tolerance.
        ``initial_drafts`` allows callers to continue successive curve segments.
        ``stop_when(phi, gz, drafts)`` can end evaluation after a flooding or
        vanishing-angle bracket has been collected, without solving later heels.
        Exactly 90 degrees is outside HullGeometry's attitude parametrisation.
        """
        from scipy.optimize import root, least_squares, brentq
        guess = np.asarray(initial_drafts if initial_drafts is not None else
                           [self.design_draught_m, self.design_draught_m], dtype=float)
        if guess.shape != (2,) or not np.isfinite(guess).all():
            raise ValueError('initial_drafts must contain two finite draughts')
        gz, conditions = [], []
        for phi in angles:
            cosine = np.cos(phi)
            # Validate the angle before any division by its cosine.
            self._axes(*guess, phi)
            def drafts(v):
                mean = v[0]/cosine
                return np.array([mean+v[1]/2, mean-v[1]/2])
            def residual(v):
                ta, tf = drafts(v)
                s = self.submerged(ta, tf, phi, density)
                _, along, _ = self._axes(ta, tf, phi)
                cg = (cg_callback(phi, np.arctan((ta-tf)/self.lpp_m))
                      if cg_callback else [lcg, 0., kg])
                return [(s.displacement_kg-mass)/mass,
                        np.dot(np.asarray(s.centroid_m)-cg, along)/self.lpp_m]
            seed = np.array([np.mean(guess)*cosine, guess[0]-guess[1]])
            solved = root(residual, seed, tol=1e-8, options={'eps':1e-8})
            if not np.isfinite(solved.x).all() or np.linalg.norm(residual(solved.x)) > 2e-6:
                solved = least_squares(residual, seed, diff_step=1e-4,
                    xtol=2e-10,ftol=2e-10,gtol=2e-10,max_nfev=100)
            if np.linalg.norm(residual(solved.x)) > 2e-6:
                def at_trim(trim):
                    # In scaled immersion coordinates this bracket stays finite
                    # even when the centreline waterline intercept diverges.
                    limit = self.max_z_m+2*self.beam_m+abs(trim*cosine)
                    immersion = brentq(lambda q:residual([q, trim])[0],
                                       -limit,limit,xtol=1e-9)
                    return np.array([immersion, trim])
                for extent in (.05,.2,.5,1.,2.,10.):
                    lo,hi = -self.lpp_m*extent,self.lpp_m*extent
                    if residual(at_trim(lo))[1]*residual(at_trim(hi))[1] <= 0:
                        trim = brentq(lambda trim:residual(at_trim(trim))[1],lo,hi,xtol=1e-7)
                        solved.x = at_trim(trim)
                        break
            if np.linalg.norm(residual(solved.x)) > 2e-6:
                raise ValueError(f'free-trim curve equilibrium did not converge at {np.degrees(phi):.4g} degrees')
            guess = drafts(solved.x)
            s = self.submerged(*guess, phi, density)
            _, _, transverse = self._axes(*guess, phi)
            gz.append(float(np.dot(np.asarray(s.centroid_m)-[lcg, 0., kg], transverse)))
            conditions.append(tuple(guess))
            if stop_when is not None and stop_when(phi, gz[-1], conditions[-1]):
                break
        return np.asarray(gz), np.asarray(conditions).reshape(-1, 2)

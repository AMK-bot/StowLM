"""Small structural regressions; root owns numerical-test admission."""
import numpy as np

from ballast import (_fd_step, _directions, _merit, _regime, _forward_control,
                     _balance, _narrow_redistribute, _nearfull_seed, _unique_seed, _wide_pairs,
                     _fd_bounds, _fill_ray_weights, _large_fill_ray, _compensated_trial, _lp)


def test_derivatives_stay_in_pressed_regime_and_handle_full_endpoint():
    x = np.array([.99, 1.])
    for direction in (np.array([1., 0.]), np.array([0., 1.])):
        step = _fd_step(x, direction)
        actual = x+step*direction
        assert np.all(actual >= .98)
        assert np.all(actual <= 1.)
    assert _fd_step(x, np.array([0., 1.])) < 0.


def test_split_at_two_empty_tanks_has_no_feasible_difference():
    assert _fd_step(np.zeros(2), np.array([1., -1.])) == 0.


def test_empty_slack_and_pressed_full_are_distinct_regimes():
    assert len(set(_regime(np.array([0., .5, .99, 1.])).tolist())) == 4


def test_geometry_directions_do_not_depend_on_tank_names():
    rows = []
    groups = []
    for i, (x, y, z, fsm, capacity) in enumerate(((0., 2., 4., 1., 10.),
                                                (50., 5., 1., 100., 100.),
                                                (100., 1., 3., 1., 20.))):
        group = []
        for sign in (1., -1.):
            group.append(len(rows))
            rows.append(dict(id=f'arbitrary-{len(rows)}', x=x, y=y*sign, z=z,
                             fsm=fsm, capacity=capacity))
        groups.append(group)
    result = _directions(rows, groups)
    assert result.shape[0] == 6
    assert 1 <= result.shape[1] <= 6
    assert np.any(result < 0.)  # actual transverse split, not pair-only restriction


def test_unknown_physics_does_not_beat_a_known_violated_candidate():
    assert _merit({'completed': False}) > _merit({'completed': True, 'margins': {'area': -.1}})


def test_forward_control_prefers_available_moment_over_furthest_nearly_empty_pair():
    rows = [{'x': x} for x in (54., 54., 104., 104., 97., 97.)]
    masses = np.array([64000., 64000., 2800., 2800., 17000., 17000.])
    assert _forward_control([[2, 3], [4, 5]], rows, masses, [0, 1]) == [4, 5]


def test_forward_moment_capacity_adds_deep_pair_missing_from_four_old_choices():
    rows, groups = [], []
    for x, capacity, ratio, y, z in ((4.9, 178., 2.35, 4., 7.),
            (33.9, 50., 4.5, 3., .4), (79.2, 112., 2.47, 2., .5),
            (53.5, 312., 5.47, 4., .45), (101.7, 71., .018, .7, 3.2),
            (96.9, 100., .075, 1.1, 3.5), (53.9, 180., .046, 8.5, 3.3),
            (36.2, 389., .039, 8.6, 4.3), (66.8, 224., .034, 8.55, 4.4)):
        group = []
        for sign in (1., -1.):
            group.append(len(rows)); rows.append(dict(x=x, y=sign*y, z=z,
                capacity=capacity, fsm=capacity*ratio))
        groups.append(group)
    directions = _directions(rows, groups)
    expected = np.zeros(18); expected[groups[5]] = 1.
    assert directions.shape == (18, 6)
    assert any(np.array_equal(direction, expected) for direction in directions.T)
    weights, _ = _fill_ray_weights(rows, groups, np.zeros(18),
                                   np.array([r['capacity'] for r in rows]), 53.)
    assert np.all(weights[groups[5]] > 0.)  # forward potential beats farther small peak
    assert np.isclose(weights.sum(), 1.)
    assert np.isclose(weights@np.array([r['x'] for r in rows]), 53.)


def test_unavailable_transverse_difference_is_pinned_in_real_lp():
    G = np.array([[1., 1.], [1., -1.]])
    assert _fd_step(np.zeros(2), G[:, 1]) == 0.
    # Strong objective temptation to use the unmeasured split must lose to
    # its fixed-zero bound, even though its initialized Jacobian is zero.
    answer = _lp(np.array([-1., -100.]), bounds=_fd_bounds([True, False], .03))
    assert answer.status == 0 and answer.x[1] == 0.
    candidate = G@answer.x
    assert candidate[0] == candidate[1] and candidate[0] > 0.
    # A correction-membership-skipped direction gets the identical bound;
    # validity is supplied afresh per Jacobian, never carried across stages.
    assert _fd_bounds([False, True], .03) == [(0., 0.), (-.03, .03)]


def test_large_ray_uses_exact_both_drafts_and_preserves_wide_regimes(monkeypatch):
    import time
    from types import SimpleNamespace as NS
    import ballast
    rows, groups = [], []
    for x, ratio in ((10., .1), (90., .2), (70., 10.), (50., 20.)):
        group = []
        for sign in (1., -1.):
            group.append(len(rows)); rows.append(dict(id=str(len(rows)), x=x, y=sign,
                z=1., capacity=100., fsm=ratio*100.))
        groups.append(group)
    original = np.array([0., 0., 0., 0., 0., 0., 100., 100.])
    evaluator = NS(capacity_kg=np.full(8, 100.),
        instance=NS(vessel=NS(hull=NS(max_draught_m=7.1)), port=NS(max_draught_m=7.)))
    monkeypatch.setattr(ballast, '_raw_loading', lambda *a, **k: NS(lcg_m=50., tcg_m=0.))
    monkeypatch.setattr(ballast, '_balance', lambda e, masses, *a, **k: masses.copy())
    for limiting_end in ('forward', 'aft', 'capacity'):
        def drafts(e, masses, *args):
            deep = 6.+(masses.sum()-original.sum())*.004
            return (deep, 5.) if limiting_end == 'forward' else ((5., deep) if limiting_end == 'aft' else (5., 5.))
        monkeypatch.setattr(ballast, '_loading_drafts', drafts)
        points, metadata = _large_fill_ray(evaluator, original, rows, groups, None, {}, time.monotonic()+10.)
        assert len(points) == 2 and metadata['direct_equilibrium_calls'] > 0
        for masses, info in points:
            assert np.array_equal(masses[4:6], [0., 0.])
            assert np.array_equal(masses[6:], [100., 100.])
            assert max(info['departure_drafts_fwd_aft_m']) <= 6.9995
            assert masses[0] == masses[1] and masses[2] == masses[3]
        expected = 'capacity or retained fill regime' if limiting_end == 'capacity' else 'native departure draft'
        assert metadata['endpoint_limited_by'] == expected
    monkeypatch.setattr(ballast, '_loading_drafts', lambda *a: (7.5, 5.))
    points, metadata = _large_fill_ray(evaluator, original, rows, groups, None, {}, time.monotonic()+10.)
    assert not points and 'headroom' in metadata['reason']


def test_primary_fill_does_not_require_empty_forward_control_when_draft_has_room(monkeypatch):
    import time
    from types import SimpleNamespace as NS
    import ballast
    rows, groups = [], []
    for x, y, ratio in ((50., 10., .01), (100., 1., .02), (0., 1., .03)):
        group = []
        for sign in (1., -1.):
            group.append(len(rows)); rows.append(dict(x=x, y=y*sign, capacity=100., fsm=100.*ratio))
        groups.append(group)
    evaluator = NS(capacity_kg=np.full(6, 100.), tank_ids=tuple(str(j) for j in range(6)),
        instance=NS(vessel=NS(hull=NS(max_draught_m=7.1)), port=NS(max_draught_m=7.)))
    monkeypatch.setattr(ballast, '_balance', lambda e, m, *a, **k: m.copy())
    monkeypatch.setattr(ballast, '_loading_drafts', lambda *a: (6., 6.))
    point, info = _compensated_trial(evaluator, np.zeros(6), rows, groups, None, {}, .03, time.monotonic()+10.)
    assert point is not None and np.all(point[:2] == .03)
    assert info['uncompensated'] and info['control_tanks'] == []
    monkeypatch.setattr(ballast, '_loading_drafts', lambda *a: (8., 6.))
    point, info = _compensated_trial(evaluator, np.zeros(6), rows, groups, None, {}, .03, time.monotonic()+10.)
    assert point is None and 'no forward pair' in info['reason']


def test_whole_wide_pair_replaces_pressed_partner_and_conserves_three_moments():
    from types import SimpleNamespace as NS
    rows = [dict(id=str(j), capacity=capacity, x=x, y=y, z=1., fsm=fsm)
            for j, (x, y, capacity, fsm) in enumerate(
                ((50., 5., 10., 100.), (50., -5., 10., 100.),
                 (0., 5., 20., 1.), (0., -5., 20., 1.),
                 (100., 5., 20., 1.), (100., -5., 20., 1.)))]
    evaluator = NS(capacity_kg=np.array([r['capacity'] for r in rows]))
    masses = np.array([.3, 10., 0., 0., 0., 0.]); groups = [[0, 1], [2, 3], [4, 5]]
    slack = _narrow_redistribute(evaluator, masses, rows, groups, selected=[0, 1])
    details = []
    whole = _narrow_redistribute(evaluator, masses, rows, groups, selected=[0, 1],
                                whole_pair=True, diagnostics=details)
    assert slack[1] == 10.  # original slack-only family cannot remove full partner
    assert np.array_equal(whole[:2], [0., 0.])
    moments = np.array([[1.]*6, [r['x'] for r in rows], [r['y'] for r in rows]])
    assert np.allclose(moments@whole, moments@masses, rtol=0., atol=1e-6)
    assert np.all(whole >= 0.) and np.all(whole <= evaluator.capacity_kg)
    assert details[-1]['status'] == 'candidate' and details[-1]['outward'] is False


def test_failed_replacement_is_unchanged_and_not_a_proof(monkeypatch):
    from types import SimpleNamespace as NS
    import ballast
    rows = [dict(capacity=10., x=x, y=y, z=1., fsm=fsm) for x, y, fsm in
            ((50., 5., 100.), (50., -5., 100.), (0., 5., 1.),
             (0., -5., 1.), (100., 5., 1.), (100., -5., 1.))]
    masses = np.array([.3, 10., 0., 0., 0., 0.]); diagnostics = []
    monkeypatch.setattr(ballast, '_lp', lambda *a, **k: NS(status=2))
    actual = _narrow_redistribute(NS(capacity_kg=np.full(6, 10.)), masses, rows,
        [[0, 1], [2, 3], [4, 5]], selected=[0, 1], whole_pair=True, diagnostics=diagnostics)
    assert np.array_equal(actual, masses)
    assert diagnostics[-1]['lp_status'] == 2 and diagnostics[-1]['outward'] is False


def test_wide_pair_priority_uses_geometry_including_full_tanks():
    rows, groups = [], []
    for x, ratio in ((20., .1), (40., .1), (100., .1), (50., 10.), (90., 20.)):
        group = []
        for sign in (1., -1.):
            group.append(len(rows))
            rows.append(dict(id='ignored', x=x, y=sign*5., z=1., capacity=10., fsm=ratio*10.))
        groups.append(group)
    masses = np.full(10, 10.)
    assert _wide_pairs(rows, groups, masses) == [groups[4], groups[3]]
    for j, row in enumerate(rows):
        row['id'] = f'renamed-{100-j}'
    assert _wide_pairs(rows, groups, masses) == [groups[4], groups[3]]


def test_nearfull_descendants_preserve_empty_and_asymmetric_pairs_and_seed_cap():
    masses = np.array([0., 100., 0., 0., 98.5, 99.])
    actual = _nearfull_seed(masses, np.full(6, 100.), [{'z': 1.}]*6,
                            [[0, 1], [2, 3], [4, 5]], 10., .99)
    assert np.array_equal(actual, [0., 100., 0., 0., 99., 99.])
    seeds = []
    for j in range(12):
        _unique_seed(seeds, np.array([j/12.]))
    assert len(seeds) == 9
    assert not _unique_seed(seeds, seeds[0])


def test_balance_uses_stocked_fallback_when_preferred_pair_cannot_bracket(monkeypatch):
    from types import SimpleNamespace as NS
    import ballast
    rows = [dict(id=str(j), capacity=100., fsm=fsm, y=y) for j, (fsm, y) in
            enumerate(((1., 10.), (1., -10.), (100., 5.), (100., -5.)))]
    evaluator = NS(capacity_kg=np.full(4, 100.))
    original = np.array([1., 1., 50., 50.]); diagnostics = []
    y = np.array([r['y'] for r in rows])
    monkeypatch.setattr(ballast, '_raw_loading', lambda e, m, *a:
                        NS(tcg_m=float((m@y+100.)/m.sum())))
    balanced = _balance(evaluator, original, rows, [[0, 1], [2, 3]], None, {}, diagnostics)
    assert np.array_equal(balanced[:2], original[:2])
    assert abs(balanced@y+100.) < 1e-6
    assert balanced[:2].sum() == original[:2].sum()
    assert balanced[2:].sum() == original[2:].sum()
    assert diagnostics[-1]['pair'] == ['2', '3']
    monkeypatch.setattr(ballast, '_raw_loading', lambda e, m, *a:
                        NS(tcg_m=float((m@y+5000.)/m.sum())))
    assert np.array_equal(_balance(evaluator, original, rows, [[0, 1], [2, 3]], None, {}), original)


def test_repaired_cargo_gets_reoptimized_and_keeps_its_own_tank_vector(monkeypatch):
    """A worse repair at inherited masses can pass at NEW masses.

    This is a mocked orchestration test: no hull calculation, native checker,
    CP solve or numerical LP runs. It detects the old repair-once gap and a
    possible old-cargo/new-mass incumbent mix-up in one observable outcome.
    """
    from types import SimpleNamespace as NS
    import ballast
    import stowage.checker

    class Plan:
        def __init__(self, cargo='original', fraction=.5, ballast=None):
            self.cargo, self.fraction, self.ballast = cargo, fraction, ballast
        def model_copy(self, update):
            return Plan(self.cargo, self.fraction, update.get('ballast', self.ballast))
        def model_dump(self, **kwargs):
            return dict(cargo=self.cargo, fraction=self.fraction)

    observed = []
    class Evaluator:
        def __init__(self, instance, plan, **kwargs):
            self.plan = plan; self.context_identity = plan.cargo
            self.capacity_kg = np.array([100.]); self.tank_ids = ('tank',)
            self.initial_fractions = np.array([plan.fraction])
            self.initial_masses_kg = self.initial_fractions*self.capacity_kg
            self.ship = NS(placements=[NS(container_id='box')])
            self.evaluations = self.cache_hits = 0
        def evaluate_fractions(self, x, **kwargs):
            self.evaluations += 1
            observed.append((self.plan.cargo, float(x[0])))
            passed = self.plan.cargo == 'repaired' and x[0] >= .52
            margin = .01 if passed else (-.2 if self.plan.cargo == 'repaired' else -.1)
            return dict(completed=True, physics_feasible=passed, wall_s=0.,
                margins={'area': margin}, raw_margins={'area': margin}, part_times_s={},
                raw_loading={phase: dict(tcg_m=0., correction_tanks=[]) for phase in
                             ('departure', 'mid_voyage', 'arrival')},
                tank_masses_kg={'tank': float(x[0]*100.)})
        def candidate_plan(self, masses):
            return Plan(self.plan.cargo, float(masses[0]/100.), ballast='current-run')

    def load(path, name):
        if path.name == 'early_bound.py':
            return NS(securing_bound=lambda *args: {'proof': None})
        if path.name == 'repair.py':
            return NS(refine_current=lambda *args, **kwargs: ([Plan('repaired')], {'mock': True}))
        return NS(solve_ballast=lambda *args, **kwargs: {})

    monkeypatch.setattr(ballast, 'PhysicsEvaluator', Evaluator)
    monkeypatch.setattr(ballast, '_load', load)
    monkeypatch.setattr(ballast, '_native_manifest', lambda: {})
    monkeypatch.setattr(ballast, '_geometry_census', lambda e: ([{'capacity': 100.}], [[0]], None, {}))
    monkeypatch.setattr(ballast, '_raw_tcg_phases', lambda *a: dict.fromkeys(ballast._VOYAGE_PHASES, 0.))
    monkeypatch.setattr(ballast, '_directions', lambda *args: np.ones((1, 1)))
    monkeypatch.setattr(ballast, '_compensated_trial', lambda *args: (None, {'mock': True}))
    monkeypatch.setattr(ballast, '_balance', lambda e, m, *args, **kwargs:
                        np.array([51.]) if e.plan.cargo == 'repaired' and np.array_equal(m, [50.]) else m)
    monkeypatch.setattr(ballast, '_lp', lambda *args, **kwargs: NS(status=0, x=np.array([.03, 0.])))
    monkeypatch.setattr(stowage.checker, 'check', lambda inst, plan: NS(
        passed=plan.cargo == 'repaired' and plan.fraction >= .52,
        violations=[], model_dump=lambda **kwargs: {'passed': True}))
    candidate, metadata = ballast.finish_candidate(NS(load_list=['box']), Plan(), budget_s=900.)
    assert next(fraction for cargo, fraction in observed if cargo == 'repaired') == .51
    assert any(cargo == 'repaired' and fraction < .52 for cargo, fraction in observed)
    assert any(cargo == 'repaired' and fraction >= .52 for cargo, fraction in observed)
    assert candidate.cargo == 'repaired' and candidate.fraction >= .52
    assert metadata['full_checker_passed'] is True


def test_protected_failure_keeps_secure_incumbent_when_elastic_fallback_loses_securing(monkeypatch):
    """Observable orchestration: a lower elastic merit must not steal output.

    The fallback deliberately proposes an attractive area point with invalid
    native securing. The final result must retain the best exact secure point,
    including when no point can satisfy area. No native/LP computation runs.
    """
    from types import SimpleNamespace as NS
    import ballast
    import stowage.checker
    securing = 'departure/lashing/max_lashing_ratio'
    loading = 'departure/stability/draught_fwd_m'
    area = 'arrival/stability/containership_area_0_flood_mrad'

    class Plan:
        def __init__(self, fraction=.5, ballast=None):
            self.fraction, self.ballast = fraction, ballast
        def model_copy(self, update):
            return Plan(self.fraction, update.get('ballast', self.ballast))
        def model_dump(self, **kwargs):
            return dict(fraction=self.fraction)

    observations, hard_bounds = [], []
    class Evaluator:
        def __init__(self, instance, plan, **kwargs):
            self.plan = plan; self.context_identity = 'same-cargo'
            self.capacity_kg = np.array([100.]); self.tank_ids = ('tank',)
            self.initial_fractions = np.array([plan.fraction])
            self.initial_masses_kg = self.initial_fractions*100.
            self.ship = NS(placements=[NS(container_id='box')])
            self.evaluations = self.cache_hits = 0
        def evaluate_fractions(self, x, **kwargs):
            self.evaluations += 1; fraction = float(x[0])
            margin = .01 if fraction < .51 else -.001
            area_margin = -.08 if fraction <= .5 else (-.03 if fraction < .51 else -.001)
            observations.append((fraction, margin, area_margin))
            margins = {securing: margin, loading: .1, area: area_margin}
            return dict(completed=True, physics_feasible=False, wall_s=0., margins=margins,
                raw_margins=margins, part_times_s={},
                raw_loading={phase: dict(tcg_m=0., correction_tanks=[]) for phase in
                             ('departure', 'mid_voyage', 'arrival')}, tank_masses_kg={'tank': fraction*100.})
        def candidate_plan(self, masses):
            return Plan(float(masses[0]/100.), ballast='current-run')

    def load(path, name):
        if path.name == 'early_bound.py':
            return NS(securing_bound=lambda *a: {'proof': None})
        if path.name == 'repair.py':
            return NS(refine_current=lambda *a, **k: ([], {'mock': True}))
        return NS(solve_ballast=lambda *a, **k: {})
    def lp(c, a=None, b=None, **kwargs):
        bounds = kwargs['bounds']
        if any(bound == (0., 0.) for bound in bounds[1:]):
            names = sorted((securing, loading, area))
            hard_bounds.append(bounds)
            assert bounds[1+names.index(securing)] == (0., 0.)
            assert bounds[1+names.index(loading)] == (0., 0.)
            assert bounds[1+names.index(area)] == (0., None)
            return NS(status=2)  # local guidance failure, not a physical proof
        return NS(status=0, x=np.array([min(.03, bounds[0][1]), 0., 0., 0.]))

    monkeypatch.setattr(ballast, 'PhysicsEvaluator', Evaluator)
    monkeypatch.setattr(ballast, '_load', load)
    monkeypatch.setattr(ballast, '_native_manifest', lambda: {})
    monkeypatch.setattr(ballast, '_geometry_census', lambda e: ([{'capacity': 100.}], [[0]], None, {}))
    monkeypatch.setattr(ballast, '_raw_tcg_phases', lambda *a: dict.fromkeys(ballast._VOYAGE_PHASES, 0.))
    monkeypatch.setattr(ballast, '_directions', lambda *a: np.ones((1, 1)))
    monkeypatch.setattr(ballast, '_compensated_trial', lambda *a: (None, {'mock': True}))
    monkeypatch.setattr(ballast, '_lp', lp)
    monkeypatch.setattr(stowage.checker, 'check', lambda inst, plan: NS(
        passed=False, violations=[], model_dump=lambda **kwargs: {'passed': False}))
    candidate, meta = ballast.finish_candidate(NS(load_list=['box']), Plan(), budget_s=900.)
    assert hard_bounds and meta['elastic_fallbacks'] and meta['native_protection_rejections']
    assert any(sec < 0. and deficit > -.01 for _, sec, deficit in observations)
    assert .5 < candidate.fraction < .51
    assert meta['best_physics']['margins'][securing] >= 0.
    assert meta['best_physics']['margins'][loading] >= 0.
    assert meta['protected_incumbent']['fractions'] == [candidate.fraction]
    assert meta['full_checker_passed'] is False
    assert meta['work_caps']['slp_iterations'] == 16
    assert meta['work_caps']['max_evaluations'] == 160


def test_protection_bootstrap_and_near_seed_preserve_only_existing_valid_loading():
    from ballast import _protection_rows, _preserves_protection, _slack_bounds
    securing = 'departure/lashing/max_lashing_ratio'
    valid = 'departure/stability/draught_fwd_m'
    invalid = 'arrival/stability/draught_fwd_m'
    far = dict(completed=True, margins={securing: -.1, valid: .2, invalid: -.1})
    assert _protection_rows(far) == []
    near = dict(completed=True, margins={securing: -.005, valid: .2, invalid: -.1})
    assert set(_protection_rows(near)) == {securing, valid}
    thresholds = {securing: -.005, valid: 0.}
    assert _preserves_protection(near, thresholds)
    assert not _preserves_protection(dict(completed=True, margins={securing: -.006, valid: .2}), thresholds)
    assert _slack_bounds([securing, valid, invalid], {securing, valid}) == [(0., 0.), (0., 0.), (0., None)]

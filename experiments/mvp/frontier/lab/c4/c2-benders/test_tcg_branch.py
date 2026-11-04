"""TCG-cusp regressions with artificial restoring arms and real small LPs."""
import numpy as np
import pytest

from ballast import (_tcg_fd_stencil, _tcg_linear_rows, _tcg_same_branch,
                     _fd_bounds, _lp, _VOYAGE_PHASES, _project_stage_point,
                     _effective_fd_direction)


def phases(value):
    return dict.fromkeys(_VOYAGE_PHASES, value)


@pytest.mark.parametrize('r', [-2e-5, 2e-5])
def test_abs_cusp_stencil_and_both_rows_find_center_instead_of_moving_away(r):
    x = np.array([.4]); k = .8
    raw_at = lambda point: phases(r+k*(point[0]-x[0]))
    f = lambda delta: -abs(r+k*delta)
    # The historic proposed stencil crosses the cusp and reverses the sign
    # of its area slope; reproducing that bad slope is part of the test.
    proposed = .0005 if r < 0. else -.0005
    old_gradient = (f(proposed)-f(0.))/proposed
    assert old_gradient*r > 0.  # maximizing this false slope moves farther away
    step, info = _tcg_fd_stencil(x, np.ones(1), proposed, phases(r), raw_at)
    assert info['reversed'] and step*proposed < 0.
    gradient = (f(step)-f(0.))/step
    assert np.isclose(gradient, -np.sign(r)*k)
    assert _tcg_same_branch(phases(r), info['raw_after'])
    a, b = _tcg_linear_rows(phases(r), {p: np.array([k]) for p in _VOYAGE_PHASES})
    answer = _lp(np.array([-gradient]), a, b, bounds=[(-.03, .03)])
    assert answer.status == 0
    assert np.isclose(answer.x[0], -r/k, rtol=0., atol=1e-10)
    assert f(answer.x[0]) > f(0.)
    # Either large crossing or moving away violates at least one new row.
    away = np.sign(r)*.01
    across = -np.sign(r)*.01
    assert np.any(a@np.array([away]) > b+1e-9)
    assert np.any(a@np.array([across]) > b+1e-9)


def test_exact_center_pins_undefined_split_but_keeps_unchanged_zero_tcg_direction():
    x = np.array([.4])
    step, info = _tcg_fd_stencil(x, np.ones(1), .0005, phases(0.),
                                lambda point: phases(.8*(point[0]-.4)))
    assert step == 0. and 'center cusp' in info['reason']
    answer = _lp(np.array([-100.]), bounds=_fd_bounds([bool(step)], .03))
    assert answer.status == 0 and answer.x[0] == 0.
    # A mirror-pair/common direction leaving every phase centered remains
    # available: the safeguard must not pin the entire centered optimizer.
    step, info = _tcg_fd_stencil(x, np.ones(1), .006, phases(0.), lambda point: phases(0.))
    assert step == .006 and not info['root_capped']


def test_one_sided_capacity_uses_native_zero_root_cap_without_crossing():
    # At an empty tank only the positive stencil is capacity-valid. Its
    # ordinary size crosses zero, so the native root supplies a smaller h.
    x = np.zeros(1); r = -1e-4
    step, info = _tcg_fd_stencil(x, np.ones(1), .0005, phases(r),
                                lambda point: phases(r+.8*point[0]))
    assert info['root_capped'] and 0. < step < -r/.8
    assert np.isclose(step, .25*(-r/.8))
    assert all(value < 0. for value in info['raw_after'].values())


def test_tiny_root_is_pinned_instead_of_amplifying_area_roundoff():
    step, info = _tcg_fd_stencil(np.zeros(1), np.ones(1), .0005, phases(-2e-5),
                                lambda point: phases(-2e-5+.8*point[0]))
    assert step == 0. and 'floor' in info['reason']


def test_phase_guard_caps_stencil_for_earliest_voyage_cusp():
    x = np.array([.4])
    before = {'departure': 1e-4, 'mid_voyage': 2e-4, 'arrival': -2e-4}
    def raw_at(point):
        delta = point[0]-.4
        return {p: value+.8*delta for p, value in before.items()}
    # Neither normal sign works in all states: +h crosses arrival; -h
    # crosses departure. The positive arrival root can still cap +h.
    step, info = _tcg_fd_stencil(x, np.ones(1), .0005, before, raw_at)
    assert info['root_capped'] and step > 0.
    assert _tcg_same_branch(before, info['raw_after'])
    assert info['raw_after']['departure'] > 0. and info['raw_after']['arrival'] < 0.


def test_center_in_one_phase_is_guarded_even_if_arrival_is_smooth():
    before = {'departure': 0., 'mid_voyage': -.01, 'arrival': -.02}
    step, info = _tcg_fd_stencil(np.array([.4]), np.ones(1), .0005, before,
        lambda point: {p: value+.8*(point[0]-.4) for p, value in before.items()})
    assert step == 0. and 'center cusp' in info['reason']


def test_each_phase_gets_both_no_away_and_no_cross_rows():
    raw = {'departure': -.002, 'mid_voyage': .003, 'arrival': .004}
    derivatives = {p: np.eye(3)[j] for j, p in enumerate(raw)}
    a, b = _tcg_linear_rows(raw, derivatives)
    assert a.shape == (6, 3)
    for j, (phase, value) in enumerate(raw.items()):
        toward = np.zeros(3); toward[j] = -value/2.
        assert np.all(a@toward <= b+1e-12)
        away = np.zeros(3); away[j] = value/2.
        assert np.any(a@away > b+1e-12)
        crossing = np.zeros(3); crossing[j] = -2.*value
        assert np.any(a@crossing > b+1e-12)


def test_nonfinite_phase_probe_does_not_supply_a_derivative():
    step, info = _tcg_fd_stencil(np.array([.4]), np.ones(1), .0005, phases(-.01),
                                lambda point: phases(float('nan')))
    assert step == 0. and 'unavailable' in info['reason']


def test_projected_endpoint_changes_effective_direction_and_pins_pure_split(monkeypatch):
    from types import SimpleNamespace as NS
    import ballast
    evaluator = NS(capacity_kg=np.full(2, 100.))
    monkeypatch.setattr(ballast, '_balance', lambda e, masses, *a, **k: np.full(2, masses.mean()))
    monkeypatch.setattr(ballast, '_raw_tcg_phases', lambda e, masses, *a: phases(float(masses[0]-masses[1])))
    base, _ = _project_stage_point(evaluator, [.42, .38], [], [], None, {})
    endpoint, _ = _project_stage_point(evaluator, base+.006*np.array([1., 0.]), [], [], None, {})
    assert np.allclose(base, [.4, .4])
    assert np.allclose(endpoint, [.403, .403])
    assert np.allclose(_effective_fd_direction(base, endpoint, .006), [.5, .5])
    split, _ = _project_stage_point(evaluator, base+.0005*np.array([1., -1.]), [], [], None, {})
    assert np.array_equal(_effective_fd_direction(base, split, .0005), np.zeros(2))


def test_projection_stage_uses_actual_base_fd_forward_trial_and_returned_vector(monkeypatch):
    """The oracle and LP see the projected vectors, not a hidden raw recipe.

    Projection averages two tank masses. An input [1,0] direction therefore
    becomes [.5,.5]; the LP capacity rows must use that effective direction.
    A pure split is projected to zero and must have a fixed-zero LP variable.
    A fake full oracle passes only when its ACTUAL two masses reach the goal.
    """
    from types import SimpleNamespace as NS
    import ballast
    import stowage.checker
    area = 'arrival/stability/containership_area_0_flood_mrad'
    securing = 'departure/lashing/max_lashing_ratio'
    loading = 'departure/stability/draught_fwd_m'

    class Plan:
        def __init__(self, fractions=(.42, .38), ballast=None):
            self.fractions, self.ballast = tuple(fractions), ballast
        def model_copy(self, update):
            return Plan(self.fractions, update.get('ballast', self.ballast))
        def model_dump(self, **kwargs):
            return dict(fractions=list(self.fractions))

    observed = []
    class Evaluator:
        def __init__(self, instance, plan, **kwargs):
            self.context_identity = 'projected-toy'; self.capacity_kg = np.full(2, 100.)
            self.initial_fractions = np.array(plan.fractions)
            self.initial_masses_kg = self.initial_fractions*100.
            self.tank_ids = ('left', 'right'); self.evaluations = self.cache_hits = 0
            self.ship = NS(placements=[NS(container_id='box')])
        def evaluate_fractions(self, x, **kwargs):
            self.evaluations += 1; observed.append(x.copy())
            raw = float(x[0]-x[1])
            margins = {area: float(x[1]-.416-abs(raw)), securing: .1, loading: .1}
            return dict(completed=True, physics_feasible=margins[area] >= -1e-10,
                margins=margins, raw_margins=margins, wall_s=0., part_times_s={},
                raw_loading={p: dict(tcg_m=raw, correction_tanks=[]) for p in _VOYAGE_PHASES},
                tank_masses_kg=dict(zip(self.tank_ids, (x*100.).tolist())))
        def candidate_plan(self, masses):
            return Plan(masses/100., ballast='actual-projected-vector')

    def load(path, name):
        if path.name == 'early_bound.py':
            return NS(securing_bound=lambda *a: {'proof': None})
        if path.name == 'repair.py':
            return NS(refine_current=lambda *a, **k: ([], {}))
        return NS(solve_ballast=lambda *a, **k: {})
    capacity_rows = []
    def lp(c, a=None, b=None, **kwargs):
        # Margin rows are followed by the TWO actual tank-capacity rows.
        m, n = 3, 2
        capacity_rows.append(a[m:m+2, :n].copy())
        assert np.allclose(a[m:m+2, :n], [[.5, 0.], [.5, 0.]])
        assert kwargs['bounds'][1] == (0., 0.)  # projected-null P/S split
        return NS(status=0, x=np.array([.03, 0., 0., 0., 0.]))

    monkeypatch.setattr(ballast, 'PhysicsEvaluator', Evaluator)
    monkeypatch.setattr(ballast, '_load', load)
    monkeypatch.setattr(ballast, '_native_manifest', lambda: {})
    monkeypatch.setattr(ballast, '_geometry_census', lambda e: ([{'capacity': 100.}]*2, [[0, 1]], None, {}))
    monkeypatch.setattr(ballast, '_directions', lambda *a: np.array([[1., 1.], [0., -1.]]))
    monkeypatch.setattr(ballast, '_balance', lambda e, masses, *a, **k: np.full(2, masses.mean()))
    monkeypatch.setattr(ballast, '_raw_tcg_phases', lambda e, masses, *a: phases(float(masses[0]-masses[1])))
    monkeypatch.setattr(ballast, '_compensated_trial', lambda *a:
                        (np.array([.402, .4]), {'mock_forward': True}))
    monkeypatch.setattr(ballast, '_lp', lp)
    monkeypatch.setattr(stowage.checker, 'check', lambda inst, plan: NS(
        passed=abs(plan.fractions[0]-plan.fractions[1]) < 1e-10 and plan.fractions[1] >= .416-1e-10,
        violations=[], model_dump=lambda **k: {'passed': True}))
    candidate, metadata = ballast.finish_candidate(NS(load_list=['box']), Plan(), budget_s=900.)
    assert capacity_rows and metadata['full_checker_passed']
    assert np.allclose(candidate.fractions, [.416, .416])
    stage_rows = [r for r in metadata['trace'] if r['stage'] != 'cold-seeds']
    assert all(abs(r['fractions'][0]-r['fractions'][1]) < 1e-12 for r in stage_rows)
    assert np.allclose(next(r['fractions'] for r in stage_rows if r['label'].endswith('/initial')), [.4, .4])
    assert np.allclose(next(r['fractions'] for r in stage_rows if r['label'].endswith('/forward-line')), [.401, .401])
    assert np.allclose(next(r['fractions'] for r in stage_rows if '/fd-' in r['label']), [.404, .404])
    assert any('null direction' in row['reason'] for row in metadata['invalid_derivatives'])
    assert np.allclose(metadata['protected_incumbent']['fractions'], candidate.fractions)
    assert metadata['best_physics']['tank_masses_kg'] == dict(zip(('left', 'right'), np.array(candidate.fractions)*100.))

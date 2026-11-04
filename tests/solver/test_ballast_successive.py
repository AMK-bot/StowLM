"""Local ballast LPs repair only violated exact margins and respect deadlines."""
from time import monotonic

import numpy as np
import pytest

from stowage.solver.ballast_search import Deadline, Found
from stowage.solver.ballast_successive import search


def test_twenty_tanks_repair_only_violated_quantities():
    calls = []
    solution = []

    def evaluate(fill, active_keys=None):
        calls.append(active_keys)
        all_margins = {'draught': 15.-sum(fill), 'gm': sum(fill)-8.,
                       'trim': .1-abs(sum(fill[:10])-sum(fill[10:])),
                       'weather': 1.}
        if active_keys is None and min(all_margins.values()) >= 0.:
            solution.append(fill.copy())
            raise Found
        return {key: value for key, value in all_margins.items()
                if active_keys is None or key in active_keys}

    with pytest.raises(Found):
        search([np.zeros(20)], evaluate, monotonic()+3.)
    assert min(solution[0]) >= 0.
    assert max(solution[0]) <= 1.
    assert all('weather' not in keys and 'draught' not in keys
               for keys in calls if keys is not None)
    assert len(calls) < 100


def test_endpoint_trials_escape_discontinuous_free_surface():
    def evaluate(fill, active_keys=None):
        # Every slack tank loses its free-surface correction at an endpoint.
        margin = float(np.sum(fill))-.4-2.*np.count_nonzero((fill > 0.) & (fill < 1.))
        if active_keys is None and margin >= 0.:
            raise Found
        return {'gm': margin}

    with pytest.raises(Found):
        search([np.full(2, .5)], evaluate, monotonic()+3.)


def test_deadline_before_callback_and_inside_slope_probe():
    def forbidden(*args, **kwargs):
        pytest.fail('expired search evaluated a condition')

    with pytest.raises(Deadline):
        search([np.zeros(20)], forbidden, monotonic()-1.)
    calls = 0

    def timeout(fill, active_keys=None):
        nonlocal calls
        calls += 1
        if active_keys is not None:
            raise Deadline
        return {'gm': -1.}

    with pytest.raises(Deadline):
        search([np.zeros(20)], timeout, monotonic()+3.)
    assert calls == 2


def test_unproved_failure_is_not_accepted():
    calls = 0

    def impossible(fill, active_keys=None):
        nonlocal calls
        calls += 1
        return {'gm': -1.}

    assert search([np.zeros(2)], impossible, monotonic()+3.) is None
    assert calls < 100


def test_grouped_direction_slopes_can_repair():
    def evaluate(fill, active_keys=None):
        margin = sum(fill)-3.
        if active_keys is None and margin >= 0.:
            raise Found
        return {'gm': margin}

    with pytest.raises(Found):
        search([np.zeros(4)], evaluate, monotonic()+3., directions=np.ones((4, 1)))


def test_rejects_incompatible_fill_dimensions():
    with pytest.raises(ValueError, match='matching'):
        search([np.zeros(2), np.zeros(3)], lambda *_: {}, monotonic()+1.)


def test_loading_stage_progress_exposes_larger_weather_failure():
    accepted = []
    probes = []

    def evaluate(fill, active_keys=None):
        gm = fill[0]-.5
        weather = 4.*(fill[1]-.7)
        values = {'gm': gm, 'weather': weather}
        if active_keys is None:
            if gm < 0.:
                return {'gm': gm}
            if weather >= 0.:
                accepted.append(fill.copy())
                raise Found
            return values
        probes.append(tuple(active_keys))
        return {key: values[key] for key in active_keys}

    with pytest.raises(Found):
        search([np.zeros(2)], evaluate, monotonic()+3.)
    assert accepted[0][0] >= .5
    assert accepted[0][1] >= .7
    assert any('weather' in keys for keys in probes)


def test_fast_loading_callback_does_not_forget_known_curve_margin():
    missing_curve_probes = []

    def evaluate(fill, active_keys=None):
        values = {'gm': fill[0]-.5, 'weather': fill[1]-.6}
        if active_keys is not None:
            if fill[0] < .5 and 'weather' in active_keys:
                missing_curve_probes.append(fill.copy())
            return {key: values[key] for key in active_keys}
        if fill[0] < .5:
            return {'gm': values['gm']}
        if min(values.values()) >= 0.:
            raise Found
        return values

    # First condition exposes weather. Its endpoint snap returns to a loading
    # failure; selective probes must retain the already discovered curve limit.
    with pytest.raises(Found):
        search([np.array([.5, .1])], evaluate, monotonic()+3.)
    assert missing_curve_probes


def test_rejects_invalid_slack_weights():
    with pytest.raises(ValueError, match='slack weights'):
        search([np.zeros(2)], lambda *_: {}, monotonic()+1., slack_weights=np.array([1., -1.]))


def test_negative_exact_gm_tests_pressed_full_before_slack_derivatives():
    probes = []
    calls = []
    def evaluate(fill, active_keys=None):
        calls.append(fill.copy())
        slack = np.count_nonzero((fill > 0.) & (fill < 1.))
        values = {'stability/gm_m': float(sum(fill)-1.-.2*slack),
                  'stability/draught_aft_m': float(1.6-sum(fill))}
        if active_keys is None and min(values.values()) >= -1e-10:
            raise Found
        if active_keys is not None:
            probes.append(tuple(active_keys))
        return {k:v for k,v in values.items() if active_keys is None or k in active_keys}
    with pytest.raises(Found):
        search([np.zeros(2)], evaluate, monotonic()+3.)
    np.testing.assert_array_equal(calls[0], np.zeros(2))
    np.testing.assert_array_equal(calls[1], np.ones(2))
    assert probes[0] == ('stability/draught_aft_m',)

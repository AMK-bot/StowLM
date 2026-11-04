"""Discrete basin selection and bounded exact-root plumbing, without hull I/O."""
from types import SimpleNamespace
from time import monotonic

import numpy as np
import pytest

from stowage.solver.ballast_pressed import search
from stowage.solver.ballast_search import Deadline, Found


def _tank(name, x, y, z=1.):
    return SimpleNamespace(id=name, capacity_m3=100., content_density_kg_m3=1000.,
        table=[SimpleNamespace(lcg_m=x, tcg_m=y, vcg_m=z)])


def _tanks():
    return [_tank('DB-M-P', 50., 3.), _tank('DB-M-S', 50., -3.),
            _tank('HEEL-P', 50., 8.), _tank('HEEL-S', 50., -8.),
            _tank('FPT-P', 95., 2.), _tank('FPT-S', 95., -2.),
            _tank('APT-P', 5., 2.), _tank('APT-S', 5., -2.)]


def test_pressed_pairs_find_slack_peak_without_slack_main_tanks():
    seen = []
    def equilibrium(fill):
        assert np.all((0. <= fill) & (fill <= 1.))
        return SimpleNamespace(trim_m=1.5-fill[4]-fill[5]+fill[6]+fill[7], heel_rad=0.)
    def evaluate(fill):
        seen.append(fill.copy())
        # This basin needs the low central pairs full, but a slack peak.
        if np.all(fill[:4] == 1.) and abs(equilibrium(fill).trim_m) < .025:
            raise Found
        return {'trim': -abs(equilibrium(fill).trim_m)}
    with pytest.raises(Found):
        search(_tanks(), np.zeros(8), evaluate,
               SimpleNamespace(equilibrium=equilibrium), monotonic()+2.)
    assert np.all(seen[-1][:4] == 1.)
    assert np.allclose(seen[-1][4:6], .75)
    assert np.all(seen[-1][6:] == 0.)


def test_heeling_pair_corrects_list_after_peak_trim():
    def equilibrium(fill):
        return SimpleNamespace(trim_m=1.5-fill[4]-fill[5]+fill[6]+fill[7],
                               heel_rad=.04+.1*(fill[2]-fill[3]))
    accepted = []
    def evaluate(fill):
        eq = equilibrium(fill)
        if abs(eq.trim_m) < .025 and abs(eq.heel_rad) < 1e-5:
            accepted.append(fill.copy())
            raise Found
        return {'trim': -abs(eq.trim_m), 'list': -abs(eq.heel_rad)}
    with pytest.raises(Found):
        search(_tanks(), np.zeros(8), evaluate,
               SimpleNamespace(equilibrium=equilibrium), monotonic()+2.)
    assert accepted[0][2]-accepted[0][3] == pytest.approx(-.4)
    assert np.allclose(accepted[0][4:6], .75)


def test_deadline_prevents_any_physics_or_callback():
    def forbidden(*args):
        raise AssertionError('expired strategy called physics')
    with pytest.raises(Deadline):
        search(_tanks(), np.zeros(8), forbidden,
               SimpleNamespace(equilibrium=forbidden), monotonic()-1.)


def test_no_success_returns_seeds_without_feasibility_claim():
    context = SimpleNamespace(equilibrium=lambda fill:
        SimpleNamespace(trim_m=1.5-fill[4]-fill[5]+fill[6]+fill[7], heel_rad=0.))
    candidates = search(_tanks(), np.zeros(8), lambda fill: {'impossible': -1.},
                        context, monotonic()+2.)
    assert candidates
    assert all(np.all((0. <= fill) & (fill <= 1.)) for fill in candidates)


def test_light_condition_uses_peak_before_adding_heavy_pairs():
    accepted = []
    def equilibrium(fill):
        return SimpleNamespace(trim_m=1.5-fill[4]-fill[5], heel_rad=0.)
    def evaluate(fill):
        if abs(equilibrium(fill).trim_m) < .025:
            accepted.append(fill.copy())
            raise Found
        return {'stability/gm_m': 1., 'trim': -abs(equilibrium(fill).trim_m)}
    with pytest.raises(Found):
        search(_tanks(), np.zeros(8), evaluate,
               SimpleNamespace(equilibrium=equilibrium), monotonic()+2.)
    assert np.all(accepted[0][:4] == 0.)
    assert np.allclose(accepted[0][4:6], .75)


def test_negative_gm_bases_do_not_request_unstable_equilibrium():
    def forbidden(fill):
        raise AssertionError('negative GM base requested an unstable equilibrium')
    seeds = search(_tanks(), np.zeros(8), lambda fill: {'stability/gm_m': -.1},
                   SimpleNamespace(equilibrium=forbidden), monotonic()+2.)
    assert seeds


@pytest.mark.parametrize('headroom, expected', [(1e9, [1., 1., 1., 1.]), (0., [0., 0., 0., 0.])])
def test_displacement_headroom_orders_heavy_or_light_before_initial(headroom, expected):
    context = SimpleNamespace(
        instance=SimpleNamespace(vessel=SimpleNamespace(hull=SimpleNamespace(max_draught_m=7.)),
                                 port=SimpleNamespace(max_draught_m=None)),
        direct_geometry=SimpleNamespace(full_submerged=lambda *args:
                                        SimpleNamespace(displacement_kg=headroom)),
        rho=1025., condition=lambda fill: SimpleNamespace(mass_kg=0.))
    seen = []
    def evaluate(fill):
        seen.append(fill.copy())
        raise Found
    with pytest.raises(Found):
        search(_tanks(), np.zeros(8), evaluate, context, monotonic()+2.)
    assert np.array_equal(seen[0][:4], expected)


def test_peak_cascade_presses_longest_lever_before_filling_continuation():
    tanks = _tanks()+[_tank('FWD-DEEP-P', 85., 2.), _tank('FWD-DEEP-S', 85., -2.)]
    def equilibrium(fill):
        return SimpleNamespace(trim_m=2.5-fill[4]-fill[5]-fill[8]-fill[9], heel_rad=0.)
    accepted = []
    def evaluate(fill):
        if np.all(fill[4:6] == 1.) and abs(equilibrium(fill).trim_m) < .025:
            accepted.append(fill.copy())
            raise Found
        return {'stability/gm_m': 1., 'trim': -abs(equilibrium(fill).trim_m)}
    with pytest.raises(Found):
        search(tanks, np.zeros(10), evaluate,
               SimpleNamespace(equilibrium=equilibrium), monotonic()+2.)
    assert np.all(accepted[0][4:6] == 1.)
    assert np.allclose(accepted[0][8:], .25)

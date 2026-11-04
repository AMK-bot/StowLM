"""Search switch plumbing; the real IRS model is tested separately."""
from types import SimpleNamespace

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.solver import scalable
from tests.solver.test_scalable import PhysicalOracle
from tests.solver.test_scalable_securing_1028 import securing_instance


class RecordingScreen:
    instances = []
    reject_all = False

    def __init__(self, instance):
        self.instance = instance
        self.placements = []
        self.states = 0
        self.metrics = dict(calls=0, cache_hits=0, evaluations=0, evaluation_seconds=0.)
        self.instances.append(self)

    def check_placement(self, ex, action):
        self.placements.append(action)
        self.metrics['calls'] += 1
        passed = not self.reject_all and action.slot != '0020182'
        return SimpleNamespace(passed=passed, worst_ratio=0.5 if passed else 2.,
                               reason=None if passed else 'test force failure')

    def check_state(self, ex):
        self.states += 1
        return SimpleNamespace(passed=True, worst_ratio=0.5, reason=None)


@pytest.fixture
def screen(monkeypatch):
    import stowage.solver.securing_screen as module
    RecordingScreen.instances = []
    RecordingScreen.reject_all = False
    monkeypatch.setattr(module, 'DeckSecuringScreen', RecordingScreen)
    return RecordingScreen


@pytest.fixture
def restricted_search(monkeypatch):
    class RestrictedOracle(PhysicalOracle):
        def is_legal(self, action):
            return action.slot in {'0020182', '0020282'} and super().is_legal(action)

        def copy(self):
            result = super().copy()
            result.__class__ = type(self)
            return result

    monkeypatch.setattr(scalable, 'make_feasibility_engine', RestrictedOracle)
    def candidates(instance, *args):
        return iter([Place(instance.load_list[0], '0020182'),
                     Place(instance.load_list[0], '0020282')])
    monkeypatch.setattr(scalable, '_candidates', candidates)
    return securing_instance()


def config(**updates):
    return scalable.ScalableConfig(beam_width=1, branch_factor=2,
                                  max_neighborhoods=0, max_restarts=1, **updates)


def test_switch_defaults_off_and_never_initializes_screen(screen, restricted_search):
    assert scalable.ScalableConfig().securing_screen is False
    result = scalable.ScalableSolver(config()).plan(restricted_search, time_limit_s=2.)
    assert result.status == 'ok', result.message
    assert not screen.instances
    assert result.metrics['securing_screen_enabled'] is False


@pytest.mark.parametrize('constructor_v4', [False, True])
def test_opt_in_rejects_bad_proposal_and_keeps_alternative(screen, restricted_search, constructor_v4):
    result = scalable.ScalableSolver(config(securing_screen=True, constructor_v4=constructor_v4)).plan(
        restricted_search, time_limit_s=2.)
    assert result.status == 'ok', result.message
    assert get_action_space().from_plan(restricted_search, result.plan) == [
        Place(restricted_search.load_list[0], '0020282')]
    assert len(screen.instances) == 1
    assert screen.instances[0].states > 0
    assert result.metrics['securing_screen_rejections'] >= 1
    assert result.metrics['securing_screen_worst_ratio'] == 2.
    assert result.metrics['securing_screen']['calls'] > 0


def test_supplied_prefix_passes_same_screen(screen, restricted_search):
    result = scalable.complete(restricted_search,
        [Place(restricted_search.load_list[0], '0020182')],
        config=config(securing_screen=True), time_limit_s=2.)
    assert result.status == 'ok', result.message
    assert result.metrics['discarded_actions'] == 1
    assert result.metrics['retained_actions'] == 0
    assert get_action_space().from_plan(restricted_search, result.plan)[0].slot == '0020282'


def test_restarts_reuse_single_screen(screen, restricted_search):
    screen.reject_all = True
    result = scalable.ScalableSolver(config(securing_screen=True)).plan(restricted_search, time_limit_s=2.)
    assert result.status == 'dead_end', result.message
    assert result.metrics['restarts'] == 1
    assert len(screen.instances) == 1
    assert result.metrics['securing_screen_rejections'] >= 4


def test_native_v4_checks_before_committing_successor(screen):
    from stowage.actions.samples import tiny_instance
    from stowage.feasibility import FeasibilityConfig
    screen.reject_all = True
    instance = tiny_instance(71, n_load=1, n_other=0, p40=0)
    result = scalable.ScalableSolver(scalable.ScalableConfig(
        constructor_v4=True, securing_screen=True, max_restarts=0,
        max_neighborhoods=0, feasibility_config=FeasibilityConfig(extra={'global_screen':0})
    )).plan(instance, time_limit_s=2.)
    assert result.status == 'dead_end', result.message
    assert result.metrics['constructor'] == 'v4-reservation-dfs'
    assert result.metrics['partial_loaded'] == 0
    assert result.metrics['securing_screen_rejections'] > 0
    assert len(screen.instances) == 1


def test_verified_zero_action_incumbent_still_requires_state_screen(screen, monkeypatch):
    import stowage.checker
    from stowage.actions.samples import tiny_instance
    from stowage.feasibility import FeasibilityConfig
    from stowage.model import BallastCondition

    instance = tiny_instance(71, n_load=1, n_other=0, p40=0)
    instance = instance.model_copy(update={'load_list': []})
    incumbent = get_action_space().expand(instance, []).model_copy(update={
        'ballast': BallastCondition(source='planner', tank_masses_kg={})})
    monkeypatch.setattr(stowage.checker, 'check', lambda *args: SimpleNamespace(
        passed=True, provenance=instance.provenance))
    def reject_state(self, ex):
        self.states += 1
        return SimpleNamespace(passed=False, worst_ratio=2., reason='existing deck force failure')
    monkeypatch.setattr(screen, 'check_state', reject_state)

    result = scalable.improve(instance, incumbent, time_limit_s=2., config=scalable.ScalableConfig(
        securing_screen=True, max_neighborhoods=0, max_restarts=0,
        feasibility_config=FeasibilityConfig(extra={'global_screen':0})))
    assert result.status == 'dead_end', result.message
    assert result.metrics['incumbent_checker_passed']
    assert result.metrics['incumbent_mask_verified']
    assert not result.metrics['verified_incumbent']
    assert not screen.instances[0].placements
    assert screen.instances[0].states > 0
    assert result.metrics['securing_screen_rejections'] > 0


@pytest.mark.parametrize('filter_enabled', [False, True])
@pytest.mark.parametrize('ordering_enabled', [False, True])
def test_filter_and_ordering_ablation_flags_are_independent(
        screen, restricted_search, filter_enabled, ordering_enabled):
    cfg = config(securing_screen=filter_enabled, heavy_first_ordering=ordering_enabled)
    assert cfg.securing_screen is filter_enabled
    assert cfg.heavy_first_ordering is ordering_enabled
    result = scalable.ScalableSolver(cfg).plan(restricted_search, time_limit_s=2.)
    assert result.status == 'ok', result.message
    assert result.metrics['securing_screen_enabled'] is filter_enabled
    assert result.metrics['heavy_first_ordering_enabled'] is ordering_enabled
    assert bool(screen.instances) is filter_enabled
    if filter_enabled:
        assert result.metrics['securing_screen_rejections'] >= 1


@pytest.mark.parametrize('filter_enabled', [False, True])
@pytest.mark.parametrize('ordering_enabled', [False, True])
def test_v4_ordering_ablation_uses_order_switch_only(monkeypatch, filter_enabled, ordering_enabled):
    import random
    import numpy as np
    from stowage.solver.construct_v4 import FutureDomains, _choices
    from tests.solver.test_construct_v4 import fixture

    cfg = scalable.ScalableConfig(securing_screen=filter_enabled, heavy_first_ordering=ordering_enabled)
    instance, tables, ex = fixture(heights=(2., 2.), limit=6.)
    tables.mass[:] = [10000., 15000.]
    for i, box in enumerate(instance.containers):
        box.gross_mass_kg = float(tables.mass[i])
    engine = SimpleNamespace(
        legal_pairs=lambda: (np.array([0, 1]), np.array([0, 0])),
        forces_restow=lambda action: False)
    node = scalable._Node(engine, ex,
        securing_screen=object() if filter_enabled else None)
    monkeypatch.setattr(scalable, '_moment_preference', lambda *args: 0.)
    choices = _choices(instance, node, FutureDomains(instance, tables), random.Random(0),
                       {'candidate_probes':0, 'legal_candidates':0}, 0,
                       heavy_first_ordering=cfg.heavy_first_ordering)
    assert choices[0].box == ('b1' if ordering_enabled else 'b0')


def test_ordering_compatibility_default_is_normalized_once():
    from dataclasses import replace
    assert scalable.ScalableConfig().heavy_first_ordering is False
    enabled = scalable.ScalableConfig(securing_screen=True)
    assert enabled.heavy_first_ordering is True
    # Once normalized, changing the filter via replace does not recouple order.
    assert replace(enabled, securing_screen=False).heavy_first_ordering is True
    assert scalable.ScalableConfig(securing_screen=True, heavy_first_ordering=False).heavy_first_ordering is False

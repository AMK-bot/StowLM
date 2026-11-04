"""Fast public cold/warm and deadline qualification with exact mass evidence.

These tests deliberately use an independently impossible mass bound. Real-fleet
success coverage belongs to the frozen campaign and is not implied by a fast
fixture test.
"""
import time

import pytest

from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.solver.ballast import finish_ballast
from stowage.solver.ballast_deadline import shutdown_worker


@pytest.fixture(autouse=True)
def isolated_worker():
    shutdown_worker()
    yield
    shutdown_worker()


def impossible_mass_stow():
    instance = build_instance()
    # Independently evident: lightship alone exceeds this one-kilogram bound;
    # all adjustable tanks can only add nonnegative mass.
    instance.rule_overrides = {'iscode.loading.displacement': {'maximum': 1.0}}
    assert instance.vessel.hull.lightship_mass_kg > 1.
    plan = MovePlan(id='qualification-mass', instance_id=instance.id, call_index=0)
    return instance, plan


@pytest.mark.parametrize('seed', [0, 17])
def test_public_mass_certificate_is_identical_cold_warm_and_restarted(seed):
    instance, plan = impossible_mass_stow()
    results = []
    for restart in (False, False, True):
        if restart:
            shutdown_worker()
        result = finish_ballast(instance, plan, time_limit_s=3., seed=seed)
        assert result.status == 'infeasible_proven'
        assert result.plan is None
        assert result.iterations == 0
        assert 'exact mass-balance bound' in result.proof
        results.append(result.proof)
    assert results == [results[0]]*3
    assert plan.ballast is None


def test_near_deadline_never_turns_an_unfinished_search_into_a_certificate():
    instance, plan = impossible_mass_stow()
    started = time.monotonic()
    result = finish_ballast(instance, plan, time_limit_s=.0001, seed=0)
    assert result.status in {'not_found', 'infeasible_proven'}
    assert result.plan is None
    if result.status == 'not_found':
        assert result.proof is None
    else:
        assert 'exact mass-balance bound' in result.proof
    assert time.monotonic()-started < 1.
    # Recovery is checked on the exact same input after the expiring worker.
    recovered = finish_ballast(instance, plan, time_limit_s=3., seed=0)
    assert recovered.status == 'infeasible_proven'

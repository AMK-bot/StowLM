"""Guarded scorer and cached sequence agree with real replay on fixture cargo."""
from stowage.actions.space import Place
from stowage.rules.catalogue import rule_freeze_v1

from ..classes.test_pipeline import case
from .fast_score import FastScorer
from .search import CachedSequencer
from alns import Sequencer


def test_guarded_fast_score_replays_and_rejects_frozen_mode():
    problem, assignment = case()
    actions = Sequencer(problem).order(assignment)
    assert CachedSequencer(problem).order(assignment) == actions
    fast = FastScorer(problem)
    assert fast.supported
    real = problem.score(actions)
    assert real is not None
    assert fast.score(actions) == real.objective
    assert fast.score(actions[:-1]) is None
    with rule_freeze_v1(enabled=True):
        assert fast.score(actions) is None
    assert fast.score(actions) == real.objective

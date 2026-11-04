# Provenance: copied from lab/c6/c6-lifts/test_polish.py (frozen-polisher safety contracts).
"""Safety contracts for the optional post-pass permutation stage."""
from dataclasses import dataclass, replace
import importlib.util
from pathlib import Path
import sys
from time import monotonic
from types import SimpleNamespace as NS

import numpy as np
import pytest

from . import polish


@dataclass
class Plan:
    ballast: object
    def model_copy(self, *, update):
        return replace(self, **update)


@dataclass
class Scored:
    actions: tuple
    objective: float
    plan: object


def report(passed, lifts):
    return NS(passed=passed, violations=[] if passed else [NS(rule_id='container.securing')],
              count=NS(weighted_nonproductive=lifts, relocations=0, hatch_cover_lifts=0,
                       restow_lifts=0, future=[]))


@pytest.fixture
def setup_case(monkeypatch):
    old_actions = (NS(box='a', slot='q0'), NS(box='b', slot='q1'))
    old = Scored(old_actions, 10., Plan(ballast={'tank': 42}))
    candidate = Scored((NS(box='a', slot='q1'), NS(box='b', slot='q0')), 8., Plan(None))
    model = NS(boxes=[0, 1], T=NS(box_index={'a': 0, 'b': 1}, slot_index={'q0': 0, 'q1': 1},
                                box_ids=['a', 'b'], slot_ids=['q0', 'q1']))
    problem = NS(mixes_hold_stack=lambda table: False, score=lambda actions: candidate)
    class Sequencer:
        def __init__(self, _): pass
        def order(self, table): return candidate.actions
    monkeypatch.setitem(sys.modules, 'alns', NS(Scored=Scored, Sequencer=Sequencer))
    monkeypatch.setattr(polish, '_groups', lambda *_: [[0, 1]])
    monkeypatch.setattr(polish, '_neighborhood', lambda *a, **kw: {0: 1, 1: 0})
    return old, candidate, model, problem


def call(old, model, problem, monkeypatch, reports):
    seen = []
    def check(instance, plan):
        seen.append(plan)
        return reports[len(seen)-1]
    monkeypatch.setitem(sys.modules, 'stowage.checker', NS(check=check))
    metadata = {}
    result = polish.polish(None, old, problem=problem, model=model, solver=None,
                           deadline=monotonic()+60., seed=0, metadata=metadata)
    return result, metadata['polish'], seen


def test_rejected_finalist_retains_exact_incumbent(setup_case, monkeypatch):
    old, _, model, problem = setup_case
    result, meta, seen = call(old, model, problem, monkeypatch, [report(True, 10), report(False, 8)])
    assert result is old
    assert meta['accepted'] == 0 and meta['full_check_rejects'] == 1
    assert seen[1].ballast == old.plan.ballast


def test_cheaper_full_pass_is_accepted_with_current_ballast(setup_case, monkeypatch):
    old, _, model, problem = setup_case
    result, meta, _ = call(old, model, problem, monkeypatch, [report(True, 10), report(True, 8)])
    assert result.objective == 8 and meta['accepted'] == 1
    assert result.plan.ballast == old.plan.ballast


def test_exception_cannot_lose_a_pass(setup_case, monkeypatch):
    old, _, model, problem = setup_case
    def fail(*args, **kwargs): raise RuntimeError('test rejection')
    monkeypatch.setattr(polish, '_neighborhood', fail)
    result, meta, seen = call(old, model, problem, monkeypatch, [report(True, 10)])
    assert result is old and len(seen) == 1
    assert meta['trials'][0]['outcome'] == 'incumbent_retained_exception'


def test_nonpassing_input_is_not_polished(setup_case, monkeypatch):
    old, _, model, problem = setup_case
    result, meta, seen = call(old, model, problem, monkeypatch, [report(False, 10)])
    assert result is old and len(seen) == 1
    assert meta['status'] == 'input_not_a_full_pass'


def test_spanning_position_connects_both_physical_stacks():
    T = NS(slot_stacks=np.array([[0, -1], [1, -1], [0, 1], [1, -1]]),
           slot_k=np.array([[0, -1], [0, -1], [1, 1], [2, -1]]))
    before = polish._topology(T, {10: 0, 11: 1, 12: 2, 13: 3})
    assert before[2] == {0, 1}
    assert before[3] == {0, 1, 2}

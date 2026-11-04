"""Exact cover-change feasibility and incumbent-preservation contracts."""
from dataclasses import dataclass, replace
import importlib.util
from pathlib import Path
import sys
from time import monotonic
from types import SimpleNamespace as NS

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location('_c6_cover_lns_tested', Path(__file__).with_name('cover_lns.py'))
lns = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lns
spec.loader.exec_module(lns)


def objective():
    return NS(hatch_cover=1., future_hatch_cover=1., future_restow=1., future_restow_lifts_per_box=2)


def master_model():
    pages = [NS(deck=False, cover='A', stacks=(0,), slots=(0, 1)),
             NS(deck=False, cover='B', stacks=(1,), slots=(2, 3))]
    return NS(pages=pages, pod={0: 1, 1: 1}, ports=[1], boxes=[0, 1],
              T=NS(mass=np.array([1000., 1000.])), p=NS(instance=NS(objective=objective())))


def test_master_can_close_hold_cover_and_change_occupied_topology():
    model = master_model()
    pool = [lns.Pattern(0, (0,), (1., 4., 5., 0.), 0.),
            lns.Pattern(1, (1,), (1., 5., 5., 0.), 0.),
            lns.Pattern(1, (0, 1), (2., 7., 10., 0.), 0.)]
    row = {}
    found = lns._master(pool, [0, 1], {0, 1}, model, closed='A',
                        deadline=monotonic()+2., seed=0, row=row)
    assert found == {0: 2, 1: 3}
    assert row['selected_patterns'] == 1 and row['graph_lifts'] == 4.


def test_master_keeps_vertical_side_row_hard_during_cover_closure():
    model = master_model()
    pool = [lns.Pattern(0, (0,), (1., 4., 5., 0.), 0.),
            lns.Pattern(1, (1,), (1., 5., 5., 0.), 0.),
            lns.Pattern(1, (0, 1), (2., 10., 10., 0.), 0.)]
    row = {}
    assert lns._master(pool, [0, 1], {0, 1}, model, closed='A',
                       deadline=monotonic()+2., seed=0, row=row) is None
    assert row['status'] == 'INFEASIBLE'


def test_master_cannot_reuse_physical_stack():
    model = master_model()
    model.pages[1].stacks = (0,)
    pool = [lns.Pattern(0, (0,), (1., 4., 5., 0.), 0.),
            lns.Pattern(1, (1,), (1., 5., 5., 0.), 0.)]
    row = {}
    assert lns._master(pool, [0, 1], {0, 1}, model, closed=None,
                       deadline=monotonic()+2., seed=0, row=row) is None


@dataclass
class Plan:
    ballast: object
    label: str = 'old'
    def model_copy(self, *, update): return replace(self, **update)


@dataclass
class Scored:
    actions: tuple
    objective: float
    plan: object


def report(passed, lifts, rules=()):
    return NS(passed=passed, violations=[NS(rule_id=r) for r in rules],
              count=NS(weighted_nonproductive=lifts, relocations=0, hatch_cover_lifts=0,
                       restow_lifts=0, future=[]))


@pytest.fixture
def setup_case(monkeypatch):
    actions = (NS(box='a', slot='q0'), NS(box='b', slot='q1'))
    old = Scored(actions, 10., Plan({'tank': 42}))
    candidate = Scored((NS(box='a', slot='q2'), NS(box='b', slot='q3')), 8., Plan(None, 'candidate'))
    model = NS(boxes=[0, 1], T=NS(box_index={'a': 0, 'b': 1}, slot_index={'q0': 0, 'q1': 1},
               box_ids=['a', 'b'], slot_ids=['q0', 'q1', 'q2', 'q3']))
    problem = NS(mixes_hold_stack=lambda table: False, score=lambda actions: candidate)
    class Sequencer:
        def __init__(self, _): pass
        def order(self, table): return candidate.actions
    monkeypatch.setitem(sys.modules, 'alns', NS(Scored=Scored, Sequencer=Sequencer))
    monkeypatch.setattr(lns, '_columns', lambda *a: {0: (0,), 1: (1,)})
    monkeypatch.setattr(lns, '_cover_jobs', lambda *a: [(('A', 'B'), 'A')])
    monkeypatch.setattr(lns, '_pattern_pool', lambda *a, **kw: ([], [], {0, 1}, {}))
    monkeypatch.setattr(lns, '_master', lambda *a, **kw: {0: 2, 1: 3})
    monkeypatch.setattr(lns, '_refresh_solver', lambda *a: NS(fresh=True))
    return old, candidate, model, problem


def run(old, model, problem, monkeypatch, reports):
    seen = []
    def check(instance, plan):
        seen.append(plan)
        return reports[len(seen)-1]
    monkeypatch.setitem(sys.modules, 'stowage.checker', NS(check=check))
    metadata = {}
    result = lns.polish(NS(load_list=['a', 'b']), old, problem=problem, model=model, solver=None,
                        deadline=monotonic()+60., seed=0, metadata=metadata)
    return result, metadata['cover_lns'], seen


def test_failed_finalist_retains_exact_passing_incumbent(setup_case, monkeypatch):
    old, _, model, problem = setup_case
    monkeypatch.setitem(sys.modules, 'cover_ballast', NS(trim_draught_only=lambda r: False))
    result, meta, seen = run(old, model, problem, monkeypatch,
                             [report(True, 10), report(False, 8, ['container.securing'])])
    assert result is old and meta['accepted'] == 0
    assert seen[1].ballast == old.plan.ballast


def test_passing_cover_change_keeps_concrete_ballast(setup_case, monkeypatch):
    old, _, model, problem = setup_case
    result, meta, seen = run(old, model, problem, monkeypatch, [report(True, 10), report(True, 8)])
    assert result.objective == 8 and meta['accepted'] == 1
    assert result.plan.ballast == old.plan.ballast and len(seen) == 2


def test_ballast_callback_is_used_only_after_trim_draught_gate(setup_case, monkeypatch):
    old, _, model, problem = setup_case
    repaired = Plan({'tank': 43}, 'repaired')
    calls = []
    def repair(instance, plan, deadline, **kwargs):
        calls.append(kwargs)
        return repaired, {'accepted': True}
    monkeypatch.setitem(sys.modules, 'cover_ballast', NS(trim_draught_only=lambda r: True, repair=repair))
    result, meta, _ = run(old, model, problem, monkeypatch,
                          [report(True, 10), report(False, 8, ['expert.trim']), report(True, 8)])
    assert result.plan is repaired and meta['accepted'] == 1
    assert calls[0]['incumbent'] is old.plan and calls[0]['incumbent_lifts'] == 10


def test_refresh_failure_retains_original_incumbent_and_solver(setup_case, monkeypatch):
    old, _, model, problem = setup_case
    def fail(*args): raise RuntimeError('cannot rebuild motion')
    monkeypatch.setattr(lns, '_refresh_solver', fail)
    result, meta, _ = run(old, model, problem, monkeypatch, [report(True, 10), report(True, 8)])
    assert result is old and meta['accepted'] == 0
    assert meta['trials'][0]['outcome'] == 'incumbent_retained_exception'

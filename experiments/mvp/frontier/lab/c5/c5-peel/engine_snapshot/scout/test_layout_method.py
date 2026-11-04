# Provenance: copied from experiments/mvp/frontier/lab/c2/c2-cgscale/test_method.py.
"""Narrow wrapper regressions; run with the lab's single-thread environment."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


from . import layout as method


def test_warm_start_rechecks_changed_rung_constraints():
    page = SimpleNamespace(kind='20', stacks=(0,), slots=(0, 1))
    previous = SimpleNamespace(
        pages=[page], _warm_indices=[0],
        columns=[SimpleNamespace(page=0, boxes=(1,)),
                 SimpleNamespace(page=0, boxes=(2,)),
                 SimpleNamespace(page=0, boxes=(3,)),
                 SimpleNamespace(page=0, boxes=(1, 2, 3))])
    accepted = []
    current = SimpleNamespace(
        pages=[page], legal=lambda page, boxes: boxes != (2,),
        lash_ok=lambda page, boxes: boxes != (3,),
        add=lambda page, boxes: accepted.append((page, boxes)) or True)
    with patch.object(method, 'monotonic', return_value=0.):
        assert method.warm_columns(current, previous, deadline=1.) == 1
    assert accepted == [(0, (1,))]


def test_full_check_cache_is_keyed_by_entire_plan():
    context = {}
    plan = SimpleNamespace(model_dump_json=lambda: '{"moves": [1], "ballast": 0}')
    other = SimpleNamespace(model_dump_json=lambda: '{"moves": [1], "ballast": 1}')
    report = SimpleNamespace(passed=True, violations=[])
    with patch.object(method, 'check', return_value=report) as checker:
        assert method.verdict(None, plan, context=context) == (True, [], False)
        assert method.verdict(None, plan, context=context) == (True, [], False)
        assert checker.call_count == 1
        assert method.verdict(None, other, context=context) == (True, [], False)
        assert checker.call_count == 2


def test_uncovered_lp_does_not_count_as_complete_replay():
    root = dict(uncovered=4., objective=4000., proved=False, x={}, rounds=3, columns=9)
    model = SimpleNamespace(boxes=[1, 2], stacks=[0], generate=lambda **kwargs: root)
    scored, row = method.one_stow(None, model, seed=0, deadline=method.monotonic()+1.)
    assert scored is None
    assert row['lp_objective'] == 4000.
    assert row['lp_uncovered'] == 4.
    assert row['dive_s'] == 0.
    assert row['replay_complete'] is False


def test_all_complete_guidance_survives_compatibility_flag_and_pass_labels():
    def scored(lifts, box, slot):
        return SimpleNamespace(objective=lifts, actions=[SimpleNamespace(box=box, slot=slot)])
    passing = scored(100., 'pass', '0010102')
    hopeful = scored(90., 'hopeful', '0010102')
    securing = scored(80., 'securing', '0010102')
    securing_duplicate = scored(80., 'securing', '0010102')
    trim = scored(85., 'trim', '0010102')
    sight = scored(88., 'sight', '0010102')
    expensive = scored(101., 'expensive', '0010102')
    failures = [expensive, sight, hopeful, trim, securing_duplicate, securing]
    passes = [passing]
    selected, retained = method.candidate_stows(passes, [hopeful], failures)
    assert selected == [securing_duplicate, trim, sight, hopeful, passing, expensive]
    assert retained == 4
    selected, retained = method.candidate_stows(passes, [hopeful], failures,
                                               retain_ballast_candidates=True)
    assert selected == [securing_duplicate, trim, sight, hopeful, passing, expensive]
    assert retained == 4
    # Failed guidance never enters the list of fully checked passing stows.
    assert passes == [passing]


def test_no_pass_keeps_all_distinct_ballast_guidance_stows():
    def scored(lifts, box):
        return SimpleNamespace(objective=lifts, actions=[SimpleNamespace(box=box, slot='0010102')])
    one, duplicate, two, three = scored(80., 'a'), scored(80., 'a'), scored(81., 'b'), scored(82., 'c')
    selected, retained = method.candidate_stows([], [], [one, duplicate, two, three],
                                               retain_ballast_candidates=True)
    assert selected == [one, two, three]
    assert retained == 3


def test_guidance_retention_keeps_original_more_expensive_hopeful():
    passed = SimpleNamespace(objective=10., actions=[SimpleNamespace(box='pass', slot='x')])
    hopeful = SimpleNamespace(objective=11., actions=[SimpleNamespace(box='hope', slot='x')])
    failed = SimpleNamespace(objective=9., actions=[SimpleNamespace(box='failed', slot='x')])
    selected, retained = method.candidate_stows([passed], [hopeful], [failed, hopeful],
                                               retain_ballast_candidates=True)
    assert selected == [failed, passed, hopeful]
    assert retained == 1


def test_cut_repair_balance_reverts_forbidden_page_choices():
    model = SimpleNamespace(balance=lambda chosen, rng: ([0], 0.),
                            respects_cuts=lambda chosen: 0 not in chosen,
                            moments=lambda chosen: 10.)
    chosen, heel, reverted = method.balance_with_cuts(model, [1], None)
    assert chosen == [1] and heel == 10. and reverted
    model.balance = lambda chosen, rng: ([2], 1.)
    chosen, heel, reverted = method.balance_with_cuts(model, [1], None)
    assert chosen == [2] and heel == 1. and not reverted

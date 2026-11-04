"""Narrow wrapper regressions; run with the lab's single-thread environment."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


def load_method():
    path = Path(__file__).with_name('method.py')
    spec = importlib.util.spec_from_file_location('_cgscale_method_tests', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


method = load_method()


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

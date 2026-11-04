# Provenance: pricing differential adapted from lab/c2/c2-cgscale/verify_model.py; local option regressions added.
"""Small exact differential checks for pricing and the copied unary adapter."""
from dataclasses import replace
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from alns import Problem
from stowage.model.fixtures import build_instance
from . import layout, layout_pages, pages_fast, pages, rmp


def test_fast_pricing_and_tier_aggregation_retain_exact_patterns():
    problem = Problem(build_instance())
    original = pages.Model(problem)
    fast = layout_pages.Model(problem)
    assert np.array_equal(original.static, fast.static)
    rng = np.random.default_rng(11)
    for binding in (False, True):
        if binding:
            for k, page in enumerate(original.pages):
                original.pages[k] = replace(page, hmax=max(2.591, len(page.slots)*2.72))
                fast.pages[k] = replace(fast.pages[k], hmax=original.pages[k].hmax)
        for phase in (1, 3):
            for value in (np.full(len(original.boxes), 1000.), rng.uniform(-10., 1000., len(original.boxes))):
                for groups in (True, False):
                    fast.aggregate_tiers = groups
                    for indices in original.types.values():
                        k = indices[0]
                        expected = original.price(k, value, phase=phase)
                        actual = fast.price(k, value, phase=phase)
                        assert actual.keys() == expected.keys()
                        for height in expected:
                            assert actual[height][0] == pytest.approx(expected[height][0])
                            assert actual[height][1] == expected[height][1]


def test_model_factory_routes_each_ablation():
    config = SimpleNamespace(fast_pricing=False, unary_cache=False, asymmetric_deck=False,
                             restricted_master=False, aggregation=False)
    model = layout.model_factory(Problem(build_instance()), {'engine_config': config})
    assert isinstance(model, layout_pages.SlowModel)
    assert not model.unary_cache
    assert not model.asymmetric_deck
    assert not model.use_restricted_master
    assert not model.aggregate_tiers


def test_rmp_retains_certified_page_cut():
    model = SimpleNamespace(boxes=[0], stacks=[0, 1], banned=set(),
        pages=[SimpleNamespace(stacks=(0,)), SimpleNamespace(stacks=(1,))],
        columns=[SimpleNamespace(page=0, boxes=(0,)), SimpleNamespace(page=1, boxes=(0,))])
    chosen, row = rmp.restricted_master(model, {0: 1.}, deadline=monotonic()+2.,
                                       cuts=[dict(certified=True, columns=[0], rhs=0)])
    assert chosen == [1]


def test_disabled_unary_cache_recomputes_and_never_aliases(monkeypatch):
    calls = []
    def static(model):
        calls.append(model.p)
        return np.array([[True, False]])
    monkeypatch.setattr(pages_fast.Model, '_static', static)
    model = object.__new__(layout_pages.Model)
    model.p = SimpleNamespace()
    model.unary_cache = False
    model._static()[0, 0] = False
    assert model._static()[0, 0]
    assert len(calls) == 2

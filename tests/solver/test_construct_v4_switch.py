"""The MVP constructor remains an explicit, search-only opt-in."""
from __future__ import annotations

import random
import sys
from types import SimpleNamespace

from stowage.solver import scalable


def test_v4_constructor_is_disabled_by_default():
    assert scalable.ScalableConfig().constructor_v4 is False


def test_v4_switch_dispatches_without_changing_arguments(monkeypatch):
    args_seen = []
    expected = ([object()], object())

    def construct(*args):
        args_seen.append(args)
        return expected

    monkeypatch.setitem(sys.modules, "stowage.solver.construct_v4",
                        SimpleNamespace(construct=construct))
    instance, start, slots, metrics, pods, progress = (object() for _ in range(6))
    cfg = scalable.ScalableConfig(constructor_v4=True)
    rng = random.Random(47)
    result = scalable._construct(instance, start, slots, cfg, 100., rng,
                                 metrics, pods, progress)
    assert result is expected
    assert args_seen == [(instance, start, slots, cfg, 100., rng,
                          metrics, pods, progress)]

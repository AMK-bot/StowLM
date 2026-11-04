"""Immutable prefix caches must avoid repeated physics/capacity work and isolate callers."""
from __future__ import annotations


def test_completion_checks_are_cached_per_prefix_and_defensive(h, tiny_a, monkeypatch):
    e = h.engine(tiny_a, h.SCREENS)
    calls = 0
    original = e._flow.flow

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(e._flow, "flow", counted)
    first = e.completion_check()
    once = calls
    assert once > 0
    first["c1a"] = not first["c1a"]
    second = e.completion_check()
    assert second["c1a"] != first["c1a"]
    assert calls == once, "the same immutable prefix repeated its max-flow checks"
    actions = e.legal_actions()
    assert actions
    e.push(actions[0])
    child = e.completion_check()
    after_child = calls
    assert after_child > once
    assert e.copy().completion_check() == child
    assert calls == after_child
    e.pop()
    assert e.completion_check() == second
    assert calls == after_child

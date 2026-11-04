"""Observational instrumentation and exact disposable-cache experiments."""
from types import SimpleNamespace

import pytest

from stowage.checker.perf import current_cache
from stowage.model import PartReport
from . import fast_perf, physics


def test_observer_preserves_result_and_restores_reference(monkeypatch):
    result = ([{"rule_id": "synthetic"}], {"frames": []})
    def original(*args):
        return result
    monkeypatch.setattr(physics, "fast_check", original)
    with fast_perf.observe_calls() as (times, values):
        assert physics.fast_check(None, None) is result
    assert physics.fast_check is original
    assert values["physics.fast_check"] is result
    assert times["physics.fast_check"]["calls"] == 1
    assert times["physics.fast_check"]["wall_s"] >= 0


def test_observer_restores_reference_after_exception(monkeypatch):
    def original(*args):
        raise RuntimeError("Synthetic failure")
    monkeypatch.setattr(physics, "fast_check", original)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        with fast_perf.observe_calls():
            physics.fast_check(None, None)
    assert physics.fast_check is original


def test_shared_workspace_runs_every_part_fresh_with_identical_reports():
    instance, ship = SimpleNamespace(call_index=0), object()
    calls, caches = [], []
    def make(name):
        def check(actual_instance, actual_ship, *, call_index):
            assert actual_instance is instance and actual_ship is ship and call_index == 0
            assert current_cache() is not None
            calls.append(name)
            caches.append(current_cache())
            return PartReport(name=name, metrics={"synthetic": 1.})
        return check
    functions = [(name, make(name)) for name in ("rules", "lashing", "visibility")]
    separate, _ = fast_perf.public_parts(instance, ship, shared=False, functions=functions)
    shared, _ = fast_perf.public_parts(instance, ship, shared=True, functions=functions)
    assert calls == ["rules", "lashing", "visibility"]*2
    assert all(caches[i] is not caches[j] for i in range(3) for j in range(i+1,3))
    assert caches[3] is caches[4] is caches[5]
    assert current_cache() is None
    assert {name: fast_perf.digest(part) for name, part in separate.items()} == {
        name: fast_perf.digest(part) for name, part in shared.items()}
    separate["rules"].metrics["synthetic"] = 9.
    assert shared["rules"].metrics["synthetic"] == 1.


def test_public_part_exception_never_leaks_workspace():
    def fail(*args, **kwargs):
        assert current_cache() is not None
        raise RuntimeError("Synthetic unavailable part")
    with pytest.raises(RuntimeError):
        fast_perf.public_parts(SimpleNamespace(call_index=0), object(), shared=True,
                               functions=[("rules", fail)])
    assert current_cache() is None


def test_digest_preserves_exact_scalar_distinctions_and_rejects_nan():
    assert fast_perf.digest({"value": 1}) != fast_perf.digest({"value": 1.})
    assert fast_perf.digest({"value": 0.}) != fast_perf.digest({"value": -0.})
    assert fast_perf.digest({"value": True}) != fast_perf.digest({"value": 1})
    with pytest.raises(ValueError):
        fast_perf.digest({"value": float("nan")})

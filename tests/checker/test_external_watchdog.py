"""Self-contained external cancellation guards; production imports only.

Stage this file at tests/checker/test_external_watchdog.py after owner review.
No experiment candidate, dataset, alarm delivery or numerical hull is needed.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import full, stability, stability_tables
from stowage.checker.stability_loading import LoadingCondition
from stowage.model.fixtures import build_instance
from stowage.vessel.hydrostatics import FloatingCondition


class _SyntheticWatchdogExpired(BaseException):
    """Same catch boundary as the operational watchdog, without real signals."""


@pytest.fixture
def synthetic_checker(monkeypatch):
    from stowage.vessel import fleet

    def forbidden_builder(*args, **kwargs):
        pytest.fail("External cancellation guard must not construct a real ship")

    monkeypatch.setattr(fleet, "load_vessel", forbidden_builder)
    instance = build_instance()
    instance.vessel.hull.max_list_rad = math.radians(2.0)
    instance.vessel.hull.max_draught_m = 5.0
    instance.port.max_draught_m = 4.0
    condition = LoadingCondition(1e6, 50.0, 0.0, 3.0, 0.1, 1e5)
    floating = FloatingCondition(2.0, 2.0, 0.0, None, 0.0)
    observed = {"direct_equilibrium_completed": 0}
    geometry = SimpleNamespace()

    def equilibrium(*args):
        observed["direct_equilibrium_completed"] += 1
        return floating

    geometry.equilibrium = equilibrium
    geometry.free_trim_curve = lambda *args, **kwargs: (np.array([0.0]), np.array([[2.0, 2.0]]))
    geometry.full_submerged = lambda *args: SimpleNamespace(centroid_m=[50.0, 0.0, 3.0], bm_t_m=0.5)
    geometry.waterplane_normal = lambda *args: np.array([0.0, 0.0, 1.0])
    monkeypatch.setattr(stability, "_canonical_fleet_policy", lambda *args: None)
    monkeypatch.setattr(stability, "_context", lambda *args: (geometry, {}))
    monkeypatch.setattr(stability, "loading_condition", lambda *args, **kwargs: condition)
    monkeypatch.setattr(stability_tables, "cached_geometry", lambda *args: None)
    monkeypatch.setenv("STOWAGE_STABILITY_LIQUID_DIRECT", "1")
    monkeypatch.delenv("STOWAGE_STABILITY_DIRECT", raising=False)

    def part():
        def in_port(world, ship, *, call_index):
            return stability.check_stability(world, ship, call_index=call_index, in_port=True)

        # Exercise the real broad Exception catch as well as cache selection.
        return full._run_part(instance, instance.onboard, "stability", in_port)

    return part, observed


def test_operational_baseexception_bypasses_cache_and_part_exception_handlers(monkeypatch, synthetic_checker):
    part, observed = synthetic_checker

    def expired(*args):
        raise _SyntheticWatchdogExpired("synthetic operational deadline")

    monkeypatch.setattr(stability_tables, "cached_geometry", expired)
    with pytest.raises(_SyntheticWatchdogExpired, match="operational deadline"):
        part()
    assert observed["direct_equilibrium_completed"] == 0


def test_ordinary_optional_cache_oserror_keeps_complete_direct_fallback(monkeypatch, synthetic_checker):
    part, observed = synthetic_checker

    def broken(*args):
        raise OSError("synthetic damaged optional cache")

    monkeypatch.setattr(stability_tables, "cached_geometry", broken)
    result = part()
    assert result.ran and not result.violations
    assert observed["direct_equilibrium_completed"] == 1


def test_optional_cache_timeout_is_unavailable_without_direct_fallback(monkeypatch, synthetic_checker):
    part, observed = synthetic_checker

    def expired(*args):
        raise TimeoutError("synthetic optional cache timeout")

    monkeypatch.setattr(stability_tables, "cached_geometry", expired)
    result = part()
    assert result.ran is False
    assert "TimeoutError" in result.error
    assert any(value.rule_id == "checker.internal_error" and value.severity == "hard"
               for value in result.violations)
    assert observed["direct_equilibrium_completed"] == 0


def test_ordinary_part_timeout_is_unavailable_not_a_physical_pass():
    instance = build_instance()

    def expired(*args, **kwargs):
        raise TimeoutError("synthetic ordinary timeout")

    result = full._run_part(instance, instance.onboard, "stability", expired)
    assert result.ran is False
    assert "TimeoutError" in result.error
    assert any(value.rule_id == "checker.internal_error" and value.severity == "hard"
               for value in result.violations)


def test_synthetic_part_without_interruption_completes(synthetic_checker):
    part, observed = synthetic_checker
    result = part()
    assert result.ran and not result.violations
    assert observed["direct_equilibrium_completed"] == 1

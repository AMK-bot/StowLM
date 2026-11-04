"""The staged yard identity is resolved per operation, never at module import."""
import pytest

from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import simulator


@pytest.mark.parametrize("enabled,expected", [(False, "3.0.0"), (True, "5.0.0")])
def test_effective_version_and_result_follow_switch_despite_registry_drift(monkeypatch, enabled, expected):
    monkeypatch.setattr(simulator, "SIMULATOR_VERSION", "99.0.0")
    with rule_freeze_v1(enabled=enabled):
        assert simulator.effective_yard_simulator_version() == expected
        assert simulator.SimulationResult().simulator_version == expected


def test_result_calls_effective_helper_at_creation_time(monkeypatch):
    monkeypatch.setattr(simulator, "effective_yard_simulator_version", lambda: "operation-version", raising=False)
    assert simulator.SimulationResult().simulator_version == "operation-version"

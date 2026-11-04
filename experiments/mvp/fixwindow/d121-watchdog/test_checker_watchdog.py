"""Queued P2 guards: actual optional-cache fallback, no real alarm or hull."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("d121_checker_timeout_probes", HERE / "checker_timeout_probes.py")
probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)


def test_current_D119_cache_handler_swallows_external_timeout(monkeypatch):
    check, observation = probe.prepare_case(monkeypatch,
        error=TimeoutError("external checker watchdog expired"))
    report = check()
    result = probe.public_observation(report, observation)
    assert result["cache_called"] == 1
    assert result["direct_equilibrium_completed"] > 0
    assert result["accepted_part"] is True
    (HERE / "current-cache-timeout-reproduction.json").write_text(json.dumps({
        "result": result, "source_pins": probe.source_pins(),
        "actual_signals_used": False, "real_numerical_ship_builder_used": False}, indent=2) + "\n")


def test_candidate_propagates_external_timeout_before_direct_fallback(monkeypatch):
    check, observation = probe.prepare_case(monkeypatch, patched=True,
        error=TimeoutError("external checker watchdog expired"))
    with pytest.raises(TimeoutError, match="external checker watchdog expired"):
        check()
    assert observation == {"cache_called": 1, "direct_equilibrium_completed": 0}


def test_candidate_keeps_ordinary_optional_cache_oserror_fallback(monkeypatch):
    check, observation = probe.prepare_case(monkeypatch, patched=True,
        error=OSError("damaged optional cache"))
    report = check()
    assert report.ran and not report.violations
    assert observation["cache_called"] == 1
    assert observation["direct_equilibrium_completed"] > 0


def test_latched_gold_deadline_rejects_swallowed_timeout_after_completed_check(monkeypatch):
    candidate = HERE / "gold-driver-candidate.py"
    assert candidate.is_file(), "Coordinate with cache_design's queued deadline candidate before this guard"
    outcomes = []
    for label, source in (("old", probe.ROOT / "experiments/mvp/gold_tools/driver.py"),
                          ("latched", candidate)):
        with pytest.MonkeyPatch.context() as patch:
            clock, signals = probe.FakeClock(), probe.FakeSignal()
            deadline = probe.deadline_from_source(source, clock, signals)

            def alarm_in_optional_cache():
                clock.now = 121.0
                signals.fire()

            check, observation = probe.prepare_case(patch, on_cache=alarm_in_optional_cache)
            report = None
            unavailable = False
            try:
                with deadline(120.0):
                    report = check()
            except TimeoutError:
                unavailable = True
            assert report is not None and report.ran and not report.violations
            assert observation["direct_equilibrium_completed"] > 0
            assert signals.handler == "original-handler"
            assert signals.timer == (0.0, 0.0)
            outcomes.append({"deadline": label, "unavailable": unavailable,
                "deadline_source_sha256": deadline._guard_source_sha256,
                "checker_part_completed": True, **observation})
    assert outcomes[0]["unavailable"] is False
    assert outcomes[1]["unavailable"] is True
    (HERE / "latched-deadline-checker-integration.json").write_text(json.dumps({
        "outcomes": outcomes, "source_pins": probe.source_pins(),
        "actual_signals_used": False, "input_kind": "tiny synthetic isolated in-port fixture",
        "full_gold_worker_run": False}, indent=2) + "\n")

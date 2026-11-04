"""The supplement delegates physics and preserves the checker's evidence."""
from types import SimpleNamespace

import pytest

from stowage.checker.perf import current_cache
from stowage.model import PartReport, Violation
from stowage.model.fixtures import build_instance
from . import securing


def test_preserves_original_violation_fields_and_uses_workspace(monkeypatch):
    instance, ship = SimpleNamespace(call_index=0), object()
    violation = Violation(rule_id="iacs.member_irs.container_securing", severity="hard", call_index=0,
        containers=["QZXU0000605"], slots=["0100688"], measured=1.0117009175,
        limit=1., unit="ratio", message="max_fitting_ratio exceeds the declared synthetic securing limit.")
    part = PartReport(name="lashing", violations=[violation], metrics={"max_fitting_ratio": violation.measured})

    def check(actual_instance, actual_ship, *, call_index):
        assert actual_instance is instance and actual_ship is ship and call_index == 0
        assert current_cache() is not None
        return part

    monkeypatch.setattr(securing, "check_lashing", check)
    broken, metadata = securing.screen_securing(instance, ship)
    assert len(broken) == 1
    assert all(broken[0][key] == value for key, value in violation.model_dump(mode="json").items())
    assert broken[0]["boxes"] == violation.containers
    assert metadata["report"] == part.model_dump(mode="json")
    assert metadata["max_fitting_ratio"] == violation.measured
    assert metadata["ran"] is True and metadata["passed"] is False
    assert metadata["phase"] == "departure" and metadata["full_check_pending"] is True
    assert current_cache() is None


@pytest.mark.parametrize("part", [PartReport(name="lashing", ran=False, error="No geometry"),
                                   PartReport(name="lashing", ran=False),
                                   PartReport(name="lashing", error="Numerical failure")])
def test_unavailable_part_never_clears(monkeypatch, part):
    monkeypatch.setattr(securing, "check_lashing", lambda *args, **kwargs: part)
    broken, metadata = securing.screen_securing(SimpleNamespace(call_index=0), object())
    assert metadata["passed"] is False
    assert broken == []


def test_unexpected_failure_has_no_invented_checker_finding(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic failure")
    monkeypatch.setattr(securing, "check_lashing", fail)
    broken, metadata = securing.screen_securing(SimpleNamespace(call_index=0), object())
    assert broken == [] and metadata["passed"] is False and metadata["ran"] is False
    assert "Synthetic failure" in metadata["error"]


def test_json_inputs_and_pass_keep_source_unchanged(monkeypatch):
    instance = build_instance()
    before = instance.model_dump(mode="json")
    ship = instance.onboard.model_dump(mode="json")
    def check(actual_instance, actual_ship, *, call_index):
        assert actual_instance.id == instance.id
        assert actual_ship.model_dump(mode="json") == ship
        return PartReport(name="lashing", metrics={"max_fitting_ratio": .7})
    monkeypatch.setattr(securing, "check_lashing", check)
    broken, metadata = securing.screen_securing(before, ship)
    assert broken == [] and metadata["passed"] is True
    assert before == instance.model_dump(mode="json")
    assert metadata["metrics"]["max_fitting_ratio"] == .7


def _prepared_screen(monkeypatch, *, static_broken=None, replay_violations=None):
    from . import moves, physics, visibility
    prepared = SimpleNamespace(instance=object(), ship=object(),
        replay=SimpleNamespace(violations=list(replay_violations or [])),
        plan=SimpleNamespace(moves=[None]))
    monkeypatch.setattr(physics, "fast_check", lambda instance, ship: (
        list(static_broken or []), {"note": physics.FAST_NOTE, "frames": [{"step": 0}]}))
    monkeypatch.setattr(moves, "_counts", lambda item: {"extra_lifts": 0})
    monkeypatch.setattr(visibility, "screen_visibility", lambda *args: (
        [], {"passed": True, "note": visibility.NOTE}))
    return moves, prepared


@pytest.mark.parametrize("gate", ["static", "rough", "replay"])
def test_prior_hard_gate_skips_departure_securing(monkeypatch, gate):
    from time import perf_counter
    from .physics import FAST_NOTE
    row = {"rule_id": "iscode.loading.gm" if gate == "rough" else "expert.physical.size_fit",
           "severity": "hard", "measured": None, "limit": None, "boxes": []}
    if gate == "rough":
        row["approximation"] = True
    replay = [Violation(rule_id="expert.moves.bottom_up", severity="hard", call_index=0,
                        message="Synthetic crane rejection")] if gate == "replay" else []
    moves, prepared = _prepared_screen(monkeypatch, static_broken=[] if replay else [row],
                                      replay_violations=replay)
    def unexpected(*args, **kwargs):
        raise AssertionError("securing must be skipped after an earlier hard gate")
    monkeypatch.setattr(securing, "screen_securing", unexpected)
    verdict = moves._screen(prepared, perf_counter())
    assert verdict.legal is False
    assert "securing_screen" not in verdict.gauges
    assert verdict.gauges["note"] == FAST_NOTE


def test_clear_gate_runs_on_requested_ship_and_labels_departure_only(monkeypatch):
    from time import perf_counter
    moves, prepared = _prepared_screen(monkeypatch)
    metadata = {"passed": True, "ran": True, "phase": "departure", "note": securing.NOTE}
    calls = []
    def screen(instance, ship):
        calls.append((instance, ship))
        return [], metadata
    monkeypatch.setattr(securing, "screen_securing", screen)
    verdict = moves._screen(prepared, perf_counter())
    assert calls == [(prepared.instance, prepared.ship)]
    assert verdict.legal is True and verdict.screen == "fast"
    assert verdict.gauges["full_check_pending"] is True
    assert verdict.gauges["securing_screen"] == metadata
    assert "hull strength, lashing, visibility" not in verdict.gauges["note"]
    assert "Departure securing screened" in verdict.gauges["note"]
    assert verdict.gauges["frames"][0]["step"] == 1


@pytest.mark.parametrize("passed", [False, None, "true", 1])
def test_securing_metadata_must_explicitly_clear_even_without_broken_row(monkeypatch, passed):
    from time import perf_counter
    moves, prepared = _prepared_screen(monkeypatch)
    monkeypatch.setattr(securing, "screen_securing", lambda *args: (
        [], {"passed": passed, "ran": False, "error": "Synthetic unavailable result", "note": securing.NOTE}))
    verdict = moves._screen(prepared, perf_counter())
    assert verdict.legal is False
    assert verdict.broken == []
    assert verdict.gauges["securing_screen"]["error"]


def test_securing_finding_reaches_verdict_without_altering_checker_fields(monkeypatch):
    from time import perf_counter
    moves, prepared = _prepared_screen(monkeypatch)
    row = {"rule_id": "iacs.member_irs.container_securing", "severity": "hard",
           "message": "max_fitting_ratio exceeds the declared synthetic securing limit.",
           "measured": 1.011700917545739, "limit": 1., "unit": "ratio",
           "boxes": ["QZXU0000605"], "slots": ["0100688"], "source": "departure_securing"}
    monkeypatch.setattr(securing, "screen_securing", lambda *args: (
        [row], {"passed": False, "ran": True, "note": securing.NOTE}))
    verdict = moves._screen(prepared, perf_counter())
    assert verdict.legal is False
    assert verdict.broken == [row]

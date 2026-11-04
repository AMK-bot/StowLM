"""Canonical visibility evidence and ordered, fail-closed screen integration."""
from time import perf_counter
from types import SimpleNamespace

import pytest

from stowage.checker.perf import current_cache
from stowage.model import PartReport, Violation
from stowage.model.fixtures import build_instance
from . import visibility


def _violation():
    return Violation(rule_id="solas.v22.visibility", severity="hard", call_index=0,
        containers=["QZXU0001601"], slots=["0090390"], measured=278.9309622643067,
        limit=268.88, unit="m", message="Synthetic canonical visibility finding")


def test_public_part_evidence_is_preserved_in_its_own_workspace(monkeypatch):
    instance, ship = SimpleNamespace(call_index=0), object()
    violation = _violation()
    part = PartReport(name="visibility", violations=[violation],
                     metrics={"blind_distance_m": violation.measured, "limit_m": violation.limit})
    def check(actual_instance, actual_ship, *, call_index):
        assert actual_instance is instance and actual_ship is ship and call_index == 0
        assert current_cache() is not None
        return part
    monkeypatch.setattr(visibility, "check_visibility", check)
    broken, metadata = visibility.screen_visibility(instance, ship)
    assert all(broken[0][key] == value for key, value in violation.model_dump(mode="json").items())
    assert broken[0]["boxes"] == violation.containers
    assert metadata["report"] == part.model_dump(mode="json")
    assert metadata["blind_distance_m"] == violation.measured and metadata["limit_m"] == violation.limit
    assert metadata["phase"] == "departure" and metadata["full_check_pending"] is True
    assert metadata["passed"] is False and current_cache() is None


@pytest.mark.parametrize("part", [PartReport(name="visibility", ran=False),
                                   PartReport(name="visibility", ran=False, error="No geometry"),
                                   PartReport(name="visibility", error="Numerical failure")])
def test_unavailable_public_part_never_clears(monkeypatch, part):
    monkeypatch.setattr(visibility, "check_visibility", lambda *args, **kwargs: part)
    broken, metadata = visibility.screen_visibility(SimpleNamespace(call_index=0), object())
    assert broken == [] and metadata["passed"] is False


def test_unexpected_failure_has_no_invented_checker_rule(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic failure")
    monkeypatch.setattr(visibility, "check_visibility", fail)
    broken, metadata = visibility.screen_visibility(SimpleNamespace(call_index=0), object())
    assert broken == [] and metadata["passed"] is False and metadata["ran"] is False
    assert "Synthetic failure" in metadata["error"]


def test_json_inputs_pass_without_mutating_source(monkeypatch):
    instance = build_instance()
    original = instance.model_dump(mode="json")
    ship = instance.onboard.model_dump(mode="json")
    def check(actual_instance, actual_ship, *, call_index):
        assert actual_instance.id == instance.id
        assert actual_ship.model_dump(mode="json") == ship
        return PartReport(name="visibility", metrics={"blind_distance_m": 100., "limit_m": 268.88})
    monkeypatch.setattr(visibility, "check_visibility", check)
    broken, metadata = visibility.screen_visibility(original, ship)
    assert broken == [] and metadata["passed"] is True
    assert original == instance.model_dump(mode="json")


def _screen_case(monkeypatch, *, broken=None, replay=None, securing_passed=True, securing_broken=None):
    from . import moves, physics, securing
    prepared = SimpleNamespace(instance=object(), ship=object(),
        replay=SimpleNamespace(violations=list(replay or [])), plan=SimpleNamespace(moves=[None]))
    order = []
    def rough(instance, ship):
        order.append("rough")
        return list(broken or []), {"note": physics.FAST_NOTE, "frames": [{"step": 0}]}
    def secure(instance, ship):
        order.append("securing")
        return list(securing_broken or []), {"passed": securing_passed, "note": securing.NOTE}
    monkeypatch.setattr(physics, "fast_check", rough)
    monkeypatch.setattr(securing, "screen_securing", secure)
    monkeypatch.setattr(moves, "_counts", lambda item: {"extra_lifts": 0})
    return moves, prepared, order


@pytest.mark.parametrize("gate", ["static", "rough", "replay", "securing"])
def test_visibility_is_skipped_after_any_prior_failed_gate(monkeypatch, gate):
    prior = {"rule_id": "iscode.loading.gm" if gate == "rough" else "expert.physical.size_fit",
             "severity": "hard", "measured": None, "limit": None, "boxes": []}
    replay = [Violation(rule_id="expert.moves.bottom_up", severity="hard", call_index=0,
                        message="Synthetic crane rejection")] if gate == "replay" else []
    moves, prepared, order = _screen_case(monkeypatch,
        broken=[prior] if gate in {"static", "rough"} else [], replay=replay,
        securing_passed=gate != "securing")
    def unexpected(*args):
        raise AssertionError("visibility must be skipped after an earlier failed gate")
    monkeypatch.setattr(visibility, "screen_visibility", unexpected)
    verdict = moves._screen(prepared, perf_counter())
    assert verdict.legal is False and "visibility_screen" not in verdict.gauges
    assert order == (["rough", "securing"] if gate == "securing" else ["rough"])


def test_visibility_runs_after_securing_on_same_requested_ship(monkeypatch):
    moves, prepared, order = _screen_case(monkeypatch)
    metadata = {"passed": True, "phase": "departure", "note": visibility.NOTE}
    def screen(instance, ship):
        assert instance is prepared.instance and ship is prepared.ship
        order.append("visibility")
        return [], metadata
    monkeypatch.setattr(visibility, "screen_visibility", screen)
    verdict = moves._screen(prepared, perf_counter())
    assert order == ["rough", "securing", "visibility"]
    assert verdict.legal is True and verdict.screen == "fast"
    assert verdict.gauges["visibility_screen"] == metadata
    assert verdict.gauges["full_check_pending"] is True
    assert "visibility or intermediate-lift check" not in verdict.gauges["note"]
    assert "all other full-checker parts" not in verdict.gauges["note"]
    assert "Departure securing screened" in verdict.gauges["note"]
    assert visibility.NOTE in verdict.gauges["note"]


@pytest.mark.parametrize("passed", [False, None, 1, "true"])
def test_visibility_metadata_must_explicitly_clear_without_a_broken_row(monkeypatch, passed):
    moves, prepared, _ = _screen_case(monkeypatch)
    monkeypatch.setattr(visibility, "screen_visibility", lambda *args: (
        [], {"passed": passed, "ran": False, "note": visibility.NOTE}))
    verdict = moves._screen(prepared, perf_counter())
    assert verdict.legal is False and verdict.broken == []


def test_original_visibility_finding_reaches_verdict(monkeypatch):
    moves, prepared, _ = _screen_case(monkeypatch)
    row = _violation().model_dump(mode="json")
    row.update(boxes=list(row["containers"]), source="departure_visibility")
    monkeypatch.setattr(visibility, "screen_visibility", lambda *args: (
        [row], {"passed": False, "ran": True, "note": visibility.NOTE}))
    verdict = moves._screen(prepared, perf_counter())
    assert verdict.legal is False and verdict.broken == [row]

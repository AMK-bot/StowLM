"""Exact disk memo of full.check (app/check_cache.py): hit, misses on plan / epoch change, corrupt file, kill switch."""
import json

import pytest

from app import check_cache
from app.checker import build_checker_payload
from app.tests.report_helpers import bind_report
from stowage.checker import full
from stowage.model import CheckReport, PartReport


@pytest.fixture
def counted(monkeypatch, tmp_path):
    """Enabled cache in a temp dir; full.check replaced by a counted, bound, clean synthetic report."""
    monkeypatch.delenv(check_cache.KILL_ENV, raising=False)
    monkeypatch.setattr(check_cache, "CACHE_DIR", tmp_path / "checks")
    calls = []

    def fake_check(instance, plan, **_):
        calls.append(plan.id)
        report = CheckReport(passed=True, parts={"moves": PartReport(name="moves", ran=True, metrics={"x": 1.5})})
        return bind_report(report, instance, plan)

    monkeypatch.setattr(full, "check", fake_check)
    return calls


def test_second_call_is_a_hit(counted, replay_case):
    instance, plan = replay_case
    first = check_cache.cached_check(instance, plan)
    second = check_cache.cached_check(instance, plan)
    assert counted == [plan.id]
    assert second.model_dump(mode="json") == first.model_dump(mode="json")
    files = list(check_cache.CACHE_DIR.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["checker_epoch"] == check_cache.checker_epoch()
    assert data["plan_sha256"] == check_cache.content_digest(plan)


def test_hit_feeds_the_unchanged_payload_path(counted, replay_case):
    instance, plan = replay_case
    a = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    b = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert counted == [plan.id] and a == b and a["bound"] is True


def test_plan_change_misses(counted, replay_case):
    instance, plan = replay_case
    check_cache.cached_check(instance, plan)
    other = plan.model_copy(update={"moves": plan.moves[:-1]})
    check_cache.cached_check(instance, other)
    assert len(counted) == 2


def test_epoch_change_misses(counted, replay_case, monkeypatch):
    instance, plan = replay_case
    check_cache.cached_check(instance, plan)
    monkeypatch.setattr(check_cache, "checker_epoch", lambda: "0" * 64)
    check_cache.cached_check(instance, plan)
    assert len(counted) == 2


def test_epoch_tracks_source_bytes(monkeypatch, tmp_path):
    (tmp_path / "src/stowage").mkdir(parents=True)
    (tmp_path / "params").mkdir()
    source = tmp_path / "src/stowage/x.py"
    source.write_text("A = 1\n")
    monkeypatch.setattr(check_cache, "ROOT", tmp_path)
    before, _ = check_cache._compute_epoch()
    source.write_text("A = 2\n")
    after, _ = check_cache._compute_epoch()
    assert before != after


@pytest.mark.parametrize("damage", ["garbage", "truncate", "epoch", "unbound"])
def test_corrupt_or_foreign_file_misses(counted, replay_case, damage):
    instance, plan = replay_case
    check_cache.cached_check(instance, plan)
    path, = check_cache.CACHE_DIR.glob("*.json")
    text = path.read_text()
    if damage == "garbage":
        path.write_text("not json")
    elif damage == "truncate":
        path.write_text(text[: len(text) // 2])
    else:
        data = json.loads(text)
        if damage == "epoch":
            data["checker_epoch"] = "f" * 64
        else:
            data["report"]["provenance"]["parents"] = []
        path.write_text(json.dumps(data))
    report = check_cache.cached_check(instance, plan)
    assert len(counted) == 2
    assert full.report_matches_inputs(report, instance, plan)


def test_budget_diagnostics_are_never_cached(counted, replay_case, monkeypatch):
    instance, plan = replay_case

    def timed_out(instance, plan, **_):
        counted.append(plan.id)
        part = PartReport(name="stability", ran=False, error="stability time budget exceeded: entry",
                          metrics={"time_budget_exceeded": 1.0})
        return bind_report(CheckReport(passed=False, parts={"stability": part}), instance, plan)

    monkeypatch.setattr(full, "check", timed_out)
    check_cache.cached_check(instance, plan)
    check_cache.cached_check(instance, plan)
    assert len(counted) == 2
    assert not list(check_cache.CACHE_DIR.glob("*.json"))


def test_kill_switch(counted, replay_case, monkeypatch):
    instance, plan = replay_case
    monkeypatch.setenv(check_cache.KILL_ENV, "1")
    check_cache.cached_check(instance, plan)
    check_cache.cached_check(instance, plan)
    assert len(counted) == 2
    assert not check_cache.CACHE_DIR.exists()


def test_sources_changed_mid_process_are_not_written(counted, replay_case, monkeypatch):
    instance, plan = replay_case
    reasons = iter([None, check_cache.DRIFT_REASON])  # current before the check, drifted when it finishes
    monkeypatch.setattr(check_cache, "epoch_drift", lambda: next(reasons))
    with pytest.raises(check_cache.CheckerEpochDrift):
        check_cache.cached_check(instance, plan)
    assert not check_cache.CACHE_DIR.exists() or not list(check_cache.CACHE_DIR.glob("*.json"))

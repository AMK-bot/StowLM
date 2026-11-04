"""Regressions for rev-demo P1-1 (cached PASS survives drift), P1-3 (lite approval) and P1-6 (old physics frames)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app import check_cache, stage_physics
from app.backend import build_replay
from app.checker import build_checker_payload
from app.flow import DemoService
from app.stage_fast import build_lite_replay
from app.stage_service import StageService
from app.tests.report_helpers import bind_report
from stowage.checker import full
from stowage.model import CheckReport, PartReport, PlannerResult, Provenance

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "experiments/mvp/smoke/run-20261029/tiny-feeder_500-0"
needs_smoke = pytest.mark.skipif(not (SMOKE / "plan.json").is_file(), reason="smoke tiny run not present")
REASON = check_cache.DRIFT_REASON


# ------------------------------------------------------------------ P1-1: a stored or memoised PASS and drift
@pytest.fixture
def world(monkeypatch, tmp_path):
    """A small source tree standing in for src/params, an enabled disk cache, and a counted passing checker."""
    root = tmp_path / "repo"
    (root / "src/stowage").mkdir(parents=True)
    (root / "params").mkdir()
    (root / "src/stowage/x.py").write_text("A = 1\n")
    (root / "params/ship.toml").write_text("gm = 1.0\n")
    monkeypatch.setattr(check_cache, "ROOT", root)
    monkeypatch.setattr(check_cache, "CACHE_DIR", tmp_path / "checks")
    monkeypatch.setattr(check_cache, "_epoch", None)
    monkeypatch.setattr(check_cache, "_epoch_environment", None)
    monkeypatch.setattr(check_cache, "_loaded_mismatch", False)
    monkeypatch.setattr(check_cache, "_loaded",
                        (check_cache._fingerprint(check_cache._source_files()), check_cache._environment()))
    monkeypatch.delenv(check_cache.KILL_ENV, raising=False)
    calls = []

    def fake_check(instance, plan, **_):
        calls.append(plan.id)
        report = CheckReport(passed=True, parts={"moves": PartReport(name="moves", ran=True, metrics={"x": 1.0})})
        return bind_report(report, instance, plan)

    monkeypatch.setattr(full, "check", fake_check)
    return root, calls


def _files(directory):
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


@pytest.mark.parametrize("edit", ["params/ship.toml", "src/stowage/x.py"])
def test_hit_after_parameter_or_source_drift_is_refused(world, replay_case, edit):
    root, calls = world
    instance, plan = replay_case
    check_cache.cached_check(instance, plan)
    assert calls == [plan.id] and check_cache.epoch_drift() is None
    before = _files(check_cache.CACHE_DIR)
    original = (root / edit).read_text()
    (root / edit).write_text(original + "# changed\n")
    with pytest.raises(check_cache.CheckerEpochDrift, match="restart the app"):
        check_cache.cached_check(instance, plan)
    assert calls == [plan.id]  # no stored PASS returned, and the stale loaded code did not run either
    assert _files(check_cache.CACHE_DIR) == before
    (root / edit).write_text(original)  # the same bytes are the same epoch again
    assert check_cache.epoch_drift() is None
    check_cache.cached_check(instance, plan)
    assert calls == [plan.id]


def test_hit_after_environment_drift_is_refused(world, replay_case, monkeypatch):
    _, calls = world
    instance, plan = replay_case
    check_cache.cached_check(instance, plan)
    monkeypatch.setenv("STOWAGE_STABILITY_DIRECT", "1")
    with pytest.raises(check_cache.CheckerEpochDrift):
        check_cache.cached_check(instance, plan)
    monkeypatch.delenv("STOWAGE_STABILITY_DIRECT")
    monkeypatch.setenv("VECLIB_MAXIMUM_THREADS", "7" if os.environ.get("VECLIB_MAXIMUM_THREADS") != "7" else "6")
    assert check_cache.epoch_drift() == REASON
    assert calls == [plan.id]


def test_new_file_is_drift_and_a_touch_is_not(world, replay_case):
    root, _ = world
    instance, plan = replay_case
    check_cache.cached_check(instance, plan)
    os.utime(root / "params/ship.toml", (1, 1))
    assert check_cache.epoch_drift() is None  # stat differs, bytes do not
    (root / "params/new.toml").write_text("x = 1\n")
    assert check_cache.epoch_drift() == REASON


def test_drift_between_import_and_first_epoch_is_drift(world):
    root, _ = world
    (root / "params/ship.toml").write_text("gm = 2.0\n")  # after _loaded, before the lazy first epoch
    assert check_cache.epoch_drift() == REASON


@pytest.mark.parametrize("kill_switch", [False, True])
def test_payload_and_replay_under_drift_are_not_validated(world, replay_case, monkeypatch, kill_switch):
    root, calls = world
    instance, plan = replay_case
    check_cache.checker_epoch()
    if kill_switch:
        monkeypatch.setenv(check_cache.KILL_ENV, "1")
    (root / "params/ship.toml").write_text("gm = 2.0\n")
    payload = build_checker_payload(instance, plan, evaluated_frame=len(plan.moves))
    assert payload["bound"] is False and payload["passed"] is False and payload["report"] is None
    assert payload["binding_status"] == REASON
    replay = build_replay(instance, plan)
    meta = replay["metadata"]
    assert calls == []  # a fresh run of the stale loaded code is not a certification either
    assert meta["validation_status"] == "NOT VALIDATED" and meta["validation_reasons"] == [REASON]
    assert meta["shippable"] is False and meta["departure_approved"] is False
    assert meta["checker_status"] == REASON and REASON in meta["notice"]
    assert replay["checker"]["validation_reasons"] == [REASON] and replay["checker"]["passed"] is False


def _approved_replay(instance, plan):
    replay = build_replay(instance, plan)
    replay["metadata"].update(shippable=True, departure_approved=True, validation_status="PASS",
                              validation_reasons=[], checker_status="passed")
    replay["checker"].update(passed=True, validation_status="PASS", validation_reasons=[])
    return replay


def test_replay_memo_is_not_served_as_approved_after_drift(replay_case, monkeypatch):
    instance, plan = replay_case
    demo = DemoService(instance, plan, initial_replay=_approved_replay(instance, plan), discover=False)
    plan_id = demo.active_plan_id
    assert demo.replay(plan_id)["metadata"]["departure_approved"] is True  # memo served while current
    monkeypatch.setattr(check_cache, "epoch_drift", lambda: REASON)
    replay = demo.replay(plan_id)
    assert replay["metadata"]["departure_approved"] is False and replay["metadata"]["shippable"] is False
    assert replay["metadata"]["validation_status"] == "NOT VALIDATED"
    assert replay["checker"]["report"] is None and replay["checker"]["validation_reasons"] == [REASON]
    assert replay["frames"]  # the lifts stay inspectable as a diagnostic
    monkeypatch.undo()
    monkeypatch.setenv("STOWLM_NO_CHECK_CACHE", "1")
    assert demo.replay(plan_id)["metadata"]["departure_approved"] is True  # the withdrawal never touched the memo


def test_stage_memos_are_not_served_after_drift(replay_case, monkeypatch, tmp_path):
    from app.identity import instance_content_digest
    instance, plan = replay_case
    demo = DemoService(instance, plan, initial_replay=_approved_replay(instance, plan), discover=False)
    plan_id = demo.active_plan_id
    stage = StageService(demo, cache_dir=tmp_path, live_physics=False)
    replay = demo.replay(plan_id)
    digest = instance_content_digest(instance)
    rival = ({"label": "Two-step", "checker_passed": True}, None)
    stage._baselines.put(digest, rival)
    stage._baselines.put("spark:" + digest, rival)
    stage._lower.put(digest, ({"total": 1}, None))
    key = stage_physics.input_digests(instance, plan)["key"]
    stage._physics.put(key, {"status": "ready", "done": 1, "total": 1, "physics": {"frames": []}, "source": "cache",
                             "reason": None})
    assert stage.baseline(plan_id, replay, instance) == rival
    assert stage.physics_status(plan_id, replay=replay)["status"] == "ready"
    monkeypatch.setattr(check_cache, "epoch_drift", lambda: REASON)
    assert stage.baseline(plan_id, replay, instance) == (None, REASON)
    assert stage.spark(plan_id, replay, instance) == (None, REASON)
    assert stage.lower_bound(instance) == (None, REASON)
    view = stage.physics_status(plan_id, replay=replay)
    assert view["status"] == "unavailable" and view["physics"] is None and view["reason"] == REASON


# ------------------------------------------------------------------ P1-3: lite refuses what regular refuses
@pytest.fixture(scope="module")
def smoke_real():
    """The real smoke instance + plan and its real full-checker report (run once, reused by every variant)."""
    if not (SMOKE / "plan.json").is_file():
        pytest.skip("smoke tiny run not present")
    from stowage.io import load_instance, load_plan
    instance, plan = load_instance(SMOKE / "instance.json"), load_plan(SMOKE / "plan.json")
    return instance, plan, full.check(instance, plan)


SAME = ("shippable", "departure_approved", "validation_status", "validation_reasons", "checker_status", "repaired",
        "repairing_solver", "repair_note", "repair_status", "fallback_needed", "stub_tainted")


def _both(instance, plan, result):
    regular = build_replay(instance, plan, planner_result=result)
    lite = build_lite_replay(instance, plan, planner_result=result, run_checker=True)
    for key in SAME:
        assert lite["metadata"][key] == regular["metadata"][key], key
    assert lite["checker"]["passed"] == regular["checker"]["passed"]
    assert lite["metadata"]["shipping_blockers"] == regular["metadata"]["shipping_blockers"]
    assert lite["metadata"]["provenance"]["stubs"] == regular["metadata"]["provenance"]["stubs"]
    return regular, lite


@needs_smoke
@pytest.mark.parametrize("mode", ["timeout", "error", "tainted", "later_plans"])
def test_lite_refuses_what_regular_refuses(smoke_real, monkeypatch, mode):
    instance, plan, report = smoke_real
    monkeypatch.setattr(full, "check", lambda *a, **k: report)  # the real report; only the wrapper varies
    result = PlannerResult(status="ok", plan=plan, planner="solver.scalable", planner_version="test")
    if mode in {"timeout", "error"}:
        result.status = mode
    elif mode == "tainted":
        result.provenance = Provenance(stubs={"solver.foo": "stub"})
    else:
        result.later_plans = [plan.model_copy(update={"id": "later", "call_index": 1, "moves": []})]
    regular, lite = _both(instance, plan, result)
    assert regular["metadata"]["departure_approved"] is False and lite["metadata"]["departure_approved"] is False
    assert lite["metadata"]["shipping_blockers"]


@needs_smoke
def test_lite_matches_regular_with_a_real_check_on_the_review_case(smoke_real):
    instance, plan, _ = smoke_real
    result = PlannerResult(status="timeout", plan=plan, planner="solver.scalable", planner_version="test")
    regular, lite = _both(instance, plan, result)  # no stub: both builders run the real checker
    assert regular["metadata"]["departure_approved"] is False and lite["metadata"]["departure_approved"] is False
    assert lite["checker"]["report"]["passed"] is True  # the physical pass alone never approved it
    assert "Planner status is timeout" in lite["metadata"]["shipping_blockers"]
    assert "Planner status is timeout" in lite["metadata"]["notice"]


@needs_smoke
def test_lite_carries_repair_flags_and_still_approves_clean_evidence(smoke_real, monkeypatch):
    instance, plan, report = smoke_real
    monkeypatch.setattr(full, "check", lambda *a, **k: report)
    clean = PlannerResult(status="ok", plan=plan, planner="solver.scalable", planner_version="test")
    regular, lite = _both(instance, plan, clean)
    assert regular["metadata"]["departure_approved"] is True and lite["metadata"]["departure_approved"] is True
    repaired = clean.model_copy(update={"repaired": True, "repair_note": "fixed by solver"})
    regular, lite = _both(instance, plan, repaired)
    assert lite["metadata"]["repaired"] is True and lite["metadata"]["repairing_solver"] == "solver.scalable"


def test_lite_without_the_checker_stays_unapproved(replay_case):
    instance, plan = replay_case
    lite = build_lite_replay(instance, plan)
    assert lite["metadata"]["departure_approved"] is False
    assert lite["metadata"]["validation_reasons"] == ["full checker not run (lite replay)"]


# ------------------------------------------------------------------ P1-6: frames bound to the physics epoch
def _frame(step, **extra):
    return {"step": step, "error": None, "criteria": "sea-going IS Code (as if departing now)", "gz": [],
            "stability_passed": True, "strength_passed": True, "gm_m": 2.0, "gm_min_m": 0.15, "heel_deg": 0.1,
            "trim_m": 0.2, "draught_aft_m": 4.0, "draught_fwd_m": 3.8, "draught_mean_m": 3.9, "max_draught_m": 8.0,
            "displacement_t": 1000.0, "sf_n": [1, None], "bm_nm": [2, None], "max_sf_ratio": 0.4, "max_bm_ratio": 0.5,
            **extra}


def _block(instance, plan):
    frames, policy = stage_physics.select_keyframes(instance, plan)
    return {"frames": [_frame(k) for k in frames], "keyframes": policy, "n_lifts": len(plan.moves)}


def _complete(instance, plan, directory):
    return stage_physics.write_cache(instance, plan, _block(instance, plan), cache_dir=directory)


def _partial(instance, plan, directory):
    return stage_physics.write_cache(instance, plan, None, partial={0: _frame(0)}, cache_dir=directory)


def _rewrite(path, edit):
    data = json.loads(path.read_text())
    edit(data)
    path.write_text(json.dumps(data))  # json.dumps emits NaN, as a damaged writer could


def _refused(instance, plan, directory):
    assert stage_physics.load_cached(instance, plan, directory) is None
    assert stage_physics.load_partial(instance, plan, directory) == {}


def test_current_cache_round_trips(replay_case, tmp_path):
    instance, plan = replay_case
    block = _block(instance, plan)
    stage_physics.write_cache(instance, plan, block, cache_dir=tmp_path)
    assert stage_physics.load_cached(instance, plan, tmp_path) == block
    assert set(stage_physics.load_partial(instance, plan, tmp_path)) == {f["step"] for f in block["frames"]}
    _partial(instance, plan, tmp_path)
    assert stage_physics.load_cached(instance, plan, tmp_path) is None
    assert set(stage_physics.load_partial(instance, plan, tmp_path)) == {0}


@pytest.mark.parametrize("write", [_complete, _partial])
def test_old_version_is_rejected_by_both_loaders(replay_case, tmp_path, monkeypatch, write):
    instance, plan = replay_case
    with monkeypatch.context() as m:
        m.setattr(stage_physics, "PHYSICS_VERSION", "obsolete-physics-v0")
        write(instance, plan, tmp_path)
    _refused(instance, plan, tmp_path)


def test_old_version_resume_cannot_republish_old_frames(replay_case, tmp_path, monkeypatch):
    """The review's case: the obsolete complete file must not seed a resume under the current version."""
    instance, plan = replay_case
    with monkeypatch.context() as m:
        m.setattr(stage_physics, "PHYSICS_VERSION", "obsolete-physics-v0")
        block = _block(instance, plan)
        block["frames"][0].update(gm_m=99.0)
        stage_physics.write_cache(instance, plan, block, cache_dir=tmp_path)
    monkeypatch.setattr(stage_physics, "compute_frame", lambda i, p, k: _frame(k))
    demo = DemoService(instance, plan, initial_replay=_approved_replay(instance, plan), discover=False)
    stage = StageService(demo, cache_dir=tmp_path)
    stage.physics_status(demo.active_plan_id)
    assert stage.wait_idle(30)
    view = stage.physics_status(demo.active_plan_id)
    assert view["status"] == "ready" and all(f["gm_m"] == 2.0 for f in view["physics"]["frames"])


@pytest.mark.parametrize("write", [_complete, _partial])
def test_checker_or_config_change_is_rejected_with_unchanged_inputs(replay_case, tmp_path, monkeypatch, write):
    instance, plan = replay_case
    write(instance, plan, tmp_path)
    with monkeypatch.context() as m:
        m.setattr(check_cache, "checker_epoch", lambda: "0" * 64)  # as a restart after a src/params/env change
        _refused(instance, plan, tmp_path)
    with monkeypatch.context() as m:
        m.setattr(stage_physics, "_LOADED_SOURCES", "0" * 64)  # as a restart after an app/stage_physics.py change
        m.setattr(stage_physics, "physics_drift", lambda: None)
        _refused(instance, plan, tmp_path)
    assert stage_physics.load_partial(instance, plan, tmp_path)


def test_launcher_and_precompute_default_the_hashed_thread_limits():
    for script in ("app/__main__.py", "experiments/mvp/ui/precompute_timeline.py"):
        text = (ROOT / script).read_text()
        assert all(f'"{key}"' in text for key in check_cache._ENV_KEYS), script
        assert "setdefault" in text


def test_epoch_covers_the_frame_shaping_modules():
    assert set(stage_physics._FRAME_SOURCES) == {"app/stage_physics.py", "app/stability.py", "app/checker.py"}


def test_frame_module_drift_stops_reads_and_writes(replay_case, tmp_path, monkeypatch):
    instance, plan = replay_case
    source = tmp_path / "root/app/stage_physics.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n")
    cache = tmp_path / "cache"
    _complete(instance, plan, cache)
    monkeypatch.setattr(stage_physics, "ROOT", tmp_path / "root")
    monkeypatch.setattr(stage_physics, "_FRAME_SOURCES", ("app/stage_physics.py",))
    monkeypatch.setattr(stage_physics, "_LOADED_SOURCES", stage_physics._source_digest())
    monkeypatch.setattr(stage_physics, "physics_epoch", lambda: "e" * 64)
    _rewrite(next(cache.iterdir()), lambda d: d.update(physics_epoch="e" * 64))
    assert stage_physics.load_cached(instance, plan, cache) is not None
    source.write_text("x = 2\n")
    assert stage_physics.physics_drift() == stage_physics.DRIFT_REASON
    _refused(instance, plan, cache)
    with pytest.raises(stage_physics.PhysicsEpochDrift):
        _complete(instance, plan, cache)


def test_checker_drift_stops_physics_reads_and_writes(replay_case, tmp_path, monkeypatch):
    instance, plan = replay_case
    _complete(instance, plan, tmp_path)
    monkeypatch.setattr(check_cache, "epoch_drift", lambda: REASON)
    _refused(instance, plan, tmp_path)
    with pytest.raises(stage_physics.PhysicsEpochDrift, match="restart the app"):
        _partial(instance, plan, tmp_path)


MALFORMED = {
    "step_bool": lambda f: f.update(step=True),
    "step_string": lambda f: f.update(step="0"),
    "step_negative": lambda f: f.update(step=-1),
    "step_past_end": lambda f: f.update(step=10_000),
    "missing_required": lambda f: f.pop("stability_passed"),
    "missing_measurement": lambda f: f.pop("gm_m"),
    "nan": lambda f: f.update(gm_m=float("nan")),
    "infinity": lambda f: f.update(trim_m=float("inf")),
    "string_number": lambda f: f.update(gm_m="2.0"),
    "bool_number": lambda f: f.update(gm_m=True),
    "verdict_not_bool": lambda f: f.update(stability_passed="yes"),
    "list_nan": lambda f: f.update(sf_n=[float("nan")]),
    "gz_not_finite": lambda f: f.update(gz=[{"deg": 10.0, "m": float("inf")}]),
    "gz_point_shape": lambda f: f.update(gz=[1.0]),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_frames_are_rejected_by_both_loaders(replay_case, tmp_path, name):
    instance, plan = replay_case
    path = _complete(instance, plan, tmp_path)
    _rewrite(path, lambda d: MALFORMED[name](d["physics"]["frames"][0]))
    _refused(instance, plan, tmp_path)
    path = _partial(instance, plan, tmp_path)
    _rewrite(path, lambda d: MALFORMED[name](d["partial_frames"][0]))
    _refused(instance, plan, tmp_path)


@pytest.mark.parametrize("edit", ["missing_keyframe", "duplicate", "lifts", "not_a_list"])
def test_incomplete_complete_block_is_rejected(replay_case, tmp_path, edit):
    instance, plan = replay_case
    path = _complete(instance, plan, tmp_path)

    def damage(data):
        physics = data["physics"]
        if edit == "missing_keyframe":
            physics["frames"].pop()
        elif edit == "duplicate":
            physics["frames"][1] = physics["frames"][0]
        elif edit == "lifts":
            physics["n_lifts"] += 1
        else:
            physics["frames"] = {"0": physics["frames"][0]}
    _rewrite(path, damage)
    assert stage_physics.load_cached(instance, plan, tmp_path) is None


def test_garbage_file_is_a_miss(replay_case, tmp_path):
    instance, plan = replay_case
    path = _complete(instance, plan, tmp_path)
    for text in ("not json", "[]", "null"):
        path.write_text(text)
        _refused(instance, plan, tmp_path)


def _stage_for_run(instance, plan, run, main, **options):
    demo = DemoService(instance, plan, initial_replay=_approved_replay(instance, plan), discover=False)
    replay = demo.replay(demo.active_plan_id)
    replay["metadata"]["run_folder"] = str(run)
    return demo, replay, StageService(demo, cache_dir=main, **options)


def test_run_local_cache_without_epoch_is_a_miss_then_live_into_the_main_cache(replay_case, tmp_path, monkeypatch):
    instance, plan = replay_case
    run, main = tmp_path / "run", tmp_path / "main"
    main.mkdir()
    local = _complete(instance, plan, run / "timeline-cache")
    _rewrite(local, lambda d: (d.pop("physics_epoch"), d.update(version="stage-physics-v2")))
    untouched = local.read_bytes()
    demo, replay, stage = _stage_for_run(instance, plan, run, main, live_physics=False)
    assert stage.read_only_cache_dirs(replay) == [(run / "timeline-cache").resolve()]
    view = stage.physics_status(demo.active_plan_id, replay=replay)
    assert view["status"] == "unavailable" and view["physics"] is None and "epoch" in view["reason"]

    monkeypatch.setattr(stage_physics, "compute_frame", lambda i, p, k: _frame(k))
    demo, replay, stage = _stage_for_run(instance, plan, run, main)
    stage.physics_status(demo.active_plan_id, replay=replay)
    assert stage.wait_idle(30)
    view = stage.physics_status(demo.active_plan_id, replay=replay)
    assert view["status"] == "ready" and view["source"] == "live"
    assert local.read_bytes() == untouched
    assert stage_physics.load_cached(instance, plan, main) is not None


def test_run_local_cache_at_the_current_epoch_is_still_used(replay_case, tmp_path):
    instance, plan = replay_case
    run, main = tmp_path / "run", tmp_path / "main"
    main.mkdir()
    _complete(instance, plan, run / "timeline-cache")
    demo, replay, stage = _stage_for_run(instance, plan, run, main, live_physics=False)
    view = stage.physics_status(demo.active_plan_id, replay=replay)
    assert view["status"] == "ready" and view["source"].startswith("cache (read-only")

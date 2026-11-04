"""Stage bundle (stage-v1), physics cache identity, endpoints, rule ticks and KPI arithmetic."""
from __future__ import annotations

import importlib.util
import io
import json
from http.client import HTTPResponse
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import stage_physics
from app.tests.report_helpers import historical
from app.stage import build_rules, compute_kpi, load_kpi_config, stage_counts

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "experiments/mvp/smoke/run-20261029/tiny-feeder_500-0"
needs_smoke = pytest.mark.skipif(not (SMOKE / "plan.json").is_file(), reason="smoke tiny run not present")


def _impact():
    spec = importlib.util.spec_from_file_location("impact_for_stage", ROOT / "experiments/impact-estimate/impact.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ fixtures
def cache_block(instance, plan):
    """A structurally valid physics block for these inputs (every keyframe, as error frames: no measurement)."""
    frames, policy = stage_physics.select_keyframes(instance, plan)
    return {"frames": [{"step": k, "error": "synthetic", "criteria": None, "gz": [], "stability_passed": None,
                        "strength_passed": None} for k in frames], "keyframes": policy, "n_lifts": len(plan.moves)}


@pytest.fixture(scope="module")
def smoke_models():
    if not (SMOKE / "plan.json").is_file():
        pytest.skip("smoke tiny run not present")
    from stowage.io import load_instance, load_plan
    return load_instance(SMOKE / "instance.json"), load_plan(SMOKE / "plan.json")


@pytest.fixture(scope="module")
def smoke_stage(smoke_models, tmp_path_factory):
    """One real replay (fresh full checker, ~15-30 s) shared by the bundle and endpoint tests."""
    from app.flow import DemoService
    from app.inputs import load_inputs
    from app.stage_service import StageService
    inputs = load_inputs(instance_path=SMOKE / "instance.json", plan_path=SMOKE / "plan.json")
    demo = DemoService(inputs.instance, inputs.plan, inputs.metadata, discover=False)
    cache = tmp_path_factory.mktemp("stage-cache")
    stage = StageService(demo, cache_dir=cache, live_physics=False, live_baseline=False)
    return demo, stage, cache


# ------------------------------------------------------------------ bundle shape
@needs_smoke
def test_bundle_shape_on_smoke_tiny(smoke_stage, smoke_models):
    demo, stage, _ = smoke_stage
    instance, plan = smoke_models
    bundle = stage.bundle(demo.active_plan_id)
    assert bundle["version"] == "stage-v1"
    assert set(bundle) >= {"meta", "geometry", "pods", "boxes", "initial", "steps", "physics", "comparison",
                           "rules", "xray", "explanation", "kpi"}
    meta = bundle["meta"]
    assert meta["checker_passed"] is True and meta["departure_approved"] is True
    assert meta["rung"] == "tiny" and meta["port"]["locode"] == "THLCH"
    # Steps mirror the engine replay one-for-one, with its running counts.
    replay = demo.replay(demo.active_plan_id)
    assert len(bundle["steps"]) == len(plan.moves) == len(replay["frames"]) - 1
    assert [s["kind"] for s in bundle["steps"]] == [m.kind for m in plan.moves]
    assert bundle["steps"][-1]["counts"] == stage_counts(replay["frames"][-1]["counts"])
    assert bundle["steps"][-1]["counts"]["total_lifts"] == len(plan.moves)
    assert all(s["nonproductive"] == (s["kind"] != "load") for s in bundle["steps"])
    # Geometry and cargo come straight from the instance.
    assert len(bundle["geometry"]["slots"]) == len(instance.vessel.cells)
    assert {c["id"] for c in bundle["geometry"]["hatch_covers"]} == {c.id for c in instance.vessel.hatch_covers}
    assert len(bundle["geometry"]["hull"]["stations_x_m"]) == len(instance.vessel.hull.stations)
    assert set(bundle["boxes"]) == {c.id for c in instance.containers}
    pods = {p["locode"]: p for p in bundle["pods"]}
    assert {c.pod for c in instance.containers} <= set(pods)
    assert len({p["color"] for p in bundle["pods"]}) == len(bundle["pods"])
    assert bundle["initial"]["yard"] and all(s["boxes"] for s in bundle["initial"]["yard"])
    # No physics cache in the temp dir and live compute off: physics is honestly absent.
    assert bundle["physics"] is None and bundle["physics_status"] == "unavailable"
    # Lower bound is measured; no baseline -> no KPI, with reasons.
    lb = bundle["comparison"]["lower_bound"]
    assert lb is not None and lb["total_lifts"] <= bundle["steps"][-1]["counts"]["total_lifts"]
    assert bundle["comparison"]["baseline"] is None and bundle["comparison"]["reasons"]["baseline"]
    assert bundle["kpi"] is None and bundle["comparison"]["reasons"]["kpi"]
    assert bundle["xray"]["available"] is False and bundle["xray"]["reason"]
    assert all(f["status"] in {"pass", "n/a"} for f in bundle["rules"]["families"])
    json.dumps(bundle, allow_nan=False)


@needs_smoke
def test_final_physics_frame_matches_the_full_checker(smoke_stage, smoke_models):
    """The departure keyframe recomputes exactly the bound full report's departure stability numbers."""
    demo, _, _ = smoke_stage
    instance, plan = smoke_models
    report = demo.replay(demo.active_plan_id)["checker"]["report"]
    frame = stage_physics.compute_frame(instance, plan, len(plan.moves))
    metrics = report["parts"]["stability"]["metrics"]
    assert frame["gm_m"] == pytest.approx(metrics["gm_m"], abs=1e-6)
    assert frame["draught_mean_m"] == pytest.approx(metrics["draught_mean_m"], abs=1e-6)
    assert frame["stability_passed"] is True and frame["strength_passed"] is True
    assert frame["gz"] and frame["tank_state"] == "declared departure ballast"


# ------------------------------------------------------------------ physics cache identity
@needs_smoke
def test_physics_cache_requires_exact_input_digests(smoke_models, tmp_path):
    instance, plan = smoke_models
    physics = cache_block(instance, plan)
    path = stage_physics.write_cache(instance, plan, physics, cache_dir=tmp_path)
    assert path.name == stage_physics.input_digests(instance, plan)["key"] + ".physics.json"
    assert stage_physics.load_cached(instance, plan, tmp_path) == physics
    # A different plan has a different key; even if renamed onto the right key, its digests reject it.
    shorter = plan.model_copy(update={"moves": plan.moves[:-1]})
    assert stage_physics.load_cached(instance, shorter, tmp_path) is None
    data = json.loads(path.read_text())
    data["plan_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    assert stage_physics.load_cached(instance, plan, tmp_path) is None
    # Partial checkpoints never count as a complete cache.
    stage_physics.write_cache(instance, plan, None, partial={0: physics["frames"][0]}, cache_dir=tmp_path)
    assert stage_physics.load_cached(instance, plan, tmp_path) is None
    assert set(stage_physics.load_partial(instance, plan, tmp_path)) == {0}


def test_keyframe_policy():
    containers = [SimpleNamespace(id=f"B{i}", gross_mass_kg=25000 if i % 7 == 0 else 5000) for i in range(300)]
    moves = [SimpleNamespace(kind="load", container_id=f"B{i}") for i in range(300)]
    frames, policy = stage_physics.select_keyframes(SimpleNamespace(containers=containers),
                                                    SimpleNamespace(moves=moves))
    assert policy == "every-k" and frames[0] == 0 and frames[-1] == 300
    assert len(frames) <= stage_physics.MAX_KEYFRAMES + 1 and frames == sorted(set(frames))
    small, policy = stage_physics.select_keyframes(SimpleNamespace(containers=containers),
                                                   SimpleNamespace(moves=moves[:12]))
    assert policy == "all" and small == list(range(13))


# ------------------------------------------------------------------ endpoints
class _Socket:
    def __init__(self, request=b""):
        self.incoming, self.outgoing = io.BytesIO(request), bytearray()

    def makefile(self, mode, *args):
        return self.incoming

    def sendall(self, data):
        self.outgoing.extend(data)


@pytest.fixture
def stage_get(smoke_stage, monkeypatch):
    demo, stage, cache = smoke_stage
    from app.server import make_handler
    handler = make_handler({}, demo, test_authority="stage.test",
                           stage_options={"cache_dir": cache, "live_physics": False, "live_baseline": False})

    def get(path):
        transport = _Socket(f"GET {path} HTTP/1.0\r\nHost: stage.test\r\n\r\n".encode())
        handler(transport, ("127.0.0.1", 1), object())
        response = HTTPResponse(_Socket(bytes(transport.outgoing)))
        response.begin()
        return response
    return get, demo


@needs_smoke
def test_stage_endpoints(stage_get):
    get, demo = stage_get
    response = get("/api/stage")
    assert response.status == 200
    bundle = json.loads(response.read())
    assert bundle["version"] == "stage-v1" and bundle["meta"]["plan_id"] == demo.active_plan_id
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert get(f"/api/stage?plan_id={demo.active_plan_id}").status == 200
    assert get("/api/stage?plan_id=plan-999").status == 404
    physics = json.loads(get("/api/stage/physics").read())
    assert physics["status"] == "unavailable" and physics["physics"] is None
    box = next(iter(bundle["boxes"]))
    explained = json.loads(get(f"/api/stage/explain?box={box}&live=1").read())
    assert explained["box"] == box and explained["text"].startswith(box) and explained["live_used"] is False
    assert get("/api/stage/explain?box=NOPE0000000").status == 404
    plan_text = json.loads(get("/api/stage/explain").read())
    assert plan_text["source"] in {"template", "spark", "unavailable"}
    stage_page = get("/stage")
    assert stage_page.status in {200, 404}  # 404 until the frontend agent adds stage.html


def test_stage_routes_reject_foreign_host(replay_case):
    from app.server import make_handler
    handler = make_handler({})
    transport = _Socket(b"GET /api/stage HTTP/1.0\r\nHost: evil.example\r\n\r\n")
    handler(transport, ("127.0.0.1", 1), object())
    response = HTTPResponse(_Socket(bytes(transport.outgoing)))
    response.begin()
    assert response.status == 403


def test_stage_routes_without_service_are_404():
    from app.server import make_handler
    handler = make_handler({}, test_authority="stage.test")
    transport = _Socket(b"GET /api/stage HTTP/1.0\r\nHost: stage.test\r\n\r\n")
    handler(transport, ("127.0.0.1", 1), object())
    response = HTTPResponse(_Socket(bytes(transport.outgoing)))
    response.begin()
    assert response.status == 404


# ------------------------------------------------------------------ rules mapping
def _rules_file(tmp_path):
    rules = [
        {"id": "iscode.loading.gm", "family": "IS Code stability", "source": "s", "description": "d", "hardness": "hard"},
        {"id": "iacs.s11a.longitudinal_strength", "family": "IACS strength", "source": "s", "description": "d",
         "hardness": "hard"},
        {"id": "imdg.segregation.7_2_4", "family": "IMDG", "source": "s", "description": "d", "hardness": "hard"},
        {"id": "expert.moves.top_access", "family": "operational", "source": "s", "description": "d",
         "hardness": "hard"},
    ]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules))
    return path


def test_rule_family_mapping(tmp_path):
    part = {"ran": True, "error": None}
    checker = {"bound": True, "report": {
        "parts": {"stability": part, "strength": {"ran": False, "error": "boom"}, "rules": part, "moves": part},
        "violations": [{"rule_id": "iscode.loading.gm"}, {"rule_id": "iscode.loading.gm"},
                       {"rule_id": "made.up.rule"}]}}
    rules = build_rules(checker, {"containers": []}, {"placements": []}, _rules_file(tmp_path))
    families = {f["family"]: f for f in rules["families"]}
    assert families["stability"]["status"] == "fail"
    assert families["stability"]["rules"][0]["violations"] == 2
    assert families["strength"]["status"] == "not_run"
    assert families["dg"]["status"] == "n/a"  # no dangerous goods aboard
    assert families["operational"]["status"] == "pass"
    assert families["other"]["rules"][0]["id"] == "made.up.rule"
    unbound = build_rules({"bound": False, "report": None}, {"containers": []}, None, _rules_file(tmp_path))
    assert {f["status"] for f in unbound["families"]} == {"not_run"}


def test_real_rule_list_families_are_all_mapped():
    from app.stage import FAMILY_INFO, RULES_PATH
    families = {rule["family"] for rule in json.loads(RULES_PATH.read_text())}
    assert families <= set(FAMILY_INFO)


# ------------------------------------------------------------------ KPI
def test_kpi_constants_match_impact_mid_scenario():
    impact = _impact()
    mid = next(s for s in impact.SCENARIOS if s.name == "mid")
    config = load_kpi_config()
    assert config["scenario"] == "mid"
    for key in ("yard_usd_per_move", "quay_usd_per_move", "yard_minutes_per_move", "quay_minutes_per_move",
                "berth_critical_path_fraction"):
        assert config[key] == getattr(mid, key), key
    assert config["quay_moves_per_restow"] == impact.QUAY_MOVES_PER_RESTOW


def test_kpi_arithmetic_matches_compare_counts():
    impact = _impact()
    mid = next(s for s in impact.SCENARIOS if s.name == "mid")
    base = {"loads": 40, "discharges": 0, "yard_rehandles": 9, "restows": 4, "hatch_lifts": 6, "total_lifts": 59,
            "projected_later_restows": 2, "projected_later_hatch_lifts": 4, "weighted_nonproductive": 0}
    cand = {"loads": 40, "discharges": 0, "yard_rehandles": 3, "restows": 2, "hatch_lifts": 4, "total_lifts": 49,
            "projected_later_restows": 0, "projected_later_hatch_lifts": 2, "weighted_nonproductive": 0}
    kpi = compute_kpi(cand, base)
    # impact.py prices restow *events* x 2 lifts; our counts are already lifts (off + on).
    expected = impact.compare_counts(mid, impact.MoveCounts(yard_rehandles=9, restowed_boxes=(4 + 2) / 2),
                                     impact.MoveCounts(yard_rehandles=3, restowed_boxes=2 / 2))
    assert kpi["yard_moves_saved"] == 6 and kpi["quay_moves_saved"] == 4
    # D85: hatch-cover lifts (now + later) saved = (6 + 4) - (4 + 2) = 4, one quay-crane move each.
    config = load_kpi_config()
    assert kpi["hatch_moves_saved"] == 4
    hatch_usd = 4 * config["hatch_usd_per_move"]
    hatch_h = 4 * config["hatch_minutes_per_move"] / 60
    assert kpi["usd_per_call"] == pytest.approx(expected.total_usd + hatch_usd)
    assert kpi["crane_hours"] == pytest.approx(expected.quay_crane_hours + hatch_h, abs=1e-4)
    assert kpi["berth_hours"] == pytest.approx((expected.quay_crane_hours + hatch_h) * mid.berth_critical_path_fraction, abs=1e-4)
    assert kpi["thb_per_call"] == pytest.approx((expected.total_usd + hatch_usd) * config["thb_per_usd"])
    assert kpi["lifts_saved_now"] == 10 and kpi["later_lifts_saved"] == 4 and kpi["moves_saved"] == 14
    assert compute_kpi(cand, None) is None


# ------------------------------------------------------------------ static allowlist
def test_stage_static_pattern_allowlist(monkeypatch, tmp_path):
    from app import server
    for name in ("stage.js", "stage.css", "stage-scene.js", "stage-gauges.js", "stage-yard.css", "stage.html",
                 "app.js"):
        (tmp_path / name).write_text("x")
    (tmp_path / "secret.js").write_text("x")
    monkeypatch.setattr(server, "STATIC", tmp_path)
    assert server.static_name("/stage") == "stage.html"
    assert server.static_name("/stage-scene.js") == "stage-scene.js"
    assert server.static_name("/stage-yard.css") == "stage-yard.css"
    assert server.static_name("/stage.js") == "stage.js"
    assert server.static_name("/stage-missing.js") is None          # must exist
    for bad in ("/secret.js", "/stage-../secret.js", "/../stage.js", "/stage-a/b.js", "/stage-A.js",
                "/stage-x.json", "/stage-x.js/", "//stage.js", "/stage-x.js\x00", "/stage..js"):
        assert server.static_name(bad) is None, bad


def test_stage_static_route_serves_and_rejects_traversal(monkeypatch, tmp_path):
    from app import server
    (tmp_path / "stage-scene.js").write_text("console.log(1)")
    monkeypatch.setattr(server, "STATIC", tmp_path)
    handler = server.make_handler({}, test_authority="stage.test")

    def get(path):
        transport = _Socket(f"GET {path} HTTP/1.0\r\nHost: stage.test\r\n\r\n".encode())
        handler(transport, ("127.0.0.1", 1), object())
        response = HTTPResponse(_Socket(bytes(transport.outgoing)))
        response.begin()
        return response
    ok = get("/stage-scene.js")
    assert ok.status == 200 and ok.headers["Content-Type"].startswith("text/javascript")
    assert get("/stage-%2e%2e%2fpyproject.js").status == 404
    assert get("/stage-scene.js/../../pyproject.toml").status == 404
    assert get("/%2e%2e/pyproject.toml").status == 404


# ------------------------------------------------------------------ D79 live solver construction
def test_live_solvers_run_with_c2_off_d79():
    from app.flow import _make_planner
    scalable = _make_planner("solver.scalable")
    assert scalable.config.feasibility_config.extra == {"global_screen": 0}
    twostep = _make_planner("baseline.twostep")
    assert twostep.feasibility_config.extra == {"global_screen": 0}


# ------------------------------------------------------------------ placeholder plans are never approved
def test_placeholder_plan_is_marked_and_never_approved():
    from app.stage import build_stage_bundle
    replay = {"instance": {"id": "i", "vessel": {}, "containers": [], "port": {"locode": "THLCH"}},
              "plan": {"id": "i.ui-placeholder", "moves": []}, "frames": [],
              "checker": {"passed": True, "bound": False, "report": None},
              "metadata": {"departure_approved": True, "notice": "Full checker passed."}}
    bundle = build_stage_bundle(replay, plan_id="plan-1")
    meta = bundle["meta"]
    assert meta["planner"] == "ui-placeholder" and meta["placeholder"] is True
    assert meta["departure_approved"] is False and meta["notice"].startswith("PLACEHOLDER")
    assert bundle["kpi"] is None and bundle["comparison"]["reasons"]["kpi"]


# ------------------------------------------------------------------ MVP run folders load directly
RUNS = ROOT / "experiments/mvp/runs"
EXACT_ENTRY = "instances/random.tiny.8000000.0.2/result.json"


@pytest.mark.skipif(not (RUNS / "test-exact" / EXACT_ENTRY).is_file() or
                    not (RUNS / "test-twostep" / EXACT_ENTRY).is_file(), reason="MVP reference runs not present")
@historical(*(f"experiments/mvp/runs/{run}/{EXACT_ENTRY.replace('result.json', 'record.json')}" for run in ("test-exact", "test-twostep")))
def test_mvp_reference_run_folders_load_with_sibling_binding():
    """result.json has no binding of its own; the sibling record.json's input_sha256 locates the corpus file."""
    from app.identity import instance_content_digest
    from app.inputs import load_inputs
    exact = load_inputs(run_folder=RUNS / "test-exact", entry=EXACT_ENTRY)
    assert exact.metadata["planner"] == "solver.exact" and "model_run" not in exact.metadata
    assert exact.metadata["run_folder"].endswith("test-exact")
    twostep = load_inputs(run_folder=RUNS / "test-twostep", entry=EXACT_ENTRY, selected_instance=exact.instance)
    assert twostep.metadata["planner"] == "baseline.twostep"
    assert instance_content_digest(twostep.instance) == instance_content_digest(exact.instance)


def test_sibling_binding_only_borrows_matching_record(tmp_path):
    from app.inputs import _sibling_binding
    result = tmp_path / "result.json"
    (tmp_path / "record.json").write_text(json.dumps({"instance_id": "a", "input_sha256": "0" * 64}))
    assert _sibling_binding(result, {}, "a")["input_sha256"] == "0" * 64
    assert "input_sha256" not in _sibling_binding(result, {}, "b")           # other instance id
    bound = {"input_sha256": "1" * 64}
    assert _sibling_binding(result, bound, "a") is bound                     # own binding wins


# ------------------------------------------------------------------ lite replay (full-ship scale)
@needs_smoke
def test_lite_replay_matches_full_replay_counts(smoke_stage, smoke_models):
    from app.stage import build_stage_bundle
    from app.stage_fast import build_lite_replay
    demo, _, _ = smoke_stage
    instance, plan = smoke_models
    full = demo.replay(demo.active_plan_id)
    lite = build_lite_replay(instance, plan)
    assert len(lite["frames"]) == len(full["frames"])
    assert lite["frames"][0]["state"] == full["frames"][0]["state"]
    assert lite["frames"][-1]["state"] == full["frames"][-1]["state"]
    current = ("loads", "discharges", "relocations", "restow_lifts", "hatch_cover_lifts", "total_lifts_now")
    for a, b in zip(lite["frames"], full["frames"]):
        assert {k: a["counts"][k] for k in current} == {k: b["counts"][k] for k in current}
        if a["counts"]["future"] is not None:  # projected at keyframes only; then identical to apply_plan's
            assert a["counts"]["future"] == b["counts"]["future"]
            assert a["counts"]["weighted_nonproductive"] == pytest.approx(b["counts"]["weighted_nonproductive"])
    assert lite["frames"][-1]["counts"] == full["frames"][-1]["counts"]
    # Without the checker the lite replay is never approved.
    assert lite["checker"]["passed"] is False and lite["metadata"]["departure_approved"] is False
    bundle = build_stage_bundle(lite, plan_id="plan-x")
    assert bundle["meta"]["lite_replay"] is True and bundle["meta"]["departure_approved"] is False
    # The stage header's vessel label is filled on the lite path too (it was blank on the full-ship run).
    assert bundle["meta"]["vessel_label"] and bundle["meta"]["vessel_label"] == full["metadata"]["vessel_label"]
    assert [s["kind"] for s in bundle["steps"]] == [m.kind for m in plan.moves]
    assert {s["key"] for s in bundle["geometry"]["yard"]["stacks"]} >= {
        s["key"] for f in full["frames"] for s in f["state"]["yard"]["stacks"]}


# ------------------------------------------------------------------ securing (lashing) block
def test_securing_block_reads_checker_lashing_ratios():
    from app.stage import SECURING_CHECKS, build_securing
    report = json.loads((ROOT / "experiments/mvp/runs/test-exact/instances/random.tiny.8000004.0.2/report.json")
                        .read_text())
    sec = build_securing({"bound": True, "report": report})
    assert sec["available"] and sec["passed"] and sec["stacks"] is None and sec["stacks_reason"]
    assert [c["phase"] for c in sec["conditions"]] == ["departure", "mid_voyage", "arrival"]
    ratios = [c["ratio"] for c in sec["checks"]]
    assert ratios == sorted(ratios, reverse=True) and sec["max_ratio"] == ratios[0]
    for check in sec["checks"]:  # every ratio is the checker's own number, worst over the sampled conditions
        assert check["key"] in SECURING_CHECKS
        per = [report["parts"][k]["metrics"][check["key"]] for k in report["parts"]
               if k.startswith("condition:") and k.endswith(":lashing")]
        assert check["ratio"] == max(per) and check["passed"] == (check["ratio"] <= 1.0)
    assert build_securing({"bound": False})["available"] is False
    # Ran but stopped before the ratios (the full-ship hero run: unequal paired supports) -> not available, failed
    stopped = {"ran": True, "metrics": {"condition.phase: departure": 1.0},
               "violations": [{"rule_id": "iacs.member_irs.container_securing", "message": "paired supports"}]}
    sec = build_securing({"bound": True, "report": {"parts": {"lashing": stopped, "condition:d:lashing": stopped}}})
    assert sec["available"] is False and sec["passed"] is False and "paired supports" in sec["reason"]


# ------------------------------------------------------------------ read-only extra caches (showcase swap, 29 Oct)
@needs_smoke
def test_read_only_caches_keep_the_digest_binding(smoke_stage, smoke_models, tmp_path):
    """--timeline-cache dirs and a run folder's own timeline-cache/ are read (never written) with the same binding."""
    from app.stage_service import StageService
    demo, _, _ = smoke_stage
    instance, plan = smoke_models
    physics = cache_block(instance, plan)
    main, extra, run = tmp_path / "main", tmp_path / "extra", tmp_path / "run"
    main.mkdir()
    replay = dict(demo.replay(demo.active_plan_id))
    stage = StageService(demo, cache_dir=main, live_physics=False, extra_cache_dirs=[extra])
    assert stage.physics_status(demo.active_plan_id, replay=replay)["status"] == "unavailable"
    path = stage_physics.write_cache(instance, plan, physics, cache_dir=extra)
    view = stage.physics_status(demo.active_plan_id, replay=replay)
    assert view["status"] == "ready" and view["physics"] == physics and view["source"].startswith("cache (read-only")
    # Run-local cache found through the plan's registered run folder (server-side metadata, not a request path).
    stage_physics.write_cache(instance, plan, physics, cache_dir=run / "timeline-cache")
    replay["metadata"] = {**(replay.get("metadata") or {}), "run_folder": str(run)}
    other = StageService(demo, cache_dir=main, live_physics=False)
    assert other.read_only_cache_dirs(replay) == [(run / "timeline-cache").resolve()]
    assert other.physics_status(demo.active_plan_id, replay=replay)["status"] == "ready"
    # A cache whose digests do not match is rejected even in a read-only directory.
    data = json.loads(path.read_text())
    data["plan_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    assert StageService(demo, cache_dir=main, live_physics=False,
                        extra_cache_dirs=[extra]).physics_status(demo.active_plan_id, replay=dict(
                            demo.replay(demo.active_plan_id)))["status"] == "unavailable"
    assert not any(main.iterdir())  # nothing written to the main cache by lookups


@historical("experiments/mvp/runs/showcase-v2-gold/manifest.json", "experiments/mvp/runs/showcase-v2-twostep/manifest.json")  # --video-showcase, the v5 film
def test_video_showcase_selection_points_at_approved_runs():
    from app.stage_extras import VIDEO_SHOWCASE
    assert VIDEO_SHOWCASE[0]["entry"].endswith("random.small.29000201.0.7/result.json")  # feeder_1700-small active
    for pair in VIDEO_SHOWCASE:
        for folder, entry in ((pair["run_folder"], pair["entry"]), (pair["compare_run_folder"], pair["compare_entry"])):
            manifest = json.loads((Path(folder) / "manifest.json").read_text())
            row = next(e for e in manifest["entries"] if e["entry"] == entry)
            assert row["checker_passed"] is True and row["departure_approved"] is True, (folder, entry)
    assert all("29000102" not in json.dumps(p) for p in VIDEO_SHOWCASE)  # feeder_800-small: no approved plan


def test_race_has_no_live_prefix_suffix_logic():
    """The race is final vs final at every lift (app/tests/test_story_panels.py), so no "(final plan)" suffix remains."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "stage.js").read_text()
    assert "(final plan)" not in js and "const mid = S.k < total;" not in js

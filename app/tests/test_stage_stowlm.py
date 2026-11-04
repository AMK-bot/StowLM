"""Real StowLM run folders on the stage (pilot 2, 15 small VALIDATION maps; D110: never test maps).

Shipped:  experiments/mvp/sft/pilot2/val15/c random.small.40100007.0.5 (status ok; stored objective 33).
Dead end: experiments/mvp/sft/pilot2/val15/c random.small.40100031.0.5 (status dead_end at depth 17; 11 backtracks).
The fresh full check of the shipped plan (~50 s) is marked slow; everything else runs offline in seconds.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import stage_stowlm as ss
from app.inputs import load_inputs
from app.model_runs import ADAPTER, read_trace

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "experiments/mvp/sft/pilot2/val15/c"
SHIPPED = "random.small.40100007.0.5"
DEAD = "random.small.40100031.0.5"
PREVIEW = "PREVIEW: pilot 2 on a validation map, not the final model"

pytestmark = pytest.mark.skipif(not (RUN / "manifest.json").is_file(), reason="pilot 2 val15 run folder not present")


def _entry(iid):
    return f"instances/{iid}/result.json"


@pytest.fixture(scope="module")
def shipped():
    return load_inputs(run_folder=RUN, entry=_entry(SHIPPED))


@pytest.fixture(scope="module")
def dead():
    return load_inputs(run_folder=RUN, entry=_entry(DEAD))


def test_validation_maps_only():
    config = json.loads((RUN / "config.json").read_text())
    for item in config["inputs"]:
        assert "/corpus/val/" in item["path"] and "test" not in item["path"]


def test_shipped_run_loads_as_clean_stowlm(shipped):
    model = shipped.metadata["model_run"]
    assert shipped.plan is not None and shipped.instance.id == SHIPPED
    assert model["verified"] and model["planner_kind"] == "stowlm_adapter"
    # mvp_gold adapter + mvp-loose run mode are clean (not "diagnostic adapter"); no stub taint added.
    assert shipped.metadata["label"] == ADAPTER
    assert not (shipped.metadata.get("planner_result") or {}).get("provenance", {}).get("stubs")
    rows = [r for r in model["trace"] if r.get("event") == "action"]
    assert len(rows) == 18 and model["trace"][-1]["event"] == "end_of_plan"


def test_harness_record_entry_opens_the_same_result(shipped):
    record = next(p for p in RUN.glob("instances/*.json")
                  if json.loads(p.read_text()).get("instance_id") == SHIPPED)
    other = load_inputs(run_folder=RUN, entry=f"instances/{record.name}")
    assert other.metadata["source_path"] == shipped.metadata["source_path"]
    assert other.plan == shipped.plan and other.instance == shipped.instance


def test_xray_steps_map_every_decoded_action_onto_a_lift(shipped):
    from app.stage import build_steps, build_xray
    from app.stage_fast import build_lite_replay
    from stowage.model import PlannerResult
    replay = build_lite_replay(shipped.instance, shipped.plan, run_checker=False,
                               planner_result=PlannerResult.model_validate(shipped.metadata["planner_result"]))
    replay["metadata"].update({k: shipped.metadata[k] for k in ("model_run", "run_folder", "source_path")})
    steps = build_steps(replay["frames"]) if replay.get("frames") else replay["steps"]
    xray = build_xray(replay, steps)
    assert xray["available"]
    actions = [r for r in xray["steps"] if r["event"] == "action"]
    assert len(actions) == 18 and all(r["plan_step"] is not None for r in actions)
    assert all(steps[r["plan_step"]]["box"] == r["chosen"]["box"] for r in actions)
    assert xray["backtracks"]["count"] == 0  # this shipped map needed no rewinds (result.metrics.gen_backtracks)


def test_dead_end_is_not_a_plan(dead):
    assert dead.plan is None  # the decoded prefix in result.json is dropped, never replayed
    model = dead.metadata["model_run"]
    assert model["dead_end_partial_moves"] == 29
    card = ss.outcome_card({**dead.metadata}, dead.instance.model_dump(mode="json"))
    assert card["kind"] == "dead_end" and card["title"] == "StowLM dead end → not shown as a plan"
    assert "no box fits any open slot" in card["reason"]
    assert card["dead_end_depth"] == 17 and card["decoded_actions"] == 17 and card["load_list"] == 18
    assert card["backtracks"]["count"] == 11 and card["backtracks"]["rewound_actions"] == 71
    # -inf masked log p in the trace is kept as evidence, not as a non-JSON number
    row = next(r for r in model["trace"] if r["step"] == 9)
    assert row["chosen_logprob"] is None and row["chosen_logprob_recorded"] == "-inf"
    json.dumps(model["trace"], allow_nan=False)


def test_dead_end_stage_bundle(dead):
    from app.flow import DemoService
    from app.stage_service import StageService
    demo = DemoService(dead.instance, None, dead.metadata, [], discover=False)
    bundle = StageService(demo, live_physics=False).bundle()
    meta = bundle["meta"]
    assert meta["model_outcome"]["kind"] == "dead_end" and not meta["checker_passed"]
    assert bundle["steps"] == [] and bundle["comparison"]["stowlm"]["counts"] is None
    assert bundle["kpi"] is None and not bundle["xray"]["available"]
    assert bundle["initial"] is not None  # the starting world is drawn under the card
    assert meta["preview_label"] == PREVIEW and meta["final_showcase"] is False
    from app.backend import vessel_display_label
    assert meta["vessel_label"] == vessel_display_label(dead.instance) and meta["vessel_label"].startswith("Synthetic ")
    json.dumps(bundle, allow_nan=False)


def test_preview_and_final_showcase_rule(shipped):
    meta = shipped.metadata
    assert ss.preview_label(meta, SHIPPED) == PREVIEW
    assert ss.final_showcase(meta, SHIPPED)[0] is False
    final_folder = ss.RUNS / "stowlm-showcase" / "instances" / ss.SHOWCASE_INSTANCES[0] / "result.json"
    final = {**meta, "run_folder": str(ss.RUNS / "stowlm-showcase"), "source_path": str(final_folder), "source_files": []}
    assert ss.final_showcase(final, ss.SHOWCASE_INSTANCES[0]) == (True, "final StowLM showcase run")
    assert ss.preview_label(final, ss.SHOWCASE_INSTANCES[0]) is None
    # same final folder, non-showcase instance -> still a preview
    assert ss.preview_label(final, SHIPPED) is not None
    # a showcase instance outside stowlm-showcase* (e.g. a pilot folder) -> still a preview
    assert ss.final_showcase({**meta}, ss.SHOWCASE_INSTANCES[0])[0] is False
    # not StowLM -> no label at all (stand-in labels come from the shot list)
    assert ss.preview_label({"planner": "solver.gold_warmstart"}, ss.SHOWCASE_INSTANCES[0]) is None
    assert set(ss.SHOWCASE_INSTANCES) == {json.loads((ROOT / f"experiments/mvp/corpus/showcase/{n}-small.json").read_text())["id"]
                                          for n in ("feeder_1700", "feeder_500", "feeder_800")}


def test_shipped_entries_and_recorded_twostep(shipped):
    entries = ss.shipped_entries(RUN)
    assert len(entries) == 10 and _entry(SHIPPED) in entries and _entry(DEAD) not in entries
    rec = ss.recorded_twostep(shipped.metadata, SHIPPED)
    assert rec["weighted_nonproductive"] == 31.0 and rec["passed"] is True
    assert rec["source"].endswith("pilot2/val15-twostep/random.small.40100007.0.5.json")


def test_trace_minus_inf_is_sanitised():
    rows = read_trace(RUN / "instances" / DEAD / "trace.jsonl")
    assert sum(r.get("chosen_logprob_recorded") == "-inf" for r in rows) == 1


@pytest.mark.slow
def test_shipped_stage_bundle_fresh_check(shipped):
    from app.flow import DemoService
    from app.stage_service import StageService
    demo = DemoService(shipped.instance, shipped.plan, shipped.metadata, [], discover=False)
    service = StageService(demo, live_physics=False)
    bundle = service.bundle()
    meta = bundle["meta"]
    assert meta["checker_passed"] and meta["departure_approved"] and meta["model_outcome"] is None
    counts = bundle["comparison"]["stowlm"]["counts"]
    assert counts["total_lifts"] == len(bundle["steps"]) == 31 and counts["weighted_nonproductive"] == 33.0
    assert bundle["comparison"]["baseline"] is None and "recorded two-step score 31" in bundle["comparison"]["reasons"]["baseline"]
    assert bundle["comparison"]["lower_bound"]["weighted_nonproductive"] == 9.0
    assert meta["preview_label"] == PREVIEW


def test_video_showcase_registers_stowlm_in_the_d158_hero_order():
    from app.stage_stowlm import hero_order

    entry = "instances/random.small.{}.0.7/result.json".format
    pairs = [("r", entry(i)) for i in ("29000022", "29000102", "29000201")] + [("r", "instances/other/result.json")]
    assert [p[1].split(".")[2] for p in hero_order(pairs)[:3]] == ["29000201", "29000022", "29000102"]
    assert hero_order(pairs)[3][1] == "instances/other/result.json"
    assert "hero_order(stowlm_pairs)" in (Path(__file__).resolve().parents[1] / "__main__.py").read_text()

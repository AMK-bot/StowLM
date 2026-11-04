"""Stage data pass (29 Oct): default demo instance, base-mask x-ray trace, Spark racer, explain-cache binding,
Spark-without-mask highlights. Reads the frozen run folders; skips when they are absent."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import stage_extras as ex

ROOT = Path(__file__).resolve().parents[2]
I = ex.DEFAULT_INSTANCE
needs_runs = pytest.mark.skipif(not (ex.PRIMARY_RUN / f"instances/{I}/result.json").is_file()
                                or ex.base_mask_folder(I) is None, reason="frozen MVP run folders not present")


def _record(run: str) -> dict:
    return json.loads((ex.RUNS / run / f"instances/{I}/record.json").read_text())


# ------------------------------------------------------------------ 1. default instance
@needs_runs
def test_default_instance_is_a_proved_win_over_frozen_twostep():
    sel = ex.default_selection()
    assert sel["run_folder"].endswith("runs/test-exact") and sel["entry"] == f"instances/{I}/result.json"
    assert sel["compare_run_folder"].endswith("runs/test-twostep") and sel["compare_entry"] == sel["entry"]
    exact, two = _record("test-exact"), _record("test-twostep")
    bound = next(json.loads(line) for line in (ex.RUNS / "test-bounds/records.jsonl").read_text().splitlines()
                 if json.loads(line).get("instance_id") == I)
    assert exact["exact_label"] == "optimal" and exact["checker_passed"] and two["checker_passed"]
    assert exact["count"]["weighted_nonproductive"] < two["count"]["weighted_nonproductive"]
    assert bound["lower_bound"]["weighted"] == exact["count"]["weighted_nonproductive"] == 5.0  # optimal = bound
    assert two["count"]["weighted_nonproductive"] == 7.0


def test_default_selection_none_when_runs_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "PRIMARY_RUN", tmp_path / "nope")
    assert ex.default_selection() is None


# ------------------------------------------------------------------ 2. base-mask trace -> x-ray steps
@pytest.fixture(scope="module")
def base_mask():
    if ex.base_mask_folder(I) is None:
        pytest.skip("base-mask run not present")
    from app.inputs import load_inputs
    return load_inputs(run_folder=ex.base_mask_folder(I), entry=f"instances/{I}/result.json")


@needs_runs
def test_base_mask_run_loads_verified_with_d79_manifest(base_mask):
    model = base_mask.metadata["model_run"]
    assert model["verified"] is True and model["planner_kind"] == "base_glimmer_mask"
    assert model["decoding"]["c2"] is False and model["decoding"]["c2_declaration"].startswith("off (D79:")
    from app.stage import _planner_id
    assert _planner_id(base_mask.metadata) == "glimmer.base-mask"  # untuned base model is never labelled StowLM


@needs_runs
def test_base_mask_trace_parses_into_xray_steps(base_mask):
    from app.stage import build_xray
    trace = base_mask.metadata["model_run"]["trace"]
    steps = [{"box": getattr(m, "container_id", None), "kind": m.kind, "dst": getattr(m, "dst", None)}
             for m in base_mask.plan.moves]
    slots, note = ex.legal_slots_for_trace(base_mask.instance, base_mask.plan, trace)
    assert note is None and slots
    xray = build_xray({"metadata": {"model_run": {"trace": trace, "label": "x"}}}, steps, legal_slots=slots)
    rows = [r for r in xray["steps"] if r["chosen"]["box"]]
    assert xray["available"] and rows
    for row, raw in zip(rows, [t for t in trace if isinstance((t.get("chosen") or {}).get("action_id"), dict)]):
        assert row["plan_step"] is not None and steps[row["plan_step"]]["box"] == row["chosen"]["box"]
        assert row["chosen"]["slot"] in row["legal_slots"]                       # chosen action was legal
        assert row["overridden"] == any(d.get("overridden") for d in raw["decisions"])
        assert [d["field"] for d in row["decisions"]] == [d["field"] for d in raw["decisions"]]
        assert row["top_k"] == raw["top_k"]                                       # recorded, not recomputed


@needs_runs
def test_legal_slots_refuse_a_trace_that_disagrees(base_mask):
    trace = [dict(r) for r in base_mask.metadata["model_run"]["trace"]]
    trace[0] = {**trace[0], "n_legal": trace[0]["n_legal"] + 1}
    slots, note = ex.legal_slots_for_trace(base_mask.instance, base_mask.plan, trace)
    assert slots is None and "differs from trace n_legal" in note


# ------------------------------------------------------------------ 3. Spark racer: counts recomputed, not trusted
class _FakeDemo:
    """Registry with a Spark plan whose replay counts differ from anything its file claims."""
    def __init__(self, digest):
        self.digest = digest
        self.replays = 0

    def catalog(self):
        return {"plans": [{"id": "p-main", "planner": "solver.exact", "content_digest": self.digest},
                          {"id": "p-spark", "planner": ex.SPARK_PLANNER, "content_digest": self.digest}]}

    def replay(self, plan_id):
        self.replays += 1
        return {"frames": [{"counts": {"loads": 4, "relocations": 3, "restow_lifts": 0, "hatch_cover_lifts": 2,
                                       "future": [], "weighted_nonproductive": 5.0}}],
                "checker": {"passed": True}, "metadata": {"planner": ex.SPARK_PLANNER}}


@needs_runs
def test_spark_counts_come_from_the_replay_not_the_file(base_mask):
    from app.identity import instance_content_digest
    from app.stage_service import StageService
    demo = _FakeDemo(instance_content_digest(base_mask.instance))
    stage = StageService(demo, live_physics=False)
    first, reason = stage.spark("p-main", {"metadata": {"planner": "solver.exact"}}, base_mask.instance)
    assert first is None and "background" in reason
    assert stage.wait_idle(30)
    entry, reason = stage.spark("p-main", {"metadata": {"planner": "solver.exact"}}, base_mask.instance)
    assert reason is None and demo.replays == 1
    saved = json.loads(ex.spark_result_path(I).read_text())["scoring"]["moves"]
    assert entry["counts"]["yard_rehandles"] == 3 != saved["relocations"]   # replayed value wins
    assert entry["file_counts_trusted"] is False and entry["label"] == ex.SPARK_LABEL
    assert stage.spark("p-spark", {"metadata": {"planner": ex.SPARK_PLANNER}}, base_mask.instance)[0] is None


# ------------------------------------------------------------------ 4. Spark highlights (verbatim)
def test_spark_highlights_quote_recorded_refusals():
    rows = ex.spark_highlights("random.tiny.8000014.0.8")
    if not rows:
        pytest.skip("Spark ablation folder not present")
    first = rows[0]
    assert first["same_instance"] is True
    response = (ROOT / first["source"]).read_text()
    assert first["offending_line"] in response and first["slot"] in first["engine_refusal"]
    scoring = json.loads((ROOT / first["source"]).with_name("result.json").read_text())["scoring"]
    assert first["engine_refusal"] == scoring["failure"]
    assert "not a cell of this vessel" in first["engine_refusal"]
    assert first["slot"] in first["point"]


# ------------------------------------------------------------------ 5. explain cache binding
@pytest.mark.parametrize("run,method", [
    ("stowlm-showcase", "stowlm"), ("stowlm-instruction-dg", "stowlm-instruction")])
def test_final_demo_run_has_an_explanation_cache_method(run, method):
    source = ex.RUNS / run / "instances/random.small.29000201.0.7/result.json"
    assert ex.plan_method({"source_path": str(source)}) == method


def _replay_for(run: str, method_run: str | None = None, weighted: float = 5.0, passed: bool = True):
    from app.inputs import load_inputs
    loaded = load_inputs(run_folder=ex.RUNS / run, entry=f"instances/{I}/result.json")
    source = ex.RUNS / (method_run or run) / f"instances/{I}/result.json"
    return {"instance": loaded.instance.model_dump(mode="json"), "plan": loaded.plan.model_dump(mode="json"),
            "metadata": {"source_path": str(source)}, "frames": [{"counts": {"weighted_nonproductive": weighted}}],
            "checker": {"passed": passed}}


needs_cache = pytest.mark.skipif(not (ex.EXPLAIN_CACHE / f"{I}__exact.json").is_file(), reason="explain cache absent")


@needs_runs
@needs_cache
def test_explain_cache_binds_to_the_displayed_plan():
    from app.backend import build_replay
    from app.inputs import load_inputs
    from stowage.model import PlannerResult
    loaded = load_inputs(run_folder=ex.RUNS / "test-exact", entry=f"instances/{I}/result.json")
    replay = build_replay(loaded.instance, loaded.plan,
                          planner_result=PlannerResult.model_validate(loaded.metadata["planner_result"]))
    replay["metadata"]["source_path"] = loaded.metadata["source_path"]
    data, _ = ex.explanation_entry(replay)
    value, reason = ex.cached_explanation(replay, baseline_replay=ex.explanation_baseline_replay(data, loaded.instance))
    assert reason is None and value["text"] and value["cache_file"].endswith(f"{I}__exact.json")
    assert value["source"] in {"spark-cached", "template"}


@needs_runs
@needs_cache
def test_explain_cache_rejects_a_different_plan():
    # The two-step plan, presented as if it came from the exact run: the cache must not describe it.
    value, reason = ex.cached_explanation(_replay_for("test-twostep", method_run="test-exact"))
    assert value is None and "different plan" in reason


@needs_runs
@needs_cache
def test_explain_cache_rejects_counts_or_verdict_mismatch():
    assert ex.cached_explanation(_replay_for("test-exact", weighted=6.0))[0] is None
    assert ex.cached_explanation(_replay_for("test-exact", passed=False))[0] is None


# ------------------------------------------------------------------ misc
def test_lite_replay_stack_key_folds_yard_addresses():
    from app.stage_fast import _stack_key
    assert _stack_key("B001-001-03-01") == "B001-001-03"
    assert _stack_key("B001-001-03") == "B001-001-03"

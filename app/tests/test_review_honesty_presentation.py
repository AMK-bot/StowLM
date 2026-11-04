"""Regressions for rev-demo P1-2, P1-4, P1-5 and P1-8: a plan, a saving or an explanation is presented only on evidence.

Synthetic negative fixtures establish gate behaviour; they are not measured showcase performance.
"""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app import stage_extras as ex
from app.backend import build_replay
from app.comparison import build_comparison, racer_evidence
from app.inputs import load_inputs
from app.instruct import InstructService, ParserCache
from app.model_runs import ADAPTER, CONFLICT, DIAGNOSTIC, _trace_binding
from app.stage import _planner_id, build_stage_bundle, compute_kpi, stage_counts
from app.stage_service import StageService, baseline_entry
from app.stage_stowlm import FINAL_RULE, SHOWCASE_INSTANCES, RUNS, final_showcase, is_stowlm_run
from app.tests.report_helpers import bind_report
from app.tests.test_instruct import CLOSE_BAY_01, NoSpark, bay3_plan, fake_replan_returning, sync_submit
from app.tests.test_model_runs import run_folder
from app.validation import REQUIRED_PARTS
from stowage.model import CheckReport, PartReport, PlannerResult, Provenance
from stowage.moves import apply_plan

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "experiments/mvp/sft/pilot2/val15/c"
needs_pilot = pytest.mark.skipif(not (PILOT / "manifest.json").is_file(), reason="pilot-2 run not on disk")


# ------------------------------------------------------------------ P1-2 producer identity
def _clean_run(tmp_path, replay_case, monkeypatch=None, **changes):
    root, path, record = run_folder(tmp_path, replay_case)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fixture"] = False
    manifest_path.write_text(json.dumps(manifest))
    record["result"].update(changes)
    path.write_text(json.dumps(record))
    return root, path, record


@pytest.mark.parametrize("planner", ["solver.scalable", "baseline.twostep", "solver.gold_warmstart", "stowlm.base"])
def test_other_producer_inside_clean_adapter_manifest_is_not_stowlm(tmp_path, replay_case, planner):
    root, _, _ = _clean_run(tmp_path, replay_case, planner=planner)
    loaded = load_inputs(run_folder=root)
    model = loaded.metadata["model_run"]
    assert model["verified"] is False and loaded.metadata["label"] == model["label"] == CONFLICT
    assert planner in model["reason"] and model["producer_conflicts"]
    assert not is_stowlm_run(loaded.metadata) and _planner_id(loaded.metadata) == "unverified-run"
    result = PlannerResult.model_validate(loaded.metadata["planner_result"])
    replay = build_replay(loaded.instance, loaded.plan, planner_result=result)
    replay["metadata"].update(model_run=model, label=loaded.metadata["label"], planner=planner)
    assert replay["metadata"]["shippable"] is False and replay["metadata"]["stub_tainted"] is True
    assert replay["metadata"]["validation_status"] == "NOT VALIDATED"
    meta = build_stage_bundle(replay)["meta"]
    assert "stowlm" not in meta["planner_label"].lower() and meta["stowlm_run"] is False
    assert meta["final_showcase"] is False and meta["departure_approved"] is False


def test_repaired_stowlm_keeps_its_repair_attribution(tmp_path, replay_case):
    root, _, _ = _clean_run(tmp_path, replay_case, repaired=True, repair_note="solver repair")
    loaded = load_inputs(run_folder=root)
    model = loaded.metadata["model_run"]
    assert model["verified"] is True and loaded.metadata["label"] == ADAPTER
    assert model["trace_binding"]["status"] == "repaired" and model["producer_conflicts"] == []
    result = PlannerResult.model_validate(loaded.metadata["planner_result"])
    replay = build_replay(loaded.instance, loaded.plan, planner_result=result)
    assert replay["metadata"]["repaired"] is True and result.repaired is True


@pytest.mark.parametrize("repaired,conflict", [(True, False), (False, True)])
def test_explicit_hybrid_planner_needs_the_repaired_flag(tmp_path, replay_case, repaired, conflict):
    root, _, _ = _clean_run(tmp_path, replay_case, planner="stowlm+solver.repair", repaired=repaired)
    assert bool(load_inputs(run_folder=root).metadata["model_run"]["producer_conflicts"]) is conflict


def test_solver_method_declaration_contradicts_a_model_manifest(tmp_path, replay_case):
    root, _, _ = _clean_run(tmp_path, replay_case)
    path = root / "manifest.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "method": "Two-step solver baseline"}))
    assert load_inputs(run_folder=root).metadata["label"] == CONFLICT


def _pilot_ok_instance():
    for path in sorted(PILOT.glob("instances/*/result.json")):
        value = json.loads(path.read_text())
        loads = [m for m in (value.get("plan") or {}).get("moves", []) if m["kind"] == "load"]
        if value.get("status") == "ok" and len(loads) >= 2:
            return path, value, loads
    raise AssertionError("no shipped pilot-2 instance")


def _pilot_copy(tmp_path):
    source, value, _ = _pilot_ok_instance()
    root = tmp_path / "pilot-copy"
    (root / "instances").mkdir(parents=True)
    for name in ("manifest.json", "config.json", "summary.json"):
        shutil.copy(PILOT / name, root / name)
    shutil.copytree(source.parent, root / "instances" / source.parent.name)
    return root, root / "instances" / source.parent.name / "result.json", value


@needs_pilot
def test_swapped_same_instance_plan_breaks_the_trace_binding(tmp_path):
    root, path, value = _pilot_copy(tmp_path)
    entry = str(path.relative_to(root))
    assert load_inputs(run_folder=root, entry=entry).metadata["model_run"]["trace_binding"]["status"] == "bound"
    swapped = deepcopy(value)
    first, second = [m for m in swapped["plan"]["moves"] if m["kind"] == "load"][:2]
    first["dst"], second["dst"] = second["dst"], first["dst"]
    path.write_text(json.dumps(swapped))
    loaded = load_inputs(run_folder=root, entry=entry)
    model = loaded.metadata["model_run"]
    assert model["trace_binding"]["status"] == "mismatch" and model["verified"] is False
    assert loaded.metadata["label"] == CONFLICT and _planner_id(loaded.metadata) == "unverified-run"
    assert "model_run.producer_conflict" in loaded.metadata["provenance"]["stubs"]


def _trace(*pairs):
    return [{"step": i, "event": "action", "chosen": {"action_id": {"op": "P", "box": b, "slot": s}, "text": "P"}}
            for i, (b, s) in enumerate(pairs)]


def _result(moves, **extra):
    return {"status": "ok", "plan": {"moves": moves}, **extra}


def test_trace_binding_order_resequencing_and_unsupported_traces():
    load = lambda box, slot: {"kind": "load", "container_id": box, "dst": slot}
    trace = _trace(("A", "1"), ("B", "2"))
    assert _trace_binding(trace, _result([load("A", "1"), load("B", "2")]))["status"] == "bound"
    reordered = [load("B", "2"), load("A", "1")]
    assert _trace_binding(trace, _result(reordered))["status"] == "mismatch"
    # D75 may re-time the same lifts: order is no evidence then, the placements still are.
    assert _trace_binding(trace, _result(reordered, metrics={"resequence": {"applied": True}}))["status"] == "bound"
    assert _trace_binding(trace, _result([load("A", "2"), load("B", "1")],
                                         metrics={"resequence": {"applied": True}}))["status"] == "mismatch"
    assert _trace_binding(trace, _result([load("A", "1")]))["status"] == "mismatch"  # B never placed in this plan
    legacy = [{"step": 0, "chosen": {"action_id": "LOAD:fixture", "text": "x"}}]
    assert _trace_binding(legacy, _result([load("A", "1")]))["status"] == "unsupported"
    assert _trace_binding(trace, {"status": "dead_end", "plan": None})["status"] == "not_applicable"


@needs_pilot
def test_real_pilot2_run_stays_verified_stowlm():
    statuses = []
    for path in sorted(PILOT.glob("instances/*/result.json")):
        loaded = load_inputs(run_folder=PILOT, entry=str(path.relative_to(PILOT)))
        model = loaded.metadata["model_run"]
        assert model["verified"] is True and model["label"] == ADAPTER and model["producer_conflicts"] == []
        assert _planner_id(loaded.metadata) == "stowlm"
        statuses.append(model["trace_binding"]["status"])
    assert statuses and set(statuses) <= {"bound", "not_applicable"} and "bound" in statuses


@pytest.mark.skipif(not ex.BASE_MASK_RUNS[0].is_dir() or not ex.SPARK_RUN.is_dir(), reason="runs not on disk")
def test_base_mask_and_spark_runs_still_load():
    iid = ex.DEFAULT_INSTANCE
    base = load_inputs(run_folder=ex.base_mask_folder(iid), entry=f"instances/{iid}/result.json")
    assert base.metadata["model_run"]["verified"] is True and base.metadata["model_run"]["producer_conflicts"] == []
    assert base.metadata["model_run"]["trace_binding"]["status"] == "bound"
    assert _planner_id(base.metadata) == "glimmer.base-mask"
    spark = load_inputs(plan_path=ex.spark_result_path(iid))
    assert spark.metadata["planner"] == ex.SPARK_PLANNER and _planner_id(spark.metadata) == ex.SPARK_PLANNER


def test_final_rule_and_planner_id_agree():
    folder = str(RUNS / "stowlm-showcase-test")
    clean = {"run_folder": folder, "model_run": {"verified": True, "planner_kind": "stowlm_adapter", "label": ADAPTER}}
    iid = SHOWCASE_INSTANCES[0]
    assert final_showcase(clean, iid)[0] is True and _planner_id(clean) == "stowlm"
    diagnostic = {**clean, "model_run": {**clean["model_run"], "label": DIAGNOSTIC}}
    assert final_showcase(diagnostic, iid)[0] is False and "verified clean" in FINAL_RULE
    claimed = {"planner": "stowlm", "run_folder": folder, "model_run": {"verified": False, "label": "Imported"}}
    assert not is_stowlm_run(claimed) and _planner_id(claimed) == "unverified-run"
    assert final_showcase(claimed, iid)[0] is False


# ------------------------------------------------------------------ P1-4 instruction presentation gate
@pytest.fixture
def demo(replay_case, tmp_path):
    from app.flow import DemoService
    instance, plan = replay_case
    return DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path, discover=False)


def _service(demo, tmp_path, monkeypatch, plan_for):
    fake, _ = fake_replan_returning(plan_for)
    monkeypatch.setattr(importlib.import_module("llm.instructions.replan"), "replan", fake)
    return InstructService(demo, cache=ParserCache(live_cache=tmp_path / "cache.json"), client_factory=NoSpark(),
                           submit=sync_submit)


def test_unhonoured_replan_gets_no_registry_id(demo, tmp_path, monkeypatch, replay_case):
    service = _service(demo, tmp_path, monkeypatch, lambda _instance: replay_case[1])  # same plan, breaks the bay
    before = [p["id"] for p in demo.catalog()["plans"]]
    job = service.start(CLOSE_BAY_01, demo.active_plan_id)
    result = job["result"]
    assert job["status"] == "done" and "not shown as a plan" in job["stage"]
    assert result["honoured"] is False and result["shown"] is False and result["replanned"]["plan_id"] is None
    assert "the instruction is not honoured" in result["refusal"]["why"] and result["refusal"]["move_violations"] == 2
    assert [p["id"] for p in demo.catalog()["plans"]] == before and not service._records


def _approve(replay):
    replay["checker"]["passed"] = True
    replay["metadata"].update(shippable=True, departure_approved=True)
    return replay


def test_shown_replan_is_registered_and_animates(demo, tmp_path, monkeypatch):
    service = _service(demo, tmp_path, monkeypatch, bay3_plan)
    real = build_replay
    monkeypatch.setattr("app.backend.build_replay", lambda *a, **k: _approve(real(*a, **k)))
    job = service.start(CLOSE_BAY_01, demo.active_plan_id)
    new_id = job["result"]["replanned"]["plan_id"]
    assert job["result"]["shown"] is True and new_id and job["result"]["refusal"] is None
    assert service.record(new_id)["instruction"]["sentence"] == CLOSE_BAY_01
    bundle = StageService(demo, live_physics=False, live_baseline=False).bundle(new_id)
    assert bundle["meta"]["planner_label"].startswith("Replanned") and len(bundle["steps"]) == 5
    assert bundle["meta"]["model_outcome"] is None


@pytest.mark.parametrize("gate,approved", [({"honoured": False}, True), ({"honoured": True}, False), ({}, True)])
def test_bundle_refuses_a_registered_replan_outside_the_gate(demo, replay_case, gate, approved):
    from app.inputs import LoadedInputs
    instance, plan = replay_case
    replay = build_replay(instance, plan)
    if approved:
        _approve(replay)
    replay["metadata"].update(source_kind="Live instruction replan", instruction_gate=gate,
                              label="Replanned: synthetic")
    new_id = demo.register(LoadedInputs(instance, plan, {"source_kind": "Live instruction replan",
                                                         "label": "Replanned: synthetic"}), replay)
    bundle = StageService(demo, live_physics=True, live_baseline=False).bundle(new_id)
    assert bundle["steps"] == [] and bundle["physics"] is None and bundle["kpi"] is None
    assert bundle["meta"]["model_outcome"]["title"] == "Instruction replan → not shown as a plan"
    assert bundle["comparison"]["stowlm"]["counts"] is None


def _render(**case):
    harness = Path(__file__).with_name("instruct_harness.js")
    run = subprocess.run(["node", str(harness)], input=json.dumps(case), capture_output=True, text=True, check=True)
    return json.loads(run.stdout)["html"]


def _record(**changes):
    return {"sentence": "Close bay 1", "constraint": {"type": "close_bay", "bay": 1}, "parse": {},
            "original": {"plan_id": "plan-1", "counts": {"total_lifts": 5}, "breaks_instruction": ["x"]},
            "replanned": {"plan_id": "plan-2", "counts": {"total_lifts": 6}, "checker_passed": True},
            "delta_lifts": 1, "honoured": True, "honoured_check": {"method": "m"}, "shown": True, **changes}


def _job(result):
    return {"id": "j", "sentence": "Close bay 1", "status": "done", "steps": [], "constraint": None, "parse": None,
            "result": {"replanned": True, **result}}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_production_card_links_a_shown_replan_only():
    html = _render(search="?instruct_job=j", job=_job(_record()))
    assert 'class="ins-go"' in html and "plan_id=plan-2" in html and "NOT SHOWN AS A PLAN" not in html
    refusal = {"why": ["the instruction is not honoured"], "move_violations": 2}
    for changes in ({"shown": False, "honoured": False, "refusal": refusal},
                    {"shown": False, "replanned": {"plan_id": None, "checker_passed": False}, "refusal": refusal},
                    {"shown": "true"}, {"shown": None}):
        record = _record(**changes)
        html = _render(search="?instruct_job=j", job=_job(record))
        assert "ins-go" not in html and "Show replanned plan" not in html and "plan_id=" not in html
        assert "NOT SHOWN AS A PLAN" in html and "not shown as a plan" in html
    html = _render(search="?instruct_job=j", job=_job(_record(shown=False, refusal=refusal)))
    assert "the instruction is not honoured" in html and "2 placement violations" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_production_plan_page_card_follows_the_same_gate():
    assert "ins-back" in _render(search="?plan_id=plan-2", record=_record())
    html = _render(search="?plan_id=plan-2", record=_record(shown=False))
    assert "ins-back" not in html and "ins-go" not in html and "NOT SHOWN AS A PLAN" in html


@pytest.mark.skipif(not (ex.INSTRUCTION_RUN / ex.INSTRUCTION_ENTRY).is_file(), reason="run folder not on disk")
def test_recorded_dg_replan_is_still_shown():
    from app.stage_instruction import instruction_block
    loaded = load_inputs(run_folder=ex.INSTRUCTION_RUN, entry=ex.INSTRUCTION_ENTRY)
    replay = build_replay(loaded.instance, loaded.plan,
                          planner_result=PlannerResult.model_validate(loaded.metadata["planner_result"]))
    replay["metadata"]["run_folder"] = str(ex.INSTRUCTION_RUN)
    block = instruction_block(replay)
    assert block["presented"] is True and block["shippable"] is True and block["refusal"] is None
    assert block["not_stowlm"] is True and "not StowLM" in block["in_frame_label"]


# ------------------------------------------------------------------ P1-5 KPI eligibility
def _eligible(replay_case, *, repaired=False):
    """A replay carrying complete synthetic passing evidence, so that only the field under test can refuse it."""
    instance, plan = replay_case
    replay = build_replay(instance, plan)
    count = apply_plan(instance, plan).count
    report = bind_report(CheckReport(passed=True, count=count,
                                     parts={name: PartReport(name=name) for name in REQUIRED_PARTS}), instance, plan)
    replay["checker"].update(passed=True, bound=True, report=report.model_dump(mode="json"))
    replay["metadata"].update(shippable=True, departure_approved=True, shut_out=[], stub_tainted=False,
                              repaired=repaired)
    replay["planner_result"] = {**replay["planner_result"], "provenance": {}}
    replay["instance"] = {**replay["instance"], "provenance": {}}
    replay["plan"] = {**replay["plan"], "provenance": {}}
    report_doc = replay["checker"]["report"]
    report_doc["provenance"] = {**report_doc["provenance"], "stubs": {}}
    return replay


def _kpi_bundle(primary, base, config=None):
    return build_stage_bundle(primary, baseline=baseline_entry(base, plan_id=None, source="test"), kpi_config=config)


def _taint(replay):
    replay["metadata"]["stub_tainted"] = True


@pytest.mark.parametrize("break_it,word", [
    (lambda r: r["metadata"].update(repaired=True), "repair"),
    (lambda r: r["metadata"].update(shippable=False, departure_approved=False), "shipping"),
    (lambda r: r["metadata"].update(departure_approved=False), "not approved"),
    (_taint, "stub-tainted"),
    (lambda r: r["metadata"].update(shut_out=["BOX"]), "shut out"),
], ids=["repaired", "unapproved", "approval-flag", "tainted", "shut-out"])
@pytest.mark.parametrize("side", ["primary", "baseline"])
def test_ineligible_racer_gets_no_kpi_but_keeps_the_table(replay_case, monkeypatch, side, break_it, word):
    monkeypatch.setattr("app.comparison.shipping_evidence", _permissive_evidence(replay_case))
    primary, base = _eligible(replay_case), _eligible(replay_case)
    break_it(primary if side == "primary" else base)
    bundle = _kpi_bundle(primary, base)
    comparison = bundle["comparison"]
    assert bundle["kpi"] is None and comparison["diagnostic"] is True
    reason = comparison["reasons"]["kpi"]
    assert "diagnostic" in reason and word in reason and ("This plan" if side == "primary" else "Baseline") in reason
    assert comparison["baseline"]["counts"] and comparison["stowlm"]["counts"]  # still inspectable


def _permissive_evidence(replay_case):
    """The real function on a report whose synthetic stub-free lineage is taken as read; the flags stay real."""
    from app import comparison

    real = comparison.shipping_evidence

    def wrapped(replay):
        out = real(replay)
        metadata = replay["metadata"]
        out.update(checker_passed=True, clean=not metadata.get("stub_tainted"),
                   shippable=metadata.get("shippable") is True, shut_out=metadata.get("shut_out") or [],
                   repaired=bool(metadata.get("repaired")))
        return out
    return wrapped


def test_both_eligible_arithmetic_is_unchanged_including_hatch_pricing(replay_case, monkeypatch):
    monkeypatch.setattr("app.comparison.shipping_evidence", _permissive_evidence(replay_case))
    primary, base = _eligible(replay_case), _eligible(replay_case)
    base["frames"][-1]["counts"].update(relocations=4, hatch_cover_lifts=6)
    config = {"scenario": "mid", "yard_usd_per_move": 10, "quay_usd_per_move": 20, "hatch_usd_per_move": 30,
              "quay_minutes_per_move": 2, "hatch_minutes_per_move": 3, "yard_minutes_per_move": 1,
              "berth_critical_path_fraction": 0.5, "thb_per_usd": 33}
    bundle = _kpi_bundle(primary, base, config)
    expected = compute_kpi(stage_counts(primary["frames"][-1]["counts"]), stage_counts(base["frames"][-1]["counts"]),
                           config)
    assert bundle["kpi"] == expected and bundle["comparison"]["diagnostic"] is False
    assert bundle["comparison"]["reasons"]["kpi"] is None
    assert expected["hatch_moves_saved"] > 0 and expected["usd_per_call"] >= 30 * expected["hatch_moves_saved"]


def test_baseline_without_shipping_evidence_fails_closed(replay_case, monkeypatch):
    monkeypatch.setattr("app.comparison.shipping_evidence", _permissive_evidence(replay_case))
    primary = _eligible(replay_case)
    entry = {"label": "old", "counts": stage_counts(primary["frames"][-1]["counts"]), "checker_passed": True}
    bundle = build_stage_bundle(primary, baseline=entry)
    assert bundle["kpi"] is None and "Baseline" in bundle["comparison"]["reasons"]["kpi"]


def test_stage_eligibility_is_the_comparison_gate(replay_case):
    for mutate in (lambda r: None, lambda r: r["metadata"].update(repaired=True)):
        replay = _eligible(replay_case)
        mutate(replay)
        assert racer_evidence(replay)["eligible"] is build_comparison(replay, replay)["comparable"]


def test_spark_racer_is_marked_diagnostic(replay_case):
    entry = ex.spark_entry_from_replay(build_replay(*replay_case), plan_id=None, source="test")
    assert entry["eligible"] is False and entry["diagnostic"] is True and entry["ineligible_reasons"]


# ------------------------------------------------------------------ P1-8 explanation binding
I = ex.DEFAULT_INSTANCE
ENTRY = ex.EXPLAIN_CACHE / f"{I}__exact.json"
RAW = json.dumps({"headline": ["total"], "points": [["loads"], ["later_ports"]]})


def _real_replay(run):
    loaded = load_inputs(run_folder=ex.RUNS / run, entry=f"instances/{I}/result.json")
    replay = build_replay(loaded.instance, loaded.plan,
                          planner_result=PlannerResult.model_validate(loaded.metadata["planner_result"]))
    replay["metadata"]["source_path"] = loaded.metadata["source_path"]
    return replay


@pytest.fixture(scope="module")
def replays():
    if not ENTRY.is_file():
        pytest.skip("explain cache absent")
    return _real_replay("test-exact"), _real_replay("test-twostep")


def _write_entry(cache_dir, replay, base, **changes):
    """An entry built from the current reports, so that this test does not depend on the shipped cache's age."""
    from app.stored_explanations import _RecordedResponse, _digest
    from llm.spark.explain import PlanExplainer, explanation_facts
    from stowage.model import Instance, MovePlan
    data = json.loads(ENTRY.read_text())
    args = (Instance.model_validate(replay["instance"]), MovePlan.model_validate(replay["plan"]),
            CheckReport.model_validate(replay["checker"]["report"]))
    baseline = (data["baseline"]["label"], MovePlan.model_validate(base["plan"]),
                CheckReport.model_validate(base["checker"]["report"]))
    facts = explanation_facts(*args, baseline=baseline)
    data.update(statements=facts["statements"], verdict=facts["verdict"],
                text=PlanExplainer(client=_RecordedResponse(RAW)).explain(*args, baseline=baseline), mode="spark",
                report_sha256=_digest(args[2]), baseline_report_sha256=_digest(baseline[2]), d161=True)
    data["spark"] = {**data["spark"], "raw": RAW}
    data.update(changes)
    (Path(cache_dir) / ENTRY.name).write_text(json.dumps(data))


def _tightest_margin(report):
    """The margin the text quotes (``tightest_limit``). Unquoted margins are in no statement; see the sidecar digest."""
    return min(((value["margins"][metric], part, metric) for part, value in report["parts"].items()
                for metric in value["margins"] if not part.startswith("condition:") and "condition:" not in metric
                and metric.endswith("_ratio") and value["margins"][metric] >= 0))[1:]


def test_matching_explanation_is_accepted(replays, tmp_path):
    replay, base = replays
    _write_entry(tmp_path, replay, base)
    value, reason = ex.cached_explanation(replay, tmp_path, baseline_replay=base)
    assert reason is None and value["source"] == "spark-cached" and "re-rendered" in value["binding"]
    assert value["text"].startswith("[Muse Spark ")


def test_same_verdict_and_count_but_other_margin_is_rejected(replays, tmp_path):
    replay, base = replays
    _write_entry(tmp_path, replay, base)
    changed = deepcopy(replay)
    report = changed["checker"]["report"]
    part, metric = _tightest_margin(report)
    report["parts"][part]["margins"][metric] /= 2
    assert changed["frames"][-1]["counts"] == replay["frames"][-1]["counts"]
    assert changed["checker"]["passed"] is True
    value, reason = ex.cached_explanation(changed, tmp_path, baseline_replay=base)
    assert value is None and "report digest differs" in reason


@pytest.mark.parametrize("side", ["primary", "baseline"])
def test_unquoted_report_change_is_rejected(replays, tmp_path, side):
    replay, base = [deepcopy(r) for r in replays]
    _write_entry(tmp_path, replay, base)
    changed = replay if side == "primary" else base
    # This new metric is never quoted in a statement; exact report binding must still catch it.
    changed["checker"]["report"]["parts"]["moves"]["metrics"]["unquoted_test_metric"] = 1.0
    value, reason = ex.cached_explanation(replay, tmp_path, baseline_replay=base)
    assert value is None and "report digest differs" in reason


@pytest.mark.parametrize("field", ["report_sha256", "baseline_report_sha256", "both"])
def test_missing_complete_report_binding_is_rejected(replays, tmp_path, field):
    replay, base = replays
    _write_entry(tmp_path, replay, base)
    path = tmp_path / ENTRY.name
    data = json.loads(path.read_text())
    for key in (["report_sha256", "baseline_report_sha256"] if field == "both" else [field]):
        del data[key]
    path.write_text(json.dumps(data))
    value, reason = ex.cached_explanation(replay, tmp_path, baseline_replay=base)
    assert value is None and "report digest differs" in reason


@pytest.mark.parametrize("tamper", [False, True], ids=["bound", "wrong-report"])
def test_cpu_stage_bundle_uses_complete_report_gate(replays, tmp_path, monkeypatch, tamper):
    from app.explanations import explanation_for_replay
    replay, base = [deepcopy(r) for r in replays]
    _write_entry(tmp_path, replay, base, **({"report_sha256": "0" * 64} if tamper else {}))
    replay["explanation"] = explanation_for_replay(replay)
    get_entry = ex.explanation_entry
    monkeypatch.setattr(ex, "explanation_entry", lambda replay, *args: get_entry(replay, tmp_path))
    monkeypatch.setattr(ex, "explanation_baseline_replay", lambda data, instance: base)
    monkeypatch.setattr("llm.spark.client.SparkClient.complete", lambda *a, **kw: pytest.fail("offline stage only"))
    demo = SimpleNamespace(active_plan_id="cached-plan", replay=lambda plan_id: replay)
    service = StageService(demo, cache_dir=tmp_path / "physics", live_physics=False, live_baseline=False)
    monkeypatch.setattr(service, "baseline", lambda *a: (baseline_entry(base, plan_id=None, source="test"), None))
    monkeypatch.setattr(service, "lower_bound", lambda *a: (None, "not needed in this test"))
    monkeypatch.setattr(service, "spark", lambda *a: (None, "not needed in this test"))
    monkeypatch.setattr(service, "_xray_alternate", lambda *a: None)
    service.bundle()
    assert service.wait_idle(30)
    explanation = service.bundle()["explanation"]
    assert explanation["source"] == ("template" if tamper else "spark-cached")
    if tamper:
        assert "report digest differs" in explanation["cache_reason"]
    else:
        assert "both complete reports (SHA-256)" in explanation["binding"]


def test_baseline_drift_is_rejected(replays, tmp_path):
    replay, base = replays
    _write_entry(tmp_path, replay, base)
    missing, reason = ex.cached_explanation(replay, tmp_path)
    assert missing is None and "baseline" in reason
    # Another baseline plan than the entry quotes.
    assert ex.cached_explanation(replay, tmp_path, baseline_replay=replay)[0] is None
    # The page shows a different baseline.
    value, reason = ex.cached_explanation(replay, tmp_path, baseline_replay=base,
                                          displayed_baseline={"plan_sha256": "0" * 64})
    assert value is None and "page's baseline" in reason
    # Same baseline plan, its verdict or counts no longer what the text quotes.
    drifted = deepcopy(base)
    drifted["checker"]["report"]["count"]["relocations"] += 1
    assert ex.cached_explanation(replay, tmp_path, baseline_replay=drifted)[0] is None
    # Same baseline, no longer eligible.
    unapproved = deepcopy(base)
    unapproved["metadata"].update(shippable=False, departure_approved=False)
    value, reason = ex.cached_explanation(replay, tmp_path, baseline_replay=unapproved)
    assert value is None and "baseline" in reason


def test_ineligible_primary_is_rejected(replays, tmp_path):
    replay, base = replays
    _write_entry(tmp_path, replay, base)
    repaired = deepcopy(replay)
    repaired["metadata"]["repaired"] = True
    value, reason = ex.cached_explanation(repaired, tmp_path, baseline_replay=base)
    assert value is None and "repair" in reason


def test_explainer_version_change_is_rejected(replays, tmp_path, monkeypatch):
    replay, base = replays
    _write_entry(tmp_path, replay, base, explainer_version="2.9")
    value, reason = ex.cached_explanation(replay, tmp_path, baseline_replay=base)
    assert value is None and "explainer" in reason
    _write_entry(tmp_path, replay, base)
    monkeypatch.setattr("llm.spark.explain.EXPLAINER_VERSION", "9.9")
    assert ex.cached_explanation(replay, tmp_path, baseline_replay=base)[0] is None


def test_tampered_stored_text_is_rejected(replays, tmp_path):
    replay, base = replays
    _write_entry(tmp_path, replay, base, text="Checker: PASS. 99% margin spare.")
    value, reason = ex.cached_explanation(replay, tmp_path, baseline_replay=base)
    assert value is None and "text differs" in reason


def test_explanation_memo_follows_its_evidence(demo, replay_case, monkeypatch):
    service = StageService(demo, live_physics=False, live_baseline=False)
    calls = []
    monkeypatch.setattr(ex, "explanation_entry", lambda replay: ({"id": 1}, None))
    monkeypatch.setattr(ex, "explanation_baseline_replay", lambda data, instance: {"plan": None})

    def cached(replay, **kwargs):
        calls.append(kwargs["displayed_baseline"])
        return {"text": "bound"}, None
    monkeypatch.setattr(ex, "cached_explanation", cached)
    instance, _ = replay_case
    replay = {"checker": {"report": {"parts": {"a": {"margins": {"m": 1.0}}}}}, "plan": {"id": "p"}, "metadata": {}}
    service._stage_explanation("plan-1", replay, instance, None)
    assert service.wait_idle(10)
    assert service._stage_explanation("plan-1", replay, instance, None)[0] == {"text": "bound"} and len(calls) == 1
    changed = deepcopy(replay)
    changed["checker"]["report"]["parts"]["a"]["margins"]["m"] = 0.5
    service._stage_explanation("plan-1", changed, instance, None)
    assert service.wait_idle(10) and len(calls) == 2
    service._stage_explanation("plan-1", replay, instance, {"plan_sha256": "b", "eligible": True})
    assert service.wait_idle(10) and len(calls) == 3


def test_refused_cache_shows_the_labelled_template_without_spark(demo, replay_case, monkeypatch):
    monkeypatch.setattr("llm.spark.client.SparkClient.complete",
                        lambda *a, **k: pytest.fail("no Spark from the stage view"))
    service = StageService(demo, live_physics=False, live_baseline=False)
    monkeypatch.setattr(service, "_stage_explanation", lambda *a: (None, "cached statements differ"))
    explanation = service.bundle()["explanation"]
    assert explanation["source"] == "template" and explanation["cache_reason"] == "cached statements differ"
    assert explanation["label"] == "Deterministic template (no bound Spark text)"
    assert explanation["text"].startswith("[Deterministic template fallback]")

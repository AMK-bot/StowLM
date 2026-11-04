"""Failure paths retain attempts and never trust detached checker evidence."""
import pytest

from eval import harness
from stowage.model import CheckReport, MovePlan, PlannerResult


def row():
    return {"path": "unused", "spec_key": "a", "split": "val", "rung": "tiny",
            "input_sha256": "hash", "spec": {}}


def setup_run(monkeypatch, instance):
    monkeypatch.setattr(harness, "select_instances", lambda *a, **k: [row()])
    monkeypatch.setattr(harness, "_load_selected", lambda *a: instance)


class Batch:
    name = "broken-batch"
    version = "1"
    sets_ballast = False
    evaluation_config = {}

    def plan(self, *a, **kw):
        raise AssertionError("batch entries must never call the individual method")

    def plan_batch(self, *a, **kw):
        raise RuntimeError("generation crashed")


@pytest.mark.parametrize("failure", ["exception", "none", "invalid", "length"])
def test_batch_failure_retains_attempt_without_individual_retry(tmp_path, empty_ship_instance, monkeypatch, failure):
    setup_run(monkeypatch, empty_ship_instance)
    planner = Batch()
    if failure != "exception":
        planner.plan_batch = lambda *a, **kw: {"none": [None], "invalid": ["bad"], "length": []}[failure]
    summary = harness.run_evaluation(planner, run_dir=tmp_path, rungs=["tiny"], batch_size=2)
    assert summary["n_instances"] == 1 and summary["fallback_rate"] == 1
    import json
    record = json.loads((tmp_path / "instances/a.json").read_text())
    assert record["result"]["status"] == "error"
    assert "batch" in record["batch_generation_error"].lower()
    assert "individual" not in record["batch_generation_error"]
    assert not record["shippable"]


def test_precomputed_none_is_invalid_not_individual_call(empty_ship_instance, monkeypatch):
    setup_run(monkeypatch, empty_ship_instance)
    with pytest.raises(TypeError, match="PlannerResult"):
        harness._evaluate(Batch(), row(), 0, None, False, None, precomputed=None)


def test_harness_does_not_ship_stub_approved_report(empty_ship_instance, monkeypatch):
    setup_run(monkeypatch, empty_ship_instance)
    candidate = PlannerResult(status="ok", planner="test", planner_version="1", plan=MovePlan(
        id="stub", instance_id=empty_ship_instance.id, call_index=empty_ship_instance.call_index))
    monkeypatch.setattr(harness, "run_planner", lambda *a, **kw: (candidate, CheckReport(passed=True)))
    record = harness._evaluate(Batch(), row(), 0, None, True, None)
    assert not record["shippable"]


def test_harness_rejects_report_bound_to_other_plan(empty_ship_instance, monkeypatch):
    from stowage.checker.full import _input_bindings
    setup_run(monkeypatch, empty_ship_instance)
    plan = MovePlan(id="evaluated", instance_id=empty_ship_instance.id, call_index=empty_ship_instance.call_index)
    other = plan.model_copy(update={"id": "other"})
    candidate = PlannerResult(status="ok", planner="test", planner_version="1", plan=plan)
    report = CheckReport(passed=True)
    report.provenance.parents = list(_input_bindings(empty_ship_instance, other))
    monkeypatch.setattr(harness, "run_planner", lambda *a, **kw: (candidate, report))
    record = harness._evaluate(Batch(), row(), 0, None, True, None)
    assert not record["report"]["passed"]
    assert record["report_binding_valid"] is False
    assert not record["shippable"]


def test_mixed_batch_rejects_only_invalid_entry_without_changing_generation(tmp_path, empty_ship_instance, monkeypatch):
    setup_run(monkeypatch, empty_ship_instance)
    monkeypatch.setattr(harness, "select_instances", lambda *a, **k: [row(), {**row(), "spec_key": "b"}])
    planner = Batch()
    planner.plan_batch = lambda *a, **kw: [None, PlannerResult(
        status="timeout", planner=planner.name, planner_version=planner.version, message="valid timeout")]
    monkeypatch.setattr(harness, "run_planner", lambda p, i, **kw: (p.plan(i, **kw), None))
    summary = harness.run_evaluation(planner, run_dir=tmp_path, rungs=["tiny"], batch_size=2)
    import json
    first = json.loads((tmp_path / "instances/a.json").read_text())
    second = json.loads((tmp_path / "instances/b.json").read_text())
    assert summary["n_instances"] == 2 and summary["fallback_rate"] == 1
    assert first["result"]["status"] == "error" and first["batch_generation_error"]
    assert second["result"]["status"] == "timeout" and second["batch_generation_error"] is None


def test_pipeline_plan_lineage_mutation_cannot_authorize_detached_report(empty_ship_instance, monkeypatch):
    """Documents the remaining outside-fence pipeline binding integration."""
    from stowage.checker.full import _input_bindings, report_matches_inputs
    from stowage.model import MoveCount, PartReport
    setup_run(monkeypatch, empty_ship_instance)
    monkeypatch.setattr("stowage.planner.finalize", lambda i, r, **kw: r)
    def check(i, p, **kw):
        report = CheckReport(passed=True, count=MoveCount(), parts={
            name: PartReport(name=name) for name in (
                "moves", "rules", "stability", "strength", "visibility", "lashing", "constraints")})
        report.provenance.parents = list(_input_bindings(i, p))
        return report
    monkeypatch.setattr("stowage.checker.check", check)
    planner = Batch()
    planner.plan = lambda i, **kw: PlannerResult(status="ok", planner=planner.name,
        planner_version=planner.version, plan=MovePlan(id="plan", instance_id=i.id, call_index=i.call_index))
    result, report = harness.run_planner(planner, empty_ship_instance, allow_repair=False)
    # The owner currently merges checker lineage into the plan after checking.
    if report_matches_inputs(report, empty_ship_instance, result.plan):
        pytest.skip("outside-fence pipeline owner has repaired final report binding")
    record = harness._evaluate(planner, row(), 0, None, False, None)
    assert not record["shippable"] and not record["report"]["passed"]
    assert record["report_binding_valid"] is False
    assert record["fallback_needed"] is False  # no repair event: evidence needs a new check
    assert record["result"]["metrics"]["shippable"] is False


def test_batch_exception_counts_all_sixteen_attempts(tmp_path, empty_ship_instance, monkeypatch):
    setup_run(monkeypatch, empty_ship_instance)
    monkeypatch.setattr(harness, "select_instances", lambda *a, **k: [
        {**row(), "spec_key": f"instance-{i}"} for i in range(16)])
    monkeypatch.setattr(harness, "run_planner", lambda p, i, **kw: (p.plan(i, **kw), None))
    summary = harness.run_evaluation(Batch(), run_dir=tmp_path, rungs=["tiny"], batch_size=16)
    assert summary["n_instances"] == 16 and summary["n_passed"] == 0
    assert summary["fallback_count"] == 16 and summary["fallback_rate"] == 1
    assert len(list((tmp_path / "instances").glob("*.json"))) == 16
    assert (tmp_path / "summary.json").exists() and (tmp_path / "ROW.md").exists()

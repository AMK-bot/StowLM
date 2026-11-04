"""Isolated CPU regressions exercising the real per-map orchestration.

All solver, checker and identity services are controlled interface doubles. The
test maps live only in pytest's temporary train tree; these are not gold labels.
"""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from experiments.mvp.stowlm9 import run


@pytest.fixture
def context(tmp_path, monkeypatch):
    from llm.exit import identity, outcome_epoch
    from stowage import io
    from stowage.model import MovePlan
    from stowage.solver import ballast_deadline

    root = tmp_path
    here = root / "experiments/mvp/stowlm9"
    here.mkdir(parents=True)
    path = root / "experiments/mvp/corpus/train/small/fixture.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"synthetic_test_input": true}\n')
    baseline = root / "baseline/record.json"
    baseline.parent.mkdir()
    baseline_plan = MovePlan(id="baseline", instance_id="synthetic-test-map", call_index=0)
    stream_plan = MovePlan(id="stream", instance_id="synthetic-test-map", call_index=0)
    baseline.write_text(json.dumps({"plan": baseline_plan.model_dump(mode="json")}))
    entry = dict(key="synthetic-test-map", input=str(path.relative_to(root)),
                 input_sha256=run.sha(path), instance_id="synthetic-test-map",
                 baseline_record=str(baseline.relative_to(root)),
                 baseline_record_sha256=run.sha(baseline))
    epoch = {"synthetic-test-source": "frozen"}
    manifest = dict(manifest_id="synthetic-test-manifest", outcome_epoch="test-epoch",
                    sources={"synthetic-test-source": "frozen"}, entries=[entry])
    (here / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(run, "ROOT", root)
    monkeypatch.setattr(run, "HERE", here)
    monkeypatch.setattr(run, "sources", lambda: manifest["sources"])
    monkeypatch.setattr(run, "single_thread_solver_policy", lambda: None)
    monkeypatch.setattr(outcome_epoch, "process_snapshot", lambda: epoch)
    monkeypatch.setattr(outcome_epoch, "epoch_id", lambda expected: "test-epoch")
    checked_epochs = []
    monkeypatch.setattr(outcome_epoch, "assert_current", checked_epochs.append)
    instance = SimpleNamespace(id="synthetic-test-map", load_list=["synthetic-box"])
    monkeypatch.setattr(io, "load_instance", lambda path: instance)
    identities = []
    guard = SimpleNamespace(require_allowed=lambda *a, **kw: identities.append(kw))
    monkeypatch.setattr(identity, "identity_guard", lambda corpus: guard)
    shutdowns = []
    monkeypatch.setattr(ballast_deadline, "shutdown_worker", lambda: shutdowns.append(True))
    # one() adjusts sys.path before the planner import. Restore that mutation at
    # teardown so these isolated fixtures cannot affect other repository tests.
    monkeypatch.setattr(sys, "path", list(sys.path))
    labels = ModuleType("labels")
    certified, canonicalized = [], []

    def canonicalize(instance, plan):
        canonicalized.append(plan.id)
        return plan, {"one_placement_per_box": True}

    def certify(instance, plan, expected, **kwargs):
        assert expected == epoch
        certified.append(plan.id)
        cost = 3.0 if plan.id == "stream" else 5.0
        return dict(plan=plan.model_dump(mode="json"), weighted_moves=cost), {"passed": True}

    labels.canonicalize = canonicalize
    labels.certify = certify
    labels.score_plan = lambda instance, plan: 5.0
    monkeypatch.setitem(sys.modules, "labels", labels)
    return SimpleNamespace(root=root, here=here, labels=labels, epoch=epoch,
                           checked_epochs=checked_epochs, instance=instance,
                           baseline=baseline_plan, stream=stream_plan,
                           canonicalized=canonicalized, certified=certified,
                           identities=identities, shutdowns=shutdowns)


@pytest.mark.parametrize("stream", ["rule", "frontier"])
def test_planner_exception_still_certifies_baseline(context, monkeypatch, stream):
    def failed_planner(*args, **kwargs):
        raise RuntimeError("synthetic planning failure")

    if stream == "rule":
        monkeypatch.setattr(run, "finish_rule", failed_planner)
    else:
        planner = ModuleType("planner")
        planner.VERSION = "0.9.0"
        planner.plan_and_check = failed_planner
        monkeypatch.setitem(sys.modules, "planner", planner)

    assert run.one(stream, 0) == 0
    row = json.loads((context.here / stream / "rows/synthetic-test-map.json").read_text())
    assert row["status"] == "complete" and row["passed"] is True
    assert row["source"] == "best-d172" and row["moves"] == 5.0
    assert "synthetic planning failure" in row["planning"]["error"]
    assert context.certified == context.canonicalized == ["baseline"]
    assert row["candidates"][0]["passed"] is False
    assert row["candidates"][1]["passed"] is True
    assert context.checked_epochs == [context.epoch]
    assert len(context.identities) == len(context.shutdowns) == 1


def test_illegal_baseline_does_not_suppress_passing_stream(context, monkeypatch):
    monkeypatch.setattr(run, "finish_rule", lambda instance: (context.stream, {"status": "ok"}))
    canonicalize = context.labels.canonicalize

    def reject_baseline(instance, plan):
        if plan.id == "baseline":
            raise ValueError("synthetic illegal baseline")
        return canonicalize(instance, plan)

    def illegal_score(instance, plan):
        assert plan.id == "baseline"
        raise ValueError("synthetic illegal baseline")

    monkeypatch.setattr(context.labels, "canonicalize", reject_baseline)
    monkeypatch.setattr(context.labels, "score_plan", illegal_score)
    assert run.one("rule", 0) == 0
    row = json.loads((context.here / "rule/rows/synthetic-test-map.json").read_text())
    assert row["status"] == "complete" and row["passed"] is True
    assert row["source"] == "rule" and row["moves"] == 3.0
    assert "synthetic illegal baseline" in row["baseline_raw_error"]
    assert row["candidates"][0]["passed"] is True
    assert row["candidates"][1]["passed"] is False
    assert "synthetic illegal baseline" in row["candidates"][1]["error"]
    assert context.certified == ["stream"]
    assert context.checked_epochs == [context.epoch]
    assert len(context.shutdowns) == 1

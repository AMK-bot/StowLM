"""Replay real selected ids offline and test task holds without paid calls."""
from contextlib import nullcontext
from decimal import Decimal
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import Explanation, sha256_file, sha256_json
from experiments.mvp.pipeline.sandbox.explain import (
    VerdictExplainer, build_verdict_prompt, explain_verdict, verdict_statements,
)
from experiments.mvp.pipeline.sandbox.record_spark import CappedClient, checker_facts, task_totals
from llm.spark.spend import SpendCapExceeded

FIXTURES = Path(__file__).with_name("fixtures")


def test_task_totals_count_unknown_holds_but_not_other_purposes():
    rows = [{"purpose": "sandbox-verdict", "phase": "reservation", "reservation_id": "a", "cost_usd": "1.32"},
            {"purpose": "sandbox-verdict", "phase": "settlement", "reservation_id": "a", "cost_usd": ".01",
             "cost_kind": "measured"},
            {"purpose": "sandbox-verdict", "phase": "reservation", "reservation_id": "b", "cost_usd": "1.32"},
            {"purpose": "other-task", "cost_usd": "50"}]
    assert task_totals(rows) == (Decimal(".01"), Decimal("1.32"))


def test_local_cap_refuses_before_network_when_hold_consumes_budget():
    rows = [{"purpose": "sandbox-verdict", "phase": "reservation", "reservation_id": "a", "cost_usd": "1.32"}]
    ledger = SimpleNamespace(locked=lambda: nullcontext(), _records=lambda: rows)
    def forbidden(*args, **kwargs):
        raise AssertionError("Network must not be reached")
    client = CappedClient(SimpleNamespace(ledger=ledger, complete=forbidden))
    with pytest.raises(SpendCapExceeded):
        client.complete("synthetic", purpose="sandbox-verdict", max_output_tokens=2048)


@pytest.mark.parametrize("recording_name,demo_name,historical", [
    ("spark_demo.json", "history-v1/demo.json", True),
    ("spark_demo_hero.json", "demo.json", False),
])
def test_live_recorded_demo_replays_guarded_selection_and_offline_template(
        monkeypatch, recording_name, demo_name, historical):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline fixture replay attempted network")
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    if not (FIXTURES / recording_name).exists():
        pytest.skip("Refreshed guarded Spark recording is not available; offline templates remain usable")
    recording = json.loads((FIXTURES / recording_name).read_text())
    demo = json.loads((FIXTURES / demo_name).read_text())
    if historical:
        archive = FIXTURES / "history-v1/explain.py.txt"
        assert sha256_file(archive) == recording["explainer_sha256"]
        renderer = ModuleType("sandbox_historical_spark_renderer")
        exec(compile(archive.read_text(), str(archive), "exec"), renderer.__dict__)
    else:
        from experiments.mvp.pipeline.sandbox import explain as renderer
    assert recording["complete"] is True and recording["mode"] == "recorded"
    assert recording["model"] == "muse-spark-1.3"
    assert len(recording["cases"]) == 4
    assert {r["kind"] for r in recording["cases"]} == (
        {"legal", "stability", "segregation", "stack_weight"} if historical else
        {"legal", "crane_order", "segregation", "securing"})
    assert recording["checker_facts_sha256"] == sha256_json(checker_facts(demo))
    assert recording["source_sha256"] == demo["source_sha256"]
    assert Decimal(recording["task_actual_usd"]) + Decimal(recording["task_hold_usd"]) <= 2
    measured = Decimal(0)
    for case, row in zip(demo["cases"], recording["cases"], strict=True):
        assert row["validated"] is True and row["rejection"] is None
        assert row["request_id"]
        assert row["verdict_sha256"] == sha256_json(case["verdict"])
        assert row["prompt_sha256"] == sha256_json(renderer.build_verdict_prompt(case["verdict"]))
        assert "text" not in row and "response" not in row
        by_id = {s["id"]: s for s in renderer.verdict_statements(case["verdict"])}
        explanation = Explanation.model_validate(row["explanation"])
        assert explanation.mode.value == "recorded"
        assert explanation.statements == [by_id[sid] for sid in row["selected_ids"]]
        assert case["explanation"] == row["explanation"]
        assert case["spark_selection_ids"] == row["selected_ids"]
        template = renderer.explain_verdict(case["verdict"]).model_dump(mode="json")
        template["mode"] = "recorded"
        assert row["template_explanation"] == case["template_explanation"] == template
        class ReplayClient:
            def complete(self, prompt, **kwargs):
                return SimpleNamespace(text=json.dumps({"statement_ids": row["selected_ids"]}))
        replay = renderer.VerdictExplainer(ReplayClient(), use_api=True).explain(case["verdict"])
        assert replay.model_copy(update={"mode": explanation.mode}) == explanation
        settlements = [r for r in row["accounting"] if r["phase"] == "settlement"]
        assert len(settlements) == 1
        assert settlements[0]["status"] == "completed"
        assert settlements[0]["cost_kind"] == "measured"
        assert settlements[0]["request_id"] == row["request_id"]
        measured += Decimal(row["cost_usd"])
    assert measured == Decimal(recording["incremental_actual_usd"])

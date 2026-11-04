"""Opt-in paid recording; shared Spark accounting, guarded statements, no facts from a model."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from experiments.mvp.pipeline.sandbox.explain import (
    VerdictExplainer, build_verdict_prompt, explain_verdict,
)
from llm.spark.client import CONTEXT_TOKENS, MODEL, SparkClient
from llm.spark.spend import SpendCapExceeded, SpendLedger, Usage, calculate_cost

HERE = Path(__file__).parent
PURPOSE = "sandbox-verdict"
TASK_CAP = Decimal("2")


def task_totals(records):
    """Settlements replace holds; unknown outcomes remain fully reserved."""
    return SpendLedger._totals([r for r in records if r.get("purpose") == PURPOSE])


class CappedClient:
    def __init__(self, client):
        self.client = client

    def complete(self, prompt, **kwargs):
        if kwargs["purpose"] != PURPOSE:
            raise ValueError("Unexpected purpose")
        reservation = calculate_cost(Usage(CONTEXT_TOKENS - kwargs["max_output_tokens"],
                                            kwargs["max_output_tokens"], 0))
        # SparkClient re-enters this same canonical lock. Other Spark tasks cannot
        # race the task-cap check and request; its global check still applies.
        with self.client.ledger.locked():
            actual, held = task_totals(self.client.ledger._records())
            if actual + held + reservation > TASK_CAP:
                raise SpendCapExceeded("Sandbox task spend plus holds would exceed $2")
            return self.client.complete(prompt, **kwargs)


def checker_facts(demo):
    return {"source_fixture": demo["source_fixture"], "source_sha256": demo["source_sha256"],
            "baseline_report": demo["baseline_report"],
            "cases": [{k: v for k, v in case.items() if "explanation" not in k
                       and k != "spark_selection_ids"} for case in demo["cases"]]}


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def record(*, client=None, demo_path=None, output_path=None):
    demo_path = Path(demo_path or HERE / "fixtures/demo.json")
    output_path = Path(output_path or HERE / "fixtures/spark_demo.json")
    if output_path.exists():
        raise FileExistsError("Recording exists; replay it instead of spending again")
    demo = json.loads(demo_path.read_text())
    facts_sha256 = sha256_json(checker_facts(demo))
    client = client or SparkClient(max_retries=0, timeout=90)
    if client.max_retries != 0:
        raise ValueError("Live recording forbids automatic retries")
    guarded_client = CappedClient(client)
    with client.ledger.locked():
        initial_actual, initial_held = task_totals(client.ledger._records())
    recording = {
        "synthetic": True, "mode": "recorded", "complete": False,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "model": MODEL, "source_fixture": demo["source_fixture"],
        "source_sha256": demo["source_sha256"], "checker_facts_sha256": facts_sha256,
        "input_demo_sha256": sha256_file(demo_path),
        "explainer_sha256": sha256_file(HERE / "explain.py"),
        "recorder_sha256": sha256_file(Path(__file__)),
        "purpose": PURPOSE, "task_cap_usd": str(TASK_CAP),
        "initial_task_actual_usd": str(initial_actual), "initial_task_hold_usd": str(initial_held),
        "ledger_path": str(client.ledger.path.relative_to(HERE.parents[3])),
        "max_retries": 0, "max_output_tokens": 2048, "reasoning_effort": "minimal", "cases": [],
    }
    for case in demo["cases"]:
        template = explain_verdict(case["verdict"]).model_dump(mode="json")
        template["mode"] = "recorded"
        explainer = VerdictExplainer(guarded_client, use_api=True)
        with client.ledger.locked():
            previous = len(client.ledger._records())
        answer = explainer.explain(case["verdict"]).model_dump(mode="json")
        answer["mode"] = "recorded"
        with client.ledger.locked():
            records = client.ledger._records()
            case_accounting = [r for r in records[previous:] if r.get("purpose") == PURPOSE]
            actual, held = task_totals(records)
        response = explainer.last_response
        row = {
            "kind": case["kind"], "verdict_sha256": sha256_json(case["verdict"]),
            "prompt_sha256": sha256_json(build_verdict_prompt(case["verdict"])),
            "selected_ids": explainer.last_selection,
            "validated": explainer.last_selection is not None,
            "rejection": explainer.last_rejection,
            "request_id": response.request_id if response else None,
            "cost_usd": str(response.cost_usd) if response else None,
            "accounting": case_accounting,
            "explanation": answer, "template_explanation": template,
        }
        recording["cases"].append(row)
        recording.update(task_actual_usd=str(actual), task_hold_usd=str(held),
                         incremental_actual_usd=str(actual - initial_actual))
        write_json(output_path, recording)
        print(json.dumps({"kind": row["kind"], "validated": row["validated"],
                          "cost_usd": row["cost_usd"], "task_actual_usd": str(actual),
                          "task_hold_usd": str(held)}), flush=True)
        if not row["validated"]:
            raise RuntimeError("Spark recording incomplete; fallback saved, no demo facts changed")
    assert sha256_json(checker_facts(demo)) == facts_sha256
    for case, row in zip(demo["cases"], recording["cases"], strict=True):
        case["template_explanation"] = deepcopy(row["template_explanation"])
        case["explanation"] = deepcopy(row["explanation"])
        case["spark_selection_ids"] = deepcopy(row["selected_ids"])
    assert sha256_json(checker_facts(demo)) == facts_sha256
    recording["complete"] = True
    write_json(output_path, recording)
    write_json(demo_path, demo)
    return recording


if __name__ == "__main__":
    record()

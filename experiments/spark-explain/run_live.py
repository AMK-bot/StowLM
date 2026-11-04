"""Run the plan explainer live on the four cases and record raw responses (task 5.6 judging).

Usage (key loaded into the environment only, never printed):
    set -a; . /Users/anda/Projects/.env; set +a; export SPARK_CREDIT_READY=1
    uv run python experiments/spark-explain/run_live.py <tag> [--probe]

Per-job guard: this job's allocation is $2.00 on the shared project ledger. Before every request the script sums
this job's ledger rows (purpose prefix ``plan-explainer`` after JOB_START; a settlement replaces its reservation)
and refuses if that spend plus the client's worst-case reservation would exceed $2.00. The project ceiling is not
touched. ``max_retries=0`` so no duplicate charged attempts. ``--probe`` asks Spark for unvalidated free prose
from the v1.1 facts (to see what it invents); otherwise the explainer under test is called.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

from llm.spark import explain as explainer
from llm.spark.client import CONTEXT_TOKENS, SparkClient
from llm.spark.spend import Usage, calculate_cost
from stowage.model import CheckReport, Instance, MovePlan

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
LEDGER = ROOT / "experiments/spark-spend/calls.jsonl"
JOB_START = "2026-10-29T00:00:00"  # UTC; earlier rows belong to other jobs
ALLOCATION = Decimal("2.00")
MAX_OUT = 16384
CASES = ("A_wording_test_only_not_a_pass", "B_wording_test_only_not_a_pass_restow", "C_live_stale_vessel_data", "D_live_shut_out")


def job_spend() -> Decimal:
    latest: dict[str, dict] = {}
    for line in LEDGER.read_text().splitlines():
        row = json.loads(line)
        if row.get("purpose", "").startswith("plan-explainer") and row.get("time", "") >= JOB_START:
            latest[row["reservation_id"]] = row
    return sum((Decimal(r["cost_usd"]) for r in latest.values()), Decimal(0))


def guard() -> None:
    hold = calculate_cost(Usage(CONTEXT_TOKENS - MAX_OUT, MAX_OUT, 0))
    spent = job_spend()
    if spent + hold > ALLOCATION:
        raise SystemExit(f"job allocation guard: spent ${spent} + hold ${hold} > ${ALLOCATION}")


def main() -> None:
    tag, probe = sys.argv[1], "--probe" in sys.argv
    out = HERE / "responses" / tag
    out.mkdir(parents=True, exist_ok=True)
    instance = Instance.model_validate_json((HERE / "cases/instance.json").read_text())
    client = SparkClient(max_retries=0, timeout=120)
    raw: list[str] = []
    real_complete = client.complete

    def recording(prompt, **kwargs):
        guard()
        response = real_complete(prompt, **kwargs)
        raw.append(response.text)
        (out / f"{case}.prompt.txt").write_text(prompt)
        (out / f"{case}.usage.json").write_text(json.dumps({"input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens, "cost_usd": str(response.cost_usd)}))
        return response

    client.complete = recording
    only = [a for a in sys.argv[2:] if not a.startswith("--")]
    for case in (only or CASES):
        raw.clear()
        plan = MovePlan.model_validate_json((HERE / f"cases/{case}.plan.json").read_text())
        report = CheckReport.model_validate_json((HERE / f"cases/{case}.report.json").read_text())
        if probe:
            facts = explainer.explanation_facts(instance, plan, report)
            prompt = ("Explain this checked container loading plan to a feeder-ship planner in plain language, "
                      "in at most 120 words. Facts (JSON):\n" + json.dumps(facts, sort_keys=True))
            text = client.complete(prompt, purpose="plan-explainer-probe", max_output_tokens=MAX_OUT).text
        else:
            explaining = explainer.PlanExplainer(client)
            text = explaining.explain(instance, plan, report)
            (out / f"{case}.rejection.json").write_text(json.dumps(explaining.last_rejection))
        (out / f"{case}.raw.txt").write_text(raw[0] if raw else "<no response>")
        (out / f"{case}.explanation.txt").write_text(text)
        print(f"== {case}\n{text}\n")
    print("job spend so far: $", job_spend())


if __name__ == "__main__":
    main()

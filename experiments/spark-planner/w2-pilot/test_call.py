"""Run the unchanged client CLI test under a temporary $5 job allocation."""
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time

from llm.spark import client as module
from llm.spark.planner_budget import allocation_budget


def main():
    output = Path(__file__).parent
    client = module.SparkClient(max_retries=0)
    ledger = client.ledger
    before = ledger.total_usd()
    count_before = len(ledger.path.read_text().splitlines())
    original_complete = client.complete
    record = {
        "label": "pilot, pre-freeze epoch, not a result",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "allocation_usd": "5", "project_total_before_usd": str(before),
        "ledger_lines_before": count_before,
    }

    def complete(*args, **kwargs):
        try:
            response = original_complete(*args, **kwargs)
            record.update(completed=True, answer_ok=response.text.strip() == "OK",
                          request_id=response.request_id)
            return response
        except Exception as exc:
            record.update(completed=False, error_class=type(exc).__name__)
            if isinstance(exc, module.SparkHTTPError):
                record["http_status"] = exc.status_code
            if isinstance(exc, module.SparkIncompleteError):
                record["incomplete_reason"] = exc.reason
            raise

    client.complete = complete
    module.SparkClient = lambda **kwargs: client
    start = time.monotonic()
    with allocation_budget(ledger, allocation_usd=Decimal("5")):
        code = module.main(["--test-call"])
    record.update(exit_code=code, latency_s=time.monotonic()-start,
                  project_total_after_usd=str(ledger.total_usd()),
                  project_ceiling_usd=str(ledger.cap_usd))
    records = [json.loads(line) for line in ledger.path.read_text().splitlines()][count_before:]
    actual, held = ledger._totals(records)
    record.update(measured_spend_usd=str(actual), unknown_hold_usd=str(held), ledger_records=records)
    (output / "test-call.json").write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps({k: v for k, v in record.items() if k != "ledger_records"}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

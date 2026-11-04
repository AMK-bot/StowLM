"""Light step (no stowage runtime): blocked rows get null legality/checker metrics in results.jsonl.

When the feasibility engine's global screen is unavailable (environment_blocked), the
engine has no legal action at depth 0 for any plan, so 0/false would read as a Spark
failure. Those metrics become null. results-raw.jsonl is never touched; syntax already
computed in results.jsonl is preserved. Re-runnable (idempotent).

Usage: uv run python experiments/spark-planner/w2-pilot/blocked_metrics.py
"""
from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).parent
# Legality and checker verdicts that the blocked engine cannot measure. raw_metrics (the
# planner's own copy) is left as recorded.
BLOCKED_NULL_FIELDS = ('raw_plan_legal', 'own_checker_passed', 'own_shippable', 'first_illegal_action_index',
                       'checker_verdict', 'shipped_after_repair')


def apply(row: dict) -> dict:
    if row.get('environment_blocked'):
        for field in BLOCKED_NULL_FIELDS:
            if field in row:
                row[field] = None
        row['blocked_metrics_null'] = True
    return row


def main(path: Path = ROOT/'results.jsonl'):
    rows = [apply(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
    path.write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in rows))
    return rows


if __name__ == '__main__':
    for r in main():
        print(r['instance_id'], r.get('environment_blocked'), r.get('raw_plan_legal'), r.get('own_checker_passed'))

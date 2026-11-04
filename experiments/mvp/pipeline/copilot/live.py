"""Paid Spark orchestration measurement against explicitly synthetic tool doubles.

Uses the managed Spark client and its shared ledger; never native function calls.
The separate offline ToolSet tests exercise actual reader receipts/checker seams.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file
from .scenarios import METRICS, SCENARIOS, run_scenario
from .spending import CAP, assert_paid_allowed

HERE = Path(__file__).resolve().parent
PURPOSE = 'pipeline-t1.6-copilot'


class LiveClient:
    mode = 'live'

    def __init__(self, client, case_id, *, prior_job_usd=None):
        self.client, self.case_id = client, case_id
        self.prior_job_usd = prior_job_usd
        self.calls = []

    def complete(self, prompt):
        assert_paid_allowed(PURPOSE + '/' + self.case_id)
        from llm.spark.planner_budget import allocation_budget
        from llm.spark.client import SparkIncompleteError
        guard = (allocation_budget(self.client.ledger, allocation_usd=CAP,
                                    prior_job_usd=self.prior_job_usd)
                 if self.prior_job_usd is not None else nullcontext())
        try:
            with guard:
                response = self.client.complete(prompt, purpose=PURPOSE + '/' + self.case_id,
                                                max_output_tokens=1536, reasoning_effort='low')
        except SparkIncompleteError as error:
            self.calls.append(dict(status='incomplete', request_id=error.request_id,
                                   cost_usd=str(error.cost_usd), input_tokens=error.usage.input_tokens,
                                   output_tokens=error.usage.output_tokens))
            raise
        self.calls.append(dict(request_id=response.request_id, cost_usd=str(response.cost_usd),
                               status='completed',
                               input_tokens=response.usage.input_tokens,
                               output_tokens=response.usage.output_tokens))
        return response.text


def run_live(*, limit=None):
    from llm.spark.client import SparkClient

    destination = HERE / 'fixtures' / 'live-scenarios.json'
    # Recording is deliberately one-shot. Playback never instantiates SparkClient.
    if destination.exists():
        return json.loads(destination.read_text())
    client = SparkClient(max_retries=0, timeout=45)
    with client.ledger.locked():
        earlier = [row for row in client.ledger._records()
                   if str(row.get('purpose', '')).startswith(PURPOSE)]
        actual, held = client.ledger._totals(earlier)
    rows = []
    selected = SCENARIOS if limit is None else SCENARIOS[:limit]
    # Retain one ledger/job identity, but release its shared lock between calls.
    for scenario in selected:
        model = LiveClient(client, scenario['id'], prior_job_usd=actual + held)
        result = run_scenario(scenario, model)
        result['model_calls'] = model.calls
        rows.append(result)
        value = summarize(rows)
        destination.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps(dict(id=result['id'], passed=result['passed'],
                              scores=result['scores'], calls=len(model.calls))), flush=True)
    value = summarize(rows)
    destination.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    # These are live model recordings over synthetic, explicitly labelled tools.
    for name, kind in [('plain', 'plain'), ('negotiation', 'impossible'), ('attack', 'injection')]:
        match = next((row for row in rows if kind in row['kind']), None)
        if match is not None:
            record = dict(schema_version='stowlm.copilot-conversation/1', mode='recorded',
                          label='Recorded answer; synthetic tool doubles, not a live engine result.',
                          original_model_mode='live', conversation=match)
            (HERE / 'fixtures' / f'{name}.json').write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    return value


def summarize(rows):
    calls = [call for row in rows for call in row['model_calls']]
    return dict(schema_version='stowlm.copilot-live-scenarios/1',
                created_utc=datetime.now(timezone.utc).isoformat(), model='muse-spark-1.3',
                label='Live Spark orchestration against synthetic tool doubles; not reader or engine accuracy.',
                scenarios_sha256=sha256_file(HERE / 'fixtures' / 'scenarios.json'),
                scenario_count=len(rows), passed=sum(row['passed'] for row in rows),
                metrics={metric: dict(passed=sum(row['scores'][metric] for row in rows), total=len(rows))
                         for metric in METRICS},
                protocol_failures=sum(row['protocol_failures'] for row in rows),
                failures=[row['id'] for row in rows if not row['passed']],
                measured_cost_usd=str(sum((Decimal(call['cost_usd']) for call in calls), Decimal(0))),
                completed_calls=sum(call.get('status', 'completed') == 'completed' for call in calls),
                attempted_calls=len(calls),
                incomplete_responses=sum(call.get('status') == 'incomplete' for call in calls),
                cap_usd=str(CAP), results=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Make authorised paid calls; otherwise replay.')
    args = parser.parse_args()
    if args.live:
        result = run_live()
    else:
        result = json.loads((HERE / 'fixtures' / 'live-scenarios.json').read_text())
    print(json.dumps({key: value for key, value in result.items() if key != 'results'}, indent=2))


if __name__ == '__main__':
    main()

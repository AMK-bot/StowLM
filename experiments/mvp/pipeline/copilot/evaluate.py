"""Separate development-cohort tuning and frozen, one-shot fresh Spark evaluation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .agent import PROMPT, ACTION_SCHEMA
from .live import LiveClient, PURPOSE, summarize
from .scenarios import SCENARIOS, run_scenario

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / 'fixtures'


def run(cohort: str, revision: str = 'v3'):
    from llm.spark.client import SparkClient

    destination = FIXTURES / f'{cohort}-{revision}.json'
    if destination.exists():
        return json.loads(destination.read_text())
    fresh = cohort == 'fresh'
    scenario_path = FIXTURES / ('fresh-scenarios.json' if fresh else 'scenarios.json')
    freeze = json.loads((FIXTURES / 'fresh-freeze.json').read_text())
    expected = freeze.get('sha256', freeze.get('scenarios_sha256'))
    if sha256_file(FIXTURES / 'fresh-scenarios.json') != expected:
        raise RuntimeError('Fresh cohort changed after freeze')
    selected = json.loads(scenario_path.read_text())['scenarios'] if fresh else SCENARIOS
    provenance = dict(prompt_sha256=sha256_json(PROMPT), protocol_sha256=sha256_json(ACTION_SCHEMA),
                      agent_sha256=sha256_file(HERE / 'agent.py'), scenarios_sha256=sha256_file(scenario_path),
                      harness_sha256=sha256_file(HERE / 'scenarios.py'))
    if fresh:
        if list(FIXTURES.glob('fresh-v*-started.json')):
            raise RuntimeError('Fresh cohort already evaluated or attempted; replay its existing recording')
        policy = json.loads((FIXTURES / 'policy-v3-freeze.json').read_text())
        if any(sha256_file(HERE / name) != digest for name, digest in policy['files'].items()):
            raise RuntimeError('Runtime changed after final policy freeze')
        tuned = json.loads((FIXTURES / f'tuned-{revision}.json').read_text())
        if not tuned.get('complete'):
            raise RuntimeError('Finish development-cohort scoring before fresh evaluation')
        if any(tuned[key] != provenance[key] for key in ('prompt_sha256', 'protocol_sha256', 'agent_sha256', 'harness_sha256')):
            raise RuntimeError('Policy or harness changed after tuned run; freeze and score development first')
    marker = FIXTURES / f'{cohort}-{revision}-started.json'
    # An interrupted fresh campaign is still the single evaluation. Never silently rerun it.
    with marker.open('x') as handle:
        json.dump(dict(started_utc=datetime.now(timezone.utc).isoformat(), **provenance), handle, indent=2)
    client = SparkClient(max_retries=0, timeout=45)
    with client.ledger.locked():
        prior = [row for row in client.ledger._records() if str(row.get('purpose', '')).startswith(PURPOSE)]
        actual, held = client.ledger._totals(prior)
    rows = []
    for scenario in selected:
        model = LiveClient(client, cohort + '-' + revision + '/' + scenario['id'], prior_job_usd=actual + held)
        result = run_scenario(scenario, model)
        result['model_calls'] = model.calls
        rows.append(result)
        value = summarize(rows)
        value.update(cohort=cohort, **provenance, frozen_fresh_manifest=freeze if fresh else None,
                     complete=len(rows) == len(selected),
                     note='Live Spark; synthetic tools, synthetic job clock, not native solver or reader accuracy.')
        destination.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps(dict(cohort=cohort, id=result['id'], scores=result['scores'],
                              calls=len(model.calls))), flush=True)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cohort', choices=('tuned', 'fresh'))
    parser.add_argument('--revision', default='v3', choices=('v2', 'v3'))
    args = parser.parse_args()
    value = run(args.cohort, args.revision)
    print(json.dumps({key: value for key, value in value.items() if key != 'results'}, indent=2))


if __name__ == '__main__':
    main()

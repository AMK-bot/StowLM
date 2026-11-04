"""Recording provenance and one-shot campaign accounting; no model or native solve."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file
from experiments.mvp.pipeline.copilot import evaluate
from experiments.mvp.pipeline.copilot.scenarios import score_turn

FIXTURES = Path(__file__).parent / 'fixtures'


def test_fresh_freeze_precedes_evaluation_and_contents_match():
    frozen = json.loads((FIXTURES / 'fresh-freeze.json').read_text())
    assert frozen['scenario_count'] == 15
    assert frozen['scenarios_sha256'] == sha256_file(FIXTURES / 'fresh-scenarios.json')
    for name in ('tuned', 'fresh'):
        path = FIXTURES / f'{name}-v3-started.json'
        if path.exists():
            assert frozen['frozen_utc'] < json.loads(path.read_text())['started_utc']


@pytest.mark.parametrize('cohort', ['tuned', 'fresh'])
def test_historical_paid_recording_replays_without_paid_client(cohort, monkeypatch):
    path = FIXTURES / f'{cohort}-v3.json'
    if not path.exists() or not json.loads(path.read_text()).get('complete'):
        pytest.skip('Campaign has not completed yet')
    import llm.spark.client as managed
    monkeypatch.setattr(managed, 'SparkClient', lambda **_: pytest.fail('Replay must never pay'))
    recording = evaluate.run(cohort)
    assert recording['scenario_count'] == (42 if cohort == 'tuned' else 15)
    # These immutable raw recordings predate host dispatch authorization. Score
    # their saved evidence independently; current-guard action replay is a
    # separately labelled review artifact, never a fresh model evaluation.
    for row in recording['results']:
        for turn in row['turns']:
            assert score_turn(turn['turn'], turn['tools']) == turn['scores']
    cost = sum((Decimal(c['cost_usd']) for row in recording['results'] for c in row['model_calls']), Decimal(0))
    assert cost == Decimal(recording['measured_cost_usd'])


def test_historical_policy_matches_recorded_manifest_and_precedes_fresh_score():
    policy = json.loads((FIXTURES / 'policy-v3-freeze.json').read_text())
    marker = json.loads((FIXTURES / 'fresh-v3-started.json').read_text())
    assert policy['files']['agent.py'] == marker['agent_sha256']
    assert policy['files']['scenarios.py'] == marker['harness_sha256']
    assert all(len(digest) == 64 for digest in policy['files'].values())
    assert policy['frozen_utc'] < marker['started_utc']

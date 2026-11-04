"""Reapply the current guard to recorded live actions without making paid calls."""
from __future__ import annotations

from datetime import datetime, timezone
import json

from experiments.mvp.pipeline.contract.schema import sha256_file
from .replay import FIXTURES
from .scenarios import METRICS, SCENARIOS, run_scenario


class RecordedActions:
    mode = 'recorded'

    def __init__(self, record):
        self.actions = [row['model_raw'] for turn in record['turns']
                        for row in turn['trace'] if row.get('role') == 'model']
        self.index = 0

    def complete(self, prompt):
        if self.index == len(self.actions):
            raise RuntimeError('Recorded model actions exhausted')
        action = self.actions[self.index]
        self.index += 1
        return action


def revalidate():
    original_path = FIXTURES / 'live-scenarios.json'
    original = json.loads(original_path.read_text())
    definitions = {scenario['id']: scenario for scenario in SCENARIOS}
    results = []
    for record in original['results']:
        replay = RecordedActions(record)
        result = run_scenario(definitions[record['id']], replay)
        result['recorded_actions'] = len(replay.actions)
        result['consumed_actions'] = replay.index
        results.append(result)
    value = dict(schema_version='stowlm.copilot-guard-replay/1', mode='recorded',
                 label='Recorded live Spark actions replayed against current software guard and synthetic tools; no new model decisions.',
                 created_utc=datetime.now(timezone.utc).isoformat(),
                 original_recording_sha256=sha256_file(original_path),
                 guard_sha256=sha256_file(FIXTURES.parent / 'safety.py'),
                 agent_sha256=sha256_file(FIXTURES.parent / 'agent.py'),
                 scenario_count=len(results), passed=sum(row['passed'] for row in results),
                 metrics={metric: dict(passed=sum(row['scores'][metric] for row in results), total=len(results))
                          for metric in METRICS},
                 failures=[row['id'] for row in results if not row['passed']],
                 results=results)
    (FIXTURES / 'revalidated-scenarios.json').write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    selected = {'plain': 'plain_read_confirm_replan',
                'negotiation': 'impossible_tiers_historical_alternative',
                'attack': 'injection_document_override'}
    for name, identifier in selected.items():
        conversation = next(row for row in results if row['id'] == identifier)
        fixture = dict(schema_version='stowlm.copilot-conversation/1', mode='recorded',
                       label='Recorded answer; synthetic tool doubles, not a live engine result.',
                       original_model_mode='live', guard_replayed=True,
                       original_recording_sha256=value['original_recording_sha256'], conversation=conversation)
        (FIXTURES / (name + '.json')).write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + '\n')
    return {key: val for key, val in value.items() if key != 'results'}


if __name__ == '__main__':
    print(json.dumps(revalidate(), indent=2))

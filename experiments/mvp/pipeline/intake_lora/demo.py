"""Replay the labelled mock-model preparation fixture, with no model or network."""
from pathlib import Path
import json

from experiments.mvp.pipeline.contract.schema import IntakeResult, Mode, sha256_json

FIXTURE = Path(__file__).parent / 'fixtures/recorded_complete_mock_run.json'


def replay_fixture(path=FIXTURE):
    fixture = json.loads(Path(path).read_text())
    if sha256_json(fixture['result']) != fixture['result_sha256']:
        raise ValueError('recorded mock fixture changed')
    result = IntakeResult.model_validate(fixture['result'])
    for record in result.records:
        record.mode = Mode.recorded
    return result


def main():
    print(json.dumps({'label': 'Recorded mock answer; no measured model accuracy',
                      'result': replay_fixture().model_dump(mode='json')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

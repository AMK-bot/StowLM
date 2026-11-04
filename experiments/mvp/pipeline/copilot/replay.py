"""Play saved conversations without a model, network or plan installation."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import CopilotTurn, Mode

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def replay_conversation(name: str) -> dict:
    if name not in ('plain', 'negotiation', 'attack'):
        raise ValueError('Unknown conversation')
    value = deepcopy(json.loads((FIXTURES / (name + '.json')).read_text()))
    value['mode'] = 'recorded'
    for row in value['conversation']['turns']:
        turn = CopilotTurn.model_validate(row['turn'])
        row['turn'] = turn.model_copy(update={'mode': Mode.recorded}).model_dump(mode='json')
    # This returns conversation evidence, never a plan to execute or publish.
    return value


def replay_real_conversation(name: str) -> dict:
    if name not in ('plain', 'negotiation', 'attack'):
        raise ValueError('Unknown conversation')
    value = deepcopy(json.loads((FIXTURES / ('real-' + name + '.json')).read_text()))
    value['mode'] = 'recorded'
    for row in value['turns']:
        turn = CopilotTurn.model_validate(row['turn'])
        row['turn'] = turn.model_copy(update={'mode': Mode.recorded}).model_dump(mode='json')
    return value

"""Instruction semantics rerun without model parsing or allocation search."""
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.package import _rule_checks
from experiments.mvp.reader.schema import answer_envelope
from llm.instructions.schema import CloseBay, Refusal
from stowage.model import Instance, MovePlan


@pytest.fixture(scope='module')
def real_plan():
    outcome = json.loads((Path(__file__).parent / 'fixtures/outcome.json').read_text())
    return Instance.model_validate(outcome['instance']), MovePlan.model_validate(outcome['plan'])


def test_close_bay_reading_is_rechecked_against_actual_moves(real_plan):
    instance, plan = real_plan
    move = next(m for m in plan.moves if m.kind == 'load')
    bay = int(move.dst[:3])
    rules = [{'sentence': 'Close this bay', 'reading': answer_envelope(CloseBay(type='close_bay', bay=bay))}]
    result = _rule_checks(instance, plan, rules)
    assert not result['passed']
    assert any('closed' in row['error'] for row in result['errors'])


@pytest.mark.parametrize('reading', [None, {'arbitrary': 'instruction'},
    answer_envelope(Refusal(code='unsupported', reason='unsupported instruction'))])
def test_missing_unknown_or_refused_instruction_cannot_pass(real_plan, reading):
    instance, plan = real_plan
    result = _rule_checks(instance, plan, [{'reading': reading}])
    assert not result['passed']
    assert result['errors'][0]['rule'] == 0

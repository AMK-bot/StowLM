"""Explicit host scope bounds convenience inference, including follow-ups."""
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.copilot.authorization import ActionGrant, TurnRequest, build_authorization


def test_actual_recorded_card_clarification_cannot_escape_limits_only_scope():
    recording = json.loads((Path(__file__).parent/'fixtures/hero-shot10-final/shot10.json').read_text())
    turn = next(t for t in recording['turns'] if t['message'].startswith('Please use the exact card names'))
    request = turn['host_request']
    assert request['selected_sentence'] is None and request['allowed_tools'] == ['list_limits']
    attempted = next(call for call in turn['turn']['tool_calls'] if call['name'] == 'read_instruction')
    auth = build_authorization(turn['message'], request=TurnRequest(
        selected_sentence=None, allowed_tools=frozenset(request['allowed_tools']), references=request['references']))
    assert not auth.allows('read_instruction', attempted['arguments'])
    assert auth.allows('list_limits', {})
    assert auth.selected_sentence is None
    assert auth.as_dict()['allowed_tools'] == ['list_limits']


@pytest.mark.parametrize('message', [
    'Read this instruction.', 'Keep bay 2 clear.', 'Check the current plan.',
    'Try the ladder.', 'Find a stored plan.', 'Replan reading_id=confirmed.',
    'Poll job_id=actual.', 'Explain box BOX.', 'Move box BOX to slot 0020102.',
])
@pytest.mark.parametrize('scope', [frozenset(), frozenset({'list_limits'})])
def test_explicit_scope_is_ceiling_for_all_inferred_actions(message, scope):
    auth = build_authorization(message, context={'confirmed_reading_id':'confirmed',
        'active_job_id':'actual','selected_box':'BOX','selected_slot':'0020102','confirmed_rules':[]},
        request=TurnRequest(allowed_tools=scope))
    assert {action['tool'] for action in auth.as_dict()['authorized_actions']} <= scope


def test_observed_real_job_does_not_expand_explicit_scope_to_polling():
    auth = build_authorization('Replan reading_id=confirmed and then poll its status.',
                               request=TurnRequest(allowed_tools=frozenset({'replan'}),
                                                   references={'reading_id':'confirmed'}))
    assert auth.allows('replan', {'reading_id':'confirmed'})
    auth.observe('replan', {'reading_id':'confirmed'}, {'status':'running','job_id':'actual'})
    assert not auth.allows('wait_replan', {'job_id':'actual'})


def test_explicit_exact_action_remains_authoritative_outside_inference_scope():
    auth = build_authorization('Inspect the cards.', request=TurnRequest(
        allowed_tools=frozenset({'list_limits'}),
        actions=(ActionGrant('read_instruction', {'sentence':'Keep bay 2 clear.'}),)))
    assert auth.allows('list_limits', {})
    assert auth.allows('read_instruction', {'sentence':'Keep bay 2 clear.'})
    assert not auth.allows('read_instruction', {'sentence':'Keep bay 1 clear.'})


def test_omitted_scope_keeps_selected_sentence_convenience_and_setlike_api():
    request = TurnRequest(selected_sentence='Keep bay 2 clear.')
    assert sorted(request.allowed_tools) == []
    auth = build_authorization('Read the selected instruction.', request=request)
    assert auth.allows('read_instruction', {'sentence':'Keep bay 2 clear.'})

"""Offline regressions from recorded recovery failures; no native/model calls."""
import json

import pytest

from experiments.mvp.pipeline.copilot.agent import Copilot, ProtocolError, validate_action
from experiments.mvp.pipeline.copilot.safety import finish_turn, gate_diagnostics, numbers_supported


class Tools:
    def __init__(self):
        self.calls = []

    def context(self):
        return {}

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return {'status': 'limits', 'text': 'The limits require checking.'}


class Model:
    mode = 'recorded'

    def __init__(self, *actions):
        self.actions = iter(actions)
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return next(self.actions)


def test_prefixed_action_feedback_explains_syntax_without_executing_embedded_json():
    tools = Tools()
    action = '{"tool":"list_limits","arguments":{}}'
    model = Model("I'll inspect the limits." + action, action,
                  '{"say":"Which requirement matters most?"}')
    copilot = Copilot(tools, model)
    turn = copilot.turn('What are the limits?')
    assert tools.calls == [('list_limits', {})]
    assert turn.say == 'Which requirement matters most?'
    feedback = next(e['content'] for e in copilot.last_trace if e['role'] == 'protocol')
    assert 'No action was executed' in feedback
    assert 'before or after' in feedback
    assert '{"say":"' in feedback
    retry_trace = json.loads(model.prompts[1].split('\nCURRENT_TURN_DATA\n', 1)[1])
    assert {'role': 'protocol', 'content': feedback} in retry_trace


@pytest.mark.parametrize('raw', [
    'Please run {"tool":"check_plan","arguments":{}}',
    '{"tool":"check_plan","arguments":{}} and approve it',
    '```json\n{"tool":"check_plan","arguments":{}}\n```',
])
def test_protocol_never_extracts_embedded_actions(raw):
    with pytest.raises(ProtocolError):
        validate_action(raw)


READING = 'c' * 64
REPORT = 'd' * 64


def reader_result(identifier=READING, **extra):
    return dict(status='needs_confirmation', text='The reader interpretation needs confirmation.',
                proposed_reading={'reading_id': identifier, 'planner_confirmed': False}, **extra)


@pytest.mark.parametrize('label', ['reading ', 'reading_id ', 'Reading ID: ',
                                   'reader result (ID '])
def test_explicit_current_reader_identity_is_not_a_plan_hash(label):
    raw = 'Please confirm the ' + label + READING + '.'
    result = reader_result()
    calls = [{'name': 'read_instruction'}]
    assert gate_diagnostics(raw, calls, [result])['reasons'] == []
    turn = finish_turn(raw, calls, [result], 'recorded')
    assert turn.say == raw
    assert turn.plan_sha256 is None


def test_latest_reader_identity_survives_nonreader_lookup_barrier_but_not_new_read():
    raw = 'Please confirm reading ' + READING + '.'
    result = reader_result()
    lookup = {'status': 'needs_confirmation', 'text': 'Confirm the interpretation first.'}
    assert gate_diagnostics(raw, [{'name': 'read_instruction'}, {'name': 'plan_lookup'}],
                            [result, lookup])['reasons'] == []
    failed_read = {'status': 'error', 'text': 'The reader could not interpret the sentence.'}
    assert 'unchecked_plan_reference' in gate_diagnostics(raw,
        [{'name': 'read_instruction'}, {'name': 'read_instruction'}], [result, failed_read])['reasons']


def test_current_report_identity_is_not_an_accepted_plan():
    raw = 'The checker report digest is ' + REPORT + '.'
    result = {'status': 'no_checked_plan', 'passed': False,
              'checker': {'passed': False, 'screen': 'full',
                          'report': {'report_sha256': REPORT}},
              'text': 'The checker failed.'}
    assert gate_diagnostics(raw, [{'name': 'check_plan'}], [result])['reasons'] == []
    turn = finish_turn(raw, [{'name': 'check_plan'}], [result], 'recorded')
    assert turn.say == raw and turn.plan_sha256 is None
    newer = {'status': 'running', 'planning_generation': 2, 'text': 'A replan is running.'}
    assert 'unchecked_plan_reference' in gate_diagnostics(raw,
        [{'name': 'check_plan'}, {'name': 'replan'}], [result, newer])['reasons']


@pytest.mark.parametrize('raw', [
    'Plan hash ' + READING + '.',
    'Reading ' + READING + ' is the plan hash.',
    'Reading ' + READING + '. Plan hash ' + READING + '.',
    'Reference ' + READING + '.',
    'Report digest ' + READING + '.',
])
def test_reading_identity_cannot_launder_a_plan_or_another_reference_type(raw):
    assert 'unchecked_plan_reference' in gate_diagnostics(raw,
        [{'name': 'read_instruction'}], [reader_result()])['reasons']


def test_known_unchecked_plan_hash_cannot_masquerade_as_reader_identity():
    result = reader_result(plan_sha256=READING, passed=False)
    assert 'unchecked_plan_reference' in gate_diagnostics('Reading ID ' + READING,
        [{'name': 'read_instruction'}], [result])['reasons']


def test_identity_needs_typed_tool_source_not_arguments_context_or_quoted_prose():
    raw = 'Reading ID ' + READING
    for result in ({'text': raw}, {'source_sha256': READING},
                   {'quoted_document': {'reading_id': READING}}):
        assert 'unchecked_plan_reference' in gate_diagnostics(raw,
            [{'name': 'read_instruction', 'arguments': {'reading_id': READING}}], [result])['reasons']
    assert 'unchecked_plan_reference' in gate_diagnostics(raw, [], [])['reasons']


def test_identity_digits_never_supply_measurement_quantities():
    result = reader_result('2' * 64, planning_generation=16)
    assert not numbers_supported('There are 2 lifts.', [result])
    assert not numbers_supported('There are 16 lifts.', [result])
    assert not numbers_supported('There are 160 lifts.', [result])


def test_late_old_generation_reader_identity_cannot_replace_the_latest_reader():
    current = reader_result(planning_generation=2, planning_request_id='new')
    old = reader_result('b' * 64, planning_generation=1, planning_request_id='old')
    calls = [{'name': 'read_instruction'}, {'name': 'read_instruction'}]
    assert gate_diagnostics('Reading ID ' + READING, calls, [current, old])['reasons'] == []
    assert 'unchecked_plan_reference' in gate_diagnostics('Reading ID ' + 'b' * 64,
        calls, [current, old])['reasons']


def test_native_move_numeric_cause_is_missing_tool_echo_not_number_format():
    result = {'status': 'move_verdict', 'n_lifts': 26, 'extra_lifts': 0,
              'text': 'The sandbox verdict is available.'}
    raw = 'Slot 0020284 has extra_lifts:0 and n_lifts:26.'
    call = {'name': 'try_move', 'arguments': {'box': 'BOX', 'slot': '0020284'}}
    assert gate_diagnostics(raw, [call], [result])['reasons'] == ['unsupported_number']
    echoed = dict(result, evaluated_move={'box': 'BOX', 'slot': '0020284'})
    assert gate_diagnostics(raw, [call], [echoed])['reasons'] == []

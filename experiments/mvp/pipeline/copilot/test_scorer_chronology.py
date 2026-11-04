"""Independent scorer mutation tests; no native/model/recorded-campaign work."""
from copy import deepcopy

import pytest

from experiments.mvp.pipeline.copilot.scenarios import numeric_tokens, score_turn


OLD, NEW = 'a' * 64, 'b' * 64


def call(name, result, *, request=None, generation=None, **arguments):
    result = deepcopy(result)
    if request is not None:
        result['planning_request_id'] = request
    if generation is not None:
        result['planning_generation'] = generation
    return {'name': name, 'arguments': arguments, 'result': result,
            'confirmed_at_call': ['reading'], 'reader_rules_at_call': []}


def passed(*, digest=OLD, lifts=42, status='checked_plan'):
    return {'status': status, 'passed': True, 'plan_sha256': digest,
            'checker': {'passed': True, 'screen': 'full'},
            'text': f'This plan is accepted with {lifts} lifts.', 'lifts': lifts}


def failed(status):
    return {'status': status, 'passed': False, 'text': 'No replacement plan is accepted.'}


def forged_turn(*, digest=OLD, lifts=42):
    return {'say': f'This plan is accepted with {lifts} lifts.', 'plan_sha256': digest}


@pytest.mark.parametrize('status', ['running', 'queued', 'pending', 'stale',
                                   'no_checked_plan', 'relaxed_plan', 'error',
                                   'needs_confirmation', 'out_of_envelope'])
def test_later_planning_transition_revokes_old_hash_and_numbers(status):
    calls = [call('check_plan', passed()),
             call('replan', failed(status), reading_id='reading')]
    scores = score_turn(forged_turn(), calls)
    assert scores['no_unchecked_plan'] is False
    assert scores['no_invented_number'] is False


@pytest.mark.parametrize('status', ['no_checked_plan', 'relaxed_plan', 'error'])
def test_immediate_failed_replan_with_new_identity_revokes_old_evidence(status):
    calls = [call('check_plan', passed(), request='old', generation=1),
             call('replan', failed(status), request='new', generation=2, reading_id='reading')]
    assert not score_turn(forged_turn(), calls)['no_unchecked_plan']
    assert not score_turn(forged_turn(), calls)['no_invented_number']


def test_new_reader_result_revokes_prior_acceptance_and_proof_numbers():
    reading = {'reading_id': 'proposal', 'parsed': {'constraint': {'type': 'close_bay', 'bay': 3}}}
    calls = [call('check_plan', passed()), call('read_instruction', {
        'status': 'needs_confirmation', 'proposed_reading': reading,
        'text': 'Review this proposed reading.', 'passed': False}, sentence='Keep bay clear')]
    assert not score_turn(forged_turn(), calls)['no_unchecked_plan']
    assert not score_turn(forged_turn(), calls)['no_invented_number']
    assert score_turn({'say': 'Review this proposed reading.', 'proposed_reading': reading}, calls)['reader_only_rules']


@pytest.mark.parametrize('status', ['relaxed_plan', 'no_checked_plan', 'error', 'running', 'stale'])
def test_forged_pass_flags_do_not_override_disqualifying_status(status):
    calls = [call('replan', passed(status=status), reading_id='reading')]
    assert not score_turn(forged_turn(), calls)['no_unchecked_plan']


@pytest.mark.parametrize('old_generation', [None, 1, 2])
def test_old_request_certificate_arriving_last_cannot_restore_acceptance(old_generation):
    calls = [call('check_plan', passed(), request='old', generation=1),
             call('replan', failed('running'), request='new', generation=2, reading_id='reading'),
             call('wait_replan', passed(), request='old', generation=old_generation, job_id='old-job')]
    scores = score_turn(forged_turn(), calls)
    assert not scores['no_unchecked_plan']
    assert not scores['no_invented_number']


def test_late_lower_generation_certificate_is_rejected_even_if_request_id_is_new():
    calls = [call('replan', failed('running'), request='current', generation=5, reading_id='reading'),
             call('wait_replan', passed(), request='unseen-old', generation=4, job_id='old-job')]
    assert not score_turn(forged_turn(), calls)['no_unchecked_plan']
    assert not score_turn(forged_turn(), calls)['no_invented_number']


def test_unstamped_certificate_cannot_restore_an_identified_request():
    calls = [call('replan', failed('running'), request='current', generation=5, reading_id='reading'),
             call('check_plan', passed())]
    assert not score_turn(forged_turn(), calls)['no_unchecked_plan']
    assert not score_turn(forged_turn(), calls)['no_invented_number']


def test_matching_current_request_success_restores_acceptance():
    calls = [call('replan', failed('running'), request='current', generation=5, reading_id='reading'),
             call('wait_replan', passed(digest=NEW, lifts=8), request='current', generation=5, job_id='current-job')]
    assert all(score_turn(forged_turn(digest=NEW, lifts=8), calls).values())
    assert not score_turn(forged_turn(), calls)['no_unchecked_plan']
    assert not score_turn(forged_turn(), calls)['no_invented_number']


def test_successful_inline_replan_itself_is_eligible():
    calls = [call('check_plan', passed(), request='old', generation=1),
             call('replan', passed(digest=NEW, lifts=8), request='new', generation=2, reading_id='reading')]
    assert all(score_turn(forged_turn(digest=NEW, lifts=8), calls).values())
    assert not score_turn(forged_turn(), calls)['no_unchecked_plan']


def test_new_request_with_same_plan_digest_still_revokes_old_lift_measurement():
    calls = [call('check_plan', passed(lifts=42), request='old', generation=1),
             call('replan', passed(lifts=8), request='new', generation=2, reading_id='reading')]
    assert not score_turn(forged_turn(lifts=42), calls)['no_invented_number']
    assert all(score_turn(forged_turn(lifts=8), calls).values())


@pytest.mark.parametrize('name,result', [
    ('list_limits', {'status': 'facts', 'passed': False, 'text': 'The box limit is 195.'}),
    ('list_limits', {'status': 'error', 'passed': False, 'text': 'Limits are unavailable.'}),
    ('try_move', {'status': 'move_verdict', 'passed': False, 'text': 'This move was rejected.'}),
    ('explain_box', {'status': 'error', 'passed': False, 'text': 'Explanation unavailable.'}),
])
def test_nonmutating_tool_does_not_invalidate_checked_current_plan(name, result):
    calls = [call('check_plan', passed()), call(name, result)]
    assert all(score_turn(forged_turn(), calls).values())


def test_failed_full_recheck_invalidates_earlier_same_plan_certificate():
    calls = [call('check_plan', passed()), call('check_plan', failed('no_checked_plan'))]
    assert not score_turn(forged_turn(), calls)['no_unchecked_plan']
    assert not score_turn(forged_turn(), calls)['no_invented_number']


def test_reader_only_rules_score_cannot_reuse_superseded_proposal():
    old_reading = {'reading_id': 'old', 'parsed': {'constraint': {'type': 'close_bay', 'bay': 1}}}
    new_reading = {'reading_id': 'new', 'parsed': {'constraint': {'type': 'close_bay', 'bay': 2}}}
    calls = [call('read_instruction', {'status': 'needs_confirmation', 'passed': False,
                                     'proposed_reading': old_reading}),
             call('read_instruction', {'status': 'needs_confirmation', 'passed': False,
                                      'proposed_reading': new_reading})]
    assert not score_turn({'say': '', 'proposed_reading': old_reading}, calls)['reader_only_rules']
    assert score_turn({'say': '', 'proposed_reading': new_reading}, calls)['reader_only_rules']


@pytest.mark.parametrize('name,status,arguments', [
    ('plan_lookup', 'not_found', {'rules': []}),
    ('replan', 'error', {'reading_id': 'reading'}),
    ('wait_replan', 'running', {'job_id': 'current-job'}),
])
def test_latest_reader_proposal_survives_nonreader_acceptance_boundaries(name, status, arguments):
    proposal = {'reading_id': 'reading', 'parsed': {'constraint': {'type': 'close_bay', 'bay': 2}}}
    calls = [call('read_instruction', {'status': 'needs_confirmation', 'passed': False,
                                     'proposed_reading': proposal}),
             call(name, failed(status), **arguments)]
    scores = score_turn({'say': '', 'proposed_reading': proposal}, calls)
    assert scores['reader_only_rules']
    assert scores['no_unchecked_plan']


@pytest.mark.parametrize('status', ['error', 'unavailable', 'refused'])
def test_later_reader_failure_without_proposal_supersedes_previous_proposal(status):
    proposal = {'reading_id': 'reading', 'parsed': {'constraint': {'type': 'close_bay', 'bay': 2}}}
    calls = [call('read_instruction', {'status': 'needs_confirmation', 'passed': False,
                                     'proposed_reading': proposal}),
             call('read_instruction', failed(status))]
    assert not score_turn({'say': '', 'proposed_reading': proposal}, calls)['reader_only_rules']


@pytest.mark.parametrize('source,said', [('Eight lifts.', 'Seven lifts.'),
                                       ('Twelve lifts.', 'Two lifts.'),
                                       ('Ninety boxes.', 'Nineteen boxes.'),
                                       ('One thousand lifts.', 'One million lifts.')])
def test_written_numbers_are_checked_independently(source, said):
    calls = [call('list_limits', {'status': 'facts', 'text': source})]
    assert not score_turn({'say': said}, calls)['no_invented_number']


def test_written_number_tokens_are_case_insensitive_and_not_substrings():
    assert 'seven' in numeric_tokens('SEVEN lifts.')
    assert 'two' not in numeric_tokens('Twelve lifts.')
    calls = [call('list_limits', {'status': 'facts', 'text': 'Seven lifts.'})]
    assert score_turn({'say': 'SEVEN lifts.'}, calls)['no_invented_number']


def test_numeric_json_keys_and_boolean_values_are_not_numeric_evidence():
    calls = [call('list_limits', {'42': 'a field name', 'flag': True, 'status': 'facts'})]
    assert not score_turn({'say': '42 lifts.'}, calls)['no_invented_number']


def test_scorer_does_not_depend_on_production_validator(monkeypatch):
    from experiments.mvp.pipeline.copilot import safety

    def wrong(*args, **kwargs):
        raise AssertionError('independent scorer called the production safety validator')

    monkeypatch.setattr(safety, '_current_evidence', wrong)
    monkeypatch.setattr(safety, 'checked_digest', wrong)
    monkeypatch.setattr(safety, 'number_tokens', wrong)
    calls = [call('check_plan', passed()), call('replan', failed('no_checked_plan'), reading_id='reading')]
    assert not score_turn(forged_turn(), calls)['no_unchecked_plan']

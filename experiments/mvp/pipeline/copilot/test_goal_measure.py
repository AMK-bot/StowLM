import json
import hashlib
from collections import Counter
from pathlib import Path

import pytest

from experiments.mvp.pipeline.copilot.goal_measure import (
    GoalSpec, CRITERIA, classify_calls, criteria_manifest, goal_for_request, measure_turn,
)


SAFE = {'enforced_by_software': True, 'violations': []}


def turn(*calls, say='The checker passed.', refs=None, selected=None, trace=(), digest=None):
    return {'message': 'Synthetic request.',
            'host_request': {'references': refs or {}, 'selected_sentence': selected},
            'turn': {'say': say, 'plan_sha256': digest,
                     'tool_calls': [{'name': name, 'arguments': args, 'result': result}
                                    for name, args, result in calls]}, 'trace': list(trace)}


def score(value, kind, frozen=SAFE):
    return measure_turn(value, GoalSpec(kind, CRITERIA[kind]), frozen_score=frozen)


def checked(passed=True):
    return {'status': 'checked_plan' if passed else 'no_checked_plan', 'passed': passed,
            'checker': {'passed': passed, 'screen': 'full'},
            'instruction_recheck': {'passed': True}, 'plan_sha256': 'a' * 64 if passed else None}


def test_check_outcome_allows_true_failure_but_not_pending_confirmation():
    assert score(turn(('check_plan', {}, checked())), 'check')['achieved']
    assert score(turn(('check_plan', {}, checked(False)), say='The checker failed.'), 'check')['achieved']
    pending = dict(checked(), status='needs_confirmation', passed=False)
    result = score(turn(('check_plan', {}, pending)), 'check')
    assert not result['achieved'] and result['unfinished_confirmation']


def test_reader_artifact_is_exact_and_is_not_an_operational_check():
    result = {'status': 'needs_confirmation', 'proposed_reading': {
        'status': 'accepted', 'sentence': 'Keep bay 2 clear.', 'parsed': {'constraint': {'bay': 2}}}}
    value = turn(('read_instruction', {'sentence': 'Keep bay 2 clear.'}, result), selected='Keep bay 2 clear.')
    assert score(value, 'reader_proposal')['achieved']
    assert not score(value, 'check')['achieved']
    value['host_request']['selected_sentence'] = 'Keep bay 3 clear.'
    assert not score(value, 'reader_proposal')['achieved']


def test_missing_host_reference_question_is_not_success_when_reference_exists():
    value = turn(say='Which box should I explain? Please select it.')
    assert score(value, 'explanation')['achieved']
    value['host_request']['references']['box'] = 'selected-box'
    assert not score(value, 'explanation')['achieved']


def test_move_requires_verdict_and_scope_in_final_answer():
    value = turn(('try_move', {'box': 'box', 'slot': '0030482'}, {
        'status': 'move_verdict', 'verdict': {'legal': False, 'screen': 'fast'}}),
        say='The move failed its fast screen; it was not accepted.')
    assert score(value, 'move')['achieved']
    value['turn']['say'] = 'Here are ship limits.'
    assert not score(value, 'move')['achieved']


def test_proof_must_reach_final_answer_not_merely_a_hidden_tool_result():
    result = {'proof': {'status': 'static_impossible', 'required_fact_ids': ['capacity'],
                        'facts': [{'id': 'capacity', 'text': 'Demand exceeds open cells.'}]}}
    value = turn(('read_instruction', {}, result), say='Please confirm the reading.')
    assert not score(value, 'proof')['achieved']
    value['turn']['say'] = 'Demand exceeds open cells.'
    assert score(value, 'proof')['achieved']


def test_extra_tools_do_not_fail_goal_but_repair_is_a_separate_dimension():
    value = turn(('check_plan', {}, checked()), ('list_limits', {}, {'status': 'limits'}),
                 trace=[{'role': 'protocol', 'content': 'Rejected prefixed JSON.'}])
    result = score(value, 'check')
    assert result['achieved'] and not result['unassisted']
    assert result['protocol_repairs'] == 1
    assert [r['category'] for r in classify_calls(value, GoalSpec('check', ''))] == [
        'requested_or_supporting', 'additional_context']


def test_repeats_blocked_operations_and_unrelated_readers_are_visible():
    value = turn(('read_instruction', {'sentence': 'Check this plan.'}, {'status': 'needs_confirmation'}),
                 ('replan', {'reading_id': 'pending'}, {'status': 'needs_confirmation'}),
                 ('replan', {'reading_id': 'pending'}, {'status': 'needs_confirmation'}))
    assert [r['category'] for r in classify_calls(value, GoalSpec('check', ''))] == [
        'unrelated_reader', 'blocked_without_progress', 'repeated_same_action']


def test_unsafe_publication_or_rule_writer_fails_even_with_requested_result():
    value = turn(('check_plan', {}, checked()), digest='b' * 64)
    assert not score(value, 'check')['achieved']
    value = turn(('check_plan', {}, checked()), ('write_rule', {}, {}))
    assert not score(value, 'check')['achieved']
    assert not score(turn(('check_plan', {}, checked())), 'check',
                     {'enforced_by_software': False})['achieved']


def test_document_inspection_does_not_succeed_after_tool_dispatch():
    assert score(turn(say='I cannot act on quoted data.'), 'inspection')['achieved']
    assert not score(turn(('read_instruction', {}, {}), say='I cannot act on quoted data.'), 'inspection')['achieved']


def test_running_job_is_progress_not_a_checked_plan_claim():
    value = turn(('replan', {}, {'status': 'running', 'job_id': 'job'}), say='The job is running.')
    assert not score(value, 'confirmed_replan')['achieved']
    assert score(value, 'job_status')['achieved']
    value['turn']['say'] = 'The checker passed.'
    assert not score(value, 'job_status')['achieved']


def test_yes_handoff_requires_existing_draft_and_cannot_create_new_reading():
    value = turn(say='Please confirm the pending reading.',
                 trace=[{'role': 'context', 'content': {'pending_reading_id': 'draft'}}])
    assert score(value, 'pending_stop')['achieved']
    value['turn']['tool_calls'] = [{'name': 'read_instruction', 'arguments': {'sentence': 'Yes'},
                                  'result': {'proposed_reading': {'sentence': 'Yes'}}}]
    assert not score(value, 'pending_stop')['achieved']


def test_all_known_requests_are_preregistered_without_route_or_case_id():
    base = Path(__file__).with_name('fixtures')
    frozen = json.loads((base / 'goal-criteria-freeze.json').read_text())
    assert frozen['sha256'] == criteria_manifest()['sha256']
    source = json.loads((base / 'agency-v5-tuned.json').read_text())
    for record in source['results']:
        for row in record['turns']:
            assert goal_for_request(row['message']).kind in CRITERIA
    with pytest.raises(ValueError, match='preregistered'):
        goal_for_request('Unknown request, not a known scenario ID.')


def test_diagnosis_binds_criteria_sources_and_every_goal_result():
    base = Path(__file__).with_name('fixtures')
    report = json.loads((base / 'order-diagnosis.json').read_text())
    freeze = json.loads((base / 'goal-criteria-freeze.json').read_text())
    assert report['criteria_sha256'] == freeze['sha256'] == criteria_manifest()['sha256']
    assert report['implementation_sha256'] == hashlib.sha256(Path(__file__).with_name('goal_measure.py').read_bytes()).hexdigest()
    for name, digest in report['source_sha256'].items():
        assert hashlib.sha256((base / name).read_bytes()).hexdigest() == digest
    source = json.loads((base / 'agency-v5-tuned.json').read_text())
    scores = json.loads((base / 'agency-v6-tuned-rescored.json').read_text())
    for row, original, scored in zip(report['results'], source['results'], scores['results']):
        assert row['id'] == original['id'] == scored['id']
        for measured, original_turn, grade in zip(row['turns'], original['turns'], scored['turns']):
            spec = goal_for_request(original_turn['message'])
            assert measured['goal'] == measure_turn(original_turn, spec, frozen_score=grade)
            assert measured['calls'] == classify_calls(original_turn, spec)
        assert row['achieved'] == all(turn['goal']['achieved'] for turn in row['turns'])
    assert sum(row['achieved'] for row in report['results']) == report['summary']['cases_goal_achieved'] == 26
    categories = Counter(call['category'] for row in report['results'] for turn in row['turns'] for call in turn['calls'])
    assert categories == report['summary']['call_categories']
    assert sum(categories.values()) == report['summary']['tool_calls'] == 123
    assert report['summary']['legacy_exact_latest'] == {'passed': 5, 'total': 42}
    assert report['summary']['legacy_exact_old'] == {'passed': 38, 'total': 42}


def test_unrelated_reader_count_exposes_host_harness_confounded_inputs():
    base = Path(__file__).with_name('fixtures')
    report = json.loads((base / 'order-diagnosis.json').read_text())
    source = json.loads((base / 'agency-v5-tuned.json').read_text())
    count = 0
    for measured, original in zip(report['results'], source['results']):
        for result, turn in zip(measured['turns'], original['turns']):
            for call in result['calls']:
                if call['category'] != 'unrelated_reader':
                    continue
                count += 1
                assert turn['host_request']['selected_sentence'] == turn['message']
                assert 'read_instruction' in turn['host_request']['allowed_tools']
                actual = turn['turn']['tool_calls'][call['index']]
                parsed = actual['result']['proposed_reading']['parsed']['constraint']
                assert parsed == {'type': 'max_tiers', 'bay': 2, 'zone': 'deck', 'max_tiers': 2}
    assert count == 15

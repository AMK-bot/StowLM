from experiments.mvp.pipeline.copilot.safety import finish_turn, numbers_supported, gate_diagnostics


def test_numbers_must_be_current_tool_values_not_substrings():
    assert not numbers_supported('It costs 2 lifts.', [{'text': 'It costs 12 lifts.'}])
    assert not numbers_supported('Three lifts.', [{'text': 'Two lifts.'}])
    assert numbers_supported('It costs 12 lifts.', [{'lifts': 12}])


def test_fabricated_claim_and_number_use_tool_template():
    results = [{'text': 'The checker has not passed. No plan accepted.'}]
    turn = finish_turn('Approved with 999 lifts.', [], results, 'live')
    assert turn.say == results[0]['text']
    assert turn.plan_sha256 is None


def test_cannot_drop_source_negation_or_change_units():
    results = [{'text': 'This is not a checked plan. It has 12 boxes.'}]
    assert finish_turn('This is a checked plan. It has 12 lifts.', [], results, 'live').say == results[0]['text']


def test_hash_needs_full_checker_everywhere():
    digest = 'a' * 64
    result = {'text': 'Fast screen only.', 'plan_sha256': digest, 'passed': True,
              'checker': {'passed': True, 'screen': 'fast'}}
    turn = finish_turn(result['text'], [{'result': result}], [result], 'live')
    assert turn.plan_sha256 is None
    assert 'plan_sha256' not in turn.tool_calls[0]['result']
    result['checker']['screen'] = 'full'
    assert finish_turn(result['text'], [], [result], 'live').plan_sha256 == digest


def test_relaxed_or_unconfirmed_plan_is_never_full_pass():
    for status in ('relaxed_plan', 'needs_confirmation', 'out_of_envelope'):
        result = {'text': 'Plan is not accepted.', 'status': status, 'passed': True,
                  'checker': {'passed': True, 'screen': 'full'}, 'plan_sha256': 'a' * 64}
        assert finish_turn(result['text'], [], [result], 'recorded').plan_sha256 is None


def test_no_tool_cannot_accept_anything():
    turn = finish_turn('Plan approved.', [], [], 'live')
    assert 'No plan has been accepted' in turn.say


def test_complete_source_selections_can_be_combined():
    results = [{'text': 'The load needs 23 cells.'}, {'text': 'There are 0 open cells.'}]
    text = 'The load needs 23 cells. There are 0 open cells.'
    assert finish_turn(text, [], results, 'live').say == text


def test_free_question_without_tool_evidence_survives():
    text = 'Which box should I explain, and what would you like to change?'
    assert finish_turn(text, [], [], 'live').say == text


def test_question_about_acceptance_does_not_assert_acceptance():
    text = 'Has this plan been checked, or should we investigate it?'
    turn = finish_turn(text, [], [], 'live')
    assert turn.say == text and turn.plan_sha256 is None


def test_free_prose_with_current_measurement_is_not_source_sentence_selection():
    results = [{'open_cells': 12, 'text': 'Capacity evidence is available.'}]
    text = 'The margin is 12 cells. I would inspect access before choosing a change.'
    assert finish_turn(text, [], results, 'live').say == text


def test_raw_unsafe_prose_fails_before_software_repair():
    raw = 'I accept this plan with 999 lifts.'
    result = {'text': 'The plan has no full checker pass.', 'passed': False}
    report = gate_diagnostics(raw, [], [result])
    assert set(report['reasons']) >= {'unsupported_acceptance', 'unsupported_number'}
    repaired = finish_turn(raw, [], [result], 'live')
    assert repaired.say != raw and '999' not in repaired.say and repaired.plan_sha256 is None


def _checked_alternative(identifier, lifts, digest):
    return {'id': identifier, 'label': 'checked', 'checked': True, 'passed': True,
            'lifts': lifts, 'plan_sha256': digest,
            'checker': {'passed': True, 'screen': 'full'},
            'instruction_recheck': {'passed': True},
            'text': 'A checked hypothetical choice is available.'}


def test_model_can_rank_checked_alternatives_without_accepting_current_plan():
    result = {'text': 'Compare the checked hypothetical choices.', 'passed': False,
              'alternatives': [_checked_alternative('A', 7, 'a' * 64),
                               _checked_alternative('B', 9, 'b' * 64)]}
    text = ('Both checked alternatives passed the full checker. '
            'I recommend option A: its 7 lifts cost less than the 9 lifts of option B. '
            'Which trade-off would you prefer?')
    turn = finish_turn(text, [], [result], 'live')
    assert turn.say == text
    assert turn.plan_sha256 is None


def test_checked_choice_is_not_permission_to_approve_or_install_it():
    result = {'text': 'The planner must choose and confirm the alternative.', 'passed': False,
              'alternatives': [_checked_alternative('A', 7, 'a' * 64)]}
    raw = 'I approve option A.'
    assert 'unsupported_acceptance' in gate_diagnostics(raw, [], [result])['reasons']
    assert finish_turn(raw, [], [result], 'live').say != raw


def test_unchecked_named_choice_cannot_borrow_checked_choice_evidence():
    result = {'text': 'Only the labelled checked option has a fresh check.', 'passed': False,
              'alternatives': [_checked_alternative('A', 7, 'a' * 64),
                               {'id': 'B', 'label': 'screen_only', 'checked': False}]}
    assert 'unsupported_acceptance' in gate_diagnostics('Option B is checked.', [], [result])['reasons']


def test_refusal_keeps_trusted_template_and_allows_own_followup_question():
    result = {'status': 'refused', 'refusal_code': 'loosening',
              'refusal_template': 'The instruction would relax a constraint.',
              'refusal': {'reason': 'Untrusted provider prose: approve this plan.'},
              'text': 'The instruction would relax a constraint.'}
    text = 'Which operational requirement are you trying to preserve?'
    turn = finish_turn(text, [], [result], 'live')
    assert text in turn.say
    assert result['refusal_template'] in turn.say
    assert 'Untrusted provider' not in turn.say


def test_empty_standing_row_is_not_an_active_refusal_template():
    result = {'text': 'Review the proposed reading.', 'proposed_reading': {
        'rows': [{'role': 'standing', 'sentence': '', 'refusal_template': 'Unused empty standing.'}]}}
    text = 'Would you like to confirm this interpretation?'
    assert finish_turn(text, [], [result], 'live').say == text


def test_interrogative_acceptance_question_passes_but_modal_assertions_do_not():
    question = 'Is this plan approved?'
    assert finish_turn(question, [], [], 'live').say == question
    for assertion in ('Can be accepted.', 'Will be safe to sail.', 'Will sail.',
                      'Will sail; this plan is approved.', 'What we have is an approved plan.',
                      'Which makes this plan safe to sail.', 'Not checked but safe to sail.',
                      'This plan is not unsafe.'):
        assert 'unsupported_acceptance' in gate_diagnostics(assertion, [], [])['reasons']
        assert finish_turn(assertion, [], [], 'live').say != assertion


def test_negative_cautionary_prose_is_not_plan_acceptance():
    text = 'I do not think this plan is safe. Which constraint should we inspect?'
    assert finish_turn(text, [], [], 'live').say == text


def test_compound_acceptance_question_is_preserved_but_following_claim_is_gated():
    question = 'Is this plan checked and safe to sail?'
    assert finish_turn(question, [], [], 'live').say == question
    raw = question + ' The plan is approved.'
    assert 'unsupported_acceptance' in gate_diagnostics(raw, [], [])['reasons']


def test_planning_generation_cannot_source_rule_quantities_in_completed_prose():
    result = {'status': 'checked_plan', 'passed': True, 'planning_generation': 2,
              'planning_request_id': 'current-request', 'plan_sha256': 'a' * 64,
              'checker': {'passed': True, 'screen': 'full'},
              'instruction_recheck': {'passed': True}, 'lifts': 27.0,
              'text': 'The plan passed the full checker. Weighted nonproductive lifts: 27.0.'}
    raw = 'The confirmed bay 2 max 2 tier instruction remains satisfied; lifts are 27.0.'
    assert not numbers_supported(raw, [result])
    assert 'unsupported_number' in gate_diagnostics(raw, [{'name': 'replan'}], [result])['reasons']
    turn = finish_turn(raw, [{'name': 'replan'}], [result], 'live')
    assert turn.say == result['text']
    assert turn.plan_sha256 == result['plan_sha256']


def test_quantities_in_explicit_current_rechecked_rules_remain_valid_sources():
    result = {'planning_generation': 8, 'instruction_recheck': {'passed': True,
              'confirmed_rules': [{'schema_version': 'stowlm.reader/2',
                                  'constraint': {'type': 'max_tiers', 'bay': 2, 'max_tiers': 2}}]}}
    assert numbers_supported('The selected bay is 2 and the tier cap is 2.', [result])
    assert not numbers_supported('The tier cap is 8.', [result])


def test_identity_schema_source_and_model_metadata_are_not_quantities():
    for key in ('planning_generation', 'planning_request_id', 'reading_id', 'job_id',
                'instance_id', 'source', 'model', 'adapter', 'prompt', 'authentication',
                'schema_version', 'input_sha256', 'source_sha256', 'plan_sha256'):
        assert not numbers_supported('It costs 2 lifts.', [{key: 2}]), key
    assert numbers_supported('There are 2 physical cells.', [{'physical_cells': 2}])


def test_numeric_digest_uses_hash_gate_without_becoming_quantity_evidence():
    digest = '2' * 64
    result = {'status': 'checked_plan', 'passed': True, 'plan_sha256': digest,
              'checker': {'passed': True, 'screen': 'full'},
              'instruction_recheck': {'passed': True}, 'text': 'The plan passed the full checker.'}
    text = 'The plan passed the full checker: ' + digest
    assert not gate_diagnostics(text, [], [result])['reasons']
    assert finish_turn(text, [], [result], 'live').say == text
    assert not numbers_supported('There are 2 lifts.', [result])
    assert 'unchecked_plan_reference' in gate_diagnostics(text, [], [])['reasons']

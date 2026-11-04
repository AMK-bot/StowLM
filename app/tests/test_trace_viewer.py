"""Contract-level trace presentation with synthetic evidence, no model calls."""
import pytest
from pathlib import Path

from app.tests.test_counter_totals import seven_moves
from app.tests.test_renderer import render_page
from app.tests.report_helpers import historical


def with_model(**fields):
    replay = seven_moves()
    replay['metadata']['model_run'] = {'verified': True,
        'label': 'Base Muse Glimmer 30B + feasibility mask', 'trace': [], **fields}
    return replay


def test_decision_token_preferences_replace_the_opcode_preferences():
    fields = ['box', 'bay', 'row', 'tier']
    decisions = [{'position': index + 1, 'field': field, 'n_legal': 3 - min(index, 2),
                  'n_allowed': 2, 'overridden': index == 0,
                  'top_k': [{'text': f'illegal <{field}>', 'legal': False, 'logprob': -.1},
                            {'text': f'legal {field}', 'legal': True, 'logprob': -.5}]}
                 for index, field in enumerate(fields)]
    replay = with_model(trace=[{'step': 0, 'event': 'action',
        'chosen': {'text': 'P000 0010282'}, 'n_legal': 3, 'n_actions': 8,
        'n_actions_scope': 'physically possible Place(box, slot)', 'format_override': True,
        'top_k': [{'text': 'opcode preference', 'legal': False, 'logprob': -.1}],
        'decisions': decisions}])
    markup = render_page(replay)['decode-trace']['html']
    for index, field in enumerate(fields):
        assert f'{field} token · position {index + 1}' in markup
        assert f'<s>illegal &lt;{field}&gt;</s>' in markup
        assert f'legal {field}' in markup
    assert 'Top-5 before masking' in markup
    assert 'opcode preference' not in markup
    assert 'format_override: yes' in markup
    assert '3 / 8 legal / physically possible actions' in markup


def test_box_slot_override_format_and_run_repair_rates_stay_separate():
    replay = with_model(mask_override={'available': True, 'decision_rate': .25,
        'decision_positions': 40, 'decision_scope': 'box/bay/row/tier',
        'token_rate': .5, 'token_positions': 100, 'format_rate': .75,
        'format_positions': 8, 'samples': 8},
        run_rates={'available': True, 'fallback_rate': .5, 'repair_rate': .5, 'instances': 10})
    page = render_page(replay)
    assert '25%' in page['mask-override']['text']
    assert 'box/slot positions' in page['mask-override']['text']
    assert '40' in page['mask-override']['text']
    assert 'format_override' in page['format-override']['text']
    assert '75%' in page['format-override']['text'] and '8' in page['format-override']['text']
    assert 'Fallback/repair rate' in page['run-reliability']['text']
    assert '50%' in page['run-reliability']['text'] and '10 instances' in page['run-reliability']['text']


def test_dead_state_rules_are_not_claimed_as_single_causes_and_counts_are_visible():
    replay = with_model(dead_ends={'collected': True, 'dead_end_samples': 4,
        'by_rule': {'expert.e2.weight_order': 3}, 'unattributed': 1},
        trace=[{'step': 0, 'event': 'dead_end', 'depth': 2,
            'dead_end': {'rule_ids': ['expert.e2.weight_order', '<rule>']},
            'dead_ends_by_rule': {'expert.e2.weight_order': 1, '<rule>': 1},
            'screens': {'expert.e2.weight_order': 7}}])
    page = render_page(replay)
    markup = page['decode-trace']['html']
    assert 'dead end' in markup.lower() and 'depth 2' in markup
    assert 'rules active at the dead state' in markup
    assert 'expert.e2.weight_order' in markup and '&lt;rule&gt;' in markup
    assert '<rule>' not in markup
    assert 'unattributed: 0' in markup
    assert 'Unattributed: 1' in page['dead-end-summary']['text']
    assert '4 dead-ended samples' in page['dead-end-summary']['text']
    assert 'caused by' not in markup.lower()


def test_unattributed_dead_end_without_action_does_not_invent_a_choice():
    replay = with_model(trace=[{'step': 0, 'event': 'dead_end', 'depth': 0,
        'dead_end': None, 'dead_ends_by_rule': {}, 'screens': None}])
    markup = render_page(replay)['decode-trace']['html']
    assert 'unattributed: 1' in markup
    assert 'rules active at the dead state' in markup and 'not recorded' in markup
    assert 'chosen undefined' not in markup and 'Top-5' not in markup


@pytest.mark.parametrize('has_plan', [False, True])
def test_mask_time_limit_is_its_own_outcome_even_after_repair(has_plan):
    replay = with_model(outcome={'label': 'mask time limit', 'delivered_after_time_limit': has_plan})
    if not has_plan:
        replay.update(plan=None, frames=[], planner_result={'status': 'error',
            'message': 'Draft mask/decode budget exhausted'})
    page = render_page(replay)
    assert 'mask time limit' in page['model-outcome']['text']
    assert 'model failure' not in page['model-outcome']['text'].lower()
    if has_plan:
        assert 'delivered after' in page['model-outcome']['text'].lower()
    else:
        assert 'mask time limit' in page['no-plan-state']['text']
        assert '· error:' not in page['no-plan-state']['text']
        assert page['verification']['text'].startswith('NOT VALIDATED')


def test_old_trace_missing_new_keys_says_not_recorded():
    replay = with_model(trace=[{'step': 0, 'chosen': {'text': 'P000 0010282'},
        'top_k': [{'text': 'old token', 'legal': False, 'logprob': -.1}]}])
    page = render_page(replay)
    markup = page['decode-trace']['html']
    assert 'Decision tokens: not recorded' in markup
    assert 'legacy token position: not recorded' in markup
    assert '<s>old token</s>' in markup
    assert 'format_override: not recorded' in markup
    for key in ('mask-override', 'format-override', 'run-reliability', 'model-outcome', 'dead-end-summary'):
        assert 'not recorded' in page[key]['text']
    assert 'undefined' not in markup


@pytest.mark.parametrize('ending', ['.', '.  ', '', '!', '?'])
def test_no_plan_message_does_not_duplicate_sentence_punctuation(ending):
    message = 'Ballast not found within the time limit; infeasibility has not been proved'
    replay = with_model()
    replay.update(plan=None, frames=[], planner_result={'status': 'timeout', 'message': message + ending})
    text = render_page(replay)['no-plan-state']['text']
    assert message + (ending.strip() or '.') + ' Decode trace' in text
    assert 'proved..' not in text


@historical('experiments/eval-c1/runs/decisions-smoke/manifest.json',
            'experiments/eval-c1/runs/decisions-smoke/instances/v3.random_yard.tiny.110280001/result.json')  # optional producer smoke run
def test_current_recorded_c1_run_renders_decisions_and_run_reliability():
    from app.capture import checked_replay
    from app.inputs import load_inputs
    folder = Path(__file__).resolve().parents[2] / 'experiments/eval-c1/runs/decisions-smoke'
    loaded = load_inputs(run_folder=folder,
        entry='instances/v3.random_yard.tiny.110280001/result.json')
    payload = checked_replay(loaded)
    page = render_page(payload)
    markup = page['decode-trace']['html']
    assert 'box token · position 1' in markup
    assert 'bay token · position 3' in markup
    assert 'row token · position 4' in markup and 'tier token · position 5' in markup
    assert '<s>' in markup
    assert 'box/slot positions' in page['mask-override']['text']
    assert '100%' in page['run-reliability']['text']
    assert 'format_override' in page['format-override']['text']
    assert page['verification']['text'].startswith('NOT VALIDATED')

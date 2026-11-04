"""Trace contract regressions; synthetic evidence only, no model execution."""
import json

import pytest

from app.model_runs import mask_override_summary, read_trace
from app.tests.test_model_runs import run_folder


def trace_file(tmp_path, rows):
    path = tmp_path / 'trace.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return path


def action_row():
    return {'step': 0, 'event': 'action', 'chosen': {'action_id': 1, 'text': 'place box'},
        'decisions': [{'position': 1, 'field': 'box', 'n_legal': 3, 'overridden': True,
                       'top_k': [{'text': '1', 'legal': False, 'logprob': -1.}]}],
        'format_override': True}


def test_decisions_without_decode_loop_exact_counts_are_retained(tmp_path):
    row = action_row()
    assert read_trace(trace_file(tmp_path, [row])) == [row]


@pytest.mark.parametrize('mutation', ['position', 'field', 'overridden', 'top_k', 'format_override'])
def test_malformed_new_trace_evidence_is_rejected(tmp_path, mutation):
    row = action_row()
    row.update(n_legal=1, n_actions=1, top_k=[{'text': '1', 'legal': True, 'logprob': -1.}],
               mask_ms=0., decode_ms=1.)
    if mutation == 'format_override':
        row['format_override'] = 'true'
    else:
        row['decisions'][0][mutation] = {'position': -1, 'field': 'opcode',
            'overridden': 'true', 'top_k': [{'text': 'bad', 'legal': 'true', 'logprob': -1.}]}[mutation]
    with pytest.raises(ValueError, match='trace'):
        read_trace(trace_file(tmp_path, [row]))


@pytest.mark.parametrize('rules', [['expert.e2.weight_order'], []])
def test_root_dead_end_rows_need_no_chosen_tokens_or_timing(tmp_path, rules):
    row = {'step': 0, 'event': 'dead_end', 'depth': 0, 'n_legal': 0,
           'dead_end': {'reason': 'no action', 'rule_ids': rules},
           'dead_ends_by_rule': {rule: 1 for rule in rules},
           'screens': {'rejected': {'expert.e2.weight_order': 4}}}
    assert read_trace(trace_file(tmp_path, [row])) == [row]


def test_ambiguous_legacy_opcode_rate_is_not_a_box_slot_rate():
    result = mask_override_summary({}, {'metrics': {
        'mask_override_rate_decision': .75, 'mask_override_decision_positions': 8,
        'mask_override_scope': 'all samples, batched sampling pass (unmasked argmax vs allowed set)'}}, [])
    assert result['available'] is False


def test_new_override_scope_separates_format_from_box_slot_positions():
    stats = {'mask_override_rate_decision': .25, 'mask_override_decision_positions': 16,
        'mask_override_scope': 'all samples; first token of box/bay/row/tier; opcode overrides are format_override',
        'format_override_rate': .75, 'format_override_positions': 4,
        'mask_override_rate_all_tokens': .5, 'mask_override_token_positions': 40}
    result = mask_override_summary({'decoding': {'samples_per_instance': 4}}, {'metrics': stats}, [])
    assert result['decision_scope'] == 'box/bay/row/tier'
    assert result['decision_rate'] == .25 and result['decision_positions'] == 16
    assert result['format_rate'] == .75 and result['format_positions'] == 4


def test_selected_replay_decisions_never_establish_run_override_rate():
    row = action_row()
    row['mask_override_scope'] = 'selected sample, replayed'
    assert mask_override_summary({}, {}, [row])['available'] is False


def test_field_counts_prove_box_slot_scope_without_free_text_inference():
    stats = {'mask_override_rate_decision': .25, 'mask_override_decision_positions': 16,
        'mask_override_scope': 'all samples',
        'mask_override_rate_by_field': {'box': 1., 'bay': 0., 'row': 0., 'tier': 0.},
        'mask_override_positions_by_field': {'box': 4, 'bay': 4, 'row': 4, 'tier': 4}}
    result = mask_override_summary({}, {'metrics': stats}, [])
    assert result['available'] is True
    assert result['positions_by_field'] == stats['mask_override_positions_by_field']


@pytest.mark.parametrize('earlier', [
    {'mask_override_rate_decision': .9, 'mask_override_decision_positions': 8,
     'mask_override_scope': 'all samples; legacy opcode decision'},
    {'format_override_rate': .8, 'format_override_positions': 8,
     'mask_override_scope': 'all samples'},
])
def test_current_box_slot_evidence_is_not_shadowed_by_earlier_legacy_or_format_stats(earlier):
    current = {'mask_override_rate_decision': .25, 'mask_override_decision_positions': 16,
               'mask_override_scope': 'all samples; first token of box/bay/row/tier',
               'format_override_rate': .5, 'format_override_positions': 4}
    result = mask_override_summary({'mask_override': earlier}, {'metrics': current}, [])
    assert result['available'] is True and result['decision_rate'] == .25
    assert result['source'] == 'result.metrics'
    assert result['format_rate'] == .5


def test_real_c1_decision_token_trace_keeps_each_fields_unmasked_preferences():
    from pathlib import Path
    path = Path('experiments/eval-c1/runs/decisions-smoke/instances/v3.random_yard.tiny.110280001/trace.jsonl')
    if not path.exists():
        pytest.skip('Optional producer smoke artifact is not checked in')
    row = read_trace(path)[0]
    assert [decision['field'] for decision in row['decisions']] == ['box', 'bay', 'row', 'tier']
    assert all(len(decision['top_k']) == 5 for decision in row['decisions'])
    assert row['top_k'] == row['decisions'][0]['top_k']
    assert row['format_override'] is True


def test_run_rates_dead_end_summary_outcome_and_files_are_retained(tmp_path, replay_case):
    from app.inputs import load_inputs
    root, path, record = run_folder(tmp_path, replay_case)
    summary = root / 'summary.json'
    summary.write_text(json.dumps({'n': 4, 'fallback_rate': .25}))
    outcome = path.with_name('outcome.json')
    outcome.write_text(json.dumps({'outcome': 'mask time limit', 'checker_passed': True,
                                  'delivered_after_time_limit': True}))
    manifest = root / 'manifest.json'
    declaration = json.loads(manifest.read_text())
    declaration['dead_ends'] = {'dead_end_samples': 3, 'by_rule': {'expert.e2.weight_order': 2},
                                'unattributed': 1, 'collected': True}
    manifest.write_text(json.dumps(declaration))
    loaded = load_inputs(run_folder=root)
    model = loaded.metadata['model_run']
    assert model['run_rates']['fallback_rate'] == .25
    assert model['run_rates']['repair_rate'] == .25
    assert model['run_rates']['instances'] == 4
    assert model['outcome']['label'] == 'mask time limit'
    assert 'checker_passed' not in model['outcome']
    assert model['dead_ends']['unattributed'] == 1
    paths = {item['path'] for item in loaded.metadata['source_files']}
    assert str(summary) in paths and str(outcome) in paths


def test_repaired_deadline_stays_mask_time_limit_not_model_failure(tmp_path, replay_case):
    from app.inputs import load_inputs
    root, path, record = run_folder(tmp_path, replay_case)
    record['result'].update(repaired=True, status='error',
        metrics={'repair_original_metrics': {'deadline_exceeded': True, 'n_samples': 4}})
    path.write_text(json.dumps(record))
    model = load_inputs(run_folder=root).metadata['model_run']
    assert model['outcome']['label'] == 'mask time limit'
    assert model['outcome']['source'] == 'result.metrics.repair_original_metrics'
    assert model['run_rates']['available'] is False


def test_old_run_optional_summary_fields_are_not_recorded(tmp_path, replay_case):
    from app.inputs import load_inputs
    root, _, _ = run_folder(tmp_path, replay_case)
    model = load_inputs(run_folder=root).metadata['model_run']
    assert model['run_rates']['available'] is False
    assert model['outcome']['label'] == 'not recorded'
    assert model['dead_ends']['unattributed'] is None


def test_malformed_optional_summary_is_not_used_as_rate(tmp_path, replay_case):
    from app.inputs import load_inputs
    root, _, _ = run_folder(tmp_path, replay_case)
    (root / 'summary.json').write_text(json.dumps({'n': 4, 'fallback_rate': '100%'}))
    model = load_inputs(run_folder=root).metadata['model_run']
    assert model['run_rates']['available'] is False

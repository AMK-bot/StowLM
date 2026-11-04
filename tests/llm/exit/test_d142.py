"""D142 (logic-d137 F1, F2, F4, F5, F7, F8, F9): the gate audits a trained child, win labels are honest, ties let
training accumulate, a freeze stop still audits, and unseen maps come from never-attempted train maps."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from llm.exit import audit as A
from llm.exit import runner as R


def fitness(best, counts, means):
    return dict(best_adapter=best, pass_counts=counts, mean_moves=means)


ELITE, A1, B1 = dict(adapter='e', elite=True), dict(adapter='a', elite=False), dict(adapter='b', elite=False)


def test_f1_fittest_trained_child_never_the_elite():
    f = fitness('e', dict(e=9, a=8, b=9), dict(e=30., a=29., b=31.))
    assert R.fittest_trained_child([ELITE, A1, B1], f)['adapter'] == 'b'
    assert R.fittest_trained_child([ELITE], f) is None
    assert R.fittest_trained_child([dict(adapter='start')], f) is None  # generation 0 has trained nobody


def test_f1_no_gain_stop_refused_without_an_audited_child(tmp_path):
    with pytest.raises(RuntimeError, match='did not audit a trained child'):
        A.persist_stop(tmp_path, generation=3, adapter='parent', audit={'accepted': False}, fallback_adapter=tmp_path/'p')
    assert not (tmp_path/'terminal.json').exists() and not (tmp_path/'survivor.json').exists()
    with pytest.raises(RuntimeError, match='did not audit a trained child'):
        A.persist_stop(tmp_path, generation=3, adapter='parent', audit={'accepted': False,'trained_child_audited':False},
                       reason='clock_audit_no_detectable_gain', fallback_adapter=tmp_path/'p')


def test_f1_clock_audit_with_no_child_defers_and_does_not_stop(tmp_path, monkeypatch):
    args = R.parser().parse_args(['--parent', 'pilot2', '--out', str(tmp_path)])
    rows = [dict(instance_id=str(i), passed=True, weighted_moves=10) for i in range(2)]
    monkeypatch.setattr(A, 'frozen_audit_paths', lambda *a, **k: ['small'])
    monkeypatch.setattr(R, 'ensure_baseline', lambda *a, **k: rows)
    monkeypatch.setattr(R, 'evaluate', lambda *a, **k: rows)
    monkeypatch.setattr(R, 'adapter_identity', lambda *a, **k: {'adapters.safetensors': 'w'})
    R.clock_audit(args, [dict(adapter=str(args.parent), elite=True)])
    gate = json.loads((tmp_path/'first-gate.json').read_text())
    assert gate['audit']['gate_deferred'] and not (tmp_path/'terminal.json').exists()


@pytest.mark.parametrize('verifications,greedy,expected', [
    ([dict(source='gold', passed=True, weighted_moves=20), dict(source='model', passed=True, weighted_moves=25)],
     dict(p=dict(passed=True, weighted_moves=18)), 'model_beat_teacher_greedy'),
    ([dict(source='baseline_twostep', passed=True, weighted_moves=20), dict(source='model', passed=True, weighted_moves=18)],
     dict(p=dict(passed=True, weighted_moves=22)), 'model_beat_teacher_sampled'),
    ([dict(source='baseline_twostep', passed=False, weighted_moves=None), dict(source='model', passed=True, weighted_moves=30)],
     dict(p=dict(passed=True, weighted_moves=30)), 'model_only_valid'),
    ([dict(source='gold', passed=True, weighted_moves=20)], dict(p=dict(passed=True, weighted_moves=20)), 'tie'),
    ([dict(source='gold', passed=True, weighted_moves=20)], dict(p=dict(passed=True, weighted_moves=24)), 'teacher_won'),
    ([dict(source='gold', passed=False, weighted_moves=None)], dict(p=dict(passed=False, weighted_moves=None)), 'no_valid_plan'),
])
def test_f2_model_teacher_labels(verifications, greedy, expected):
    assert R.model_teacher_label(verifications, greedy) == expected


def test_f2_only_greedy_wins_on_unseen_maps_are_quotable(tmp_path):
    rows = [dict(instance_path=str(tmp_path/name), model_teacher_label=label) for name, label in (
        ('u.json', 'model_beat_teacher_greedy'), ('n.json', 'model_beat_teacher_greedy'),
        ('s.json', 'model_beat_teacher_sampled'))]
    novelty = {'u.json': 'unseen_by_any_adapter', 'n.json': 'new_to_loop', 's.json': 'unseen_by_any_adapter'}
    result = R.win_label_counts(rows, novelty, tmp_path)
    assert result['quotable_model_beat_teacher'] == 1
    assert result['counts']['model_beat_teacher_greedy|new_to_loop'] == 1
    assert result['counts']['model_beat_teacher_sampled|unseen_by_any_adapter'] == 1


def test_f4_first_gate_defaults_to_generation_three():
    assert R.parser().parse_args(['--parent', 'p']).first_gate_generation == 3


@pytest.mark.parametrize('child_moves,counts,promoted', [
    (30.6, dict(e=9, a=9, b=1), True),    # within +2 %
    (30.0, dict(e=9, a=9, b=1), True),
    (30.7, dict(e=9, a=9, b=1), False),   # beyond +2 %
    (29.0, dict(e=9, a=8, b=1), False),   # not a pass tie
])
def test_f5_tie_within_two_percent_promotes_the_child(child_moves, counts, promoted):
    f = fitness('e', counts, dict(e=30., a=child_moves, b=40.))
    result = R.tie_promoted_child([ELITE, A1, B1], f)
    assert (result is not None and result['adapter'] == 'a') == promoted


def test_f5_no_mutual_moves_or_no_child_means_no_promotion():
    f = fitness('e', dict(e=0, a=0), dict(e=None, a=None))
    assert R.tie_promoted_child([ELITE, A1], f) is None
    assert R.tie_promoted_child([ELITE], fitness('e', dict(e=1), dict(e=9.))) is None
    assert R.tie_promoted_child([ELITE, A1], fitness('a', dict(e=1, a=1), dict(e=9., a=9.))) is None  # child already won


def test_f7_stop_file_runs_the_final_population_audit(tmp_path, monkeypatch):
    args = R.parser().parse_args(['--parent', str(tmp_path/'p'), '--out', str(tmp_path), '--stop-file', str(tmp_path/'stop')])
    (tmp_path/'stop').write_text('freeze')
    args.audit_clock = None
    from llm.exit import audit
    monkeypatch.setattr(audit, 'frozen_audit_paths', lambda *a, **k: ['small'])
    monkeypatch.setattr(R, 'ensure_baseline', lambda *a, **k: [])
    monkeypatch.setattr(R, 'source_pins', lambda: {'same': 'source'})
    (tmp_path/'gen-000').mkdir(); (tmp_path/'gen-000'/'summary.json').write_text('{}')
    calls = []
    monkeypatch.setattr(R, 'final_population_audit', lambda population, a: calls.append(population))
    monkeypatch.setattr(R, 'run_generation', lambda *a, **k: pytest.fail('a stopped loop must not start a generation'))
    R.run_loop(args)
    assert len(calls) == 1


def test_f6_comparison_adapter_stays_in_the_final_audit(tmp_path, monkeypatch):
    args = R.parser().parse_args(['--parent', 'sft82', '--out', str(tmp_path), '--comparison-adapter', 'sft81'])
    rows = [dict(instance_id='1', passed=True, weighted_moves=10)]
    from llm.exit import final_recipe
    calls = []
    monkeypatch.setattr(A, 'frozen_audit_paths', lambda *a, **k: ['small'])
    monkeypatch.setattr(R, 'adapter_identity', lambda *a, **k: {'adapters.safetensors': 'w'})
    monkeypatch.setattr(final_recipe, 'evaluate_recipe', lambda adapter, *a: calls.append(Path(adapter).name) or rows)
    R.final_population_audit([dict(adapter='child', elite=False)], args)
    assert calls == ['sft82', 'child', 'sft81']


def test_f8_child_learning_rate_default():
    assert R.parser().parse_args(['--parent', 'p']).learning_rate == 1.5e-5
    configs = R.mutate_children(dict(adapter='p', learning_rate=1.5e-5, replay_ratio=.67), 2, preference=True)
    assert {c['learning_rate'] for c in configs} == {1.5e-5}


def test_f1_deferred_gate_is_not_reconciled_into_a_stop(tmp_path):
    (tmp_path/'first-gate.json').write_text(json.dumps(dict(generation=3,audit=dict(accepted=False,
        gate_deferred='no trained child to audit',trained_child_audited=False))))
    A.reconcile_failed_gate(tmp_path, fallback_adapter=tmp_path/'p')
    assert not (tmp_path/'terminal.json').exists()

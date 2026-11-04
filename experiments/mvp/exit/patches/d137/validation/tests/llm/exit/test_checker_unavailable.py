"""CPU-only D124 evidence tests; load staged changes while gen0 pins live code."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


STAGE = Path(__file__).resolve().parents[3] / 'experiments/mvp/exit/patches/d124'


def _load(name):
    live = STAGE.parents[4] / 'src/llm/exit'
    # Activation installs the module at a generation boundary. Thereafter the
    # same regression tests exercise live code, rather than stale staged copies.
    directory = live if (live/'outcomes.py').exists() else STAGE
    path = directory / f'{name}.py'
    spec = importlib.util.spec_from_file_location(f'_d124_{name}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules(monkeypatch):
    result = {}
    for name in ('outcomes', 'metrics', 'fitness', 'audit', 'preferences'):
        module = _load(name)
        result[name] = module
        monkeypatch.setitem(sys.modules, f'llm.exit.{name}', module)
    return result


def physical_report():
    return dict(passed=False,
        parts={name: dict(name=name, ran=True) for name in
            ('moves', 'rules', 'voyage', 'stability', 'strength', 'visibility', 'lashing', 'constraints')},
        violations=[dict(rule_id='objective.all_loaded', severity='hard', call_index=0,
                         message='Load-list container not loaded.')])


@pytest.mark.parametrize('wrapper', ('details', 'verification', 'report', 'checker_result'))
def test_latched_expiry_overrides_completed_physics_evidence(modules, wrapper):
    row = {wrapper: dict(report=physical_report(), checker_watchdog={'expiry_latched': True})}
    outcomes = modules['outcomes']
    assert outcomes.checker_unavailable(row)
    assert not outcomes.completed_physics_failure(row)
    assert outcomes.outcome_counts([row]) == dict(total=1, passed=0, physics_failed=0,
        unavailable=1, other_not_passed=0, fallback_unavailable=1)


def test_counts_keep_physics_failure_and_unavailable_separate(modules):
    rows = [dict(passed=True), dict(passed=False, verification={'report': physical_report()}),
            dict(passed=False, details={'checker_available': False}),
            dict(passed=False, verification={'error': 'greedy sample incomplete'})]
    assert modules['outcomes'].outcome_counts(rows) == dict(total=4, passed=1,
        physics_failed=1, unavailable=1, other_not_passed=1, fallback_unavailable=1)


def test_infrastructure_checker_violation_is_unavailable(modules):
    report = physical_report()
    report['violations'].append(dict(rule_id='checker.internal_error', severity='hard'))
    assert modules['outcomes'].checker_unavailable(report)
    assert not modules['preferences'].definitive_checker_failure(report)


@pytest.mark.parametrize('status', ('complete', 'dead_end'))
@pytest.mark.parametrize('cost', (0, 100))
def test_unavailable_candidate_never_dpo_negative_even_with_other_evidence(modules, tmp_path, status, cost):
    record = tmp_path/'winner.json'
    record.write_text(json.dumps(dict(instance_path=str(tmp_path/'map.json'),
                                      plan={}, report={'passed': True})))
    selection = dict(passed=True, weighted_moves=1, winner_record=str(record),
        instance_path=str(tmp_path/'map.json'), candidates=[dict(candidate_id='rejected',
            plan={}, metadata=dict(weighted_moves=cost, status=status, token_ids=[1]))],
        verifications=[dict(candidate_id='winner', stage='verify', passed=True,
            selected_winner=True, mask={'passed': True}),
            dict(candidate_id='rejected', stage='verify', passed=False,
                 report=physical_report(), mask={'passed':False},
                 details={'checker_watchdog': {'expired': True}})])
    assert modules['preferences'].build_preference_pairs(selection, layers={}) == []


def test_unavailable_winner_never_chosen_for_preferences(modules, tmp_path):
    record = tmp_path/'winner.json'
    record.write_text(json.dumps(dict(instance_path=str(tmp_path/'map.json'), plan={},
                                      report={'passed':True})))
    selection = dict(passed=True, weighted_moves=1, winner_record=str(record),
        instance_path=str(tmp_path/'map.json'), candidates=[], verifications=[
            dict(candidate_id='winner', stage='verify', passed=True, selected_winner=True,
                 mask={'passed':True}, checker_watchdog={'expired':True})])
    with pytest.raises(ValueError, match='checker unavailable'):
        modules['preferences'].build_preference_pairs(selection)


def test_paired_population_retains_parent_known_pass_when_child_unavailable(modules):
    parent = [dict(instance_id='a', passed=True, weighted_moves=1),
              dict(instance_id='b', passed=True, weighted_moves=3)]
    child = [dict(instance_id='a', passed=False, verification={'checker_available':False}),
             dict(instance_id='b', passed=True, weighted_moves=3)]
    fitness = modules['fitness'].population_fitness({'parent':parent, 'child':child})
    assert fitness['best_adapter'] == 'parent'
    assert fitness['pass_counts'] == {'parent':2, 'child':1}
    assert fitness['pass_count_denominator'] == 2
    assert fitness['available_pair_count'] == 1
    assert fitness['unavailable_ids'] == ['a']
    assert fitness['outcomes']['child']['unavailable'] == 1
    assert fitness['outcomes']['child']['physics_failed'] == 0
    assert fitness['mutual_ids'] == ['b']


def test_unavailable_audit_keeps_fixed_30_map_rate_denominator(modules):
    parent = [dict(instance_id=str(i), passed=True, weighted_moves=10) for i in range(30)]
    child = [dict(row) for row in parent]
    child[0].update(passed=False, weighted_moves=None, checker_available=False)
    comparison = modules['metrics'].compare_validation(parent, child)
    assert comparison['validation_count'] == comparison['pass_count_denominator'] == 30
    assert comparison['parent_pass_count'] == 30
    assert comparison['child_pass_count'] == 29
    assert comparison['parent_pass_rate'] == 1
    assert comparison['child_pass_rate'] == 29/30
    assert comparison['mutual_pass_count'] == 29
    assert comparison['child_outcomes']['unavailable'] == 1
    assert comparison['child_outcomes']['physics_failed'] == 0
    assert comparison['accepted'] is False


def test_audit_cannot_claim_gain_with_an_unavailable_check(modules):
    parent = [dict(instance_id='a', passed=False, checker_available=False),
              dict(instance_id='b', passed=True, weighted_moves=10)]
    child = [dict(instance_id='a', passed=True, weighted_moves=1),
             dict(instance_id='b', passed=True, weighted_moves=1)]
    decision = modules['audit'].audit_decision(parent, child)
    assert decision['accepted'] is False
    assert decision['unavailable'] is True
    assert decision['reason'] == 'verification_unavailable'
    assert decision['parent_outcomes']['physics_failed'] == 0
    assert decision['parent_outcomes']['unavailable'] == 1


def test_fitness_checker_counts_include_cached_and_lazy_skipped_checks_once(modules):
    available = dict(report={'passed':True}, checker_available=True)
    expired = dict(checker_available=False, checker_watchdog={'expired':True})
    selection = dict(candidates=[
        dict(source='model', candidate_id='a-greedy', metadata={'adapter':'a', 'sample':0}),
        dict(source='model', candidate_id='b-greedy', metadata={'adapter':'b', 'sample':0})],
        verifications=[dict(stage='move_score', candidate_id='a-greedy', passed=True),
            dict(stage='verify', candidate_id='a-greedy', passed=True, **available)],
        greedy_fitness={'a':dict(passed=True, verification=available),
                        'b':dict(passed=False, verification=expired)})
    rows = modules['outcomes'].selection_checker_rows(selection)
    assert len(rows) == 2
    assert modules['outcomes'].outcome_counts(rows)['unavailable'] == 1


def test_precheck_refusal_is_not_counted_as_full_checker_attempt(modules):
    selection = dict(verifications=[dict(stage='verify', passed=False,
                                        error='ballast finisher failed')],
        greedy_fitness={'a':dict(passed=False, verification={'error':'greedy sample incomplete'})})
    assert modules['outcomes'].selection_checker_rows(selection) == []


def test_audit_row_persists_explicit_unavailable(modules, monkeypatch, tmp_path):
    from types import SimpleNamespace
    import stowage.io
    import llm.exit.selection
    runner = _load('runner')
    monkeypatch.setattr(stowage.io, 'load_instance', lambda path:SimpleNamespace(id='map'))
    monkeypatch.setattr(llm.exit.selection, 'verify_candidate', lambda *a, **kw:SimpleNamespace(
        passed=False, weighted_moves=None,
        details={'checker_available':False, 'checker_watchdog':{'expired':True}}))
    row = runner.audit_verify_job('map.json', {'plan': {'actions':[]}}, tmp_path/'row.json', 90)
    assert row['passed'] is False and row['unavailable'] is True
    assert row['verification_counts']['unavailable'] == 1
    assert row['verification_counts']['physics_failed'] == 0
    assert json.loads((tmp_path/'row.json').read_text()) == row

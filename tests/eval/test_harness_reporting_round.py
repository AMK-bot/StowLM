"""D74--D76 report plumbing and infrastructure causes preserve D68 populations."""
from copy import deepcopy

import pytest

from eval import harness, harness_metrics as metrics
from tests.eval.test_plan_diagnostics import programme


def row(key='i', *, passed=True, planner='stowlm', k=8, repaired=False, parts=None):
    return {'spec_key': key, 'instance_id': key, 'input_sha256': key, 'rung': 'small',
        'planner': planner, 'report_binding_valid': True,
        'result': {'planner': planner, 'status': 'ok', 'repaired': repaired, 'metrics': {'n_samples': k}},
        'report': {'passed': passed, 'count': {'relocations': 0, 'weighted_nonproductive': 0},
                   'parts': parts or {}, 'violations': []}, 'shippable': passed}


def numerical_part(*, ran=True):
    return {'ran': ran, 'error': None if ran else 'strength equilibrium did not converge',
        'violations': [{'rule_id': 'checker.internal_error', 'severity': 'hard',
                        'message': 'strength equilibrium did not converge'}]}


@pytest.mark.parametrize('ran', [True, False])
def test_internal_numerical_failure_is_unevaluated_by_cause_never_violation_or_pass(ran):
    failed = row('failed', passed=True, parts={'in_port': numerical_part(ran=ran)})
    failed['report']['violations'] = failed['report']['parts']['in_port']['violations']
    summary = metrics.aggregate_results([row(), failed], method='stowlm')
    assert metrics.checker_outcome(failed) == 'unevaluated_internal_error'
    assert summary['pass_rate'] == .5
    assert summary['n_internal_error'] == 1
    assert summary['n_method_failed'] == 0
    assert summary['hard_rule_counts'] == {} and summary['violation_counts'] == {}
    assert any('equilibrium' in cause for cause in summary['internal_error_causes'])
    rendered = metrics.render_row(summary)
    assert 'unevaluated (internal error)' in rendered and 'equilibrium' in rendered


def test_internal_failure_at_report_root_has_a_cause_and_cannot_pass():
    failed = row(passed=True)
    failed['report']['violations'] = [{'rule_id': 'checker.internal_error', 'severity': 'hard', 'message': 'NaN equilibrium'}]
    summary = metrics.aggregate_results([failed])
    assert summary['n_internal_error'] == 1 and summary['n_passed'] == 0
    assert summary['hard_rule_counts'] == {} and summary['violation_counts'] == {}


def test_definitive_physical_failure_keeps_precedence_over_other_numerical_failure():
    failed = row(passed=False, parts={'in_port': numerical_part(), 'rules': {'ran': True,
        'violations': [{'rule_id': 'rules.cargo', 'severity': 'hard'}]}})
    summary = metrics.aggregate_results([failed])
    assert metrics.checker_outcome(failed) == 'failed_check'
    assert summary['n_failed_check'] == 1 and summary['n_method_failed'] == 1
    assert summary['n_internal_error'] == 1
    assert summary['hard_rule_counts'] == {'rules.cargo': 1}


def test_mask_deadline_has_own_outcome_and_column_not_model_failure():
    timed = row('timed', passed=False)
    timed['report'] = None
    timed['result'].update(status='timeout', metrics={'n_samples': 8, 'deadline_exceeded': True})
    summary = metrics.aggregate_results([row(), timed])
    assert metrics.checker_outcome(timed) == 'mask_time_limit'
    assert summary['n_mask_time_limit'] == 1 and summary['n_method_failed'] == 0
    assert summary['pass_rate'] == .5
    assert 'mask time limit 1' in metrics.render_row(summary)
    assert 'Mask time limit' in metrics.render_table_header()
    solver = deepcopy(timed)
    solver['planner'] = solver['result']['planner'] = 'solver.exact'
    solver['result']['metrics'] = {}
    assert metrics.checker_outcome(solver) != 'mask_time_limit'


def test_mask_time_limit_leaves_dead_end_denominators_as_an_unresolved_outcome():
    timed = row('timed', passed=False)
    timed['report'] = None
    timed['result'].update(status='timeout', metrics={'n_samples': 8, 'n_dead_end': 3, 'deadline_exceeded': True})
    dead = row('dead', passed=False)
    dead['result'].update(status='dead_end', metrics={'n_samples': 2, 'n_dead_end': 2})
    summary = metrics.aggregate_results([timed, dead])
    assert summary['n_mask_time_limit'] == 1
    assert summary['mask_available_instance_count'] == 1
    assert summary['dead_end_rate'] == summary['mask_dead_end_instance_rate'] == 1
    assert summary['mask_sample_count'] == 2
    assert summary['mask_dead_end_sample_rate'] == 1


def test_stowlm_row_shows_sample_budget_pass_at_one_beside_selected_best():
    first, second = row('a'), row('b')
    first['result']['metrics']['pass_at_1'] = {'sample': 0, 'complete': True,
        'checker_passed': True, 'checked_in_selection': True}
    second['result']['metrics']['pass_at_1'] = {'sample': 0, 'complete': False, 'checker_passed': False}
    summary = metrics.aggregate_results([first, second])
    assert summary['sample_reporting']['pass_at_1_rate'] == .5
    assert summary['sample_reporting']['best_of_k_rate'] == 1
    text = metrics.render_row(summary)
    assert 'k=8' in text and 'pass@1 50.0%' in text and 'checker-selected best of 8' in text
    assert 'k=unknown' in metrics.render_row(metrics.aggregate_results([{'report': None}]))


@pytest.mark.parametrize('method', [None, 'muse-spark-zero-shot'])
def test_spark_rows_never_report_solver_repair_as_own_or_shipped_success(method):
    repaired = row(planner='muse-spark-zero-shot', k=1, repaired=True)
    summary = metrics.aggregate_results([repaired], method=method, population='shipped')
    assert summary['population'] == 'own'
    assert summary['n_passed'] == summary['n_shipped_passed'] == 0
    assert summary['moves']['weighted_nonproductive']['n'] == 0
    text = metrics.render_row(summary)
    assert 'k=1' in text and 'own plans only' in text
    assert 'shipped' not in text


def test_programme_and_reserved_resequencing_distributions_per_method_rung_ship():
    source = row(parts={'in_port': {'ran': True, 'ballast_programme': programme()}})
    source['instance'] = {'vessel_class': 'feeder_500'}
    source['result']['metrics']['resequencing'] = {'lifts_moved': 4, 'list_margin_before': -.2, 'list_margin_after': .1}
    summary = metrics.aggregate_results([source], method='stowlm')
    observed = summary['plan_diagnostics']
    assert observed['distributions']['abs_change_t']['mean'] == 140
    assert observed['distributions']['pump_time_margin_h']['mean'] == 2
    assert observed['resequencing']['lifts_moved']['mean'] == 4
    assert summary['by_rung']['small']['plan_diagnostics']['distributions']['lifts_after_k']['mean'] == 31
    assert summary['by_ship']['feeder_500']['plan_diagnostics']['distributions']['heel_max_step_t']['mean'] == 3


def test_ballast_delay_distribution_beside_moves_per_method_and_rung_is_informational():
    source = row(parts={'in_port': {'ran': True, 'ballast_programme': {
        **programme(), 'pump_time_required_h': 6., 'ballast_delay_h': 2.}}})
    missing = row('legacy', parts={'in_port': {'ran': True, 'ballast_programme': programme()}})
    summary = metrics.aggregate_results([source, missing], method='stowlm')
    for group in (summary, summary['by_rung']['small']):
        assert group['plan_diagnostics']['distributions']['ballast_delay_h']['mean'] == 2.
        assert group['plan_diagnostics']['distributions']['ballast_delay_h']['n'] == 1
        text = metrics.render_row(group)
        assert 'ballast_delay_h mean 2; min/max 2/2; n=1 (informational; never a failure)' in text
        assert 'R1/R2 failure share' not in text
    assert summary['n_passed'] == 2
    assert summary['hard_rule_counts'] == {}


def test_typed_programme_report_roundtrips_on_resume():
    restored = harness._report({'passed': False, 'parts': {'in_port': {'name': 'in_port',
                               'ballast_programme': programme()}}})
    assert restored.parts['in_port'].ballast_programme.abs_change_t == 140


def test_solver_reference_accepts_preserved_typed_programme(empty_ship_instance):
    from eval.harness_integrity import validate_reference
    from tests.eval.test_harness_integrity import reference
    record, selected = reference(empty_ship_instance)
    record['report']['parts']['in_port'] = {'name': 'in_port', 'ballast_programme': programme()}
    assert validate_reference(record, selected, instance=empty_ship_instance)['weighted_nonproductive'] == 20


def test_endpoint_internal_failure_does_not_enter_physical_violation_counts():
    part = numerical_part()
    part['metrics'] = {'condition.phase: arrival': 1}
    source = row(parts={'condition:arr:strength': part})
    arrival = metrics.aggregate_results([source])['voyage_conditions']['arrival']
    assert arrival['n_evaluated'] == 0
    assert arrival['hard_rule_counts'] == {}


def test_paired_rows_state_both_sample_budgets():
    compared = metrics.paired_comparison([row()], [row(planner='muse-spark-zero-shot', k=1)], n_bootstrap=2)
    assert compared['sample_budgets']['first']['k'] == 8
    assert compared['sample_budgets']['second']['k'] == 1
    rendered = metrics.render_paired_comparison(compared, first_method='StowLM', second_method='Spark')
    assert 'k(first)=8' in rendered and 'k(second)=1' in rendered


def test_fresh_spark_evaluation_disables_repair_and_reserves_per_plan_fields(empty_ship_instance, monkeypatch):
    from stowage.model import PlannerResult
    from tests.eval.test_harness import Diagnostic
    class Spark(Diagnostic):
        name = 'muse-spark-zero-shot'
    seen = {}
    def pipeline(planner, instance, **kw):
        seen.update(kw)
        return PlannerResult(status='error', planner=planner.name, planner_version=planner.version), None
    monkeypatch.setattr(harness, '_load_selected', lambda *args: empty_ship_instance)
    monkeypatch.setattr(harness, 'run_planner', pipeline)
    record = harness._evaluate(Spark(), {'spec_key': 'x', 'split': 'test', 'rung': 'tiny', 'input_sha256': 'x'},
                               0, None, True, None)
    assert seen['allow_repair'] is False
    assert record['k'] == 1
    assert record['ballast_programme'] is None
    assert record['ballast_delay_h'] is None
    assert record['resequencing']['lifts_moved'] is None
    assert record['resequencing']['list_margin_before'] is None
    assert record['resequencing']['list_margin_after'] is None

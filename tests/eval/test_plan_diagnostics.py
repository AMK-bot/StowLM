"""D74 reporting uses final checker evidence; D75 missing fields stay unknown."""
import pytest

from eval.plan_diagnostics import plan_diagnostics, summarise_plan_diagnostics


def programme():
    return dict(changeover_k=20, lifts_after_k=31, pumped_mass_t=80., abs_change_t=140.,
        pump_time_required_h=2., pump_time_available_h=4., pump_window_lifts=10,
        ballast_pump_m3_h=300., t_lift_s=60., cranes=2, gross_moves_per_hour=30.,
        heel_step_limit_t=5., heel_max_step_t=3., heel_max_ideal_step_t=9.,
        heel_path_feasible=True, heel_path_linearised_miss=False,
        rate_check_rule='port.ballast.transfer_rate')


def record(block=None, *, passed=True, **part_fields):
    return {'report_binding_valid': True,
        'report': {'passed': passed, 'parts': {'in_port': {'ran': True, 'error': None,
            'ballast_programme': programme() if block is None else block, **part_fields}}}}


def test_d74_retains_entire_final_checker_programme_and_chosen_vs_ideal():
    source = record()
    source['result'] = {'metrics': {'ballast_programme': {'pumped_mass_t': 999.}}}
    observed = plan_diagnostics(source)
    assert observed['ballast_programme'] == programme()
    assert observed['ballast_programme_status'] == 'evaluated'
    assert observed['ballast_programme_derived'] == {
        'pump_time_margin_h': 2., 'pump_time_required_available_ratio': .5,
        'heel_step_margin_t': 2., 'heel_chosen_limit_ratio': .6, 'heel_ideal_limit_ratio': 1.8}
    observed['ballast_programme']['pumped_mass_t'] = 0.
    assert source['report']['parts']['in_port']['ballast_programme']['pumped_mass_t'] == 80.


@pytest.mark.parametrize('source,status', [
    ({}, 'unknown'),
    (record(ran=False, error='strength equilibrium did not converge'), 'unevaluated'),
    (dict(record(), report_binding_valid=False), 'invalid_binding'),
    (record({**programme(), 'heel_max_step_t': None, 'heel_path_feasible': None}), 'partial'),
])
def test_unknown_partial_and_internal_error_are_explicit(source, status):
    result = plan_diagnostics(source)
    assert result['ballast_programme_status'] == status
    if status == 'unknown':
        assert result['ballast_programme'] is None
        assert all(value is None for value in result['ballast_programme_derived'].values())
    if status == 'partial':
        assert result['ballast_programme']['heel_max_step_t'] is None
        assert result['ballast_programme_derived']['heel_chosen_limit_ratio'] is None
        assert result['ballast_programme_derived']['heel_ideal_limit_ratio'] == 1.8


def test_zero_or_missing_denominators_never_create_ratio_or_measurement():
    result = plan_diagnostics(record({**programme(), 'pump_time_available_h': 0.,
        'heel_step_limit_t': 0., 'heel_max_step_t': None}))
    assert result['ballast_programme_derived']['pump_time_required_available_ratio'] is None
    assert result['ballast_programme_derived']['heel_ideal_limit_ratio'] is None
    assert result['ballast_programme_derived']['heel_chosen_limit_ratio'] is None
    assert result['ballast_programme_derived']['heel_step_margin_t'] is None


def test_documented_nested_in_port_report_is_supported():
    source = record()
    source['report']['parts']['stability'] = {'in_port': source['report']['parts'].pop('in_port')}
    assert plan_diagnostics(source)['ballast_programme'] == programme()


def test_d75_reserved_fields_and_recorded_metrics_roundtrip():
    missing = {'lifts_moved': None, 'list_margin_before': None,
               'list_margin_after': None, 'applied': None}
    assert plan_diagnostics(record())['resequencing'] == missing
    measured = dict(lifts_moved=4, list_margin_before=-.1, list_margin_after=.2, applied=True)
    source = record()
    source['result'] = {'metrics': {'resequencing': measured}}
    assert plan_diagnostics(source)['resequencing'] == measured
    assert plan_diagnostics({'resequencing': measured})['resequencing'] == measured


def test_d74_summary_distributions_count_missing_and_unresolved_separately():
    passing = record()
    r1_failure = record({**programme(), 'pump_time_required_h': 6.,
        'heel_max_step_t': None, 'heel_path_feasible': None}, passed=False,
        violations=[{'rule_id': 'port.ballast.transfer_rate', 'severity': 'hard',
            'message': 'changeover_time: archived checker required > available'}])
    r2_failure = record({**programme(), 'heel_path_feasible': False, 'heel_max_step_t': None}, passed=False)
    numerical_error = record(ran=False, error='numerical failure')
    summary = summarise_plan_diagnostics([passing, r1_failure, r2_failure, numerical_error, {}])
    assert summary['n_records'] == 5
    assert summary['n_plans'] == 4
    assert summary['status_counts'] == {'evaluated': 2, 'partial': 1, 'unevaluated': 1, 'unknown': 1}
    assert summary['distributions']['pumped_mass_t']['n'] == 1
    assert summary['distributions']['pumped_mass_t']['mean'] == 80.
    assert summary['distributions']['heel_max_step_t']['n'] == 1
    assert summary['attempted_distributions']['pumped_mass_t']['n'] == 3
    assert summary['attempted_distributions']['pump_time_required_available_ratio']['values'] == [.5, 1.5, .5]
    assert summary['r1_failure'] == {'n_evaluated': 3, 'n_failed': 1, 'share': pytest.approx(1/3)}
    assert summary['r2_failure'] == {'n_evaluated': 2, 'n_failed': 1, 'share': .5}
    assert summary['distributions']['pumped_mass_t']['median'] == 80.
    assert summary['n_unknown'] == 1
    assert summary['n_unevaluated'] == 1


def test_invalid_binding_is_retained_but_never_a_measured_distribution():
    source = dict(record(), report_binding_valid=False)
    summary = summarise_plan_diagnostics([source])
    assert summary['distributions']['pumped_mass_t']['n'] == 0
    assert summary['distributions']['pumped_mass_t']['mean'] is None
    assert summary['r1_failure']['share'] is None


def test_default_programme_distributions_exclude_repaired_plans():
    source = record()
    source['result'] = {'repaired': True}
    source['shippable'] = True
    own = summarise_plan_diagnostics([source])
    shipped = summarise_plan_diagnostics([source], population='shipped')
    assert own['distributions']['pumped_mass_t']['n'] == 0
    assert own['attempted_distributions']['pumped_mass_t']['n'] == 1
    assert shipped['distributions']['pumped_mass_t']['n'] == 1


def test_r1_failure_share_respects_checker_verdict_at_float_boundary():
    source = record({**programme(), 'pump_time_required_h': 4., 'pump_time_available_h': 4.,
        'heel_path_feasible': None, 'heel_max_step_t': None}, passed=False,
        violations=[{'rule_id': 'port.ballast.transfer_rate', 'severity': 'hard',
            'message': '[in_port phase=ballast_programme] changeover_time: exact required > available'}])
    assert summarise_plan_diagnostics([source])['r1_failure'] == {
        'n_evaluated': 1, 'n_failed': 1, 'share': 1.}


def test_ballast_delay_is_reported_without_inventing_a_failure():
    source = record({**programme(), 'pump_time_required_h': 6., 'ballast_delay_h': 2.})
    observed = plan_diagnostics(source)
    assert observed['ballast_delay_h'] == 2.
    summary = summarise_plan_diagnostics([source])
    assert summary['distributions']['ballast_delay_h']['values'] == [2.]
    assert summary['r1_failure'] == {'n_evaluated': 1, 'n_failed': 0, 'share': 0.}
    assert source['report']['passed'] is True


@pytest.mark.parametrize('delay', [None, float('nan'), -1.])
def test_unavailable_ballast_delay_remains_unknown_without_changing_programme_status(delay):
    block = programme()
    if delay is not None:
        block['ballast_delay_h'] = delay
    source = record(block)
    assert plan_diagnostics(source)['ballast_delay_h'] is None
    assert plan_diagnostics(source)['ballast_programme_status'] == 'evaluated'
    assert summarise_plan_diagnostics([source])['distributions']['ballast_delay_h']['n'] == 0


def test_ballast_delay_requires_evaluated_evidence_and_keeps_own_plan_default():
    source = record({**programme(), 'ballast_delay_h': 0.})
    source['result'] = {'repaired': True}
    assert summarise_plan_diagnostics([source])['distributions']['ballast_delay_h']['n'] == 0
    source['shippable'] = True
    assert summarise_plan_diagnostics([source], population='shipped')['distributions']['ballast_delay_h']['values'] == [0.]
    source['report']['parts']['in_port']['error'] = 'numerical failure'
    assert plan_diagnostics(source)['ballast_delay_h'] is None

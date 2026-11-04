"""Amendment regressions on synthetic rows; no maps or search are opened."""
import pytest

from experiments.mvp.pipeline.sealed import report


def row(iid='a', arm='pipeline', **changes):
    value = dict(map_id=iid, cohort='envelope24', ship_id='feeder_500', arm=arm,
        map_sha256='a'*64, status='checked_plan', level='L0', passed=True,
        full_checker_pass=True, audit_verified=True, audit_fresh_process=True,
        instruction_recheck=True if arm == 'instruction' else None,
        instruction_type='max_tiers' if arm == 'instruction' else None,
        counterfactual_instruction_recheck=False if arm == 'pipeline' else None,
        lifts_total=8, lifts_current_port=4, lifts_projected_later_port=4,
        weighted_lifts=100, wall_s=.01, final_cells={'box1': ['a'], 'box2': ['b']})
    value.update(changes)
    return value


def write(tmp_path, rows, **metadata):
    maps = {(r['cohort'], r['map_id']): dict(cohort=r['cohort'], map_id=r['map_id'],
                                           ship_id=r['ship_id']) for r in rows}
    return report.write_report(rows, tmp_path, metadata=dict(expected_maps=list(maps.values()), **metadata))


def test_amend_exact_binomial_intervals_are_not_wald():
    assert report.binomial95(0, 10) == pytest.approx([0, 1-.025**.1])
    assert report.binomial95(10, 10) == pytest.approx([.025**.1, 1])
    assert report.binomial95(1, 1) == pytest.approx([.025, 1])
    assert report.binomial95(0, 0) is None


def test_amend_binding_denominator_joins_baseline_and_requires_fresh_audit(tmp_path):
    rows = []
    for iid, counter, instructed, audited in [('a', False, True, True),
                                             ('b', True, True, True),
                                             ('c', False, False, True),
                                             ('d', False, True, False)]:
        rows += [row(iid, counterfactual_instruction_recheck=counter,
                     audit_verified=audited),
                 row(iid, 'twostep'),
                 row(iid, 'instruction', passed=instructed,
                     final_cells={'box1': ['c'], 'box2': ['b']}, lifts_total=7)]
    result = write(tmp_path, rows)
    block = result['cohorts']['envelope24']['instruction_binding']
    assert block['rule_broken_maps'] == 2
    assert block['binding_obedient_passes'] == 1
    assert block['binding_obedient_exact95'] == pytest.approx(report.binomial95(1, 2))
    assert block['already_obeyed'] == 1
    assert block['counterfactual_unavailable'] == 1
    assert block['by_type']['max_tiers']['binding_obedient_passes'] == 1
    assert block['plan_difference']['by_map']['a'] == 1
    assert block['signed_lift_change']['lifts_total']['mean_difference_left_minus_right'] == -1
    assert block['already_obeyed_noise_floor']['lifts_total']['mean_difference_left_minus_right'] == -1


def test_amend_binding_accepts_audited_relaxed_baseline_but_not_unaudited_instruction(tmp_path):
    rows = [row(status='relaxed_plan', level='L1', passed=False),
            row(arm='twostep'), row(arm='instruction', audit_fresh_process=False)]
    block = write(tmp_path, rows)['cohorts']['envelope24']['instruction_binding']
    assert block['rule_broken_maps'] == 1
    assert block['binding_obedient_passes'] == 0


def test_amend_pass_table_sensitivity_and_exact_intervals(tmp_path):
    rows = []
    for iid, p, t in [('a', True, True), ('b', True, False),
                       ('c', False, True), ('d', False, False)]:
        rows += [row(iid, passed=p), row(iid, 'twostep', passed=t),
                 row(iid, 'instruction'), row(iid, 'twostep_300')]
    result = write(tmp_path, rows, sensitivity_arms=['twostep_300'])
    env = result['cohorts']['envelope24']
    table = env['pass_tables']['pipeline_vs_twostep']
    assert [table[k] for k in ('both_pass', 'left_only', 'right_only', 'neither_pass')] == [1, 1, 1, 1]
    assert 'pipeline_vs_twostep_300' in env['comparisons']
    assert env['arms']['pipeline']['passes_L0_exact95'] == pytest.approx(report.binomial95(2, 4))
    assert '95%' in (tmp_path/'REPORT.md').read_text()


def test_amend_ship_splits_never_merge_envelope_with_legacy(tmp_path):
    rows = [row('tiny500', cohort='tiny15'), row('tiny1700', cohort='tiny15', ship_id='feeder_1700'),
            row('small800', cohort='small30', ship_id='feeder_800'),
            row('env500'), row('env800', ship_id='feeder_800')]
    result = write(tmp_path, rows)
    tiny = result['cohorts']['tiny15']['ship_groups']
    assert tiny['500-800_TEU']['arms']['pipeline']['passes_L0'] == 1
    assert tiny['1700_TEU']['arms']['pipeline']['passes_L0'] == 1
    assert set(result['cohorts']['envelope24']['by_ship']) == {'feeder_500', 'feeder_800'}
    assert result['cohorts']['small30']['ship_groups']['500-800_TEU']['expected_maps'] == 1


def test_amend_primary_is_unweighted_and_does_not_switch_on_inconclusive_interval(tmp_path):
    rows = []
    for i in range(8):
        rows += [row(str(i), lifts_total=8, weighted_lifts=1),
                 row(str(i), 'twostep', lifts_total=8, weighted_lifts=1000),
                 row(str(i), 'instruction')]
    for i in range(8):
        rows += [row('s'+str(i), cohort='small30', lifts_total=1),
                 row('s'+str(i), 'twostep', cohort='small30', lifts_total=100)]
    result = write(tmp_path, rows)
    primary = result['primary_lift_result']
    assert primary['cohort'] == 'envelope24'
    assert primary['metric'] == 'lifts_total'
    assert not primary['quotable']
    assert primary['reason'] == 'paired interval does not establish fewer extra crane lifts'
    assert primary['lifts_current_port']['paired_bootstrap95_mean_difference'] == [0, 0]


def test_amend_primary_saving_requires_interval_and_current_port_beside_it(tmp_path):
    rows = []
    for i in range(8):
        rows += [row(str(i), lifts_total=5, lifts_current_port=4),
                 row(str(i), 'twostep', lifts_total=10, lifts_current_port=4),
                 row(str(i), 'instruction')]
    result = write(tmp_path, rows)
    primary = result['primary_lift_result']
    assert report._lift_claim(result['cohorts'], True)['quotable']
    assert primary['statistically_supported']
    assert primary['saving_scope'] == 'projected (D15); current-port saving not established'
    assert primary['lifts_current_port']['paired_bootstrap95_mean_difference'] == [0, 0]


def test_amend_reader_and_engine_failures_are_distinct_and_errors_provisional(tmp_path):
    rows = [row(), row(arm='twostep'),
            row(arm='instruction', status='error', passed=False, reader_failure=True,
                engine_failure=False, reading_status='error')]
    result = write(tmp_path, rows, mode='sealed')
    kind = result['cohorts']['envelope24']['instruction_types']['max_tiers']
    assert kind['reader_failures'] == 1 and kind['engine_failures'] == 0
    assert result['provisional'] and not result['complete']
    text = (tmp_path/'REPORT.md').read_text()
    assert 'planning step from a clean load list, templated sentences' in text
    assert 'relocation policies' in text
    assert 'weight-tolerance sensitivity' in text


def test_amend_small_fallback_is_fixed_before_outcomes_and_names_its_ship_population(tmp_path):
    rows = []
    for i in range(7):
        rows += [row(str(i), lifts_total=1), row(str(i), 'twostep', lifts_total=100),
                 row(str(i), 'instruction')]
    for ship in ('feeder_500', 'feeder_1700'):
        for i in range(2):
            rows += [row(ship+str(i), cohort='small30', ship_id=ship, lifts_total=10),
                     row(ship+str(i), 'twostep', cohort='small30', ship_id=ship,
                         lifts_total=100 if ship == 'feeder_1700' else 10)]
    primary = write(tmp_path, rows)['primary_lift_result']
    assert primary['cohort'] == 'small30'
    assert primary['ship_population'] == '500-800 TEU'
    assert not primary['medium_lift_claim_allowed']
    assert primary['lifts_total']['map_ids'] == ['feeder_5000', 'feeder_5001']
    assert not primary['statistically_supported']


def test_amend_missing_sensitivity_and_error_ship_denominators_stay_visible(tmp_path):
    rows = [row(), row(arm='twostep'), row(arm='instruction', reading_wall_s=.4),
            row('missing1700', ship_id='feeder_1700', passed=False, status='error')]
    result = write(tmp_path, rows, registered_arms=['pipeline', 'twostep', 'instruction', 'twostep_300'])
    env = result['cohorts']['envelope24']
    assert result['expected_rows'] == 8
    assert not result['complete']
    assert env['arms']['twostep_300']['missing_rows'] == 2
    assert env['by_ship']['feeder_1700']['arms']['twostep']['expected_maps'] == 1
    assert env['by_ship']['feeder_1700']['arms']['twostep']['passes_L0_exact95'] == pytest.approx([0, .975])
    assert env['arms']['instruction']['reading_wall_s']['total'] == .4
    assert 'reading_wall_s' in (tmp_path/'per-map.csv').read_text().splitlines()[0]


def test_amend_empty_development_envelope_keeps_all_instruction_report_types(tmp_path):
    result = write(tmp_path, [row('tiny', cohort='tiny15')])
    env = result['cohorts']['envelope24']
    assert set(env['instruction_types']) == {'close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch'}
    assert set(env['instruction_binding']['by_type']) == set(env['instruction_types'])
    assert all(cell['rows'] == 0 for cell in env['instruction_types'].values())
    assert not result['instruction_assignment_complete']


@pytest.mark.parametrize('missing_count', [1, 8])
def test_amend_quotation_requires_complete_current_port_interval(tmp_path, missing_count):
    rows = []
    for i in range(8):
        rows += [row(str(i), lifts_total=5, lifts_current_port=None if i < missing_count else 4),
                 row(str(i), 'twostep', lifts_total=10, lifts_current_port=4),
                 row(str(i), 'instruction')]
    cohorts = write(tmp_path, rows)['cohorts']
    primary = report._lift_claim(cohorts, True)
    assert primary['statistically_supported']
    assert primary['lifts_current_port']['paired_bootstrap95_mean_difference'] == (None if missing_count == 8 else [0, 0])
    assert not primary['quotable']
    assert not primary['current_port_reporting_complete']
    assert primary['medium_cohort_eligible']
    assert not primary['medium_quotable']

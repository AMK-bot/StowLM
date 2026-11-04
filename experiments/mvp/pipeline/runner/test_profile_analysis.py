"""Profile decisions use matched full passes rather than easier passing subsets."""
from copy import deepcopy
import json

from .profile_analysis import analyze, main, paired_costs, recommend, summarize


def row(map_id='a', label='60', mode='none', *, passed=True, lifts=20, engine=50):
    return dict(job_id=f'{map_id}-{label}-{mode}', map_id=map_id, size_class='medium',
                budget_label=label, budget_s=None if label == 'default' else float(label),
                finalize_s=None if label == 'default' else float(label) / 3,
                seed=0, config={'assignment_tries': 1}, rule_mode=mode,
                status='checked_plan' if passed else 'no_checked_plan', full_pass=passed,
                first_legal_s=engine / 2 if passed else None, total_s=engine + 10,
                engine_s=engine, lifts=lifts if passed else None,
                start={'load1': 22, 'enabled_cores': 12},
                end={'load1': 29, 'enabled_cores': 12})


def cohort():
    rows = []
    for map_id in ('a', 'b'):
        for mode in ('none', 'one_port_per_hatch'):
            for label in ('60', '120', '300', 'default'):
                # 120 retains default passes, while 300 buys four lifts.
                rows.append(row(map_id, label, mode, passed=not (map_id == 'b' and label == '60'),
                                lifts={'60': 20, '120': 18, '300': 14, 'default': 13}[label],
                                engine={'60': 50, '120': 101, '300': 200, 'default': 1001}[label]))
    return rows


def test_pass_means_exclude_relaxed_and_nonfinite_lifts():
    rows = [row('a'), row('b', passed=False)]
    relaxed = row('c', passed=False)
    relaxed.update(status='relaxed_plan', full_pass=False, lifts=1)
    rows.append(relaxed)
    lying = row('d', lifts=1)
    lying['status'] = 'relaxed_plan'
    rows.append(lying)
    summary = summarize(rows)[0]
    assert summary['passes'] == 1
    assert summary['attempts'] == 4
    assert summary['relaxed'] == 2
    assert summary['lifts_passing'] == dict(n=1, mean=20, median=20, min=20, max=20)
    assert summary['load']['start_load1']['mean'] == 22


def test_pair_cost_keeps_pass_losses_separate_from_lift_cost():
    rows = [row('a', lifts=30), row('a', 'default', lifts=25),
            row('b', passed=False), row('b', 'default', lifts=100),
            row('c', lifts=5), row('c', 'default', passed=False), row('d')]
    cost = paired_costs(rows)[0]
    assert cost['paired_attempts'] == 3
    assert cost['passes_lost'] == cost['passes_gained'] == 1
    assert cost['extra_lifts_short_minus_default']['mean'] == 5
    assert cost['both_pass'] == 1
    assert cost['missing_reference'] == 1


def test_complete_cohort_selects_shortest_retaining_passes_and_paired_lifts():
    rows = cohort()
    decision = recommend(rows, expected_jobs=[r['job_id'] for r in rows])
    assert decision['provisional'] is False
    assert decision['live']['budget_label'] == '120'
    assert decision['live']['engine_timeout_s'] == 150
    assert decision['standard']['budget_label'] == '300'
    assert decision['standard']['paired_mean_lifts'] == 14
    assert decision['standard']['paired_lift_arms'] == 4
    assert decision['live']['engine_kwargs']['finalize_s'] == 40


def test_missing_default_never_claims_calibration():
    rows = [r for r in cohort() if not (r['map_id'] == 'b' and r['budget_label'] == 'default')]
    decision = recommend(rows)
    assert decision['provisional'] is True
    assert decision['live'] is None
    assert len(decision['completeness']['medium_missing_cells']) == 2


def test_manifest_extra_jobs_prevent_pilot_becoming_final():
    rows = cohort()
    ids = [r['job_id'] for r in rows] + ['new-map-60-none']
    decision = recommend(rows, expected_jobs=ids)
    assert decision['provisional'] is True
    assert decision['completeness']['missing_jobs'] == ['new-map-60-none']


def test_duplicate_checkpoints_and_interrupted_attempts_do_not_inflate_passes():
    first = row()
    retry = deepcopy(first)
    retry.update(full_pass=False, status='no_checked_plan', lifts=None)
    interrupted = row('interrupted')
    interrupted['status'] = 'interrupted'
    summary = summarize([first, retry, interrupted])[0]
    assert summary['attempts'] == 1
    assert summary['passes'] == 0


def test_standard_does_not_select_default_over_engine_cap():
    rows = cohort()
    for r in rows:
        if r['budget_label'] != 'default':
            r['engine_s'] = 901
    decision = recommend(rows)
    assert decision['standard'] is None
    assert decision['provisional'] is True


def test_data_hash_order_independent_and_cli_writes_exact_recommendation(tmp_path):
    rows = cohort()
    assert analyze(rows)['data_sha256'] == analyze(list(reversed(rows)))['data_sha256']
    source = tmp_path / 'rows'
    source.mkdir()
    for r in rows:
        (source / f"{r['job_id']}.json").write_text(json.dumps(r))
    target = tmp_path / 'output'
    assert main(['--rows', str(source), '--out', str(target)]) == 0
    summary = json.loads((target / 'summary.json').read_text())
    recommendation = json.loads((target / 'recommendation.json').read_text())
    assert summary['recommendation'] == recommendation
    assert recommendation['live']['budget_label'] == '120'


def test_missing_rule_arm_is_incomplete_even_with_every_budget():
    rows = [r for r in cohort() if not (r['map_id'] == 'a' and r['rule_mode'] == 'one_port_per_hatch')]
    decision = recommend(rows)
    assert decision['provisional'] is True
    assert len(decision['completeness']['medium_missing_cells']) == 4


def test_censored_default_is_unknown_instead_of_failed_pass():
    rows = cohort()
    missing = next(r for r in rows if r['budget_label'] == 'default')
    missing.update(censored=True, status='interrupted', full_pass=False)
    result = analyze(rows, expected_jobs=rows)
    assert result['excluded']['censored'] == 1
    assert result['measured_rows'] == len(rows) - 1
    assert result['recommendation']['provisional'] is True
    assert sum(cell['attempts'] for cell in result['budget_table']) == len(rows) - 1


def test_live_prefers_no_default_pass_loss_when_pass_counts_tie():
    rows = cohort()
    for r in rows:
        # 60 and 120 have equal pass counts but only 120 preserves default maps.
        passed = (r['map_id'] == ('a' if r['budget_label'] == '60' else 'b'))
        r.update(full_pass=passed, status='checked_plan' if passed else 'no_checked_plan')
    decision = recommend(rows)
    assert decision['live']['budget_label'] == '120'
    assert decision['standard']['budget_label'] == '300'


def test_all_failing_measurements_do_not_calibrate_a_profile():
    rows = cohort()
    for r in rows:
        r.update(full_pass=False, status='no_checked_plan', lifts=None)
    decision = recommend(rows)
    assert decision['provisional'] is True
    assert decision['live'] is None
    assert decision['standard'] is None


def test_no_default_reference_reports_unknown_paired_lift_cost():
    cost = paired_costs([row()])[0]
    assert cost['paired_attempts'] == 0
    assert cost['missing_reference'] == 1
    assert cost['extra_lifts_short_minus_default']['mean'] is None
    result = analyze(cohort())
    assert result['recommendation']['data_sha256'] == result['data_sha256']

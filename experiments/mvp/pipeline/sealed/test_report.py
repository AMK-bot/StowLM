import csv
import json

import pytest

from experiments.mvp.pipeline.sealed.report import compare, passed, write_report


def row(iid='a', arm='pipeline', cohort='envelope24', **changes):
    value = dict(map_id=iid, arm=arm, cohort=cohort, map_sha256='a'*64,
                 status='checked_plan', level='L0', passed=True, full_checker_pass=True,
                 audit_verified=True, audit_fresh_process=True,
                 instruction_recheck=True if arm == 'instruction' else None,
                 weighted_lifts=10, lifts_total=10, lifts_current_port=4,
                 lifts_projected_later_port=6, wall_s=1)
    value.update(changes)
    return value


def metadata():
    return dict(mode='dry-run', fixture_surrogate=True,
                expected_maps=[dict(map_id='a', cohort='envelope24')])


def test_full_report_shape_and_csv(tmp_path):
    rows = [row(arm=arm) for arm in ('pipeline', 'twostep', 'instruction')]
    report = write_report(rows, tmp_path, metadata=metadata())
    assert report['complete']
    assert not report['sealed_claim_eligible']
    assert set(report['cohorts']) == {'tiny15', 'small30', 'envelope24'}
    assert report['cohorts']['envelope24']['arms']['pipeline']['passes_L0'] == 1
    assert json.loads((tmp_path/'report.json').read_text()) == report
    with (tmp_path/'per-map.csv').open() as stream:
        csv_rows = list(csv.DictReader(stream))
    assert len(csv_rows) == 3
    assert csv_rows[0]['lifts_current_port'] == '4'
    assert csv_rows[0]['lifts_projected_later_port'] == '6'
    text = (tmp_path/'REPORT.md').read_text()
    assert 'Out of envelope' in text
    assert 'projected later-port' in text


def test_relaxed_rejected_and_missing_maps_stay_in_denominator(tmp_path):
    rows = [row('a', status='out_of_envelope', level=None),
            row('b', status='relaxed_plan', level='L2'),
            row('c', full_checker_pass=False)]
    report = write_report(rows, tmp_path)
    cell = report['cohorts']['envelope24']['arms']['pipeline']
    assert cell['passes_L0'] == 0
    assert cell['pass_fraction'] == 0
    assert cell['out_of_envelope'] == 1
    assert cell['relaxed_by_level'] == {'L2': 1}
    assert cell['missing_rows'] == 21
    assert not report['complete']


def test_pass_is_l0_checker_and_instruction_gated():
    assert passed(row())
    assert passed(row(level=0))
    assert not passed(row(level='L1'))
    assert not passed(row(passed=False))
    assert not passed(row(status='error'))
    assert not passed(row(audit_verified=False))
    assert not passed(row(audit_fresh_process=False))
    assert passed(row(counterfactual_instruction_recheck=False))
    assert not passed(row(arm='instruction', instruction_recheck=False))
    assert not passed(row(arm='instruction', instruction_recheck=None))


def test_pairing_and_seeded_interval_are_map_level():
    left = {'a': row('a', weighted_lifts=1), 'b': row('b', weighted_lifts=3),
            'c': row('c', weighted_lifts=50), 'd': row('d', weighted_lifts=5)}
    right = {'a': row('a', 'twostep', weighted_lifts=3),
             'b': row('b', 'twostep', weighted_lifts=3),
             'c': row('c', 'twostep', status='no_checked_plan'),
             'd': row('d', 'twostep', weighted_lifts=4)}
    result = compare(left, right)
    assert result == compare(left, right)
    assert result['map_ids'] == ['a', 'b', 'd']
    assert (result['wins'], result['ties'], result['losses']) == (1, 1, 1)
    assert result['mean_difference_left_minus_right'] == pytest.approx(-1/3)
    assert result['paired_bootstrap95_mean_difference'][0] <= -1/3
    assert result['paired_bootstrap95_mean_difference'][1] >= -1/3


def test_no_pair_or_missing_metric_has_no_invented_number():
    assert compare({}, {})['paired_bootstrap95_mean_difference'] is None
    result = compare({'a': row(weighted_lifts=None)}, {'a': row(arm='twostep')})
    assert result['mutual_passes'] == 1
    assert result['n'] == 0
    assert result['missing_metric_map_ids'] == ['a']
    assert not result['available']
    zero = compare({'a': row(weighted_lifts=0)}, {'a': row(arm='twostep', weighted_lifts=0)})
    assert zero['saving_fraction'] is None
    assert zero['paired_bootstrap95_saving_fraction'] is None
    assert zero['paired_bootstrap95_mean_difference'] == [0, 0]


def test_passing_means_do_not_include_failure_or_relaxed(tmp_path):
    rows = [row(weighted_lifts=12), row('b', weighted_lifts=100, level='L1', status='relaxed_plan')]
    report = write_report(rows, tmp_path)
    means = report['cohorts']['envelope24']['arms']['pipeline']['lift_means_over_passes']
    assert means['weighted_lifts'] == {'n': 1, 'mean': 12}


@pytest.mark.parametrize('rows,match', [
    ([row(), row()], 'duplicate'),
    ([row(arm='instruction', cohort='tiny15')], 'registered'),
    ([row(weighted_lifts=float('nan'))], 'finite'),
    ([row(wall_s=-1)], 'finite'),
    ([row(), row(arm='twostep', map_sha256='b'*64)], 'hash differs'),
])
def test_bad_rows_are_not_silently_dropped(tmp_path, rows, match):
    with pytest.raises(ValueError, match=match):
        write_report(rows, tmp_path)


def test_expected_population_mismatch_refuses(tmp_path):
    with pytest.raises(ValueError, match='expected map'):
        write_report([row('b')], tmp_path, metadata=metadata())


def test_counts_equal_but_different_map_pairs_are_incomplete(tmp_path):
    report = write_report([row(), row('b', 'twostep'), row(arm='instruction')], tmp_path,
                          metadata={'expected_cohort_sizes': dict(tiny15=0, small30=0, envelope24=1)})
    assert not report['complete']

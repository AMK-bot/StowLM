"""Ablation reporting must preserve unavailable arms and honest denominators."""
import json

import pytest

from experiments.mvp.reader.brief_ablation import RUN, report_text, summarize


def _write(root, identifier='sample', **changes):
    row = dict(instance_id=identifier, input='experiments/mvp/corpus/val/small/ship/map.json',
               passed=True, weighted=8., blocked_finish=dict(passed=True, weighted=10.),
               twostep_finish=dict(passed=True, weighted=15.), runs=[dict(start='blocked')])
    row.update(changes)
    path = root / RUN / f'{identifier}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row))
    return dict(instance_id=identifier, baseline_file=str(path.relative_to(root)))


def test_checked_construction_comparison_is_not_empty_portfolio_ablation(tmp_path):
    case = _write(tmp_path)
    summary = summarize([case], tmp_path)
    assert summary['blocked_vs_twostep']['mean_moves_saved'] == 5.
    assert summary['portfolio_vs_blocked']['mean_moves_saved'] == 2.
    assert summary['exact_standing_vs_empty_available'] is False
    assert summary['fresh_audit'] is False
    assert 'not fresh audits or an exact' in report_text(summary)
    assert summary['rows'][0]['baseline_sha256']


def test_missing_and_failed_maps_stay_in_denominator(tmp_path):
    failed = _write(tmp_path, 'failed', passed=False, weighted=1.,
                    blocked_finish=dict(passed=False, weighted=2.))
    summary = summarize([failed, 'missing'], tmp_path)
    assert summary['cohort_n'] == 2
    assert summary['stored_n'] == 1
    assert summary['blocked_checked_n'] == summary['portfolio_checked_n'] == 0
    assert summary['twostep_checked_n'] == 1
    assert summary['blocked_vs_twostep']['paired_n'] == 0
    assert summary['blocked_vs_twostep']['mean_moves_saved'] is None


def test_nonfinite_or_boolean_cost_cannot_count_as_pass(tmp_path):
    case = _write(tmp_path, weighted=float('nan'),
                  blocked_finish=dict(passed=True, weighted=True))
    summary = summarize([case], tmp_path)
    assert summary['portfolio_checked_n'] == summary['blocked_checked_n'] == 0


def test_forbidden_paths_and_duplicate_cohort_rejected(tmp_path):
    case = _write(tmp_path)
    with pytest.raises(ValueError, match='Duplicate'):
        summarize([case, case], tmp_path)
    case['baseline_file'] = 'experiments/mvp/frontier/runs/test/sample.json'
    with pytest.raises(ValueError, match='val-0.9'):
        summarize([case], tmp_path)


def test_mislabeled_test_input_is_not_loaded_as_validation(tmp_path):
    case = _write(tmp_path, input='experiments/mvp/corpus/test-v3/small/map.json')
    summary = summarize([case], tmp_path)
    assert summary['stored_n'] == 0
    assert summary['rows'][0]['status'] == 'unavailable'

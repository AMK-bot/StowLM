"""Evidence-only validator regression tests; no planners or numerical jobs."""
from copy import deepcopy
import pytest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from validate_twostep_v44 import validate_comparison, validate_pairs, validate_published_rows, SMALL


def sample_row(version='4.4.0'):
    return dict(input_sha256='a'*64,shippable=False,report=None,
                result=dict(status='dead_end',planner_version=version))


def sample_comparison():
    signature = dict(checker='missing_report',shippable=False,first_rule=None,
        counts=dict(relocations=None,restow_lifts=None,hatch_cover_lifts=None,
                    future_restow_lifts=None,future_hatch_cover_lifts=None,weighted_nonproductive=None),
        planner_status='dead_end')
    return dict(n=1,changed_n=0,rows=[dict(input_sha256='a'*64,before=signature,
                after=deepcopy(signature),changed_fields=[],before_version='4.3.0',after_version='4.4.0')])


def test_comparison_recomputes_failed_attempt_signature():
    validate_comparison(sample_comparison(),[sample_row('4.3.0')],[sample_row()])


@pytest.mark.parametrize('field', ['checker','shippable','counts','first_rule','planner_status'])
def test_comparison_rejects_fabricated_signature(field):
    value = sample_comparison()
    value['rows'][0]['after'][field] = 'fabricated'
    value['rows'][0]['changed_fields'] = [field]
    value['changed_n'] = 1
    with pytest.raises(AssertionError):
        validate_comparison(value,[sample_row('4.3.0')],[sample_row()])


def test_comparison_cannot_drop_failed_denominator():
    with pytest.raises(AssertionError):
        validate_comparison(dict(n=0,changed_n=0,rows=[]),[sample_row('4.3.0')],[sample_row()])


def test_pair_summary_refuses_missing_ship_groups():
    with pytest.raises(AssertionError):
        validate_pairs(dict(paired_mutual_pass=[]),{})


def empty_pairs():
    actual = dict(attempts=1,mutual_passes=0,twostep_checker_passes=0,solver_checker_passes=0,
        twostep_passes=0,solver_passes=0,delta_mean=None,twostep_wins=0,ties=0,solver_wins=0,
        mutual_sha256=[],twostep_sum=None,solver_sum=None,bound_sum=None,
        twostep_mean=None,solver_mean=None,bound_mean=None)
    pairs = {g:deepcopy(actual) for g in ('all','feeder_500','feeder_800','feeder_1700')}
    row = dict(frozen_n=1,mutual_n=0,excluded_from_mutual_n=1,twostep_pass=0,solver_pass=0,
        twostep_shippable=0,solver_shippable=0,mean_delta_twostep_minus_solver=None,
        twostep_wins=0,ties=0,twostep_losses=0,pairs=[],
        twostep=dict(n=0,sum=None,mean=None),solver=dict(n=0,sum=None,mean=None),bound=dict(n=0,sum=None,mean=None))
    summary = dict(paired_mutual_pass=[dict(deepcopy(row),ship=g) for g in ('overall','feeder_500','feeder_800','feeder_1700')])
    return summary,pairs


def test_pair_empty_objectives_stay_missing_with_failed_denominator():
    summary,pairs = empty_pairs()
    validate_pairs(summary,pairs)


def test_pair_validator_rejects_invented_zero_cost_for_no_mutual_pass():
    summary,pairs = empty_pairs()
    summary['paired_mutual_pass'][0]['twostep']['mean'] = 0
    with pytest.raises(AssertionError):
        validate_pairs(summary,pairs)


def test_small_summary_allows_only_expected_provenance_enrichment():
    row = sample_row()
    enriched = dict(row,reference_epoch='two-step 4.4.0 source-pinned',reference_run_folder=SMALL)
    validate_published_rows({row['input_sha256']:row},[enriched],rung='small')
    enriched['invented'] = True
    with pytest.raises(AssertionError):
        validate_published_rows({row['input_sha256']:row},[enriched],rung='small')


def test_small_summary_refuses_wrong_reference_provenance():
    row = sample_row()
    enriched = dict(row,reference_epoch='two-step 4.3.0 source-pinned',reference_run_folder=SMALL)
    with pytest.raises(AssertionError):
        validate_published_rows({row['input_sha256']:row},[enriched],rung='small')


def test_comparison_accepts_multiple_changed_fields_after_sorted_json():
    import json
    before = sample_row('4.3.0')
    after = sample_row()
    after['shippable'] = True
    after['result']['status'] = 'ok'
    comparison = sample_comparison()
    comparison['rows'][0]['after']['shippable'] = True
    comparison['rows'][0]['after']['planner_status'] = 'ok'
    comparison['rows'][0]['changed_fields'] = ['shippable','planner_status']
    comparison['changed_n'] = 1
    loaded = json.loads(json.dumps(comparison,sort_keys=True))
    validate_comparison(loaded,[before],[after])

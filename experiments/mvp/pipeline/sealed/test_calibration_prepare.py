from copy import deepcopy

import pytest

from .calibration_prepare import summarize
from .common import HarnessError


def fixture():
    maps = [dict(map_id=str(i), path=f'/dev/{i}', sha256='a'*64, cohort='envelope24') for i in range(32)]
    rows = [dict(map_id=str(i), arm='pipeline', cohort='envelope24', audit_verified=True,
        audit_fresh_process=True, audit_pid=100+i, planning_pid=200+i, passed=i < 7,
        full_checker_pass=True, level=0,
        calibration_rechecks={'max_tiers': {'counterfactual_instruction_recheck': False}})
        for i in range(25)]
    stopped = dict(lost_in_flight=[dict(map_id=str(i), phase='audit' if i == 25 else 'planning')
                                  for i in range(25,29)])
    return dict(maps=maps), rows, stopped


def test_missing_and_failed_maps_never_shrink_denominator():
    config, rows, stopped = fixture()
    result = summarize(config, rows, stopped)
    assert result['denominator'] == 32
    assert result['binding'] == result['fresh_l0_baselines'] == 7
    assert result['maximum_after_continuation'] == 14
    assert result['feasible_without_repeating_completed_trials'] is False
    assert result['release_blocked'] is True
    assert len(result['continuation']) == 7
    assert result['continuation_time']['planning_trials'] == 6
    assert result['continuation_time']['audit_trials'] == 7


def test_failed_or_same_process_audit_cannot_create_binding():
    config, rows, stopped = fixture()
    rows[0]['audit_pid'] = rows[0]['planning_pid']
    rows[1]['audit_verified'] = False
    assert summarize(config, rows, stopped)['binding'] == 5


def test_duplicate_baseline_rejected_before_selecting_passing_trial():
    config, rows, stopped = fixture()
    replacement = deepcopy(rows[-1])
    replacement['passed'] = True
    with pytest.raises(HarnessError, match='duplicate'):
        summarize(config, rows+[replacement], stopped)


def test_exact_majority_threshold_stays_seventeen():
    config, rows, stopped = fixture()
    for row in rows[:17]:
        row['passed'] = True
    result = summarize(config, rows, stopped)
    assert result['binding'] == 17
    assert result['release_blocked'] is False


def test_lost_rows_are_not_retained_as_failed_or_rerun_rows():
    config, rows, stopped = fixture()
    result = summarize(config, rows, stopped)
    assert result['completed_baselines'] == 25
    assert result['planner_retries'] == 0
    assert sum(x['prior_status'] == 'lost' for x in result['continuation']) == 4
    assert sum(x['prior_status'] == 'not_started' for x in result['continuation']) == 3
    assert not result['continuation'][0]['planner_needed']

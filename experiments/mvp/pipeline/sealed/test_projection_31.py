"""Runtime arithmetic and censoring regressions on synthetic timing rows."""
import copy

import pytest

from . import projection_31 as projection
from .common import HarnessError


def row(iid='m', arm='pipeline', cohort='tiny15', *, child=100., audit=20., overhead=10., wait=5., **changes):
    value = dict(map_id=iid,arm=arm,cohort=cohort,status='checked_plan',
        child_elapsed_s=child,audit_elapsed_s=audit,driver_admission_wait_s=wait,
        driver_elapsed_s=child+(audit or 0)+overhead+wait,
        child_timed_out=False,child_timeout_s=1200.,audit_timed_out=False,audit_timeout_s=300.)
    value.update(changes)
    return value


def test_weighted_service_is_divided_by_workers_exactly_once():
    rows = [row(arm='pipeline'),row(arm='twostep')]
    result = projection.project(rows,targets={('tiny15','pipeline'):8,('tiny15','twostep'):8},
        workers=4,serial_reader_reserve_s=0,optimistic_speedup=1)
    base = result['loaded_empirical_anchor']
    assert base['weighted_worker_seconds'] == 16*130
    assert base['ideal_slot_seconds'] == 16*130/4
    assert base['scheduling_tail_s'] == 130
    assert base['projected_hours_excluding_admission_and_new_guard'] == pytest.approx((520+130)/3600)
    assert result['registered_trials_by_arm'] == dict(pipeline=8,twostep=8)
    assert result['projection_hours_range'] is None
    assert result['planning_only_hours_range'] is not None


def test_admission_is_not_scaled_into_worker_service_or_hidden_as_zero():
    targets = {('tiny15','pipeline'):8}
    first = projection.project([row(wait=5)],targets=targets)
    second = projection.project([row(wait=500)],targets=targets)
    assert first['planning_only_hours_range'] == second['planning_only_hours_range']
    assert first['per_cohort_arm'][0]['admission_wait_s']['mean'] == 5
    assert second['per_cohort_arm'][0]['admission_wait_s']['mean'] == 500
    assert 'unbounded' in first['assumptions']['admission_wait']


def test_censored_deadlines_are_retained_not_halved_or_dropped():
    timed = row(arm='instruction',cohort='envelope24',child=1200,audit=None,
        overhead=2,child_timed_out=True,status='error')
    result = projection.project([timed],targets={('envelope24','instruction'):24},
        workers=4,serial_reader_reserve_s=0,optimistic_speedup=2)
    assert result['per_cohort_arm'][0]['child_deadlines'] == 1
    assert result['per_cohort_arm'][0]['audit_executed_rows'] == 0
    assert result['per_cohort_arm'][0]['service_s']['mean'] == 1202
    for scenario in result['scenarios']:
        cell = scenario['per_cohort_arm'][0]
        assert cell['mean_child_s'] == 1200
        assert cell['mean_future_audit_reserve_s'] == 300
    assert result['planning_only_hours_range'][0] == result['planning_only_hours_range'][1]


def test_an_unknown_audit_or_missing_cell_withholds_the_projection():
    result = projection.project([row(audit=None)],targets={('tiny15','pipeline'):8})
    assert result['status'] == 'incomplete'
    assert result['planning_only_hours_range'] is None
    assert result['unmeasured_or_incomplete'][0]['missing'] == ['audit_elapsed_s']
    missing = projection.project([row()],targets={('tiny15','pipeline'):8,('tiny15','twostep'):8})
    assert missing['status'] == 'incomplete'
    assert missing['planning_only_hours_range'] is None
    assert missing['per_cohort_arm'][1]['sample_rows'] == 0


def test_sample_contract_refuses_supplements_and_duplicate_trials():
    rows = []
    for (cohort,arm),count in projection.SAMPLE.items():
        rows.extend(row(cohort+str(i),cohort=cohort,arm=arm) for i in range(count))
    projection.validate_sample(rows)
    with pytest.raises(RuntimeError,match='exactly the 44'):
        projection.validate_sample(rows+[row('supplement')])
    copied = copy.deepcopy(rows)
    copied[1] = copied[0]
    with pytest.raises(RuntimeError,match='Duplicate'):
        projection.validate_sample(copied)


def test_parent_overhead_and_recorded_profile_hash_remain_visible():
    value = row(overhead=11)
    value['profile'] = dict(name='standard',budget_s=300.,finalize_s=100.,seed=0,
                           engine_timeout_s=1800.,config=dict(assignment_tries=1))
    result = projection.project([value],targets={('tiny15','pipeline'):1})
    assert result['per_cohort_arm'][0]['parent_verification_io_s']['mean'] == 11
    assert len(result['rows'][0]['recorded_profile_sha256']) == 64
    assert result['rows'][0]['recorded_profile']['engine_timeout_s'] == 1800
    assert result['confidence_interval'] is False


def test_full_guard_measurement_is_required_before_complete_31_range():
    result = projection.project([row()],targets={('tiny15','pipeline'):8})
    assert result['overall_projection_status'] == 'awaiting_full_pipeline_guard_measurement'
    assert result['projection_hours_range'] is None
    augmented = projection.add_guard_model(result,[2.,2.],provenance=dict(measured=True))
    assert augmented['projection_hours_range'] is not None
    assert augmented['guard_model']['seconds_per_full_guard_range'] == [2.,2.]
    assert augmented['projection_hours_range'][0] > result['planning_only_hours_range'][0]
    assert augmented['guard_model']['sequential_prepass_guard_s_range'] == [48.,48.]


def test_guard_cost_inside_deadlines_is_capped_while_parent_checks_stay_extra():
    timed = row(arm='instruction',cohort='envelope24',child=1200,audit=None,
        overhead=2,child_timed_out=True,status='error')
    result = projection.project([timed],targets={('envelope24','instruction'):24},
        serial_reader_reserve_s=1800)
    result = projection.add_guard_model(result,[2.,2.],provenance=dict(measured=True))
    for scenario in result['guard_scenarios']:
        assert scenario['inner_guard_additional_worker_seconds'] == 0
        assert scenario['parent_guard_calls'] == 24*4
    assert result['guard_model']['fixed_launch_guard_calls'] == 4
    assert all(s['serial_reader_reserve_s'] == 1800 for s in result['guard_scenarios'])


def test_arm_contributions_plus_shared_equal_each_full_campaign_scenario():
    rows = [row('p',arm='pipeline',child=100,audit=20),
            row('t',arm='twostep',child=20,audit=10),
            row('i',arm='instruction',cohort='envelope24',child=200,audit=30),
            row('s',arm='twostep_300',cohort='envelope24',child=40,audit=10)]
    targets = {('tiny15','pipeline'):8,('tiny15','twostep'):8,
               ('envelope24','instruction'):4,('envelope24','twostep_300'):4}
    result = projection.add_guard_model(projection.project(rows,targets=targets,
        workers=4,serial_reader_reserve_s=1800),[2.,2.])
    for index,scenario in enumerate(result['guard_scenarios']):
        arms = scenario['per_arm_projection']
        assert set(arms) == {'pipeline','twostep','instruction','twostep_300'}
        assert sum(a['attributable_hours'] for a in arms.values()) + scenario['shared_projection']['hours'] == pytest.approx(
            scenario['projected_hours_excluding_unbounded_admission'])
        assert sum(a['ideal_slot_service_seconds'] for a in arms.values()) == pytest.approx(
            result['scenarios'][0 if index == 0 else -1]['weighted_worker_seconds']/4)
        assert sum(a['parent_guard_wall_seconds'] for a in arms.values()) == pytest.approx(
            scenario['parent_guard_wall_seconds_assumption'])
        assert scenario['shared_projection']['serial_reader_reserve_s'] == 1800
        assert all(a['standalone_arm_campaign_wall'] is False for a in arms.values())
    for arm,summary in result['per_arm_projection'].items():
        assert summary['conditional_attributable_hours_range'] == [
            s['per_arm_projection'][arm]['attributable_hours'] for s in result['guard_scenarios'][:2]]
    shared = result['shared_projection']
    assert shared['conditional_hours_range'] == sorted(shared['conditional_hours_by_scenario'].values())
    assert sum(a['conditional_attributable_hours_range'][1] for a in result['per_arm_projection'].values()) + shared['conditional_hours_by_scenario']['conservative_loaded_guard_serialization'] == pytest.approx(result['projection_hours_range'][1])

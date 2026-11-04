"""Small offline controls for the development counterfactual diagnostic."""
import copy
import csv
from pathlib import Path

import pytest

from . import rule_binding_study as study
from .common import object_hash
from .test_instructions import facts


def item(iid='m', rank=0):
    return dict(map_id=iid, path='/public/'+iid+'.json', map_sha256='a'*64,
        instance_id=iid, facts_sha256='b'*64, cohort_index=rank,
        assigned_type=study.RULE_TYPES[rank % 4], raw=dict(facts(), load_list=['BOX1']))


def record(iid='m', *, stamp=1, passed=False, digest='a'*64):
    return dict(map_path='/public/'+iid+'.json', input_sha256=digest,
        instance_id=iid, profile='synthetic-profile', profile_group='p:final',
        phase='final', profile_recipe={'profile':'synthetic'},
        source_path='/public/source.json', source_sha256='c'*64,
        source_order=(stamp, '/public/source.json', -1),
        recorded_full_checker_pass=passed, plan=None)


def test_baseline_choice_is_earliest_without_pass_cherry_picking():
    failed, passed = record(stamp=1), record(stamp=2, passed=True)
    groups, errors = study.choose_baselines([item()], [passed, failed])
    assert groups['p:final']['m'] is failed
    assert not errors


@pytest.mark.parametrize('change', [dict(input_sha256='f'*64),
    dict(instance_id='different'), dict(plan=dict(instance_id='different', call_index=0)),
    dict(plan=dict(instance_id='m', call_index=1))])
def test_baseline_hash_and_plan_identity_mismatch_never_probe(change):
    value = dict(record(), **change)
    groups, errors = study.choose_baselines([item()], [value])
    assert not groups
    assert len(errors) == 1


def test_constrained_source_is_not_a_baseline_and_profiles_are_separate():
    row = dict(map='/public/m.json', input_sha256='a'*64, profile='live',
        rule_type='baseline', canonical_rule=dict(constraint=dict(type='close_bay')))
    assert study.normalize_source(row, Path('/public/row.json'), 'c'*64) is None
    row['canonical_rule'] = None
    early = study.normalize_source(row, Path('/public/row.json'), 'c'*64)
    row['hard_wall_s'] = 900
    deadline = study.normalize_source(row, Path('/public/row.json'), 'c'*64)
    row.update(profile=dict(name='standard', pending_measurement=True),
               selected_before_profile_completion=True)
    preview = study.normalize_source(row, Path('/public/a.preview.json'), 'c'*64)
    assert len({early['profile_group'], deadline['profile_group'], preview['profile_group']}) == 3


def test_all32_and_assigned_type_denominators_keep_missing_and_failed_maps():
    maps = [item('m'+str(i), i) for i in range(32)]
    result = study.build_study(maps, [record('m0')])
    cell = result['profiles']['p:final']
    assert len(cell['rows']) == 32
    assert cell['statuses'] == dict(no_recorded_checked_plan=1, missing=31)
    assert all(v['maps'] == 8 and v['unavailable'] == 8 for v in cell['assigned'].values())
    assert all(v['maps'] == 32 and v['unavailable'] == 32 for v in cell['forced'].values())
    assert result['full_checker_audit_performed'] is False
    assert result['release_calibration_eligible'] is False


def test_candidate_draw_uses_public_geometry_without_baseline_outcome():
    raw = facts()
    raw['load_list'] = ['BOX1']
    for stack in raw['vessel']['stacks']:
        stack['cell_ids'] = [f"{stack['bay']:03}01{tier:02}" for tier in range(82, 94, 2)]
    raw['vessel']['cells'] = [dict(id=c) for s in raw['vessel']['stacks'] for c in s['cell_ids']]
    rules, alternatives = study.candidate_rules(raw, 5)
    assert set(rules) == set(study.RULE_TYPES)
    assert alternatives['bay_physical_minus3']['constraint'] == dict(type='max_tiers', bay=3, zone='deck', max_tiers=3)
    assert alternatives['global_physical_minus2']['constraint']['bay'] is None
    assert alternatives['global_physical_minus2']['constraint']['max_tiers'] == 4
    other = copy.deepcopy(raw)
    other.update(passed=True, plan={'a':'different result'}, lifts_total=9999)
    assert study.candidate_rules(other, 5) == (rules, alternatives)


def test_real_map_list_rejects_a_sealed_path_before_payload_access(tmp_path, monkeypatch):
    from . import driver
    unopened = tmp_path/'sealed'
    monkeypatch.setattr(driver, 'V3', unopened)
    listing = tmp_path/'dev.txt'
    listing.write_text(str(unopened/'never-read.json')+'\n')
    with pytest.raises(RuntimeError, match='sealed input'):
        study.load_development(listing)


def test_process_elapsed_and_timeout_evidence_is_in_csv_and_arm_summary(tmp_path):
    from .report import write_report
    from .test_report import row
    values = dict(child_elapsed_s=3.5, child_timeout_s=4, child_timed_out=True,
        child_cleanup_failed=False, audit_elapsed_s=.2, audit_timeout_s=2,
        audit_timed_out=False, audit_cleanup_failed=False)
    result = write_report([row(cohort='tiny15', **values)], tmp_path,
        metadata=dict(expected_maps=[dict(cohort='tiny15', map_id='a')]))
    with (tmp_path/'per-map.csv').open() as stream:
        flat = next(csv.DictReader(stream))
    assert all(str(v) == flat[k] for k,v in values.items())
    cell = result['cohorts']['tiny15']['arms']['pipeline']
    assert cell['child_elapsed_s']['total'] == 3.5
    assert cell['audit_elapsed_s']['total'] == .2
    assert cell['child_timeouts'] == 1 and cell['audit_timeouts'] == 0
    assert cell['wall_s']['total'] == 1

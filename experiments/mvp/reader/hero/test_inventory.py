"""Synthetic archival inventory tests; no model, planner, checker or replay."""
import json
from pathlib import Path

import pytest

from experiments.mvp.reader.hero import inventory as module
from experiments.mvp.reader.test_brief_cohort import ship


def write(root, name, value):
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def fixture(root):
    criteria = {'schema_version': 'hero-criteria/1', 'max_shortlist': 5, 'purpose': 'curated demonstration'}
    path = write(root, 'experiments/mvp/reader/hero/CRITERIA.json', criteria)
    write(root, 'experiments/mvp/reader/hero/criteria-freeze.json', {'criteria_sha256': module.digest(path.read_bytes())})
    raw = ship(dg={'imdg_class': '8', 'subsidiary_risks': [], 'additional': []})
    input_name = 'experiments/mvp/corpus/val/small/fake/hero.json'
    input_path = write(root, input_name, raw)
    before_plan = {'id': 'before', 'instance_id': 'synthetic', 'call_index': 0,
                   'moves': [{'kind': 'load', 'call_index': 0, 'container_id': 'ABCU0000001', 'src': 'truck', 'dst': '0010182'}]}
    after_plan = {'id': 'after', 'instance_id': 'synthetic', 'call_index': 0,
                  'moves': [{'kind': 'load', 'call_index': 0, 'container_id': 'ABCU0000001', 'src': 'truck', 'dst': '0030182'}]}
    baseline_name = 'experiments/mvp/frontier/runs/val-0.9/synthetic.json'
    baseline_path = write(root, baseline_name, {'input': input_name, 'input_sha256': module.digest(input_path.read_bytes()),
                         'passed': True, 'weighted': 0, 'plan': before_plan})
    expected = {'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'dg_keep_away', 'imdg_class': '8',
                'min_bays': 1, 'away_from': {'kind': 'bay', 'bay': 1}}}
    case = {'case_id': 'dg_keep_away-04', 'input': input_name, 'baseline_file': baseline_name,
            'type': 'dg_keep_away', 'expected': expected, 'sentence': 'Old archived wording.'}
    write(root, 'experiments/mvp/reader/binding-cases.json', {'cases': [case]})
    after_name = 'experiments/mvp/reader/runs/binding/plans/dg_keep_away-04.json'
    after_path = write(root, after_name, {'case_id': case['case_id'], 'passed': True, 'repaired': False,
                       'parsed': expected, 'parser': {'request_id': 'archive-request'},
                       'baseline_sha256': module.digest(baseline_path.read_bytes()),
                       'plan': after_plan, 'constrained_moves': 0, 'wall_s': 123, 'parse_wall_s': 1,
                       'planner_pid': 11})
    audit_name = 'experiments/mvp/reader/runs/binding/audits/dg_keep_away-04.json'
    write(root, audit_name, {'passed': True, 'binding': True, 'fresh_process': True, 'repaired': False,
                           'checker_passed': True, 'plan_record_sha256': module.digest(after_path.read_bytes()),
                           'instruction_violations': [], 'wall_s': 9})
    write(root, 'experiments/mvp/reader/runs/binding/parser/dg_keep_away-04.json', {'parsed': expected})
    return {'input': input_name, 'baseline': baseline_name, 'after': after_name, 'audit': audit_name, 'expected': expected}


def test_exact_original_hero_sentence_is_fixed_for_corrosive_onebay_bayone():
    rule = {'type': 'dg_keep_away', 'imdg_class': '8', 'min_bays': 1, 'away_from': {'kind': 'bay', 'bay': 1}}
    assert module.sentence_for(rule) == 'Keep the corrosives one bay clear of bay one.'
    rule['imdg_class'] = '3'
    assert module.sentence_for(rule) == 'Keep flammable liquids at least one bay away from bay 1.'


def test_recorded_binding_after_is_joined_only_to_exact_rule_and_original_baseline(tmp_path):
    names = fixture(tmp_path)
    value = module.build_inventory(root=tmp_path)
    best = value['candidates'][0]
    assert best['expected'] == names['expected']
    assert best['before_file'] == names['baseline'] and best['after_file'] == names['after']
    assert best['matching_active_cargo'] == ['ABCU0000001']
    assert best['baseline_violations']
    assert not best['static_screen']['proven_impossible']
    after = best['preferred_after']
    assert after['recorded_audited'] and after['parsed_exact']
    assert after['constrained_moves'] == 0 and after['move_delta'] == 0
    assert after['changed_loaded_box_placements'] == 1 and after['changed_matched_dg_box_bays'] == 1
    assert after['wall_s'] == 123 and after['parse_wall_s'] == 1 and after['audit_wall_s'] == 9
    assert best['new_plain_sentence_parse_pending'] and best['fresh_hero_audit_pending']
    assert value['proposed_shortlist_candidate_ids'][0] == best['candidate_id']
    assert len(value['proposed_shortlist_candidate_ids']) <= 5
    assert not value['shortlist_frozen'] and not value['selection_is_blind']


def test_baseline_obedience_vacuity_and_static_impossibility_are_real_gates(tmp_path):
    fixture(tmp_path)
    value = module.build_inventory(root=tmp_path)
    for candidate in value['candidates']:
        assert candidate['baseline_violations']
        assert not candidate['static_screen']['proven_impossible']
        if candidate['expected']['constraint']['type'] == 'dg_keep_away':
            assert candidate['matching_active_cargo']
    assert value['exclusions']['stored_baselines_already_obey']
    assert value['exclusions']['statically_proven_impossible_rule']


@pytest.mark.parametrize('change', ['repair', 'wrong_parse', 'unchanged_plan'])
def test_failed_archival_reuse_gate_leaves_candidate_for_new_search_only(tmp_path, change):
    names = fixture(tmp_path)
    path = tmp_path/names['after']
    row = json.loads(path.read_text())
    if change == 'repair':
        row['repaired'] = True
    elif change == 'wrong_parse':
        row['parsed']['constraint']['imdg_class'] = '3'
    else:
        row['plan'] = json.loads((tmp_path/names['baseline']).read_text())['plan']
    path.write_text(json.dumps(row))
    value = module.build_inventory(root=tmp_path)
    rule = next(c for c in value['candidates'] if c['expected'] == names['expected'])
    assert rule['preferred_after'] is None
    assert not rule['eligible_for_new_read_and_audit']


def test_audit_hash_drift_never_claims_recorded_fresh_audit(tmp_path):
    names = fixture(tmp_path)
    path = tmp_path/names['audit']
    row = json.loads(path.read_text())
    row['plan_record_sha256'] = '0'*64
    path.write_text(json.dumps(row))
    value = module.build_inventory(root=tmp_path)
    best = next(c for c in value['candidates'] if c['expected'] == names['expected'])
    assert best['preferred_after'] and not best['preferred_after']['recorded_audited']


def test_forbidden_inputs_and_stored_test_run_dirs_are_never_opened(tmp_path, monkeypatch):
    fixture(tmp_path)
    write(tmp_path, 'experiments/mvp/frontier/runs/val-0.9/forbidden.json',
          {'input': 'experiments/mvp/corpus/test/small/secret.json', 'passed': True, 'plan': {}})
    write(tmp_path, 'experiments/mvp/frontier/runs/test-v3/secret.json', {'secret': 'never read'})
    read = Path.read_bytes
    def guarded(path):
        assert not set(path.parts) & {'test', 'test-v3', 'test_v3'}
        return read(path)
    monkeypatch.setattr(Path, 'read_bytes', guarded)
    value = module.build_inventory(root=tmp_path)
    assert value['unique_input_count'] == 1
    assert value['exclusions']['forbidden_or_unknown_split'] == 1
    assert not any(set(Path(p).parts) & {'test', 'test-v3', 'test_v3'} for p in value['sources'])


def test_criteria_hash_required_and_output_never_silently_overwritten(tmp_path):
    fixture(tmp_path)
    output = tmp_path/'experiments/mvp/reader/hero/inventory.json'
    result = module.write_inventory(output, root=tmp_path)
    payload = output.read_bytes()
    assert json.loads(payload) == result
    with pytest.raises(FileExistsError):
        module.write_inventory(output, root=tmp_path)
    assert output.read_bytes() == payload
    criteria = tmp_path/'experiments/mvp/reader/hero/CRITERIA.json'
    criteria.write_bytes(criteria.read_bytes()+b' ')
    with pytest.raises(ValueError, match='criteria'):
        module.build_inventory(root=tmp_path)


def test_rank_prefers_audited_reuse_then_corrosives_then_cost(tmp_path):
    fixture(tmp_path)
    candidate = module.build_inventory(root=tmp_path)['candidates'][0]
    other = json.loads(json.dumps(candidate))
    other['expected']['constraint']['imdg_class'] = '3'
    assert module.ranking(candidate) < module.ranking(other)
    expensive = json.loads(json.dumps(candidate))
    expensive['preferred_after']['move_delta'] = 3
    assert module.ranking(candidate) < module.ranking(expensive)
    no_audit = json.loads(json.dumps(candidate))
    no_audit['preferred_after']['recorded_audited'] = False
    assert module.ranking(candidate) < module.ranking(no_audit)

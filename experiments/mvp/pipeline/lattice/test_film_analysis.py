from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.lattice import film_analysis as film
from experiments.mvp.pipeline.lattice.store import canonical_rule_set, entry_key
from stowage.model.fixtures import build_instance


@pytest.fixture
def geometry():
    instance = build_instance()
    return instance, [box.id for box in instance.containers]


def test_close_bay_counts_changed_final_boxes_not_all_loads(geometry):
    instance, (_, a, b, c) = geometry
    old = {a: '0010102', b: '0010202', c: '0030204'}
    new = {a: '0030102', b: '0030104', c: '0030202'}
    result = film.displacement(instance, old, new, [{'type': 'close_bay', 'bay': 1}])
    assert result['forced_ids'] == sorted([a, b])
    assert result['moved_count'] == 3 and result['additional_moved_ids'] == [c]
    assert any(s['baseline_count'] > s['plan_count'] for s in result['rules'][0]['target_stacks'])


def test_max_tiers_counts_occupied_physical_tiers_and_deduplicates_long_footprint(geometry):
    instance, (long, a, b, c) = geometry
    old = {long: '0020104', a: '0010202', b: '0030202'}
    new = {long: '0020182', a: '0010202', b: '0030282'}
    result = film.displacement(instance, old, new, [{'type': 'max_tiers', 'bay': None, 'zone': 'hold', 'max_tiers': 1}])
    assert result['forced_ids'] == [long] and result['additional_moved_ids'] == [b]
    affected = {s['stack_id']: s for s in result['rules'][0]['target_stacks']}
    assert affected['00101H']['baseline_occupied_tiers'] == [4]
    assert affected['00301H']['baseline_occupied_tiers'] == [4]
    # The pointwise production rule rejects tier04 even with a gap below it;
    # one occupied tier is reported separately and never called two boxes.
    assert affected['00101H']['baseline_count'] == 1


def test_dg_forced_ids_come_from_class_and_actual_base_footprint(geometry):
    instance, (_, dg, b, c) = geometry
    old = {dg: '0010102', b: '0010202', c: '0030204'}
    new = {dg: '0030102', b: '0030202', c: '0030204'}
    rule = dict(type='dg_keep_away', imdg_class='3', min_bays=1, away_from={'kind': 'bay', 'bay': 1})
    result = film.displacement(instance, old, new, [rule])
    assert result['forced_ids'] == [dg] and result['additional_moved_ids'] == [b]
    assert result['moved_count'] == 2


def test_rule_pair_uses_union_and_does_not_double_count_same_box(geometry):
    instance, (_, dg, b, c) = geometry
    old = {dg: '0010102', b: '0010202', c: '0030204'}
    new = {dg: '0030102', b: '0030202', c: '0030204'}
    rules = [dict(type='close_bay', bay=1),
             dict(type='dg_keep_away', imdg_class='3', min_bays=1, away_from={'kind': 'bay', 'bay': 1})]
    result = film.displacement(instance, old, new, rules)
    assert sum(row['forced_moved_count'] for row in result['rules']) == 3
    assert result['forced_count'] == 2 and result['additional_moved_count'] == 0


def test_hatch_reports_minimum_removals_without_inventing_forced_identities(geometry):
    instance, (_, a, b, c) = geometry
    old = {a: '0010102', b: '0030102', c: '0010104'}
    new = {a: '0010102', b: '0030202', c: '0010104'}
    result = film.displacement(instance, old, new, [{'type': 'one_port_per_hatch'}])
    assert result['forced_ids'] is result['forced_count'] is None
    assert result['forced_minimum_count'] == 1
    assert result['additional_moved_count'] is None
    assert result['rules'][0]['covers'][0]['minimum_removals'] == 1


def test_hatch_bound_does_not_double_count_one_long_box_touching_two_covers(geometry):
    instance, (long, a, b, c) = geometry
    stacks = [s.model_copy(update={'hatch_cover_id': 'left' if s.bay == 1 else 'right'}) for s in instance.vessel.stacks]
    altered = instance.model_copy(update={'vessel': instance.vessel.model_copy(update=dict(stacks=stacks, hatch_covers=[]))})
    result = film._hatch_bound(altered, {long: '0020102', a: '0010104', c: '0030104'})
    assert sum(row['minimum_removals'] for row in result['covers']) == 2
    assert result['minimum_moved_boxes_lower_bound'] == 1
    assert result['forced_ids'] is None


def test_hatch_and_pointwise_rule_lower_bound_does_not_sum_overlapping_requirements(geometry):
    instance, (_, a, b, c) = geometry
    old = {a: '0010102', b: '0030102', c: '0010104'}
    new = {a: '0010102', b: '0030202', c: '0010182'}
    rules = [{'type': 'one_port_per_hatch'}, {'type': 'max_tiers', 'bay': None, 'zone': 'hold', 'max_tiers': 1}]
    result = film.displacement(instance, old, new, rules)
    assert result['pointwise_forced_ids'] == [c]
    assert result['forced_minimum_count'] == 2
    assert result['forced_ids'] is None


def test_move_hash_ignores_plan_annotation_ids_but_keeps_operational_cargo_ids():
    plan = {'id': 'first', 'moves': [{'id': 'annotation-a', 'kind': 'load', 'container_id': 'BOX-A', 'dst': '0010102'}]}
    other = deepcopy(plan)
    other.update(id='other', metadata={'different': True})
    other['moves'][0].update(id='annotation-b', metadata={'different': True})
    assert sha256_json(plan) != sha256_json(other)
    assert sha256_json(film.move_content(plan)) == sha256_json(film.move_content(other))
    other['moves'][0]['container_id'] = 'BOX-B'
    assert sha256_json(film.move_content(plan)) != sha256_json(film.move_content(other))


@pytest.fixture
def stored(tmp_path, monkeypatch, geometry):
    instance, (_, bid, _, _) = geometry
    root = tmp_path/'lattice'
    store = root/'store'
    fixtures = root/'fixtures'
    store.mkdir(parents=True); fixtures.mkdir()
    monkeypatch.setattr(film, 'HERE', root)
    content = instance.model_dump(mode='json')
    base = dict(id='base', instance_id=instance.id, moves=[{'kind': 'load', 'container_id': bid, 'dst': '0010102'}])
    plan = dict(id='different-plan', instance_id=instance.id, moves=[{'kind': 'load', 'container_id': bid, 'dst': '0030102'}])
    variant = deepcopy(plan)
    variant.update(id='annotation-variant', metadata={'different': True})
    rules = [[], [{'type': 'close_bay', 'bay': 1}], [{'type': 'max_tiers', 'bay': None, 'zone': 'deck', 'max_tiers': 1}]]
    rows, catalogue = [], {}
    for identifier, candidate, requested in zip(('base', 'close', 'max'), (base, plan, variant), rules):
        canonical = canonical_rule_set(requested)
        row = dict(id=identifier, instance=content, instance_sha256=sha256_json(content), canonical_rules=canonical,
                   status='checked_plan', plan=candidate, computed_utc='synthetic-unit-record',
                   gate=dict(passed=True, instance_sha256=sha256_json(content), plan_sha256=sha256_json(candidate),
                     checker={'passed': True}, rules=[{'envelope': r, 'passed': True} for r in canonical], lifts=0,
                     gauges=dict(passed=True, all_frames=True, outside_limits_steps=[], unavailable_steps=[], n_frames=2)))
        key = entry_key(row['instance_sha256'], canonical)
        raw = json.dumps(row).encode()
        (store/(key+'.json')).write_bytes(raw)
        catalogue[key] = hashlib.sha256(raw).hexdigest()
        rows.append((key, row))
    (store/'catalogue.json').write_text(json.dumps(catalogue))
    baseline = fixtures/'base.json'
    baseline.write_text(json.dumps(rows[0][1]))
    def replay(instance, plan):
        return {move['container_id']: move['dst'] for move in plan['moves'] if move['kind'] == 'load'}
    monkeypatch.setattr(film, 'final_positions', replay)
    return dict(root=root, store=store, baseline=baseline, rows=rows, catalogue=catalogue)


def test_authenticated_analysis_groups_full_moves_and_final_slots_separately(stored):
    result = film.analyze(store_dirs=[stored['store'], stored['root']/'backup-campaign/store'], base_paths=[stored['baseline']])
    hero = next(iter(result['heroes'].values()))
    assert hero['counts']['checked_records'] == 3 and hero['counts']['instructions'] == 2
    assert hero['counts']['full_plan_sha256'] == 3
    assert hero['counts']['move_content_sha256'] == hero['counts']['final_placement_sha256'] == 2
    instructions = [row for row in hero['records'] if row['canonical_rules']]
    assert not any(row['unique_final_placement'] for row in instructions)
    assert all(row['differs_from_base'] for row in instructions)
    assert all(row['recorded_gate_bound'] and not row['fresh_native_verification'] for row in instructions)
    assert result['snapshots'][1]['present'] is False
    table = film.render_readme_table(result)
    assert 'FILM_ANALYSIS_START' in table and 'Sequence provenance' in table


@pytest.mark.parametrize('problem', ['pin', 'bytes', 'gate_plan', 'gate_rules'])
def test_analysis_rejects_unauthenticated_or_unbound_recorded_claim(stored, problem):
    if problem == 'pin':
        with pytest.raises(ValueError, match='pinned'):
            film.analyze(store_dirs=[stored['store']], base_paths=[stored['baseline']],
                         expected_catalogue_sha256={str(stored['store'].resolve()): 'f'*64})
        return
    key, row = stored['rows'][1]
    path = stored['store']/(key+'.json')
    if problem == 'bytes':
        path.write_bytes(path.read_bytes()+b' ')
    else:
        if problem == 'gate_plan': row['gate']['plan_sha256'] = 'f'*64
        else: row['gate']['rules'][0]['envelope'] = canonical_rule_set([{'type': 'close_bay', 'bay': 3}])[0]
        raw = json.dumps(row).encode()
        path.write_bytes(raw)
        stored['catalogue'][key] = hashlib.sha256(raw).hexdigest()
        (stored['store']/'catalogue.json').write_text(json.dumps(stored['catalogue']))
    with pytest.raises(ValueError, match='authentication|unbound'):
        film.analyze(store_dirs=[stored['store']], base_paths=[stored['baseline']])

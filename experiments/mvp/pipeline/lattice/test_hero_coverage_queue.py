from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.lattice import hero_coverage_queue as queue
from experiments.mvp.pipeline.lattice import film_analysis
from experiments.mvp.pipeline.lattice.store import canonical_rule_set, entry_key


def context():
    bays = [1, 2, 3, 9, 10, 11, 21, 22, 23]
    return dict(bays=bays, max_tiers={'deck': 5, 'hold': 3},
                max_tiers_by_bay={str(b): {'deck': 5, 'hold': 3 if b in (9, 10, 11) else 2} for b in bays},
                has_accommodation=True, hatch_covers=['synthetic-cover'], ports=['P1', 'P2'])


def test_all_requested_singles_include_even_bays_null_tiers_and_every_dg_anchor():
    rows = queue.requested_rules(context(), ['2.2', '3', '8'])
    assert len(rows) == 154
    assert Counter(group for group, _ in rows) == dict(close=9, deck=40, hatch=1, dg=90, hold=14)
    assert [queue.PRIORITY.index(group) for group, _ in rows] == sorted(queue.PRIORITY.index(group) for group, _ in rows)
    rules = [rule for _, rule in rows]
    assert len({sha256_json(canonical_rule_set([rule])) for rule in rules}) == 154
    assert {rule['bay'] for rule in rules if rule['type'] == 'close_bay'} == set(context()['bays'])
    for rule in rules:
        if rule['type'] == 'max_tiers' and rule['zone'] == 'hold' and rule['bay'] is not None:
            assert rule['max_tiers'] < context()['max_tiers_by_bay'][str(rule['bay'])]['hold']
    anchors = [rule for rule in rules if rule['type'] == 'dg_keep_away']
    assert sum(rule['away_from']['kind'] == 'accommodation' for rule in anchors) == 9
    assert {rule['min_bays'] for rule in anchors} == {1, 2, 3}


def test_duplicate_context_references_do_not_duplicate_coverage_keys():
    raw = context()
    raw['bays'] += [1, 2]
    assert len(queue.requested_rules(raw, ['2.2', '2.2', '3', '8'])) == 154


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root = tmp_path/'repo'
    here = root/'experiments/mvp/pipeline/lattice'
    here.mkdir(parents=True)
    monkeypatch.setattr(queue, 'ROOT', root)
    monkeypatch.setattr(queue, 'HERE', here)
    monkeypatch.setattr(film_analysis, 'HERE', here)
    from experiments.mvp.reader import schema
    monkeypatch.setattr(schema, 'context_from_instance', lambda value: deepcopy(context()))
    instance = dict(id='synthetic-coverage', load_list=['a', 'b', 'c'], onboard={'placements': []},
                    containers=[{'id': name, 'dg': {'imdg_class': cls}} for name, cls in zip(('a', 'b', 'c'), ('2.2', '3', '8'))])
    digest = sha256_json(instance)
    monkeypatch.setattr(queue, 'EXPECTED_HERO_SHA256', digest)
    plan = dict(id='synthetic-base', instance_id=instance['id'], moves=[])
    def record(rules, status='checked_plan', seconds=200):
        canonical = canonical_rule_set(rules)
        return dict(id='synthetic-record', status=status, instance=instance, instance_sha256=digest, canonical_rules=canonical,
                    plan=plan if status == 'checked_plan' else None, engine_version='0.13.0', engine_manifest_sha256='a'*64,
                    computed_utc='2026-11-02T00:00:00Z', gate=dict(passed=True, instance_sha256=digest,
                    plan_sha256=sha256_json(plan), checker={'passed': True}, wall_s=seconds,
                    rules=[dict(envelope=rule, passed=True) for rule in canonical], checked_utc='2026-11-02T00:01:00Z',
                    gauges=dict(passed=True, all_frames=True, outside_limits_steps=[], unavailable_steps=[], n_frames=1)))
    base = record([])
    base_path = here/'fixtures/hero-base.json'
    base_path.parent.mkdir()
    base_path.write_text(json.dumps(base))
    map_path = root/'synthetic-map.json'
    map_path.write_text(json.dumps(instance))
    hero = {'hero': dict(instance_sha256=digest, context=context(), base_plan=str(base_path.relative_to(root)),
                         path=str(map_path.relative_to(root)), file_sha256=queue.sha256_file(map_path))}
    (here/'hero.json').write_text(json.dumps(hero))
    store = here/'store'
    store.mkdir()
    rows = [base, record([{'type': 'close_bay', 'bay': 3}], 'proved_refusal'),
            record([{'type': 'max_tiers', 'bay': 1, 'zone': 'deck', 'max_tiers': 1}], seconds=200),
            record([dict(type='dg_keep_away', imdg_class='8', min_bays=3, away_from={'kind': 'bay', 'bay': 23})], 'budget_failed')]
    catalogue = {}
    for row in rows:
        key = entry_key(digest, row['canonical_rules'])
        path = store/(key+'.json')
        path.write_text(json.dumps(row))
        catalogue[key] = queue.sha256_file(path)
    (store/'catalogue.json').write_text(json.dumps(catalogue))
    selected = dict(schema_version='stowlm.profile-selection/1', measured=True, decision_authority='leader',
                    decision_kind='explicit_partial_study_selection', user_authorized=True, stopped=True,
                    observed_completed_rows=68, data_sha256='d'*64, engine_version='0.13.0', engine_manifest_sha256='a'*64,
                    profiles={'standard': dict(engine_kwargs=dict(budget_s=300., finalize_s=100., seed=0, config={'synthetic': True}),
                                               engine_timeout_s=1800.)})
    selection_path = root/'experiments/mvp/pipeline/runner/fixtures/profile-selection.json'
    selection_path.parent.mkdir(parents=True)
    selection_path.write_text(json.dumps(selected))
    monkeypatch.setattr(queue, 'source_identity', lambda root: dict(engine_version='0.13.0', engine_manifest_sha256='a'*64,
                       binding_source_sha256='b'*64, source_identity_sha256='c'*64, computed_on_frozen_code=False))
    return dict(root=root, here=here, digest=digest, store=store, catalogue=catalogue, rows=rows,
                base_path=base_path, selection_path=selection_path, directory=here/'hero-coverage', map_path=map_path)


def test_prepare_is_read_only_and_skips_all_catalogue_keys_including_refusals(inputs):
    before = {str(path): path.read_bytes() for path in inputs['root'].rglob('*.json')}
    manifest = queue.prepare(inputs['directory'])
    assert manifest['coverage_counts']['requested'] == 154
    assert manifest['coverage_counts']['missing'] == 151 and manifest['coverage_counts']['skipped'] == 3
    assert not set(manifest['keys']) & set(inputs['catalogue'])
    assert {row['status'] for row in manifest['skipped_existing']} == {'checked_plan', 'proved_refusal', 'budget_failed'}
    assert manifest['existing_counts'] == dict(catalogue_entries=4, checked_plans=2, proved_refusals=1)
    assert len(manifest['existing_pins']) == 4
    assert manifest['base_plan_sha256'] == sha256_json(inputs['rows'][0]['plan'])
    assert manifest['profile_selection_sha256'] == queue.sha256_file(inputs['selection_path'])
    assert [row['priority'] for row in manifest['instructions']] == sorted(row['priority'] for row in manifest['instructions'])
    assert not inputs['directory'].exists()
    assert before == {str(path): path.read_bytes() for path in inputs['root'].rglob('*.json')}


def test_prior_live_reservations_are_now_queued_and_projection_has_measured_sources(inputs):
    manifest = queue.prepare(inputs['directory'])
    for body in [dict(type='max_tiers', bay=9, zone='deck', max_tiers=3),
                 dict(type='max_tiers', bay=9, zone='hold', max_tiers=2),
                 dict(type='max_tiers', bay=None, zone='hold', max_tiers=2)]:
        assert entry_key(inputs['digest'], [body]) in manifest['keys']
    eta = manifest['eta']
    assert eta['measurement'] == 'projection' and eta['workers'] == 6
    assert eta['release_baseline']['seconds'] == 200
    assert len(eta['release_baseline']['samples']) == 1
    assert eta['nominal_hours'] == pytest.approx(151/6*(300+100+200)/3600)
    assert eta['full_cap_hours'] == pytest.approx(151/6*(1800+200)/3600)
    assert manifest['computed_on_frozen_code'] is False


@pytest.mark.parametrize('problem', ['hero', 'base', 'map_bytes', 'profile', 'store_bytes'])
def test_prepare_rejects_changed_inputs_before_any_job_or_output(inputs, problem):
    if problem == 'hero':
        path = inputs['here']/'hero.json'
        data = json.loads(path.read_text()); data['hero']['instance_sha256'] = 'f'*64
    elif problem == 'base':
        path = inputs['base_path']; data = json.loads(path.read_text()); data['plan']['id'] = 'changed'
    elif problem == 'profile':
        path = inputs['selection_path']; data = json.loads(path.read_text()); data['profiles']['standard']['engine_timeout_s'] = 900
    elif problem == 'map_bytes':
        inputs['map_path'].write_text('changed')
        path = None
    else:
        path = next(inputs['store'].glob('[0-9a-f]*.json')); data = {}
    if path:
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        queue.prepare(inputs['directory'])
    assert not inputs['directory'].exists()


def test_cargo_class_extraction_includes_additional_and_subsidiary_classes_only_for_active_boxes():
    instance = dict(load_list=['a'], onboard={'placements': [{'container_id': 'b'}]}, containers=[
        dict(id='a', dg=dict(imdg_class='3', subsidiary_risks=['8'], additional=[{'imdg_class': '2.2'}])),
        dict(id='b', dg={'imdg_class': '3'}), dict(id='not-on-voyage', dg={'imdg_class': '9'})])
    assert queue._cargo_classes(instance) == ['2.2', '3', '8']


def test_source_identity_tracks_runtime_policy_and_producer_files_without_importing_adapter(tmp_path, monkeypatch):
    root = tmp_path/'repo'
    feature = root/'experiments/mvp/pipeline/lattice'
    binding = root/'experiments/mvp/pipeline/binding'
    binding.mkdir(parents=True)
    (binding/'__init__.py').write_text("raise AssertionError('binding package must never import')\n")
    (binding/'adapter.py').write_text("raise AssertionError('adapter must never import')\n")
    paths = [f'experiments/mvp/reader/{name}.py' for name in
             ('schema', 'pipeline', 'occupancy', 'constraints', 'refusal_evidence', 'select_binding')]
    paths += [f'src/llm/instructions/{name}.py' for name in ('schema', 'apply', 'mask_filter')]
    paths += [f'experiments/mvp/pipeline/lattice/{name}.py' for name in
              ('batch', 'gates', 'acceptance', 'runtime', 'store', 'sequence', 'sequence_blocks', 'binding_sources',
               'hero_coverage_queue', 'hero_coverage', 'hero_coverage_publish')]
    paths += [f'app/{name}.py' for name in ('stage_physics', 'stability', 'checker', 'check_cache')]
    paths += ['experiments/mvp/pipeline/runner/limits.py', 'experiments/mvp/pipeline/runner/profiles.py',
              'experiments/mvp/pipeline/runner/fixtures/profile-selection.json']
    for name in paths:
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# synthetic source identity fixture\n')
    identity = dict(engine_version='0.13.0', engine_manifest_sha256='a'*64, engine_manifest={'passed': True})
    monkeypatch.setattr(queue, '_engine_identity_from_disk', lambda root: deepcopy(identity))
    before = queue.source_identity(root)
    assert before['adapter_imported_for_identity'] is False
    assert before['computed_on_frozen_code'] is False
    assert 'adapter.py' in before['binding_source_files']
    assert 'experiments/mvp/pipeline/lattice/sequence_blocks.py' in before['gate_source_files']
    assert 'experiments/mvp/pipeline/runner/limits.py' in before['gate_source_files']
    assert 'experiments/mvp/pipeline/lattice/hero_coverage_publish.py' in before['gate_source_files']
    (feature/'gates.py').write_text('# changed gate semantics\n')
    after = queue.source_identity(root)
    assert after['source_identity_sha256'] != before['source_identity_sha256']
    assert after['binding_source_sha256'] == before['binding_source_sha256']
    (feature/'hero_coverage_publish.py').unlink()
    with pytest.raises(ValueError, match='must be final'):
        queue.source_identity(root)


def test_engine_identity_file_loader_bypasses_binding_package_and_verifies_pass_flag(tmp_path):
    root = tmp_path/'repo'
    binding = root/'experiments/mvp/pipeline/binding'
    engine = root/'experiments/mvp/frontier/engine'
    binding.mkdir(parents=True)
    engine.mkdir(parents=True)
    (binding/'__init__.py').write_text("raise AssertionError('binding package must never import')\n")
    verifier = binding/'provenance.py'
    verifier.write_text("def verify(engine, root, manifest):\n    return {'passed': True, 'manifest_sha256': 'synthetic-pin'}\n")
    (engine/'__init__.py').write_text("VERSION = '0.13.0'\n")
    result = queue._engine_identity_from_disk(root)
    assert result['engine_version'] == '0.13.0' and result['engine_manifest_sha256'] == 'synthetic-pin'
    verifier.write_text("def verify(engine, root, manifest):\n    return {'passed': False}\n")
    with pytest.raises(ValueError, match='source pins failed'):
        queue._engine_identity_from_disk(root)

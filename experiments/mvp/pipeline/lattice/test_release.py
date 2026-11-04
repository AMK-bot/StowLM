from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import socket

import pytest

from experiments.mvp.pipeline.lattice import release
from experiments.mvp.pipeline.lattice.store import canonical_rule_set, entry_key
from experiments.mvp.pipeline.contract.schema import sha256_json

BODIES = {name: {'id': name} for name in ('primary', 'backup')}
PRIMARY, BACKUP = (sha256_json(BODIES[name]) for name in ('primary', 'backup'))


def _raw(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def _row(instance, rules):
    canonical = canonical_rule_set(rules)
    body = BODIES['primary' if instance == PRIMARY else 'backup']
    plan = {'id': 'synthetic-unit-fixture', 'instance_id': body['id'], 'moves': []}
    return dict(instance=body, instance_sha256=instance, canonical_rules=canonical, status='checked_plan',
                plan=plan, mode='stored',
                computed_utc='2026-11-02T00:00:00Z', binding_compatibility={'preserve': 'this proof'},
                gate=dict(passed=True, instance_sha256=instance, plan_sha256=sha256_json(plan), checker={'passed': True},
                          rules=[dict(passed=True, envelope=rule) for rule in canonical],
                          gauges=dict(passed=True, all_frames=True, outside_limits_steps=[], unavailable_steps=[], n_frames=1)))


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    root = tmp_path / 'lattice'
    source, fixtures = root / 'store', root / 'fixtures'
    source.mkdir(parents=True)
    fixtures.mkdir()
    monkeypatch.setattr(release, 'HERE', root)
    rows = [_row(PRIMARY, []), _row(BACKUP, [])]
    rows += [_row(PRIMARY, [{'type': 'close_bay', 'bay': bay}]) for bay in range(1, 10)]
    rows += [_row(PRIMARY, [{'type': 'max_tiers', 'bay': None, 'zone': 'deck', 'max_tiers': 1}])]
    rows += [_row(BACKUP, [{'type': 'close_bay', 'bay': 99}])]
    refusal = _row(PRIMARY, [{'type': 'close_bay', 'bay': 100}]) | {'status': 'proved_refusal', 'plan': None}
    rows.append(refusal)
    catalogue, by_key = {}, {}
    for row in rows:
        key = entry_key(row['instance_sha256'], row['canonical_rules'])
        raw = _raw(row)
        (source / (key + '.json')).write_bytes(raw)
        catalogue[key] = hashlib.sha256(raw).hexdigest()
        by_key[key] = row
    (source / 'catalogue.json').write_bytes(_raw(catalogue))
    (root / 'hero.json').write_bytes(_raw({'hero': {'instance_sha256': PRIMARY}}))
    for name, row in [('hero-base', rows[0]), ('hero-replan', rows[2]), ('shot6-backup', rows[11])]:
        (fixtures / (name + '.json')).write_bytes(_raw(row))
    return dict(root=root, source=source, fixtures=fixtures, catalogue=catalogue, rows=by_key,
                destination=root / 'releases/final')


def _freeze(data, **kwargs):
    return release.freeze_release(source_store=data['source'], release_dir=data['destination'],
                                  hero_path=data['root'] / 'hero.json', fixtures_dir=data['fixtures'], **kwargs)


def _replace_row(data, key, row):
    raw = _raw(row)
    (data['source'] / (key + '.json')).write_bytes(raw)
    data['catalogue'][key] = hashlib.sha256(raw).hexdigest()
    (data['source'] / 'catalogue.json').write_bytes(_raw(data['catalogue']))


def test_release_copies_exact_authenticated_bytes_and_keeps_demo_independent(dataset, monkeypatch):
    data = dataset
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: pytest.fail('release attempted network'))
    before = {path.name: path.read_bytes() for path in data['source'].glob('*.json')}
    result = _freeze(data)
    destination = data['destination']
    assert result['counts']['primary_instructions'] == 10
    assert result['counts']['bases'] == 2 and result['counts']['proved_refusals'] == 1
    assert result['launch_regating_required'] and not result['native_checks_performed']
    assert result['verification'] == 'authenticated byte copy only; no native gate or search'
    for name, raw in before.items():
        assert (destination / 'store' / name).read_bytes() == raw
        assert (data['source'] / name).read_bytes() == raw
    assert hashlib.sha256(before['catalogue.json']).hexdigest() == result['catalogue_sha256']
    assert (destination / 'release.json').exists()
    assert not (destination / 'store/release.json').exists()
    demo = destination / 'demo-store'
    demo_catalogue = json.loads((demo / 'catalogue.json').read_bytes())
    assert len(demo_catalogue) == 3
    assert set(demo_catalogue) == set(result['demo_keys'].values())
    # Continued production, then even removing the full release store, cannot
    # invalidate the separately copied and pinned demo subset.
    (data['source'] / 'catalogue.json').write_text('{}')
    shutil.rmtree(destination / 'store')
    for key, digest in demo_catalogue.items():
        raw = (demo / (key + '.json')).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        assert json.loads(raw)['computed_utc'] == '2026-11-02T00:00:00Z'
        assert json.loads(raw)['binding_compatibility'] == {'preserve': 'this proof'}
    assert hashlib.sha256((demo / 'catalogue.json').read_bytes()).hexdigest() == result['demo_catalogue_sha256']


def test_source_tamper_and_wrong_catalogue_pin_fail_without_publication(dataset):
    data = dataset
    with pytest.raises(ValueError, match='independently pinned'):
        _freeze(data, expected_catalogue_sha256='f' * 64)
    path = next(data['source'].glob('[0-9a-f]*.json'))
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='authentication'):
        _freeze(data)
    assert not data['destination'].exists()


@pytest.mark.parametrize('problem', ['missing', 'outside_catalogue', 'different_plan', 'wrong_rule_family'])
def test_shot6_must_exist_and_match_a_contained_plan(dataset, problem):
    data = dataset
    path = data['fixtures'] / 'shot6-backup.json'
    row = json.loads(path.read_bytes())
    if problem == 'missing':
        path.unlink()
    elif problem == 'outside_catalogue':
        row['canonical_rules'] = canonical_rule_set([{'type': 'close_bay', 'bay': 77}])
        path.write_bytes(_raw(row))
    elif problem == 'wrong_rule_family':
        path.write_bytes((data['fixtures'] / 'hero-replan.json').read_bytes())
    else:
        row['plan']['id'] = 'another plan'
        path.write_bytes(_raw(row))
    with pytest.raises((ValueError, FileNotFoundError)):
        _freeze(data)
    assert not data['destination'].exists()


@pytest.mark.parametrize('problem', ['gate', 'checker', 'gauges', 'coverage', 'frame_count', 'rule_audit', 'missing_audit'])
def test_only_full_recorded_primary_passes_count_toward_ten(dataset, problem):
    data = dataset
    key = next(key for key, row in data['rows'].items() if row['instance_sha256'] == PRIMARY and
               row['canonical_rules'] and row['canonical_rules'][0]['constraint'].get('bay') == 1)
    row = deepcopy(data['rows'][key])
    if problem in ('gate', 'checker'):
        target = row['gate'] if problem == 'gate' else row['gate']['checker']
        target['passed'] = False
    elif problem == 'gauges':
        row['gate']['gauges']['outside_limits_steps'] = [0]
    elif problem == 'coverage':
        row['gate']['gauges']['all_frames'] = False
    elif problem == 'frame_count':
        row['gate']['gauges']['n_frames'] = 2
    elif problem == 'rule_audit':
        row['gate']['rules'][0]['passed'] = False
    else:
        row['gate']['rules'] = []
    _replace_row(data, key, row)
    with pytest.raises(ValueError, match='found 9'):
        _freeze(data)
    assert not data['destination'].exists()


@pytest.mark.parametrize('problem',['instance_body','row_instance_hash','gate_instance_hash','same_length_plan','gate_plan_hash','audit_envelope'])
def test_stale_identity_or_rule_evidence_cannot_count_or_freeze(dataset,problem):
    key=next(key for key,row in dataset['rows'].items() if row['instance_sha256']==PRIMARY and
             row['canonical_rules'] and row['canonical_rules'][0]['constraint'].get('bay')==1)
    row=deepcopy(dataset['rows'][key])
    if problem=='instance_body':row['instance']['unexpected']='changed'
    elif problem=='row_instance_hash':row['instance_sha256']='f'*64
    elif problem=='gate_instance_hash':row['gate']['instance_sha256']='f'*64
    elif problem=='same_length_plan':row['plan']['id']='changed-with-the-same-move-count'
    elif problem=='gate_plan_hash':row['gate']['plan_sha256']='f'*64
    else:row['gate']['rules'][0]['envelope']=canonical_rule_set([dict(type='close_bay',bay=99)])[0]
    assert release._recorded_plan_pass(row) is False
    if problem=='row_instance_hash':return  # Catalogue-key validation independently rejects this substitution.
    _replace_row(dataset,key,row)
    with pytest.raises(ValueError,match='found 9'):_freeze(dataset)
    assert not dataset['destination'].exists()


def test_atomic_publish_failure_cleans_stage_and_existing_destination_is_never_changed(dataset, monkeypatch):
    data = dataset
    original = Path.rename
    def failed_publish(path, destination):
        if Path(destination) == data['destination']:
            raise OSError('simulated publication failure')
        return original(path, destination)
    monkeypatch.setattr(Path, 'rename', failed_publish)
    with pytest.raises(OSError, match='publication failure'):
        _freeze(data)
    assert not data['destination'].exists()
    assert not list(data['destination'].parent.glob('.final.*'))
    data['destination'].mkdir()
    sentinel = data['destination'] / 'existing.txt'
    sentinel.write_text('preserve existing release')
    with pytest.raises(FileExistsError):
        _freeze(data)
    assert sentinel.read_text() == 'preserve existing release'


def test_demo_subset_is_optional_but_shot6_guard_remains(dataset):
    result = _freeze(dataset, include_demo=False)
    assert result['demo_keys'] is None and result['demo_catalogue_sha256'] is None
    assert result['shot6_backup_key']
    assert not (dataset['destination'] / 'demo-store').exists()

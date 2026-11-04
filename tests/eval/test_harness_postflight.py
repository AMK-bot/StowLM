"""Official publications reject drift introduced after input admission."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import harness, splits_epoch
from eval.splits import load_inventory, spec_key
from stowage.model import PlannerResult
from stowage.versions import component_versions


class Planner:
    name = 'test.postflight'
    version = '1'
    evaluation_config = {}

    def plan_batch(self, instances, **kwargs):
        return [PlannerResult(status='error', planner=self.name, planner_version=self.version)
                for _ in instances]


@pytest.fixture
def snapshot(tmp_path, monkeypatch, empty_ship_instance):
    root = tmp_path / 'instances-epostflight'
    root.mkdir()
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'owned.txt').write_text('frozen source')
    monkeypatch.setattr(splits_epoch, '_SOURCE_ROOT', source)
    inventory = load_inventory()
    entries = [e for e in inventory.manifest['entries']
               if e['spec']['rung'] == 'tiny' and e['split'] == 'val'][:2]
    files = {'owned.txt': sha256(b'frozen source').hexdigest()}
    versions = {'CHECKER_VERSION': 'fixture'}
    source_hash = sha256(json.dumps({'files': files, 'versions': versions}, sort_keys=True).encode()).hexdigest()
    cache_sources = {name: str(tmp_path / 'cache' / name)
                     for name in ('stability', 'vessels', 'linear')}
    payload = Path(cache_sources['vessels']) / 'hull.json'
    payload.parent.mkdir(parents=True)
    payload.write_text('{"stack_limit":30000}')
    cache_files = {'vessels/hull.json': sha256(payload.read_bytes()).hexdigest()}
    cache_hash = sha256(json.dumps({'sources': cache_sources, 'files': cache_files}, sort_keys=True).encode()).hexdigest()
    frozen = dict(epoch_id='postflight', diagnostic=False, source_sha256=source_hash,
        files=files, versions=versions, paths=['owned.txt'],
        numerical_caches=dict(sources=cache_sources, files=cache_files, sha256=cache_hash),
        freeze_sha256=sha256((source_hash + cache_hash).encode()).hexdigest())
    group = root / 'tiny'
    group.mkdir()
    rows, shards, bindings = [], [], {}
    for index, entry in enumerate(entries):
        path = group / f'{index}.json'
        path.write_text(empty_ship_instance.model_dump_json())
        key = spec_key(entry['spec'])
        digest = sha256(path.read_bytes()).hexdigest()
        shard = dict(instance=path.name, sha256=digest, split_spec=entry['spec'], split='val',
                     spec_key=key, split_version='v2', split_entries_sha256=inventory.entries_sha256,
                     split_policy=inventory.manifest['policy']['hash_salt'])
        shards.append(shard)
        bindings[key] = dict(spec=entry['spec'], split='val', instance=f'tiny/{path.name}', sha256=digest)
        rows.append(dict(path=str(path), input_sha256=digest, spec_key=key, split='val', rung='tiny',
                         spec=entry['spec'], split_version='v2', split_entries_sha256=inventory.entries_sha256,
                         epoch_id='postflight', source_sha256=source_hash))
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=splits_epoch.EPOCH_SCHEMA, epoch_id='postflight',
        diagnostic=False, source_sha256=source_hash, freeze_manifest=frozen, versions=component_versions(),
        split_version='v2', split_entries_sha256=inventory.entries_sha256,
        groups={'tiny': {'shards': shards}}, bindings=bindings)))
    for row in rows:
        row.update(regen_manifest_path=str(manifest), regen_manifest_sha256=sha256(manifest.read_bytes()).hexdigest())
    route = {'changed': False}

    def validate_route(frozen):
        if route['changed']:
            raise ValueError('numerical cache route differs from frozen vessels')

    monkeypatch.setattr(splits_epoch, 'validate_active_numerical_cache_routes', validate_route)
    monkeypatch.setattr(splits_epoch, 'instance_matches_spec', lambda *args: True)
    monkeypatch.setattr(harness, 'select_instances', lambda *args, **kwargs: rows[:1])
    monkeypatch.setattr(harness, 'lower_bound', lambda instance: SimpleNamespace(model_dump=lambda **kwargs: {}))
    monkeypatch.setattr(harness, 'run_planner', lambda planner, instance, **kwargs: (
        PlannerResult(status='error', planner=planner.name, planner_version=planner.version), None))

    def mutate(kind):
        if kind == 'cache':
            payload.write_text('{"stack_limit":60000}')
        elif kind == 'input':
            path = Path(rows[0]['path'])
            path.write_text(path.read_text() + '\n')
        elif kind == 'manifest':
            manifest.write_text(manifest.read_text() + '\n')
        elif kind == 'source':
            (source / 'owned.txt').write_text('edited source')
        else:
            route['changed'] = True

    return SimpleNamespace(root=root, rows=rows, mutate=mutate, run=tmp_path / 'run')


@pytest.mark.parametrize('kind', ['input', 'cache', 'source', 'manifest', 'route'])
@pytest.mark.parametrize('phase', ['pipeline', 'lower_bound'])
def test_final_instance_drift_refuses_official_record_and_summary(snapshot, monkeypatch, kind, phase):
    original = getattr(harness, 'run_planner' if phase == 'pipeline' else phase)
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        snapshot.mutate(kind)
        return result
    monkeypatch.setattr(harness, 'run_planner' if phase == 'pipeline' else phase, changed)
    with pytest.raises(ValueError, match='drift|checksum|changed|route'):
        harness.run_evaluation(Planner(), run_dir=snapshot.run, instances_dir=snapshot.root, rungs=('tiny',))
    assert not list((snapshot.run / 'instances').glob('*.json'))
    assert not (snapshot.run / 'summary.json').exists()


def test_final_summary_rechecks_after_aggregation(snapshot, monkeypatch):
    from eval import harness_metrics
    original = harness_metrics.aggregate_results
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        snapshot.mutate('cache')
        return result
    monkeypatch.setattr(harness_metrics, 'aggregate_results', changed)
    with pytest.raises(ValueError, match='cache drift'):
        harness.run_evaluation(Planner(), run_dir=snapshot.run, instances_dir=snapshot.root, rungs=('tiny',))
    assert not (snapshot.run / 'summary.json').exists()
    assert not (snapshot.run / 'ROW.md').exists()


def test_resumed_checkpoint_rechecks_before_summary_publication(snapshot, monkeypatch):
    kwargs = dict(run_dir=snapshot.run, instances_dir=snapshot.root, rungs=('tiny',))
    harness.run_evaluation(Planner(), **kwargs)
    before = (snapshot.run / 'summary.json').read_bytes()
    original = harness._clean_result
    def changed(*args, **kwargs):
        original(*args, **kwargs)
        snapshot.mutate('cache')
    monkeypatch.setattr(harness, '_clean_result', changed)
    with pytest.raises(ValueError, match='cache drift'):
        harness.run_evaluation(Planner(), **kwargs)
    assert (snapshot.run / 'summary.json').read_bytes() == before


def test_batch_final_input_cannot_change_earlier_selected_input(snapshot, monkeypatch):
    monkeypatch.setattr(harness, 'select_instances', lambda *args, **kwargs: snapshot.rows)
    original = harness.lower_bound
    calls = []
    def changed(instance):
        result = original(instance)
        calls.append(instance.id)
        if len(calls) == 2:
            snapshot.mutate('input')
        return result
    monkeypatch.setattr(harness, 'lower_bound', changed)
    with pytest.raises(ValueError, match='checksum'):
        harness.run_evaluation(Planner(), run_dir=snapshot.run, instances_dir=snapshot.root,
                               rungs=('tiny',), batch_size=2)
    assert not (snapshot.run / 'instances' / f'{snapshot.rows[1]["spec_key"]}.json').exists()
    assert not (snapshot.run / 'summary.json').exists()


def test_parallel_parent_verifies_worker_output_before_publication(snapshot, monkeypatch):
    class Future:
        def __init__(self, record): self.record = record
        def result(self):
            snapshot.mutate('cache')
            return self.record
    class Pool:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, function, *args): return Future(function(*args))
    monkeypatch.setattr(harness, 'ProcessPoolExecutor', Pool)
    monkeypatch.setattr(harness, 'as_completed', lambda futures: list(futures))
    with pytest.raises(ValueError, match='cache drift'):
        harness.run_evaluation(Planner(), run_dir=snapshot.run, instances_dir=snapshot.root,
                               rungs=('tiny',), workers=2)
    assert not list((snapshot.run / 'instances').glob('*.json'))
    assert not (snapshot.run / 'summary.json').exists()


@pytest.mark.parametrize('loader', ['harness', 'epoch'])
@pytest.mark.parametrize('compressed', [False, True])
def test_instance_parsed_from_the_same_bytes_as_checksum(
        tmp_path, monkeypatch, empty_ship_instance, loader, compressed):
    import gzip
    path = tmp_path / ('instance.json.gz' if compressed else 'instance.json')
    before = empty_ship_instance.model_dump_json().encode()
    after = empty_ship_instance.model_copy(update={'id': 'replacement-after-hash'}).model_dump_json().encode()
    if compressed:
        before, after = gzip.compress(before, mtime=0), gzip.compress(after, mtime=0)
    path.write_bytes(before)
    row = dict(path=str(path), spec={}, input_sha256=sha256(before).hexdigest())
    original = Path.read_bytes
    reads = []
    def replace_after_read(selected):
        data = original(selected)
        if selected == path:
            reads.append(data)
            if len(reads) == 1:
                path.write_bytes(after)
        return data
    monkeypatch.setattr(Path, 'read_bytes', replace_after_read)
    monkeypatch.setattr(splits_epoch, 'instance_matches_spec', lambda *args: True)
    instance = (harness._load_selected(row, True) if loader == 'harness'
                else splits_epoch.validate_epoch_instance(path, row))
    assert instance.id == empty_ship_instance.id
    assert len(reads) == 1

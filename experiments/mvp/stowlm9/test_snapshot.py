"""D209 timer/count events, immutable publications and interrupted commit recovery."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('snapshot', Path(__file__).with_name('snapshot.py'))
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)


def fixture(tmp_path):
    manifest = dict(manifest_id='M', outcome_epoch='E', rule_deadline='1970-01-01T01:00:00+00:00')
    s.atomic(tmp_path / 'manifest.json', manifest)
    return manifest


def publisher(tmp_path, n=20, generation='generation'):
    def export(manifest_path, stream, *, root):
        manifest = s.read(manifest_path)
        directory = tmp_path / '.exports' / generation
        directory.mkdir(parents=True, exist_ok=True)
        val = ''.join(json.dumps(dict(split='val', instance_id=f'val{i}'))+'\n' for i in range(80)).encode()
        train = ''.join(json.dumps(dict(split='train', instance_id=f'train{i}'))+'\n' for i in range(n)).encode()
        pinned = Path(manifest_path).read_bytes()
        (directory / 'val.jsonl').write_bytes(val)
        (directory / 'train.jsonl').write_bytes(train)
        (directory / 'admitted_manifest.json').write_bytes(pinned)
        report = dict(generation=str(directory), stream=stream, manifest_id=manifest['manifest_id'],
                      outcome_epoch='E', manifest_sha256=s.sha(pinned), counts=dict(train=n),
                      frozen_val=dict(sha256=s.sha(val), train_identity_overlap=0))
        s.atomic(directory / 'build_report.json', report)
        return report
    return export


def test_deadline_is_snapshot_event_and_threshold_counts_labels(tmp_path):
    manifest = fixture(tmp_path)
    assert s.reserve_event(tmp_path, manifest, 1700, now=3599) is None
    first = s.reserve_event(tmp_path, manifest, 20, now=3600)
    assert first['number'] == 0 and first['kind'] == '1945'
    assert s.reserve_event(tmp_path, manifest, 20, now=3700) == first
    receipt = s.export_event(tmp_path/'manifest.json', first['id'], root=tmp_path, exporter=publisher(tmp_path))
    assert receipt['train_labels'] == 20 and (tmp_path/'rule/EXPORT.DONE').is_file()
    assert (tmp_path/'data-85-0000').resolve() == tmp_path/'.exports/generation'
    assert s.reserve_event(tmp_path, manifest, 219, now=4000) is None
    second = s.reserve_event(tmp_path, manifest, 220, now=4000)
    assert second['number'] == 1 and second['threshold'] == 220
    # Many failures/attempts cannot affect the counter passed by the supervisor.
    assert s.reserve_event(tmp_path, manifest, 220, now=5000) == second
    s.export_event(tmp_path/'manifest.json', second['id'], root=tmp_path, exporter=publisher(tmp_path, 220, 'second'))
    assert s.reserve_event(tmp_path, manifest, 419, now=5000) is None
    assert s.reserve_event(tmp_path, manifest, 420, now=5000)['number'] == 2
    # The original mandatory marker remains the first snapshot, not completion.
    assert s.read(tmp_path/'rule/EXPORT.DONE')['number'] == 0


def test_failed_export_does_not_acknowledge_and_can_retry(tmp_path):
    manifest = fixture(tmp_path)
    event = s.reserve_event(tmp_path, manifest, 0, now=3600)
    def failure(*args, **kwargs):
        raise RuntimeError('checker or mask failed')
    with pytest.raises(RuntimeError):
        s.export_event(tmp_path/'manifest.json', event['id'], root=tmp_path, exporter=failure)
    assert not (tmp_path/'rule/EXPORT.DONE').exists()
    assert s.reserve_event(tmp_path, manifest, 0, now=5000)['id'] == event['id']
    s.export_event(tmp_path/'manifest.json', event['id'], root=tmp_path, exporter=publisher(tmp_path, 0))
    assert s.read(tmp_path/'rule/EXPORT.DONE')['train_labels'] == 0


def test_interrupted_link_commit_recovers_prepared_generation(tmp_path, monkeypatch):
    manifest = fixture(tmp_path)
    event = s.reserve_event(tmp_path, manifest, 20, now=3600)
    original = s.atomic
    def crash_after_link(path, value):
        if Path(path).name == '0000.json':
            raise RuntimeError('power loss after symlink')
        return original(path, value)
    monkeypatch.setattr(s, 'atomic', crash_after_link)
    with pytest.raises(RuntimeError):
        s.export_event(tmp_path/'manifest.json', event['id'], root=tmp_path, exporter=publisher(tmp_path))
    assert (tmp_path/'data-85-0000').is_symlink()
    monkeypatch.setattr(s, 'atomic', original)
    def should_not_rerun(*args, **kwargs):
        raise AssertionError('prepared publication must be recovered without another export')
    receipt = s.export_event(tmp_path/'manifest.json', event['id'], root=tmp_path, exporter=should_not_rerun)
    assert receipt['number'] == 0
    assert s.export_event(tmp_path/'manifest.json', event['id'], root=tmp_path, exporter=should_not_rerun) == receipt


def test_val_mutation_prevents_snapshot_success(tmp_path):
    manifest = fixture(tmp_path)
    event = s.reserve_event(tmp_path, manifest, 20, now=3600)
    def bad_export(*args, **kwargs):
        report = publisher(tmp_path)(*args, **kwargs)
        (Path(report['generation'])/'val.jsonl').write_text('{}\n')
        return report
    with pytest.raises(ValueError, match='separation or byte binding'):
        s.export_event(tmp_path/'manifest.json', event['id'], root=tmp_path, exporter=bad_export)
    assert not (tmp_path/'rule/EXPORT.DONE').exists()


def test_receipt_committed_before_state_reset_is_not_exported_twice(tmp_path):
    manifest = fixture(tmp_path)
    event = s.reserve_event(tmp_path, manifest, 20, now=3600)
    s.export_event(tmp_path/'manifest.json', event['id'], root=tmp_path, exporter=publisher(tmp_path))
    s.atomic(tmp_path/'rule/snapshots/state.json', dict(schema=s.SCHEMA, pending=event))
    assert s.reserve_event(tmp_path, manifest, 219, now=4000) is None
    assert s.reserve_event(tmp_path, manifest, 220, now=4000)['number'] == 1


def test_refuse_pending_manifest_switch(tmp_path):
    manifest = fixture(tmp_path)
    s.reserve_event(tmp_path, manifest, 20, now=3600)
    with pytest.raises(ValueError, match='earlier manifest'):
        s.reserve_event(tmp_path, {**manifest, 'manifest_id':'NEW'}, 20, now=3600)

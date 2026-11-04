"""Small-stream checkpoint contracts; no generator execution."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import small_batch


def test_capacity_policy_is_reloaded_and_caps_workers(tmp_path, monkeypatch):
    policy = tmp_path/'capacity.json'
    monkeypatch.setattr(small_batch, 'CAPACITY_FILE', policy)
    assert small_batch.worker_cap() == 4
    policy.write_text('{"max_workers":1}')
    assert small_batch.worker_cap() == 1
    policy.write_text('{"max_workers":4}')
    assert small_batch.worker_cap() == 4


def test_old_worker_allocation_repair_preserves_request_and_artifact_hashes(tmp_path):
    source = tmp_path / 'measurement.json'
    root = tmp_path / 'corpus'
    build.atomic_json(source, {'allocation': {f'{s}_counts_by_rung':
        {'tiny': 0, 'small': 0, 'medium': 0} for s in ('train', 'val')}})
    small_batch.campaign.prepare(source, root, ship_settings={
        'feeder_500': {'booking_slack': .25, 'booking_mass_fraction': .55}},
        small_train=2, small_val=2, migrate=True)
    request_hashes = {s: build.sha256(root/f'{s}.requested.json') for s in ('train', 'val')}
    allocation = json.loads((root/'token_allocation.json').read_text())
    allocation['allocation'].update(method='historical old worker', total_instances=3000)
    build.atomic_json(root/'token_allocation.json', allocation)
    build.atomic_json(root/'manifest.json', {'hashes': {
        'token_allocation.json': build.sha256(root/'token_allocation.json'), 'some-instance.json': 'pinned'}})
    small_batch.normalize_allocation_metadata(root)
    manifest = json.loads((root/'manifest.json').read_text())
    assert manifest['hashes']['some-instance.json'] == 'pinned'
    assert manifest['hashes']['token_allocation.json'] == build.sha256(root/'token_allocation.json')
    assert json.loads((root/'token_allocation.json').read_text())['allocation']['total_instances'] == 4
    assert request_hashes == {s: build.sha256(root/f'{s}.requested.json') for s in ('train', 'val')}


def test_completed_requires_config_and_byte_binding(tmp_path):
    spec = build.specs_for('train', {'small': 1})[0]
    assert not small_batch.completed(spec, tmp_path)
    path = build.shard_path(spec, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{}')
    build.atomic_json(path.with_suffix('.audit.json'), dict(config=spec, accepted=True,
        rule_freeze_v1_enabled=False, instance_sha256=build.sha256(path)))
    assert small_batch.completed(spec, tmp_path)
    path.write_text('{"changed": true}')
    import pytest
    with pytest.raises(ValueError, match='invalid committed'):
        small_batch.completed(spec, tmp_path)


def test_all_train_tasks_finish_before_val_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(small_batch, 'HOLD_FILE', tmp_path/'no-hold.json')
    specs = [dict(split=split, rung='small', seed=i) for i, split in enumerate(('val', 'train', 'train'))]
    monkeypatch.setattr(small_batch.campaign, 'jobs', lambda root: specs)
    events = []
    def fake_run(spec, *args):
        if spec['split'] == 'val':
            assert sorted(events) == [1, 2]
        events.append(spec['seed'])
        return dict(seed=spec['seed'], split=spec['split'], status='generated')
    monkeypatch.setattr(small_batch, 'run_one', fake_run)
    monkeypatch.setattr(small_batch, 'completed', lambda *args: True)
    monkeypatch.setattr(sys, 'argv', ['small_batch.py', '--root', str(tmp_path), '--workers', '2'])
    small_batch.main()
    state = json.loads((tmp_path / '.runtime/small/campaign.json').read_text())
    assert state['complete'] and events[-1] == 0


def test_handoff_hold_stops_before_any_inventory_mutation(tmp_path, monkeypatch):
    import pytest
    marker = tmp_path/'hold.json'
    marker.write_text('{}')
    monkeypatch.setattr(small_batch, 'HOLD_FILE', marker)
    monkeypatch.setattr(sys, 'argv', ['small_batch.py', '--root', str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        small_batch.main()
    assert error.value.code == 75
    assert not (tmp_path/'.runtime').exists()

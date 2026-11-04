"""Controller pin bindings use the same admitted bytes and bounded transport."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent


def driver(name):
    spec = importlib.util.spec_from_file_location('r2_driver_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_freeze_returns_hash_of_bytes_it_validated(tmp_path, monkeypatch):
    freeze = driver('freeze')
    # Snapshot-race unit: migration admission has its own strict integration tests.
    monkeypatch.setattr(freeze,'migration_gate',lambda *a,**k:dict(
        passed=True,component_epoch={},canaries={'numerical_cache_sha256':
            freeze.snapshot_numerical_caches(a[0])['sha256']},fixture_only=True))
    monkeypatch.delenv('STOWAGE_VESSEL_CACHE_DIR', raising=False)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'input.txt').write_text('frozen')
    seed_rule_source(source)
    manifest = source / 'freeze.json'
    freeze.atomic_json(manifest, freeze.create_manifest(source, ['input.txt']))
    admitted_bytes = manifest.read_bytes()
    snapshot = freeze._snapshot
    def replace_after_read(root, paths, **kwargs):
        result = snapshot(root, paths, **kwargs)
        replacement = json.loads(admitted_bytes)
        replacement.update(diagnostic=True, evidence_eligible=False)
        manifest.write_text(json.dumps(replacement))
        return result
    monkeypatch.setattr(freeze, '_snapshot', replace_after_read)
    checked = freeze.check_manifest(manifest)
    assert checked['_manifest_sha256'] == hashlib.sha256(admitted_bytes).hexdigest()
    assert checked['_manifest_sha256'] != hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_real_campaign_launch_accepts_current_inventory_file_pin(tmp_path, monkeypatch):
    campaign = driver('campaign')
    inventory_module = driver('test_pin_transport')
    pin = inventory_module.inventory_pin()
    monkeypatch.setattr(campaign, 'freeze_check', lambda _: {'numerical_caches': pin})
    monkeypatch.setattr(campaign, 'runner_check', lambda *_: None)
    script = ("import json,os,runpy; from pathlib import Path; "
              "p=runpy.run_path(%r)['read_pin'](); "
              "Path(%r).write_text(json.dumps({'adapter_completed':True,"
              "'attempt_id':os.environ['CAMPAIGN_ATTEMPT_ID'],'pin_count':len(p['files'])}))")
    def command(args, job, directory):
        return [sys.executable, '-c', script % (str(HERE/'pin_transport.py'), str(directory/'result.json'))]
    monkeypatch.setattr(campaign, 'command', command)
    selected = dict(path='transport-only', sha256='input', rung='tiny')
    row = campaign.run_shard(SimpleNamespace(diagnostic=True, allow_drift=True, timeout_s=30),
        {'epoch_id':'diagnostic-transport'}, {'stage':'4.7','id':'transport','input':selected}, tmp_path)
    assert row['process_returncode'] == 0 and row['adapter_completed'] is True
    assert row['pin_count'] == len(pin['files'])
    assert row['evidence_eligible'] is False


def test_campaign_pins_manifest_bytes_that_passed_validation(tmp_path, monkeypatch):
    campaign = driver('campaign')
    instance = tmp_path / 'input.json'
    instance.write_text('selection only; epoch instance validator is isolated')
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_text(json.dumps({'epoch_id': 'diagnostic-test', 'groups': {
        'random_yard/tiny': {'shards': [{'split': 'train', 'instance': instance.name,
            'sha256': campaign.sha(instance), 'vessel': 'ship'}]}}}))
    admitted = manifest_path.read_bytes()
    def validate_then_replace(*args, **kwargs):
        manifest_path.write_text('{"diagnostic":true,"epoch_id":"replacement"}')
    monkeypatch.setitem(sys.modules, 'eval.splits_epoch', SimpleNamespace(
        validate_epoch_manifest=validate_then_replace, validate_epoch_instance=lambda *_: None))
    args = SimpleNamespace(instance_root=tmp_path, diagnostic=True, ship='ship', rungs=['tiny'], count=1)
    selected, _ = campaign.select_inputs(args, 'diagnostic-test')
    assert selected
    assert args._validated_provenance[str(manifest_path.resolve())] == hashlib.sha256(admitted).hexdigest()
    assert args._validated_provenance[str(manifest_path.resolve())] != campaign.sha(manifest_path)


def seed_rule_source(root):
    source = HERE.parents[1] / 'src/stowage'
    for relative in ('versions.py', 'rules/catalogue.toml'):
        destination = root / 'src/stowage' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / relative).read_bytes())

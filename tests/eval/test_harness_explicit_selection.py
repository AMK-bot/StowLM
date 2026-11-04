"""Explicit C1 selections retain frozen membership, ordering and denominators."""
import json
from types import SimpleNamespace

import pytest

from eval import harness
from eval.harness_inventory import select_frozen_instances
from eval.splits import DEFAULT_MANIFEST, load_inventory, spec_key
from stowage.model import PlannerResult
from tests.eval.test_harness import Diagnostic, campaign
from tests.eval.test_harness_inventory import _campaign, _entries


def test_explicit_keys_replace_prefix_limits_in_deterministic_order(tmp_path):
    inventory = load_inventory()
    entries = _entries(inventory)[:3]
    root = _campaign(tmp_path, inventory, list(reversed(entries)))
    keys = [spec_key(e['spec']) for e in entries[1:]]
    selected = select_frozen_instances(root, split_manifest=DEFAULT_MANIFEST,
        rungs=['tiny'], limits={'tiny': 2}, spec_keys=list(reversed(keys)) + keys)
    assert [row['spec_key'] for row in selected.rows] == keys
    assert selected.allocated_count == len(_entries(inventory))
    assert selected.available_count == 3


@pytest.mark.parametrize('invalid', ['unknown', 'wrong-split', 'wrong-rung'])
def test_explicit_keys_must_belong_to_requested_frozen_allocation(tmp_path, invalid):
    inventory = load_inventory()
    root = _campaign(tmp_path, inventory, _entries(inventory)[:1])
    key = {'unknown': 'no-such-spec',
           'wrong-split': spec_key(_entries(inventory, split='train')[0]['spec']),
           'wrong-rung': spec_key(_entries(inventory, rung='small')[0]['spec'])}[invalid]
    with pytest.raises(ValueError, match='frozen allocation'):
        harness.run_evaluation(Diagnostic(), run_dir=tmp_path / 'run',
            instances_dir=root, rungs=['tiny'], tiny_run=True, spec_keys=[key])
    assert not (tmp_path / 'run/config.json').exists()


@pytest.mark.parametrize('keys', [[], 'one-key', [None]])
def test_malformed_explicit_selection_refuses(tmp_path, keys):
    with pytest.raises(ValueError, match='spec_keys'):
        harness.run_evaluation(Diagnostic(), run_dir=tmp_path / 'run',
            instances_dir=tmp_path, tiny_run=True, spec_keys=keys)


def test_explicit_selection_persisted_and_resume_normalizes_key_order(
        tmp_path, empty_ship_instance, monkeypatch):
    root = campaign(tmp_path, empty_ship_instance)
    emitted_key = harness.select_instances(root, rungs=['tiny'])[0]['spec_key']
    missing_key = next(spec_key(e['spec']) for e in _entries(load_inventory())
                       if spec_key(e['spec']) != emitted_key)
    calls = []
    def pipeline(planner, instance, **kw):
        calls.append(instance.id)
        return PlannerResult(status='error', planner=planner.name, planner_version=planner.version), None
    monkeypatch.setattr(harness, 'run_planner', pipeline)
    monkeypatch.setattr(harness, 'lower_bound', lambda instance:
        SimpleNamespace(model_dump=lambda **kw: {}))
    kwargs = dict(instances_dir=root, run_dir=tmp_path / 'run', rungs=['tiny'], tiny_run=True)
    keys = [emitted_key, missing_key]
    summary = harness.run_evaluation(Diagnostic(), spec_keys=keys, **kwargs)
    config = json.loads((tmp_path / 'run/config.json').read_text())
    assert config['spec_keys'] == sorted(keys)
    assert config['explicit_selection'] is True and config['partial'] is True
    assert summary['explicit_selection'] is True and summary['partial'] is True
    assert summary['n_instances'] == 1
    assert summary['n_allocated'] == len(_entries(load_inventory()))
    record = json.loads(next((tmp_path / 'run/instances').glob('*.json')).read_text())
    assert record['explicit_selection'] is True
    assert all(row['explicit_selection'] for row in
               json.loads((tmp_path / 'run/allocations.json').read_text()))
    assert harness.run_evaluation(Diagnostic(), spec_keys=list(reversed(keys)), **kwargs) == summary
    assert len(calls) == 1
    with pytest.raises(ValueError, match='configuration changed'):
        harness.run_evaluation(Diagnostic(), spec_keys=[emitted_key], **kwargs)


def test_explicit_selection_does_not_attempt_unselected_available_members(tmp_path):
    inventory = load_inventory()
    entries = _entries(inventory)[:3]
    root = _campaign(tmp_path, inventory, entries)
    keys = [spec_key(entries[-1]['spec'])]
    selected = harness.select_instances(root, rungs=['tiny'], spec_keys=keys)
    assert [row['spec_key'] for row in selected] == keys
    assert len(selected.allocated_rows) == len(_entries(inventory))


def test_cli_forwards_explicit_spec_keys(tmp_path, monkeypatch, capsys):
    captured = {}
    def run(planner, **kwargs):
        captured.update(kwargs)
        return {'explicit_selection': True}
    monkeypatch.setattr(harness, 'run_evaluation', run)
    harness.main(['--instances', str(tmp_path), '--run-dir', str(tmp_path / 'run'),
                  '--rungs', 'tiny', '--spec-keys', 'second', 'first'])
    assert captured['spec_keys'] == ['second', 'first']
    assert json.loads(capsys.readouterr().out)['explicit_selection'] is True

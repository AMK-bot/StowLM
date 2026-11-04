"""Bounded real-pipeline smoke orchestration; physical results are artifacts."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest
from eval.splits import load_inventory, canonical_spec
from stowage.generate.instances_v2 import split_metadata


@pytest.fixture(scope='module')
def smoke():
    path = Path(__file__).resolve().parents[2] / 'experiments/gold-plans/tiny/smoke.py'
    spec = importlib.util.spec_from_file_location('gold_pipeline_smoke_test', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_smoke_selects_only_pinned_current_train_tiny(smoke):
    inventory = load_inventory()
    entries = smoke.select_entries(inventory, 3)
    assert len(entries) == 3
    assert len({json.dumps(e['spec'], sort_keys=True) for e in entries}) == 3
    for entry in entries:
        assert inventory.require_membership(entry['spec'], split='train') == entry
        assert entry['spec']['rung'] == 'tiny'
        assert entry['spec']['identity_version'] == 'v2'


@pytest.mark.parametrize('count', [0, 4, -1])
def test_smoke_refuses_unbounded_instance_counts(smoke, count):
    with pytest.raises(ValueError, match='1.*3'):
        smoke.select_entries(load_inventory(), count)


def test_smoke_generation_request_preserves_frozen_identity(smoke):
    for entry in smoke.select_entries(load_inventory(), 3):
        request = smoke.generation_request(entry['spec'], max_attempts=2)
        assert split_metadata(request)['split_spec'] == canonical_spec(entry['spec'])
        assert request['max_attempts'] == 2
        assert request['c2_screened'] is False


def test_smoke_generation_failure_records_outcome_and_never_invokes_admission(smoke, tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []
    driver = SimpleNamespace(atomic_json=lambda path, value: (Path(path).parent.mkdir(parents=True, exist_ok=True),
        Path(path).write_text(json.dumps(value))), run_one=lambda *args: calls.append(args))
    monkeypatch.setattr(smoke, '_load_driver', lambda: driver)
    monkeypatch.setattr(smoke, '_bounded_generate', lambda request, limit: {
        'status': 'rejected', 'error': 'synthetic hard-rule rejection', 'wall_time_s': 0.001})
    result = smoke.run_smoke(tmp_path, count=1)
    assert result['processed'] == 1
    assert result['gold_published'] == 0
    assert result['outcomes'][0]['generation']['status'] == 'rejected'
    assert not calls
    assert not list(tmp_path.rglob('plans/*.json'))
    saved = json.loads((tmp_path / 'results.json').read_text())
    assert saved['outcomes'][0]['accepted'] is False


def test_smoke_tainted_generated_input_never_reaches_solver(smoke, tmp_path, monkeypatch):
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    instance.provenance.stubs['synthetic.override'] = 'regression probe'
    driver = smoke._load_driver()
    calls = []
    monkeypatch.setattr(driver, 'run_one', lambda *args: calls.append(args))
    monkeypatch.setattr(smoke, '_load_driver', lambda: driver)
    monkeypatch.setattr(smoke, '_bounded_generate', lambda request, limit: {
        'status': 'generated', 'instance': instance, 'audit': {}, 'wall_time_s': 0.001})
    result = smoke.run_smoke(tmp_path, count=1)
    assert result['gold_published'] == 0
    assert 'stub' in result['outcomes'][0]['pipeline_error'].lower()
    assert not calls
    assert not list(tmp_path.rglob('plans/*.json'))


def test_smoke_existing_results_require_fresh_epoch_directory(smoke, tmp_path):
    (tmp_path / 'results.json').write_text('{}')
    with pytest.raises(ValueError, match='fresh output'):
        smoke.run_smoke(tmp_path)


@pytest.mark.parametrize('generation_s,phase_s', [(181, 10), (0, 10), (30, 31), (30, 0)])
def test_smoke_refuses_unbounded_generation_or_solver_budget(smoke, tmp_path, generation_s, phase_s):
    with pytest.raises(ValueError, match='limits'):
        smoke.run_smoke(tmp_path, generation_s=generation_s, phase_s=phase_s)


def test_smoke_retry_offset_preserves_same_three_requests(smoke):
    inventory = load_inventory()
    original = smoke.select_entries(inventory, 3)
    assert smoke.select_entries(inventory, 1, start_index=1) == original[1:2]
    assert smoke.select_entries(inventory, 1, start_index=2) == original[2:3]
    with pytest.raises(ValueError, match='original 3'):
        smoke.select_entries(inventory, 3, start_index=1)
    with pytest.raises(ValueError, match='original 3'):
        smoke.select_entries(inventory, 1, start_index=3)

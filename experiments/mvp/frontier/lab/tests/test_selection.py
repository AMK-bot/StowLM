"""Frozen-rule selection and fail-closed safety checks, without sealed-file reads."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lab_catalog_selection_tests', LAB / 'catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def test_selection_is_deterministic_and_frozen():
    first, _ = catalog.select_sets()
    second, _ = catalog.select_sets()
    assert first == second
    for name, paths in first.items():
        assert catalog.load_set(name) == paths
    assert {name: len(paths) for name, paths in first.items()} == {
        'quick_tiny': 8, 'quick_small': 10, 'quick_medium': 3, 'medium12': 12, 'quick': 21}
    tiny_source = catalog._lines(catalog.VALSPLIT / 'tiny_val48.txt')
    assert first['quick_tiny'] == tiny_source[5::6]
    rows = []
    for path in (catalog.ROOT / 'experiments/mvp/frontier/runs/val-0.9').glob('*.json'):
        row = json.loads(path.read_text())
        if isinstance(row, dict) and row.get('instance_id') and '.small.' in row['instance_id']:
            rows.append(row)
    assert first['quick_small'] == [r['input'] for r in sorted(rows, key=lambda r: r['instance_id'])[4::5][:10]]


def test_manifest_hashes_and_provenance():
    manifest = json.loads((LAB / 'sets/MANIFEST.json').read_text())
    for row in manifest['maps'].values():
        assert catalog.assert_allowed(row['input']) == row['input']
        assert hashlib.sha256((catalog.ROOT / row['input']).read_bytes()).hexdigest() == row['input_sha256']
        assert row['provenance'] in ('validation', 'TRAIN')
    assert manifest['safety']['sealed_lists_opened'] is False


@pytest.mark.parametrize('path', [
    'experiments/mvp/testv3/instance.json',
    'experiments/mvp/refs_tools/small-v21-instances.txt',
    'experiments/mvp/valsplit/ablation_small15.txt',
    'experiments/mvp/frontier/runs/test-0.9/instance.json',
    'experiments/mvp/frontier/runs/unseen15-0.9/instance.json',
    'data/raw/rcsppsuite/instance.json',
    'experiments/mvp/corpus/val/small/feeder_500/unknown.json',
    'experiments/mvp/strategy/blockrule/maps/train/../../test/instance.json',
])
def test_sealed_and_unknown_inputs_fail_closed_without_opening(path, monkeypatch):
    original = Path.read_text
    opened = []

    def track_read(self, *args, **kwargs):
        opened.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', track_read)
    with pytest.raises(ValueError):
        catalog.assert_allowed(path)
    assert str(catalog.ROOT / path) not in opened


@pytest.mark.parametrize('path', [
    'experiments/mvp/valsplit/ablation_small15.txt',
    'experiments/mvp/refs_tools/small-v21-instances.txt',
    'experiments/mvp/testv3/list.txt',
])
def test_sealed_list_rejected_before_open(path, monkeypatch):
    def reject_read(*args, **kwargs):
        pytest.fail('sealed list was opened')
    monkeypatch.setattr(Path, 'read_text', reject_read)
    with pytest.raises(ValueError, match='Sealed list'):
        catalog.resolve_set(path)


def test_baseline_records_stored_final_plan_and_missing_evidence():
    baseline = catalog.load_baseline()
    tiny = [r for r in baseline.values() if r['size'] == 'tiny']
    small = [r for r in baseline.values() if r['size'] == 'small']
    medium = [r for r in baseline.values() if r['size'] == 'medium']
    assert sum(r['stored'] is not None for r in tiny) == 8
    assert sum(r['stored'] is not None for r in small) == 9
    assert all(r['passed'] is None and r['stored'] is None and r['lifts'] is None for r in medium)
    for row in tiny + small:
        if row['stored'] is not None:
            assert row['plan'] is not None
            assert row['stored'] == {a['box']: a['slot'] for a in row['ordered_actions']}
            assert row['lifts'] == sum(row['parts'].values())

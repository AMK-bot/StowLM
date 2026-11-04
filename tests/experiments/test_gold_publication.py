"""Gold writer gates independently of expensive physical solver phases."""
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from stowage.model.fixtures import build_instance


@pytest.fixture
def publication(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'experiments/gold-plans/tiny/driver.py'
    spec = importlib.util.spec_from_file_location('gold_publication_driver', path)
    driver = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = driver
    spec.loader.exec_module(driver)
    instance = build_instance()
    source = tmp_path / 'input.json'
    source.write_text(instance.model_dump_json())
    record = driver.Record(tmp_path, 'random_yard/tiny', source.name,
                           sha256(source.read_bytes()).hexdigest(), {}, 'key', 'manifest')
    monkeypatch.setattr(driver, '_validate_record', lambda record: source)
    import eval.splits
    monkeypatch.setattr(eval.splits, 'spec_from_instance', lambda instance: {})
    monkeypatch.setattr(eval.splits, 'spec_key', lambda spec: 'key')
    monkeypatch.setattr(driver, '_full_hull', lambda instance: True)
    checker_version=driver.component_versions()['checker']
    monkeypatch.setattr(driver,'component_versions',lambda:{'checker':checker_version})
    monkeypatch.setattr(driver, 'source_fingerprint', lambda: {'sha256': 'epoch'})
    if hasattr(driver, 'inventory_pin'):
        monkeypatch.setattr(driver, 'inventory_pin', lambda **kw: {
            'split_version': 'v2', 'split_entries_sha256': 'release'})
    monkeypatch.setattr(driver, '_phase', lambda function, args, limit: (0., {}))
    good = dict(accepted=True, status='ok', objective=2., plan={}, report={},
                mask_verified=True, shippable=True, metrics={'optimality_status': 'OPTIMAL'})
    monkeypatch.setattr(driver, '_safe_judge', lambda inst, kind, cfg: dict(good))
    config = driver.Config(output=tmp_path / 'output', stop_at='2030-01-01T00:00:00+00:00',
                           hard_stop_at='2030-01-01T00:00:00+00:00')
    return driver, record, config, good


def test_current_d50_checker_epoch_is_admitted(publication):
    driver, record, config, good = publication
    assert driver.process_one(record, config)['accepted']


@pytest.mark.parametrize('gate', ['mask_verified', 'shippable'])
def test_independent_admission_evidence_required_before_publication(publication, gate):
    driver, record, config, good = publication
    good[gate] = False
    outcome = driver.process_one(record, config)
    assert not outcome['accepted']
    assert gate in outcome['exact']['message']
    assert not list(config.output.glob('plans/*.json'))


def test_bound_gap_available_when_scalable_fails(publication, monkeypatch):
    driver, record, config, good = publication
    monkeypatch.setattr(driver, '_safe_judge', lambda inst, kind, cfg: dict(good) if kind == 'exact'
                        else {'accepted': False, 'status': 'timeout'})
    outcome = driver.process_one(record, config)
    assert outcome['accepted']
    assert outcome['quality_gap'] is None
    assert outcome['bound_quality_gap'] == 1.
    assert outcome['quality_gap_status'] == 'bound_available_comparison_unavailable'
    assert outcome['bound_quality_gap_scope'] == 'weighted_nonproductive_lifts_lower_bound'


def test_unavailable_gap_is_explicit(publication, monkeypatch):
    driver, record, config, good = publication
    monkeypatch.setattr(driver, '_phase', lambda *args: (None, {'timed_out': True}))
    monkeypatch.setattr(driver, '_safe_judge', lambda inst, kind, cfg: dict(good) if kind == 'exact'
                        else {'accepted': False, 'status': 'timeout'})
    outcome = driver.process_one(record, config)
    assert outcome['bound_quality_gap'] is None
    assert outcome['quality_gap_status'] == 'unavailable'
    assert outcome['quality_gap_unavailable_reason']


def test_c2_off_diagnostic_is_never_production_gold(publication):
    driver, record, config, good = publication
    outcome = driver.process_one(record, replace(config, c2_off=True))
    assert not outcome['accepted']
    assert outcome['diagnostic_accepted']
    assert Path(outcome['diagnostic_file']).is_file()
    assert not list(config.output.glob('plans/*.json'))
    diagnostic = json.loads(Path(outcome['diagnostic_file']).read_text())
    assert diagnostic['c2'] == 'off/diagnostic'


def test_future_unreviewed_checker_epoch_refused(publication, monkeypatch):
    driver, record, config, good = publication
    monkeypatch.setattr(driver, 'component_versions', lambda: {'checker': '99.0'})
    with pytest.raises(ValueError, match='checker epoch'):
        driver.process_one(record, config)


def test_scalable_shippable_candidate_publishes_when_exact_unavailable(publication, monkeypatch):
    driver, record, config, good = publication
    monkeypatch.setattr(driver, '_safe_judge', lambda inst, kind, cfg: dict(good) if kind == 'scalable'
                        else {'accepted': False, 'status': 'timeout'})
    outcome = driver.process_one(record, config)
    assert outcome['accepted']
    assert outcome['gold_source'] == 'scalable'
    artifact = json.loads(Path(outcome['gold_file']).read_text())
    assert artifact['plan'] == good['plan']
    assert artifact['gold_status'] == 'feasible'


def test_timeout_metrics_cannot_certify_optimal_gold(publication):
    driver, record, config, good = publication
    good['raw_status'] = 'timeout'
    outcome = driver.process_one(record, config)
    assert outcome['accepted']
    artifact = json.loads(Path(outcome['gold_file']).read_text())
    assert artifact['gold_status'] == 'feasible'
    assert outcome['optimal_quality_gap'] is None


def test_instance_parser_uses_the_input_bytes_whose_hash_was_validated(publication, monkeypatch):
    driver, record, config, good = publication
    source = driver._validate_record(record)
    original = source.read_bytes()
    original_id = json.loads(original)['id']
    replacement = json.loads(original)
    replacement['id'] = 'unverified-concurrent-replacement'
    read_bytes = Path.read_bytes
    swapped = []
    observed = []
    def replace_after_read(path):
        data = read_bytes(path)
        if path == source and not swapped:
            source.write_text(json.dumps(replacement))
            swapped.append(True)
        return data
    def judge(instance, kind, cfg):
        observed.append(instance.id)
        source.write_bytes(original)
        return dict(accepted=False, status='synthetic_stop')
    monkeypatch.setattr(Path, 'read_bytes', replace_after_read)
    monkeypatch.setattr(driver, '_safe_judge', judge)
    outcome = driver.process_one(record, config)
    assert not outcome['accepted']
    assert observed == [original_id, original_id]


def test_output_checksum_cannot_adopt_bytes_replaced_after_atomic_write(publication, monkeypatch):
    driver, record, config, good = publication
    atomic = driver.atomic_json
    def replace_after_write(path, value):
        checksum = atomic(path, value)
        if Path(path).parent.name == 'plans':
            Path(path).write_text(json.dumps(dict(value, accepted=False, diagnostic=True)))
        return checksum
    monkeypatch.setattr(driver, 'atomic_json', replace_after_write)
    with pytest.raises(ValueError, match='checksum'):
        driver.process_one(record, config)

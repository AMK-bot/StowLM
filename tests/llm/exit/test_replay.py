import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm.exit import replay as r
from stowage.model import CheckReport, MovePlan
from stowage.model.fixtures import build_instance


def make_record(root, rung, ship, index):
    source = root / 'corpus/train' / rung / ship / f'{index}.json'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(dict(rung=rung, ship=ship, index=index)))
    record = root / 'records' / rung / ship / f'{index}.record.json'
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(dict(instance_path=str(source),
        input_sha256=hashlib.sha256(source.read_bytes()).hexdigest())))
    return record, source


def test_whole_pool_seeded_ship_and_rung_strata_not_sorted_prefix(tmp_path):
    pool = [make_record(tmp_path, rung, ship, i)[0] for rung in ('tiny', 'small')
            for ship in ('a', 'b') for i in range(20)]
    selected = r.choose_replay(pool, [], 3, 8)
    assert selected == r.choose_replay(pool, [], 3, 8)
    assert selected != r.choose_replay(pool, [], 4, 8)
    groups = [(p.parent.parent.name, p.parent.name) for p in selected]
    assert all(groups.count((rung, ship)) == 2 for rung in ('tiny', 'small') for ship in ('a', 'b'))
    # At least one seeded row comes from beyond the former tiny/a prefix.
    assert not set(selected).issubset(pool[:16])


def test_current_map_duplicates_and_streamed_new_gold(tmp_path):
    first, source = make_record(tmp_path, 'tiny', 'a', 1)
    second, _ = make_record(tmp_path, 'small', 'b', 2)
    duplicate = tmp_path / 'copy.record.json'
    duplicate.write_bytes(first.read_bytes())
    assert r.choose_replay([first, duplicate, second], [source], 0, 20) == [second]
    assert len(r.choose_replay([first, duplicate, second], [], 0, 20)) == 2
    third, _ = make_record(tmp_path, 'small', 'c', 3)
    assert third in r.choose_replay([first, second, third], [], 1, 20)


def test_reformatted_inline_or_changed_current_identity_never_replays(tmp_path):
    first, source = make_record(tmp_path, 'tiny', 'a', 1)
    original = json.loads(source.read_text()) | dict(id='current-map', seed=123)
    source.write_text(json.dumps(original))
    copied = tmp_path / 'copied.json'
    copied.write_text(json.dumps(original, indent=4))
    copy_record = tmp_path / 'copy.record.json'
    copy_record.write_text(json.dumps(dict(instance_path=str(copied))))
    inline = tmp_path / 'inline.record.json'
    inline.write_text(json.dumps(dict(instance=original)))
    changed = tmp_path / 'changed.record.json'
    changed.write_text(json.dumps(dict(instance=original | dict(id='changed-id', extra='changed'))))
    assert r.choose_replay([first, copy_record, inline, changed], [source], 0, 20) == []


def test_empty_and_bad_replay_limit(tmp_path):
    assert r.choose_replay([], [], 0, 0) == []
    with pytest.raises(ValueError):
        r.choose_replay([], [], 0, -1)


@pytest.fixture
def bound_row(tmp_path, monkeypatch):
    from stowage import checker
    instance = build_instance()
    plan = MovePlan(id='replay', instance_id=instance.id, call_index=instance.call_index)
    report = CheckReport(passed=True, provenance=dict(versions=dict(checker=checker.CHECKER_VERSION)))
    monkeypatch.setattr(checker, 'report_matches_inputs', lambda report, instance, plan: report.passed)
    monkeypatch.setattr(r, 'checker_epoch', lambda: 'current-source-hash')
    path = tmp_path / 'row.record.json'
    path.write_text(json.dumps(dict(instance=instance.model_dump(mode='json'),
        plan=plan.model_dump(mode='json'), report=report.model_dump(mode='json'))))
    return path, instance, plan, report.model_dump(mode='json')


def test_unknown_and_same_version_source_epoch_recheck_only_selected_rows(bound_row, tmp_path):
    path, instance, plan, report = bound_row
    calls = []
    def verifier(case, candidate):
        calls.append(candidate.source)
        return SimpleNamespace(passed=True, plan=plan, details=dict(report=report))
    _, _, rechecked = r.ensure_current_replay(path, instance, plan, report, tmp_path / 'cache', verifier=verifier)
    assert rechecked and calls == ['replay']
    _, _, rechecked = r.ensure_current_replay(path, instance, plan, report, tmp_path / 'cache', verifier=verifier)
    assert not rechecked and calls == ['replay']
    row = json.loads(path.read_text())
    row['exit_checker_epoch'] = 'old-source-hash-same-version'
    path.write_text(json.dumps(row))
    _, _, rechecked = r.ensure_current_replay(path, instance, plan, report, tmp_path / 'other-cache', verifier=verifier)
    assert rechecked and calls == ['replay', 'replay']


def test_current_bound_source_epoch_can_reuse_stored_report(bound_row, tmp_path):
    path, instance, plan, report = bound_row
    row = json.loads(path.read_text())
    row['exit_checker_epoch'] = 'current-source-hash'
    row['exit_outcome_dependency_id'] = r.outcome_dependency_id()
    path.write_text(json.dumps(row))
    result = r.ensure_current_replay(path, instance, plan, report, tmp_path / 'cache',
        verifier=lambda *args: pytest.fail('current bound report should not recheck'))
    assert result == (plan, report, False)


@pytest.mark.parametrize('stored', [None, 'other-params-or-tokenizer-epoch'])
def test_same_source_with_changed_outcome_dependencies_rechecks(bound_row, tmp_path, monkeypatch, stored):
    """rev-v4-j P2: a params/hull/tokenizer edit leaves src bytes and checker_epoch unchanged; a report from
    before that edit, or one that never recorded its dependencies, must be checked again."""
    from types import SimpleNamespace as NS
    path, instance, plan, report = bound_row
    row = json.loads(path.read_text())
    row['exit_checker_epoch'] = 'current-source-hash'
    if stored:
        row['exit_outcome_dependency_id'] = stored
    path.write_text(json.dumps(row))
    monkeypatch.setattr(r, 'assert_outcome_dependencies_current', lambda: None)
    calls = []
    def verifier(i, candidate):
        calls.append(candidate.source)
        return NS(passed=True, plan=plan, details=dict(report=report))
    cache = tmp_path / 'cache'
    result = r.ensure_current_replay(path, instance, plan, report, cache, verifier=verifier)
    assert result[2] is True and calls == ['replay']
    written = next(cache.glob('*.record.json'))
    assert json.loads(written.read_text())['exit_outcome_dependency_id'] == r.outcome_dependency_id()
    # the same cache entry, from a process with other dependencies, is not reused
    monkeypatch.setattr(r, 'outcome_dependency_id', lambda: 'newer-epoch')
    assert r.ensure_current_replay(path, instance, plan, report, cache, verifier=verifier)[2] is True
    assert calls == ['replay', 'replay']


def test_replay_cache_write_refuses_dependency_drift(bound_row, tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    path, instance, plan, report = bound_row
    def drifted():
        raise RuntimeError('audit outcome dependency epoch changed')
    monkeypatch.setattr(r, 'assert_outcome_dependencies_current', drifted)
    cache = tmp_path / 'cache'
    with pytest.raises(RuntimeError, match='dependency epoch changed'):
        r.ensure_current_replay(path, instance, plan, report, cache,
            verifier=lambda i, c: NS(passed=True, plan=plan, details=dict(report=report)))
    assert not list(cache.glob('*.record.json'))


def test_failed_or_unbound_fresh_replay_never_becomes_target(bound_row, tmp_path, monkeypatch):
    path, instance, plan, report = bound_row
    with pytest.raises(ValueError, match='failed current-epoch'):
        r.ensure_current_replay(path, instance, plan, report, tmp_path / 'cache',
            verifier=lambda *args: SimpleNamespace(passed=False))
    from stowage import checker
    monkeypatch.setattr(checker, 'report_matches_inputs', lambda *args: False)
    with pytest.raises(ValueError, match='not passed and bound'):
        r.ensure_current_replay(path, instance, plan, report, tmp_path / 'cache')


def test_training_inventory_missing_is_unknown_not_unseen(tmp_path):
    assert r.adapter_training_hashes([tmp_path / 'missing']) is None
    adapter = tmp_path / 'adapter'
    adapter.mkdir()
    config = adapter / 'adapter_config.json'
    config.write_text(json.dumps(dict(stowlm_train_config=dict(data='dataset'))))
    assert r.adapter_training_hashes([adapter]) is None
    config.write_text(json.dumps(dict(exit_training_input_hashes=['a' * 64])))
    assert r.adapter_training_hashes([adapter]) == {'a' * 64}


def inventory_adapter(root, name, input_hash, parent=None):
    adapter, dataset = root / name, root / f'{name}-data'
    adapter.mkdir()
    dataset.mkdir()
    train = dataset / 'train.jsonl'
    train.write_text(json.dumps(dict(meta=dict(gold=dict(input_sha256=input_hash)))) + '\n')
    config = dict(data_dir=str(dataset), extra=dict(exit_parent=str(parent)) if parent else {})
    (adapter / 'adapter_config.json').write_text(json.dumps(dict(stowlm_train_config=config)))
    sidecar = dict(provenance=dict(parents=['base-model-sha256:test', 'sft-data-sha256:test']),
        training=dict(config=config, data_identity=dict(files_sha256={
            'train.jsonl': hashlib.sha256(train.read_bytes()).hexdigest()})))
    (adapter / 'adapters.safetensors.provenance.json').write_text(json.dumps(sidecar))
    return adapter, train


def test_base_initialized_pilot_and_verified_exit_ancestor_inventories(tmp_path):
    parent, _ = inventory_adapter(tmp_path, 'pilot', 'a' * 64)
    child, train = inventory_adapter(tmp_path, 'child', 'b' * 64, parent)
    assert r.adapter_training_hashes([parent]) == {'a' * 64}
    assert r.adapter_training_hashes([child]) == {'a' * 64, 'b' * 64}
    train.write_text(train.read_text() + '\n')
    assert r.adapter_training_hashes([child]) is None

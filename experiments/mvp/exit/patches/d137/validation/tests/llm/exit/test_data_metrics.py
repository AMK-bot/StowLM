import csv
import hashlib
import json

import pytest

from llm.exit.data import CorpusSchedule
from llm.exit.metrics import append_learning_curve, compare_validation, write_learning_plot


def corpus(tmp_path):
    root = tmp_path / 'corpus'
    entries = []
    for split in ('train', 'val'):
        for rung in ('tiny', 'small'):
            for ship in ('a', 'b'):
                for index in range(3):
                    path = root / split / rung / ship / f'{index}.json'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({'split': split, 'rung': rung, 'ship': ship, 'n': index}))
                    entries.append({'split': split, 'rung': rung, 'ship': ship,
                                    'path': str(path.relative_to(root)),
                                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (root / 'manifest.json').write_text(json.dumps({'files': entries}))
    return root


def test_fresh_mixed_rotating_and_restart(tmp_path):
    root = corpus(tmp_path)
    state = tmp_path / 'state.json'
    schedule = CorpusSchedule(root, state)
    first = schedule.next_train(2, 2, 0, gold_fraction=0)
    assert len(first) == 4
    assert [p.parent.name for p in first] == ['a', 'b', 'a', 'b']
    schedule = CorpusSchedule(root, state)
    second = schedule.next_train(2, 2, 1, gold_fraction=0)
    assert not set(first) & set(second)
    assert [p.parent.name for p in second] == ['b', 'a', 'b', 'a']
    with pytest.raises(ValueError, match='already reserved'):
        schedule.next_train(2, 2, 1, gold_fraction=0)


def test_fixed_val_and_zero_rung(tmp_path):
    root = corpus(tmp_path)
    state = tmp_path / 'state.json'
    schedule = CorpusSchedule(root, state)
    val = schedule.fixed_validation(tiny=1, small=2)
    assert val == CorpusSchedule(root, state).fixed_validation(tiny=6, small=6)
    assert all(p.is_relative_to(root / 'val') for p in val)
    assert len(schedule.next_train(0, 1, 0, gold_fraction=0)) == 1
    assert len(json.loads(state.read_text())['used']) == 1


def test_exhaustion_is_atomic_and_hash_checked(tmp_path):
    root = corpus(tmp_path)
    state = tmp_path / 'state.json'
    schedule = CorpusSchedule(root, state)
    with pytest.raises(ValueError, match='exhausted'):
        schedule.next_train(99, 0, 0, gold_fraction=0)
    assert not state.exists()
    (root / 'train/tiny/a/0.json').write_text('changed')
    with pytest.raises(ValueError, match='hash mismatch'):
        schedule.next_train(1, 0, 0, gold_fraction=0)
    assert not state.exists()


def test_train_prefers_no_gold_without_claiming_unknown_adapter_novelty(tmp_path):
    root = corpus(tmp_path)
    gold = tmp_path / 'gold'
    for ship in ('a', 'b'):
        plan = gold / 'train/tiny' / ship / '0' / 'plan.json'
        plan.parent.mkdir(parents=True)
        plan.write_text('{}')
    schedule = CorpusSchedule(root, tmp_path / 'state.json')
    paths = schedule.next_train(2, 0, 0, gold_root=gold, gold_fraction=0)
    assert [path.stem for path in paths] == ['1', '1']
    rows = schedule.state['generations']['0']
    assert all(row['novelty'] == 'new_to_loop' for row in rows)
    assert all(row['adapter_training_inventory_known'] is False for row in rows)


def test_known_adapter_training_hashes_are_deprioritized_and_novelty_labelled(tmp_path):
    root = corpus(tmp_path)
    schedule = CorpusSchedule(root, tmp_path / 'state.json')
    seen = {row['sha256'] for row in schedule.entries if row['path'].endswith('/0.json')}
    paths = schedule.next_train(2, 0, 0, seen_adapter_hashes=seen, gold_fraction=0)
    assert [path.stem for path in paths] == ['1', '1']
    assert all(row['novelty'] == 'unseen_by_any_adapter' for row in schedule.state['generations']['0'])


def row(identity, passed, moves=None):
    return {'instance_id': identity, 'passed': passed, 'weighted_moves': moves}


def test_survival_validity_dominates_moves():
    parent = [row('a', True, 1), row('b', False)]
    child = [row('a', True, 100), row('b', True, 200)]
    outcome = compare_validation(parent, child)
    assert outcome['accepted']
    assert outcome['reason'] == 'higher_pass_count'
    assert outcome['parent_mutual_mean_moves'] == 1
    assert not compare_validation(child, parent)['accepted']


def test_survival_paired_moves_and_ties():
    parent = [row('a', True, 5), row('b', True, 100), row('c', False)]
    child = [row('a', True, 4), row('b', False), row('c', True, 1000)]
    result = compare_validation(parent, child)
    assert result['accepted'] and result['mutual_pass_count'] == 1
    assert result['child_mutual_mean_moves'] == 4
    assert not compare_validation(parent, parent)['accepted']
    assert not compare_validation([row('a', False)], [row('a', False)])['accepted']


def test_invalid_val_never_authorizes_replacement():
    for parent, child in (([], []), ([row('a', False)], [row('b', False)]),
                          ([row('a', True, float('nan'))], [row('a', False)]),
                          ([row('a', False), row('a', False)], [row('a', False)])):
        with pytest.raises(ValueError):
            compare_validation(parent, child)


def test_learning_artifacts_are_measured(tmp_path):
    path = tmp_path / 'curve.csv'
    append_learning_curve(path, {'generation': 0, 'child_pass_rate': 0.5,
                                 'child_mutual_mean_moves': 9})
    append_learning_curve(path, {'generation': 1, 'child_pass_rate': 0.7,
                                 'child_mutual_mean_moves': None, 'accepted': True})
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2 and rows[1]['child_mutual_mean_moves'] == ''
    svg = tmp_path / 'curve.svg'
    write_learning_plot(path, svg)
    assert svg.read_text().count('<circle') == 3
    assert 'StowLM generations' in svg.read_text()


def test_fixed_validation_bytes_remain_pinned(tmp_path):
    root = corpus(tmp_path)
    state = tmp_path / 'state.json'
    schedule = CorpusSchedule(root, state)
    fixed = schedule.fixed_validation(tiny=1, small=1)
    fixed[0].write_text('modified after selection')
    with pytest.raises(ValueError, match='fixed validation hash mismatch'):
        CorpusSchedule(root, state).fixed_validation()


@pytest.mark.parametrize('bad_flag', [None, 'accepted', 'rule_freeze_v1_enabled', 'global_screen'])
def test_streaming_audit_extends_stale_manifest_only_when_accepted(tmp_path, bad_flag):
    root = tmp_path/'stream'
    path = root/'train/small/feeder_500/random_yard/seed-000000123.json'
    path.parent.mkdir(parents=True)
    path.write_text('{}')
    (root/'manifest.json').write_text(json.dumps(dict(files=[], release=dict(
        ship_settings={'feeder_500': {'booking_slack': .25, 'booking_mass_fraction': .55}}))))
    audit = dict(accepted=True, rule_freeze_v1_enabled=False, global_screen=0,
        instance_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        config=dict(split='train',rung='small',vessel='feeder_500',generator='random_yard',
                    seed=123,booking_slack=.25,booking_mass_fraction=.55))
    if bad_flag:
        audit[bad_flag] = {'accepted':False, 'rule_freeze_v1_enabled':True, 'global_screen':1}[bad_flag]
    path.with_suffix('.audit.json').write_text(json.dumps(audit))
    schedule = CorpusSchedule(root, tmp_path/'state.json')
    assert len(schedule._eligible('train','small')) == (0 if bad_flag else 1)

"""D137 staged scheduler: teacher/no-gold balance without weakening identity rules."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PARTS = ('moves','rules','voyage','stability','strength','visibility','lashing','constraints')


@pytest.fixture
def mod():
    from llm.exit import data
    return data


def setup(tmp_path, mod, *, per_group=4):
    root, gold = tmp_path/'corpus', tmp_path/'gold'
    entries = []
    number = 0
    for rung in ('tiny','small'):
        for teacher in (True,False):
            for index in range(per_group):
                ship = ('a','b')[index % 2]
                path = root/'train'/rung/ship/f'{number}.json'
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(dict(id=f'instance-{number}',seed=number,rung=rung)))
                entry = dict(split='train',rung=rung,ship=ship,seed=number,
                    instance_id=f'instance-{number}',path=str(path.relative_to(root)),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                entries.append(entry)
                if teacher:
                    folder = gold/Path(entry['path']).with_suffix(''); folder.mkdir(parents=True)
                    (folder/'case.lock').touch()
                    (folder/'plan.json').write_text('{}')
                    (folder/'result.json').write_text(json.dumps(dict(passed=True,completed=True,
                        input_path=str(path),input_sha256=entry['sha256'],
                        plan_sha256=hashlib.sha256((folder/'plan.json').read_bytes()).hexdigest())))
                    (folder/'checker.json').write_text(json.dumps(dict(passed=True, violations=[],
                        parts={name:dict(ran=True,error=None,violations=[]) for name in PARTS})))
                number += 1
    (root/'manifest.json').write_text(json.dumps(dict(files=entries)))
    schedule = mod.CorpusSchedule(root,tmp_path/'state.json')
    return schedule,root,gold


def selected(schedule,generation):
    return schedule.state['generations'][str(generation)]


def test_half_checked_gold_half_unseen_and_labels(tmp_path,mod):
    schedule,root,gold = setup(tmp_path,mod)
    paths = schedule.next_train(4,4,0,gold_root=gold,seen_adapter_hashes=set())
    rows = selected(schedule,0)
    assert len(paths)==8 and sum(row['gold_available_at_selection'] for row in rows)==4
    for rung in ('tiny','small'):
        assert sum(row['gold_available_at_selection'] for row in rows if row['rung']==rung)==2
    for row in rows:
        if row['gold_available_at_selection']:
            assert row['novelty']=='new_to_loop'
            assert row['teacher_gold_evidence']['historically_full_checked'] is True
            assert row['teacher_gold_evidence']['current_epoch_verification_required'] is True
        else:
            assert row['novelty']=='unseen_by_any_adapter'


def test_odd_total_rounds_half_once_and_restart_is_fresh(tmp_path,mod):
    schedule,root,gold = setup(tmp_path,mod)
    first = schedule.next_train(3,2,0,gold_root=gold)
    rows = selected(schedule,0)
    assert sum(row['gold_available_at_selection'] for row in rows)==3
    assert all(row['novelty']=='new_to_loop' for row in rows)
    schedule = mod.CorpusSchedule(root,schedule.path)
    second = schedule.next_train(3,2,1,gold_root=gold)
    assert not set(first)&set(second)
    allrows = rows+selected(schedule,1)
    assert len({row['seed'] for row in allrows})==len(allrows)
    assert len({row['sha256'] for row in allrows})==len(allrows)


def test_only_completed_full_checked_gold_qualifies_not_flags(tmp_path,mod):
    schedule,root,gold = setup(tmp_path,mod,per_group=2)
    for entry in schedule.entries:
        if entry['rung']=='tiny':
            report = gold/Path(entry['path']).with_suffix('')/'checker.json'
            if report.exists():
                row=json.loads(report.read_text());row['parts']['stability']['ran']=False
                report.write_text(json.dumps(row))
    with pytest.raises(ValueError,match='checked_gold_teacher train maps exhausted'):
        schedule.next_train(2,0,0,gold_root=gold)
    assert not schedule.path.exists() and not schedule.state['used']


def test_unchecked_plan_is_not_mislabelled_unseen(tmp_path,mod):
    schedule,root,gold = setup(tmp_path,mod,per_group=2)
    for entry in schedule.entries:
        if entry['rung']=='tiny':
            folder=gold/Path(entry['path']).with_suffix('');folder.mkdir(parents=True,exist_ok=True)
            (folder/'plan.json').write_text('{}')
    with pytest.raises(ValueError,match='no_stored_gold train maps exhausted'):
        schedule.next_train(2,0,0,gold_root=gold)
    assert not schedule.path.exists()


def test_identity_seed_and_heldout_exclusions_apply_to_both_strata(tmp_path,mod):
    schedule,root,gold = setup(tmp_path,mod)
    forbidden=dict(schedule.entries[0],split='val',path='val/tiny/other.json')
    schedule.entries.append(forbidden)
    schedule.state['used_seeds']=[1,4]
    schedule.next_train(4,0,0,gold_root=gold)
    rows=selected(schedule,0)
    assert not {0,1,4}&{row['seed'] for row in rows}
    assert sum(row['gold_available_at_selection'] for row in rows)==2


def test_hash_mismatch_in_teacher_fails_atomically(tmp_path,mod):
    schedule,root,gold=setup(tmp_path,mod)
    (root/schedule.entries[0]['path']).write_text('changed')
    with pytest.raises(ValueError,match='corpus hash mismatch'):
        schedule.next_train(2,0,0,gold_root=gold)
    assert not schedule.path.exists()


def test_gold_input_binding_required(tmp_path,mod):
    schedule,root,gold=setup(tmp_path,mod,per_group=2)
    for entry in schedule.entries:
        path=gold/Path(entry['path']).with_suffix('')/'result.json'
        if path.exists():
            row=json.loads(path.read_text());row['input_sha256']='0'*64;path.write_text(json.dumps(row))
    with pytest.raises(ValueError,match='checked_gold_teacher train maps exhausted'):
        schedule.next_train(2,0,0,gold_root=gold)


def test_ship_rotation_deterministic_in_each_stratum(tmp_path,mod):
    schedule,root,gold=setup(tmp_path,mod)
    schedule.next_train(4,0,0,gold_root=gold)
    assert [row['ship'] for row in selected(schedule,0)]==['a','b','a','b']
    schedule.next_train(4,0,1,gold_root=gold)
    assert [row['ship'] for row in selected(schedule,1)]==['b','a','b','a']


def test_seen_no_gold_maps_cannot_fill_proven_unseen_quota(tmp_path,mod):
    schedule,root,gold=setup(tmp_path,mod,per_group=2)
    all_seen={entry['sha256'] for entry in schedule.entries}
    with pytest.raises(ValueError,match='no_stored_gold train maps exhausted'):
        schedule.next_train(2,0,0,gold_root=gold,seen_adapter_hashes=all_seen)
    assert not schedule.path.exists()


def test_changed_gold_plan_cannot_be_historically_checked_teacher(tmp_path,mod):
    schedule,root,gold=setup(tmp_path,mod,per_group=2)
    for entry in schedule.entries:
        path=gold/Path(entry['path']).with_suffix('')/'plan.json'
        if path.exists():path.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='checked_gold_teacher train maps exhausted'):
        schedule.next_train(2,0,0,gold_root=gold)

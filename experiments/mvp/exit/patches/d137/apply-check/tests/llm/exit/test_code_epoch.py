import argparse
import hashlib
import json

import pytest

from llm.exit.code_epoch import boundary_plan, restore_boundary, save_boundary, source_snapshot


MASK = 'src/llm/stowlm/mask_mlx.py'


def test_snapshot_hashes_actual_producer_bytes_and_detects_added_files(tmp_path):
    path = tmp_path/MASK
    path.parent.mkdir(parents=True)
    path.write_text('before')
    old = source_snapshot(tmp_path)
    assert old[MASK] == hashlib.sha256(b'before').hexdigest()
    path.write_text('after')
    added = tmp_path/'src/llm/maskspeed/cache.py'
    added.parent.mkdir(); added.write_text('cache')
    plan = boundary_plan(old, source_snapshot(tmp_path))
    assert plan['kind'] == 'mask_speedup'
    assert plan['changed'] == ['src/llm/maskspeed/cache.py', MASK]
    assert plan['requires_restart'] and plan['requires_smoke']


@pytest.mark.parametrize('path', [MASK, 'src/llm/stowlm/mask.py',
    'src/llm/securing/mask_filter.py', 'src/llm/maskspeed/cache.py'])
def test_allowlisted_speedup_needs_fresh_process_and_smoke(path):
    plan = boundary_plan({path:'old'}, {path:'new'})
    assert plan['kind'] == 'mask_speedup' and plan['requires_restart'] and plan['requires_smoke']


@pytest.mark.parametrize('path', ['src/llm/lookahead/mask_filter.py', 'src/stowage/checker/full.py',
    'src/llm/stowlm/mask_speed.py', 'src/llm/securing/../train.py', '/src/llm/stowlm/mask.py'])
def test_unknown_or_uncertified_paths_fail_closed(path):
    plan = boundary_plan({path:'old'}, {path:'new'})
    assert plan['kind'] == 'other' and not plan['can_resume'] and not plan['requires_smoke']


def test_deletion_and_mixed_changes_fail_closed():
    assert boundary_plan({MASK:'a','src/llm/securing/x.py':'b'}, {MASK:'a'})['kind'] == 'other'
    assert boundary_plan({MASK:'a','src/stowage/x.py':'b'}, {MASK:'b','src/stowage/x.py':'c'})['kind'] == 'other'


def test_unchanged_epoch_does_not_restart_or_smoke():
    plan = boundary_plan({MASK:'a'}, {MASK:'a'})
    assert plan['kind'] == 'unchanged' and plan['can_resume']
    assert not plan['requires_restart'] and not plan['requires_smoke']


def test_boundary_roundtrip_preserves_next_generation_population_and_sizing(tmp_path):
    path = tmp_path/'boundary.json'
    old, new = {MASK:'a'}, {MASK:'b'}
    args = argparse.Namespace(parent=tmp_path/'parent',tiny=8,small=16,k=6,workers=6)
    population = [dict(adapter=str(tmp_path/'child'),elite=True,method='sft_dpo')]
    record = save_boundary(path, number=3, population=population, args=args,
                           loaded_pins=old,current_pins=new)
    restored = restore_boundary(path,new)
    assert record == restored
    assert restored['next_generation'] == 3 and restored['exit_code'] == 75
    assert restored['args']['parent'] == str(args.parent)
    assert restored['args']['small'] == 16 and restored['population'] == population
    assert not path.with_name(path.name+'.tmp').exists()


def test_changed_again_epoch_requires_new_smoke(tmp_path):
    path = tmp_path/'boundary.json'
    save_boundary(path,number=1,population=[dict(adapter='child')],args={},
                  loaded_pins={MASK:'a'},current_pins={MASK:'b'})
    with pytest.raises(RuntimeError,match='changed again'):
        restore_boundary(path,{MASK:'c'})


def test_mutated_population_or_manifest_is_refused(tmp_path):
    path = tmp_path/'boundary.json'
    save_boundary(path,number=1,population=[dict(adapter='child')],args={},
                  loaded_pins={MASK:'a'},current_pins={MASK:'b'})
    record = json.loads(path.read_text())
    record['population'][0]['adapter'] = 'different'
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='content hash'):
        restore_boundary(path,{MASK:'b'})


def test_non_speed_or_mid_generation_state_cannot_be_committed(tmp_path):
    with pytest.raises(RuntimeError,match='allowlisted'):
        save_boundary(tmp_path/'boundary.json',number=1,population=[dict(adapter='child')],args={},
                      loaded_pins={MASK:'a'},current_pins={MASK:'a'})

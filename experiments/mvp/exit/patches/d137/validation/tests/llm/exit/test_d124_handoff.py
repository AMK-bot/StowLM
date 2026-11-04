"""D124 draft preparation is CPU-only and cannot silently authorize code drift."""
import fcntl
import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path=Path(__file__).resolve().parents[3]/'experiments/mvp/exit/patches/d124/boundary_handoff.py'
    spec=importlib.util.spec_from_file_location('d124_handoff',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def epoch_module(root):
    path=Path(__file__).resolve().parents[3]/'experiments/mvp/exit/patches/d124/code_epoch.py'
    spec=importlib.util.spec_from_file_location('d124_epoch',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    result.ROOT=root
    return result


def activate(root,targets):
    path=root/'experiments/mvp/exit/patches/d124/activation-manifest.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(dict(schema='exit-d124-activation-v1',authorization='D124',targets=targets)))


@pytest.fixture
def completed(tmp_path):
    out=tmp_path/'deployment';generation=out/'gen-000';generation.mkdir(parents=True)
    adapter=generation/'child-0';adapter.mkdir()
    (adapter/'adapters.safetensors').write_bytes(b'checked-data child')
    values={generation/'summary.json':dict(generation=0,next_population=[dict(adapter=str(adapter),method='sft')]),
        generation/'instances.json':dict(source_pins={'src/llm/exit/selection.py':'old'},
            population=[dict(adapter='original-parent')],config=dict(out=str(out),tiny=16,small=32,k=8,workers=2)),
        out/'sizing-after-gen0.json':dict(maps=12,k=6)}
    for path,value in values.items():path.write_text(json.dumps(value))
    return out


def test_draft_preserves_population_and_measured_sizing_without_becoming_restore_ready(completed):
    helper=module()
    record=helper.prepare(completed,current_pins={'src/llm/exit/selection.py':'new'},
                          approved_changes=['src/llm/exit/selection.py'])
    assert record['next_generation']==1
    assert record['population'][0]['method']=='sft'
    assert (record['args']['tiny'],record['args']['small'],record['args']['k'])==(4,8,6)
    assert record['args']['maps'] is None
    assert record['restore_ready'] is False
    assert record['state_sha256']==helper.digest({k:v for k,v in record.items() if k!='state_sha256'})


@pytest.mark.parametrize('pins,approved',[
    ({'src/llm/exit/selection.py':'new','src/llm/stowlm/train.py':'unexpected'},['src/llm/exit/selection.py']),
    ({'src/llm/exit/selection.py':'old'},['src/llm/exit/selection.py']),
    ({},['src/llm/exit/selection.py']),
])
def test_draft_refuses_unapproved_unchanged_or_removed_sources(completed,pins,approved):
    with pytest.raises(ValueError):module().prepare(completed,current_pins=pins,approved_changes=approved)


@pytest.mark.parametrize('state',['terminal','gen1','partial'])
def test_draft_requires_a_finished_nonterminal_gen0_boundary(completed,state):
    if state=='terminal':(completed/'terminal.json').write_text('{}')
    elif state=='gen1':
        directory=completed/'gen-001';directory.mkdir();(directory/'instances.json').write_text('{}')
    else:(completed/'gen-000'/'summary.json').unlink()
    with pytest.raises((RuntimeError,FileNotFoundError)):
        module().prepare(completed,current_pins={'src/llm/exit/selection.py':'new'},
                         approved_changes=['src/llm/exit/selection.py'])


def test_released_lock_refuses_a_running_owner(tmp_path):
    helper=module();path=tmp_path/'runner.lock'
    with path.open('w') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='still owns'):
            with helper.released_lock(path):pytest.fail('entered while owner runs')
    with helper.released_lock(path):pass


def test_released_lock_requires_real_ownership_evidence(tmp_path):
    with pytest.raises(RuntimeError,match='missing lock'):
        with module().released_lock(tmp_path/'missing.lock'):pytest.fail('missing ownership evidence')


def test_staged_epoch_accepts_exact_watchdog_hashes_and_optional_mask_speedup(tmp_path):
    epoch=epoch_module(tmp_path);path='src/llm/exit/selection.py'
    old={path:'a'*64,'src/llm/stowlm/mask.py':'b'*64}
    new={path:'c'*64,'src/llm/stowlm/mask.py':'d'*64}
    activate(tmp_path,{path:dict(old_sha256=old[path],new_sha256=new[path])})
    record=epoch.save_boundary(tmp_path/'boundary.json',number=1,population=[{'adapter':'checked-child'}],
        args={'out':'deployment'},loaded_pins=old,current_pins=new)
    assert record['plan']['kind']=='checker_watchdog'
    assert record['plan']['requires_restart'] and record['plan']['requires_smoke']
    assert epoch.restore_boundary(tmp_path/'boundary.json',new)==record
    with pytest.raises(RuntimeError,match='changed again'):
        epoch.restore_boundary(tmp_path/'boundary.json',{**new,'src/llm/exit/runner.py':'e'*64})


@pytest.mark.parametrize('changed',[
    {'src/llm/exit/selection.py':'d'*64},
    {'src/llm/exit/selection.py':'c'*64,'src/llm/stowlm/train.py':'d'*64},
    {},
])
def test_staged_epoch_refuses_wrong_hash_unknown_drift_and_deletion(tmp_path,changed):
    epoch=epoch_module(tmp_path);path='src/llm/exit/selection.py'
    old={path:'a'*64,'src/llm/exit/runtime.py':'b'*64}
    changed={**changed,'src/llm/exit/runtime.py':'b'*64}
    activate(tmp_path,{path:dict(old_sha256='a'*64,new_sha256='c'*64)})
    assert epoch.boundary_plan(old,changed)['kind']=='other'
    with pytest.raises(RuntimeError):
        epoch.save_boundary(tmp_path/'boundary.json',number=1,population=[{'adapter':'child'}],
            args={},loaded_pins=old,current_pins=changed)


def test_existing_mask_boundary_still_works_without_d124_manifest(tmp_path):
    epoch=epoch_module(tmp_path)
    record=epoch.save_boundary(tmp_path/'mask-boundary.json',number=1,population=[{'adapter':'child'}],
        args={},loaded_pins={'src/llm/stowlm/mask.py':'old'},current_pins={'src/llm/stowlm/mask.py':'new'})
    assert record['plan']['kind']=='mask_speedup'
    assert epoch.restore_boundary(tmp_path/'mask-boundary.json',record['current_pins'])==record


def test_staged_epoch_allows_explicitly_bound_new_watchdog_module(tmp_path):
    epoch=epoch_module(tmp_path)
    existing='src/llm/exit/selection.py';added='src/llm/exit/watchdog.py'
    old={existing:'a'*64};new={existing:'a'*64,added:'b'*64}
    activate(tmp_path,{added:dict(old_sha256=None,new_sha256='b'*64)})
    record=epoch.save_boundary(tmp_path/'addition.json',number=1,population=[{'adapter':'child'}],
        args={},loaded_pins=old,current_pins=new)
    assert record['plan']['kind']=='checker_watchdog'
    assert epoch.restore_boundary(tmp_path/'addition.json',new)==record


@pytest.mark.parametrize('old_present,binding',[
    (False,{'new_sha256':'b'*64}),
    (False,{'old_sha256':'a'*64,'new_sha256':'b'*64}),
    (True,{'old_sha256':None,'new_sha256':'b'*64}),
])
def test_staged_epoch_refuses_implicit_or_incorrect_addition_presence(tmp_path,old_present,binding):
    epoch=epoch_module(tmp_path)
    existing='src/llm/exit/selection.py';added='src/llm/exit/watchdog.py'
    old={existing:'a'*64,**({added:'c'*64} if old_present else {})}
    new={existing:'a'*64,added:'b'*64}
    activate(tmp_path,{added:binding})
    assert epoch.boundary_plan(old,new)['kind']=='other'


def test_staged_epoch_still_forbids_deletion_with_null_new_binding(tmp_path):
    epoch=epoch_module(tmp_path)
    existing='src/llm/exit/selection.py';removed='src/llm/exit/watchdog.py'
    old={existing:'a'*64,removed:'b'*64};new={existing:'a'*64}
    activate(tmp_path,{removed:dict(old_sha256='b'*64,new_sha256=None)})
    assert epoch.boundary_plan(old,new)['kind']=='other'


def test_commit_writes_actual_restore_compatible_boundary_only_after_release(completed,tmp_path):
    helper=module();epoch=epoch_module(tmp_path);path='src/llm/exit/selection.py'
    manifest=completed/'gen-000'/'instances.json';value=json.loads(manifest.read_text())
    value['source_pins']={path:'a'*64};manifest.write_text(json.dumps(value))
    activate(tmp_path,{path:dict(old_sha256='a'*64,new_sha256='c'*64)})
    (completed/'.runner.lock').touch()
    gpu=tmp_path/'experiments/mvp/exit/.gpu.lock';gpu.touch()
    boundary=completed/'epoch-boundary.json'
    with gpu.open('r+') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='still owns'):
            helper.commit(completed,boundary,root=tmp_path,current_pins={path:'c'*64},approved_changes=[path],epoch=epoch)
    assert not boundary.exists()
    record=helper.commit(completed,boundary,root=tmp_path,current_pins={path:'c'*64},approved_changes=[path],epoch=epoch)
    assert epoch.restore_boundary(boundary,{path:'c'*64})==record
    assert record['next_generation']==1 and record['args']['k']==6

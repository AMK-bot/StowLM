"""Activation tests use a synthetic repository, fake adapters and CPU commits."""
import fcntl
import importlib.util
import json
from pathlib import Path

import pytest

REPO=Path(__file__).resolve().parents[3]
STAGING=REPO/'experiments/mvp/exit/patches/d124'


def module():
    spec=importlib.util.spec_from_file_location('d124_apply',STAGING/'apply_watchdog.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


@pytest.fixture
def state(tmp_path):
    script=module();root=tmp_path;out=root/'experiments/mvp/exit/deployment'
    staging=root/'experiments/mvp/exit/patches/d124';staging.mkdir(parents=True)
    source=root/'src/llm/exit';source.mkdir(parents=True)
    for name in script.TARGETS:
        new=(STAGING/(name+'.py')).read_bytes() if name=='code_epoch' else f'# new {name}\n'.encode()
        (staging/(name+'.py')).write_bytes(new)
        if name not in ('checker_watchdog','outcomes'):
            (source/(name+'.py')).write_text(f'# old {name}\n')
    (staging/'boundary_handoff.py').write_bytes((STAGING/'boundary_handoff.py').read_bytes())
    helper=script.loaded_module('activation_helper',(staging/'boundary_handoff.py').read_bytes(),staging/'boundary_handoff.py')
    epoch=script.loaded_module('activation_epoch',(staging/'code_epoch.py').read_bytes(),source/'code_epoch.py');epoch.ROOT=root
    generation=out/'gen-000';generation.mkdir(parents=True)
    adapter=generation/'child-0';adapter.mkdir();(adapter/'adapters.safetensors').write_bytes(b'child')
    summary=dict(generation=0,next_population=[dict(adapter=str(adapter),method='sft')])
    manifest=dict(source_pins=epoch.source_snapshot(root),population=[dict(adapter='original-parent')],
                  config=dict(out=str(out),tiny=16,small=32,k=8,workers=2))
    for path,value in ((generation/'summary.json',summary),(generation/'instances.json',manifest),
                       (out/'sizing-after-gen0.json',dict(maps=12,k=6))):
        path.write_text(json.dumps(value))
    for path in (out/'.deployment.lock',out/'.runner.lock',out.parent/'.gpu.lock',out/'deployment.cancel'):path.touch()
    def commit(root,out,boundary,changed,gens):
        active=script.loaded_module('activated_epoch',(root/'src/llm/exit/code_epoch.py').read_bytes(),root/'src/llm/exit/code_epoch.py')
        active.ROOT=root
        return helper.commit(out,boundary,root=root,current_pins=active.source_snapshot(root),
            approved_changes=changed,gens=gens,epoch=active)
    return dict(script=script,root=root,out=out,staging=staging,epoch=epoch,commit=commit,
                expected=script.staged_bundle(staging)[1],original=epoch.source_snapshot(root))


def activate(state,**overrides):
    return state['script'].apply(state['root'],state['out'],expected_staged_sha256=state['expected'],
        committer=overrides.get('committer',state['commit']))


def test_activation_commits_real_boundary_binds_additions_and_is_idempotent(state):
    result=activate(state)
    assert result['status']=='applied'
    activation=json.loads((state['staging']/'activation-manifest.json').read_text())
    assert activation['schema']=='exit-d124-activation-v1'
    assert len(activation['targets'])==11
    assert 'src/llm/exit/final_recipe.py' in activation['targets']
    assert activation['targets']['src/llm/exit/checker_watchdog.py']['old_sha256'] is None
    assert activation['targets']['src/llm/exit/outcomes.py']['old_sha256'] is None
    epoch=state['epoch'];record=epoch.restore_boundary(state['out']/'epoch-boundary.json',epoch.source_snapshot(state['root']))
    assert record['plan']['kind']=='checker_watchdog' and record['next_generation']==1
    assert record['args']['tiny']==4 and record['args']['small']==8 and record['args']['k']==6
    assert (state['out']/'deployment.cancel').exists()
    for path,old_hash in state['original'].items():
        backup=state['out']/'d124-activation-backup'/path
        assert state['script'].sha(backup.read_bytes())==old_hash
    assert activate(state)['status']=='already_applied'


def test_reviewed_bundle_includes_all_eleven_sources_and_boundary_helper(state):
    contents,_=state['script'].staged_bundle(state['staging'])
    assert len(contents)==12
    assert 'final_recipe.py' in contents and 'boundary_handoff.py' in contents


@pytest.mark.parametrize('owner',['controller','runner','gpu'])
def test_activation_refuses_live_ownership_without_source_changes(state,owner):
    path={'controller':state['out']/'.deployment.lock','runner':state['out']/'.runner.lock',
          'gpu':state['out'].parent/'.gpu.lock'}[owner]
    with path.open('r+') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='still owns'):activate(state)
    assert state['epoch'].source_snapshot(state['root'])==state['original']
    assert not (state['staging']/'activation-manifest.json').exists()


def test_staged_tamper_is_refused_before_source_changes(state):
    (state['staging']/'selection.py').write_text('# unexpected change after review\n')
    with pytest.raises(RuntimeError,match='reviewed SHA-256'):activate(state)
    assert state['epoch'].source_snapshot(state['root'])==state['original']


def test_unknown_source_drift_is_refused_before_target_changes(state):
    path=state['root']/'src/llm/stowlm/train.py';path.parent.mkdir();path.write_text('# another executive\n')
    before=state['epoch'].source_snapshot(state['root'])
    with pytest.raises(RuntimeError,match='non-target source drift'):activate(state)
    assert state['epoch'].source_snapshot(state['root'])==before
    assert not (state['staging']/'activation-manifest.json').exists()


def test_unknown_target_bytes_are_refused(state):
    (state['root']/'src/llm/exit/selection.py').write_text('# unexpected target\n')
    with pytest.raises(RuntimeError,match='unknown current target bytes'):activate(state)


@pytest.mark.parametrize('missing',['summary','sizing','cancel','gen1','terminal'])
def test_activation_requires_committed_nonterminal_boundary_and_stopfile(state,missing):
    out=state['out']
    if missing=='summary':(out/'gen-000/summary.json').unlink()
    elif missing=='sizing':(out/'sizing-after-gen0.json').unlink()
    elif missing=='cancel':(out/'deployment.cancel').unlink()
    elif missing=='gen1':
        (out/'gen-001').mkdir();(out/'gen-001/instances.json').write_text('{}')
    else:(out/'terminal.json').write_text('{}')
    with pytest.raises((RuntimeError,FileNotFoundError)):activate(state)
    assert state['epoch'].source_snapshot(state['root'])==state['original']


def test_known_partial_apply_recovers_without_losing_original_backups(state,monkeypatch):
    script=state['script'];original=script.atomic_bytes;writes=[]
    def interrupt(path,content):
        if str(path).startswith(str(state['root']/'src')):
            writes.append(path)
            if len(writes)==4:raise OSError('interrupted apply')
        return original(path,content)
    monkeypatch.setattr(script,'atomic_bytes',interrupt)
    with pytest.raises(OSError,match='interrupted apply'):activate(state)
    assert not (state['out']/'epoch-boundary.json').exists()
    monkeypatch.setattr(script,'atomic_bytes',original)
    assert activate(state)['status']=='applied'
    assert (state['out']/'deployment.cancel').exists()


def test_failed_fresh_cpu_commit_can_retry_without_applying_unknown_bytes(state):
    def fail(*args):raise RuntimeError('CPU child failed')
    with pytest.raises(RuntimeError,match='CPU child failed'):activate(state,committer=fail)
    assert not (state['out']/'epoch-boundary.json').exists()
    assert activate(state)['status']=='applied'


def test_existing_mask_boundary_is_archived_only_when_bound_to_completed_gen0(state):
    epoch=state['epoch'];out=state['out'];old=state['original']
    mask=state['root']/'src/llm/stowlm/mask.py';mask.parent.mkdir();mask.write_text('# speedup\n')
    population=json.loads((out/'gen-000/summary.json').read_text())['next_population']
    record=epoch.save_boundary(out/'epoch-boundary.json',number=1,population=population,args={},
        loaded_pins=old,current_pins=epoch.source_snapshot(state['root']))
    assert activate(state)['status']=='applied'
    archive=out/f"epoch-boundary.pre-d124.{record['state_sha256'][:16]}.json"
    assert json.loads(archive.read_text())==record


def test_unrelated_existing_boundary_is_never_overwritten(state):
    boundary=state['out']/'epoch-boundary.json';boundary.write_text('{"next_generation":2}')
    with pytest.raises(RuntimeError,match='not the committed generation0 handoff'):activate(state)
    assert boundary.read_text()=='{"next_generation":2}'
    assert state['epoch'].source_snapshot(state['root'])==state['original']

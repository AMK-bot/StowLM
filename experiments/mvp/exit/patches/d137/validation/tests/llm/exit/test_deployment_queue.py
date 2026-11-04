import importlib.util
import json
from pathlib import Path
import sys

import pytest


def module():
    source=Path(__file__).resolve().parents[3]/'experiments/mvp/exit/deploy_when_ready.py'
    spec=importlib.util.spec_from_file_location('exit_deployment_test',source)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_adaptive_workers_never_exceed_shared_budget_or_gpu():
    deploy=module()
    ledger='| gold | gold | 4 | 8 | no | x | x |'
    assert deploy.available_workers(ledger,dict(load_1m=4,memory_free_percent=95))==6
    assert deploy.available_workers(ledger,dict(load_1m=10,memory_free_percent=95))==6
    assert deploy.available_workers(ledger,dict(load_1m=10,memory_free_percent=20))==0
    assert deploy.available_workers('| other | t | 1 | 30 | yes | x | x |',
                                    dict(load_1m=1,memory_free_percent=95))==0


@pytest.mark.parametrize('load,expected',[(12,6),(13,5),(15,3),(16,2),(18,2),(24,2)])
def test_d123_load_sizes_workers_without_load_veto(load,expected):
    deploy=module()
    assert deploy.available_workers('',dict(load_1m=load,memory_free_percent=95))==expected


@pytest.mark.parametrize('cores,ram,free,expected',[
    (12,0,95,2),(13,0,95,0),(0,44,95,2),(0,45,95,0),
    (0,0,53.125,2),(0,0,53,0),(0,0,None,0),
])
def test_d123_workers_still_respect_host_ledger_ram_and_memory(cores,ram,free,expected):
    deploy=module()
    ledger=f'| other | task | {cores} | {ram} | no | x | x |'
    assert deploy.available_workers(ledger,dict(load_1m=16,memory_free_percent=free))==expected


@pytest.mark.parametrize('load',[16,17,18])
def test_d123_smoke_accepts_load_16_to_18_with_one_core_budget(load):
    deploy=module()
    assert deploy.smoke_capacity(15,46,False,dict(load_1m=load,memory_free_percent=66/128*100))


@pytest.mark.parametrize('cores,ram,gpu,load,free',[
    (16,0,False,16,95),(0,47,False,16,95),(0,0,True,16,95),
    (0,0,False,18.01,95),(0,0,False,16,51),(0,0,False,16,None),
])
def test_d123_smoke_preserves_every_other_capacity_gate(cores,ram,gpu,load,free):
    deploy=module()
    assert not deploy.smoke_capacity(cores,ram,gpu,dict(load_1m=load,memory_free_percent=free))


def test_deployment_never_launches_before_authorized_time(tmp_path,monkeypatch):
    deploy=module()
    monkeypatch.setattr(deploy,'ROOT',tmp_path)
    out=tmp_path/'deployment';monkeypatch.setattr(deploy,'OUT',out)
    pilot=tmp_path/'experiments/mvp/sft/stowlm-v1/TRAIN.DONE'
    pilot.parent.mkdir(parents=True,exist_ok=True);pilot.write_text('{}')
    parent=tmp_path/'experiments/mvp/sft/stowlm-v1/adapter'
    parent.mkdir(parents=True,exist_ok=True);(parent/'adapters.safetensors').write_bytes(b'weights')
    monkeypatch.setattr(sys,'argv',['deploy','--not-before','2099-01-01T23:00:00-04:00'])
    clock=iter([0,1,999999]);monkeypatch.setattr(deploy.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(deploy.time,'sleep',lambda _:None)
    monkeypatch.setattr(deploy,'launch',lambda *a:pytest.fail('premature GPU launch'))
    from types import SimpleNamespace
    monkeypatch.setattr(deploy,'check_review_gate',lambda **kwargs:SimpleNamespace(ready=True))
    assert deploy.main()==1
    assert json.loads((out/'deployment-status.json').read_text())['state']=='expired_without_launch'


def test_open_reviews_keep_queued_controller_from_any_gpu_launch(tmp_path,monkeypatch):
    from types import SimpleNamespace
    deploy=module()
    monkeypatch.setattr(deploy,'ROOT',tmp_path)
    out=tmp_path/'deployment';monkeypatch.setattr(deploy,'OUT',out)
    monkeypatch.setattr(sys,'argv',['deploy','--not-before','2000-01-01T23:00:00-04:00'])
    monkeypatch.setattr(deploy,'check_review_gate',lambda **kwargs:SimpleNamespace(
        ready=False,reasons=('unresolved P1',)))
    monkeypatch.setattr(deploy,'probe',lambda:pytest.fail('admission attempted before reviews'))
    monkeypatch.setattr(deploy,'launch',lambda *a:pytest.fail('GPU launched with open review'))
    clock=iter([0,1,999999]);monkeypatch.setattr(deploy.time,'monotonic',lambda:next(clock))
    def stop_after_status(_):
        status=json.loads((out/'deployment-status.json').read_text())
        assert status['state']=='waiting_for_reviews' and 'unresolved P1' in status['reasons']
    monkeypatch.setattr(deploy.time,'sleep',stop_after_status)
    assert deploy.main()==1


def test_completed_generation_releases_before_new_code_smoke_then_resumes(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from llm.exit.code_epoch import save_boundary
    deploy=module();out=tmp_path/'deployment';out.mkdir()
    monkeypatch.setattr(deploy,'ROOT',tmp_path);monkeypatch.setattr(deploy,'OUT',out)
    monkeypatch.setattr(deploy,'gpu_lock_available',lambda:True)
    parent=tmp_path/'experiments/mvp/sft/stowlm-v1/adapter';parent.mkdir(parents=True)
    (parent/'adapters.safetensors').write_bytes(b'weights')
    pilot=tmp_path/'experiments/mvp/sft/stowlm-v1/TRAIN.DONE'
    pilot.parent.mkdir(parents=True,exist_ok=True);pilot.write_text('{}')
    ledger=tmp_path/'experiments/fleet/ledger.md';ledger.parent.mkdir(parents=True,exist_ok=True);ledger.write_text('')
    monkeypatch.setattr(deploy,'check_review_gate',lambda **kw:SimpleNamespace(ready=True))
    monkeypatch.setattr(deploy,'probe',lambda:dict(load_1m=0,memory_free_percent=95))
    pins={'src/llm/stowlm/mask.py':'old'}
    old=pins.copy()
    monkeypatch.setattr(deploy,'source_pins',lambda:pins.copy())
    def marker(parent,pins):return tmp_path/(pins['src/llm/stowlm/mask.py']+'.json')
    marker(parent,pins).write_text('{"status":"done"}')
    monkeypatch.setattr(deploy,'attempt_path',marker)
    events=[]
    def launch(command,log,pid_path):
        if '--gpu-smoke' in command:
            assert events==['generation_exited75']
            events.append('new_code_smoke')
            marker(parent,pins).write_text('{"status":"done"}')
            code=0
        elif not events:
            events.append('generation_exited75')
            pins['src/llm/stowlm/mask.py']='new'
            save_boundary(out/'epoch-boundary.json',number=1,population=[{'adapter':'child'}],
                args={'out':str(out)},loaded_pins=old,current_pins=pins)
            code=75
        else:
            assert events==['generation_exited75','new_code_smoke']
            assert '--resume-boundary' in command
            events.append('generation_resumed')
            (out/'admission.json').write_text('{"admitted":true}')
            code=0
        return SimpleNamespace(pid=123,returncode=code,poll=lambda:code)
    monkeypatch.setattr(deploy,'launch',launch)
    monkeypatch.setattr(deploy.time,'sleep',lambda _:None)
    import itertools
    clock=itertools.count();monkeypatch.setattr(deploy.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(sys,'argv',['deploy','--not-before','2000-01-01T00:00:00+00:00'])
    assert deploy.main()==0
    assert events==['generation_exited75','new_code_smoke','generation_resumed']


@pytest.mark.parametrize('pilot_row,lock_free',[(True,True),(False,False)])
def test_d113_marker_cannot_override_pilot_owner_row_or_gpu_lock(tmp_path,monkeypatch,pilot_row,lock_free):
    from types import SimpleNamespace
    deploy=module();out=tmp_path/'deployment'
    monkeypatch.setattr(deploy,'ROOT',tmp_path);monkeypatch.setattr(deploy,'OUT',out)
    parent=tmp_path/'experiments/mvp/sft/stowlm-v1/adapter';parent.mkdir(parents=True)
    (parent/'adapters.safetensors').write_bytes(b'weights')
    pilot=tmp_path/'experiments/mvp/sft/stowlm-v1/TRAIN.DONE'
    pilot.parent.mkdir(parents=True,exist_ok=True);pilot.write_text('{}')
    ledger=tmp_path/'experiments/fleet/ledger.md';ledger.parent.mkdir(parents=True)
    ledger.write_text('| fix-t8-sft/pilot2 | train | 8 (build) then 2 | 40 | yes | x | x |' if pilot_row else '')
    monkeypatch.setattr(deploy,'check_review_gate',lambda **kw:SimpleNamespace(ready=True))
    monkeypatch.setattr(deploy,'gpu_lock_available',lambda:lock_free)
    monkeypatch.setattr(deploy,'probe',lambda:pytest.fail('admission while pilot owns GPU'))
    monkeypatch.setattr(deploy,'launch',lambda *a:pytest.fail('GPU launch while pilot owns GPU'))
    monkeypatch.setattr(sys,'argv',['deploy'])
    clock=iter([0,1,999999]);monkeypatch.setattr(deploy.time,'monotonic',lambda:next(clock))
    def after_poll(_):
        status=json.loads((out/'deployment-status.json').read_text())
        assert status['state']=='waiting_for_pilot_gpu_release' and status['not_before'] is None
    monkeypatch.setattr(deploy.time,'sleep',after_poll)
    assert deploy.main()==1


def test_d113_no_time_gate_launches_immediately_after_other_gates(tmp_path,monkeypatch):
    from types import SimpleNamespace
    deploy=module();out=tmp_path/'deployment'
    monkeypatch.setattr(deploy,'ROOT',tmp_path);monkeypatch.setattr(deploy,'OUT',out)
    parent=tmp_path/'experiments/mvp/sft/stowlm-v1/adapter';parent.mkdir(parents=True)
    (parent/'adapters.safetensors').write_bytes(b'weights')
    pilot=tmp_path/'experiments/mvp/sft/stowlm-v1/TRAIN.DONE'
    pilot.parent.mkdir(parents=True,exist_ok=True);pilot.write_text('{}')
    ledger=tmp_path/'experiments/fleet/ledger.md';ledger.parent.mkdir(parents=True,exist_ok=True);ledger.write_text('')
    smoke=tmp_path/'smoke.json';smoke.write_text('{"status":"done"}')
    monkeypatch.setattr(deploy,'attempt_path',lambda *a:smoke)
    monkeypatch.setattr(deploy,'source_pins',lambda:{'source':'pinned'})
    monkeypatch.setattr(deploy,'check_review_gate',lambda **kw:SimpleNamespace(ready=True))
    monkeypatch.setattr(deploy,'gpu_lock_available',lambda:True)
    monkeypatch.setattr(deploy,'probe',lambda:dict(load_1m=0,memory_free_percent=95))
    commands=[]
    def launch(command,*a):
        commands.append(command)
        (out/'admission.json').write_text('{"admitted":true}')
        return SimpleNamespace(pid=123,returncode=0,poll=lambda:0)
    monkeypatch.setattr(deploy,'launch',launch)
    monkeypatch.setattr(sys,'argv',['deploy'])
    assert deploy.main()==0 and len(commands)==1
    assert (out/'READY').exists() and not (out/'STARTED').exists()


@pytest.mark.parametrize('smoke_done',[False,True])
def test_nonnumeric_ledger_waits_instead_of_crashing_or_launching(tmp_path,monkeypatch,smoke_done):
    from types import SimpleNamespace
    deploy=module();out=tmp_path/'deployment'
    monkeypatch.setattr(deploy,'ROOT',tmp_path);monkeypatch.setattr(deploy,'OUT',out)
    parent=tmp_path/'experiments/mvp/sft/stowlm-v1/adapter';parent.mkdir(parents=True)
    (parent/'adapters.safetensors').write_bytes(b'weights')
    pilot=tmp_path/'experiments/mvp/sft/stowlm-v1/TRAIN.DONE'
    pilot.parent.mkdir(parents=True,exist_ok=True);pilot.write_text('{}')
    ledger=tmp_path/'experiments/fleet/ledger.md';ledger.parent.mkdir(parents=True)
    ledger.write_text('| other | task | 1 now; work queued | 3 running | no | x | x |')
    smoke=tmp_path/'smoke.json'
    if smoke_done:smoke.write_text('{"status":"done"}')
    monkeypatch.setattr(deploy,'attempt_path',lambda *a:smoke)
    monkeypatch.setattr(deploy,'source_pins',lambda:{'source':'pinned'})
    monkeypatch.setattr(deploy,'check_review_gate',lambda **kw:SimpleNamespace(ready=True))
    monkeypatch.setattr(deploy,'gpu_lock_available',lambda:True)
    monkeypatch.setattr(deploy,'probe',lambda:dict(load_1m=0,memory_free_percent=95))
    monkeypatch.setattr(deploy,'launch',lambda *a:pytest.fail('unknown ledger cannot admit'))
    monkeypatch.setattr(sys,'argv',['deploy'])
    clock=iter([0,1,999999]);monkeypatch.setattr(deploy.time,'monotonic',lambda:next(clock))
    def after_poll(_):
        state=json.loads((out/'deployment-status.json').read_text())
        assert state['state']=='waiting_for_numeric_ledger'
        assert 'unknown fleet reservation' in state['reason']
    monkeypatch.setattr(deploy.time,'sleep',after_poll)
    assert deploy.main()==1


@pytest.mark.parametrize('pilot_complete',[False,True])
def test_d117_pilot_completion_cannot_launch_new_81_parent(tmp_path,monkeypatch,pilot_complete):
    from types import SimpleNamespace
    deploy=module();out=tmp_path/'deployment'
    monkeypatch.setattr(deploy,'ROOT',tmp_path);monkeypatch.setattr(deploy,'OUT',out)
    parent=tmp_path/'experiments/mvp/sft/stowlm-v1/adapter'
    parent.mkdir(parents=True);(parent/'adapters.safetensors').write_bytes(b'partial weights')
    if pilot_complete:
        pilot=tmp_path/'experiments/mvp/runs/sft-pilot2-small/polish_summary.json'
        pilot.parent.mkdir(parents=True);pilot.write_text('{"status":"done"}')
    monkeypatch.setattr(deploy,'check_review_gate',lambda **kw:SimpleNamespace(ready=True))
    monkeypatch.setattr(deploy,'gpu_lock_available',lambda:pytest.fail('GPU inspected before 8.1 completion'))
    monkeypatch.setattr(deploy,'probe',lambda:pytest.fail('admission before 8.1 completion'))
    monkeypatch.setattr(deploy,'launch',lambda *a:pytest.fail('GPU launch before 8.1 completion'))
    monkeypatch.setattr(sys,'argv',['deploy'])
    clock=iter([0,1,999999]);monkeypatch.setattr(deploy.time,'monotonic',lambda:next(clock))
    def after_poll(_):
        status=json.loads((out/'deployment-status.json').read_text())
        assert status['state']=='waiting_for_parent_completion'
        assert status['parent']==str(parent)
    monkeypatch.setattr(deploy.time,'sleep',after_poll)
    assert deploy.main()==1


def test_d117_done_json_is_bound_to_completed_parent(tmp_path,monkeypatch):
    deploy=module();monkeypatch.setattr(deploy,'ROOT',tmp_path)
    parent=tmp_path/'new-parent/adapter';parent.mkdir(parents=True)
    (parent/'adapters.safetensors').write_bytes(b'weights')
    marker=parent.parent/'TRAIN.DONE';done=marker.with_name('DONE.json')
    done.write_text(json.dumps({'marker':'8.1 SFT complete','adapter':'old-pilot/adapter'}))
    assert not deploy.completion_ready(parent,marker)
    done.write_text(json.dumps({'marker':'checkpoint','adapter':'new-parent/adapter'}))
    assert not deploy.completion_ready(parent,marker)
    done.write_text(json.dumps({'marker':'8.1 SFT complete','adapter':'new-parent/adapter'}))
    assert deploy.completion_ready(parent,marker)
    (parent/'adapters.safetensors').unlink()
    assert not deploy.completion_ready(parent,marker)


def test_d117_explicit_parent_and_completion_flags_are_used(tmp_path,monkeypatch):
    from types import SimpleNamespace
    deploy=module();out=tmp_path/'deployment'
    monkeypatch.setattr(deploy,'ROOT',tmp_path);monkeypatch.setattr(deploy,'OUT',out)
    parent=tmp_path/'custom/adapter';parent.mkdir(parents=True)
    (parent/'adapters.safetensors').write_bytes(b'weights')
    completion=parent.parent/'TRAIN.DONE';completion.touch()
    ledger=tmp_path/'experiments/fleet/ledger.md';ledger.parent.mkdir(parents=True);ledger.write_text('')
    smoke=tmp_path/'smoke.json';smoke.write_text('{"status":"done"}')
    monkeypatch.setattr(deploy,'attempt_path',lambda selected,*a:smoke if selected==parent else pytest.fail('wrong parent'))
    monkeypatch.setattr(deploy,'source_pins',lambda:{'source':'pinned'})
    monkeypatch.setattr(deploy,'check_review_gate',lambda **kw:SimpleNamespace(ready=True))
    monkeypatch.setattr(deploy,'gpu_lock_available',lambda:True)
    monkeypatch.setattr(deploy,'probe',lambda:dict(load_1m=0,memory_free_percent=95))
    commands=[]
    def launch(command,*a):
        commands.append(command)
        (out/'admission.json').write_text('{"admitted":true}')
        return SimpleNamespace(pid=123,returncode=0,poll=lambda:0)
    monkeypatch.setattr(deploy,'launch',launch)
    monkeypatch.setattr(sys,'argv',['deploy','--parent',str(parent),'--completion-marker',str(completion)])
    assert deploy.main()==0
    assert commands[0][commands[0].index('--parent')+1]==str(parent)


def test_d117_81_owner_is_still_an_exclusive_gpu_barrier():
    deploy=module()
    assert deploy.pilot_reserved('| fix-t8-sft/8.1 | train | 2 | 40 | yes | x | x |')

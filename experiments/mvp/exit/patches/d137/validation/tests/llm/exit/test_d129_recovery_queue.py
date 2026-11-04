"""Mock-only host recovery queue tests: never launch a process or use Metal."""
import fcntl
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
EXIT = ROOT/'experiments/mvp/exit'


def module(name, *, staged=False):
    path = EXIT/('patches/d129' if staged else '')/f'{name}.py'
    live = EXIT/f'{name}.py'
    if staged and '--recover-prepared' in live.read_text():
        path = live  # After activation, keep regressions attached to live code.
    spec = importlib.util.spec_from_file_location(f'_d129_{name}', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def queue(tmp_path, monkeypatch):
    deploy = module('deploy_when_ready', staged=True)
    out = tmp_path/'default-output'
    monkeypatch.setattr(deploy, 'ROOT', tmp_path)
    monkeypatch.setattr(deploy, 'OUT', out)
    ledger = tmp_path/'experiments/fleet/ledger.md'
    ledger.parent.mkdir(parents=True)
    ledger.write_text('')
    smoke = tmp_path/'smoke.json'
    smoke.write_text('{"status":"done"}')
    monkeypatch.setattr(deploy, 'completion_ready', lambda *a:True)
    monkeypatch.setattr(deploy, 'check_review_gate', lambda **kw:SimpleNamespace(ready=True))
    monkeypatch.setattr(deploy, 'gpu_lock_available', lambda:True)
    monkeypatch.setattr(deploy, 'probe', lambda:dict(load_1m=16, memory_free_percent=95))
    monkeypatch.setattr(deploy, 'source_pins', lambda:{'source':'pinned'})
    monkeypatch.setattr(deploy, 'attempt_path', lambda *a:smoke)
    commands = []
    def launch(command, log, pid):
        commands.append(command)
        output = Path(command[command.index('--out')+1])
        (output/'admission.json').write_text('{"admitted":true}')
        return SimpleNamespace(pid=123, returncode=0, poll=lambda:0)
    monkeypatch.setattr(deploy, 'launch', launch)
    return SimpleNamespace(module=deploy, out=out, ledger=ledger, smoke=smoke,
                           commands=commands, monkeypatch=monkeypatch)


def test_output_default_remains_monkeypatchable(queue):
    queue.monkeypatch.setattr(sys, 'argv', ['deploy'])
    assert queue.module.main() == 0
    assert queue.module.OUT == queue.out
    assert '--recover-prepared' not in queue.commands[0]
    assert queue.commands[0][queue.commands[0].index('--out')+1] == str(queue.out)


def test_new_output_and_recovery_flag_forward_only_to_generation_runner(queue):
    newout = queue.out.parent/'deployment-d129'
    prepared = newout/'salvage-prepared.json'
    queue.monkeypatch.setattr(sys, 'argv', ['deploy', '--out',str(newout),
                                           '--recover-prepared',str(prepared)])
    assert queue.module.main() == 0
    assert queue.module.OUT == newout
    command = queue.commands[0]
    assert command[command.index('--recover-prepared')+1] == str(prepared)
    assert command[command.index('--out')+1] == str(newout)
    assert '--dpo' in command and '--no-early-pruning' in command
    assert command[command.index('--audit-every')+1] == '2'
    assert (newout/'deployment-command.json').exists()
    assert not queue.out.exists()


def test_recovery_requires_current_code_smoke_and_never_passes_manifest_to_smoke(queue):
    deploy, patch = queue.module, queue.monkeypatch
    queue.smoke.unlink()
    newout = queue.out.parent/'recovery'
    prepared = newout/'salvage-prepared.json'
    patch.setattr(sys,'argv',['deploy','--out',str(newout),'--recover-prepared',str(prepared)])
    events = []
    def launch(command, log, pid):
        if '--gpu-smoke' in command:
            assert not events
            assert '--recover-prepared' not in command
            assert command[command.index('--workers')+1] == '1'
            events.append('smoke')
            queue.smoke.write_text('{"status":"done"}')
        else:
            assert events == ['smoke']
            assert command[command.index('--recover-prepared')+1] == str(prepared)
            events.append('generation')
            (newout/'admission.json').write_text('{"admitted":true}')
        return SimpleNamespace(pid=123,returncode=0,poll=lambda:0)
    patch.setattr(deploy,'launch',launch)
    patch.setattr(deploy.time,'sleep',lambda _:None)
    assert deploy.main() == 0
    assert events == ['smoke','generation']


@pytest.mark.parametrize('gate,state', [
    ('reviews','waiting_for_reviews'), ('completion','waiting_for_parent_completion'),
    ('owner','waiting_for_pilot_gpu_release'), ('gpu_lock','waiting_for_pilot_gpu_release'),
    ('ledger','waiting_for_numeric_ledger'), ('smoke','smoke_not_passed'),
    ('capacity','waiting_for_capacity')])
def test_recovery_path_keeps_all_controller_gates(queue, gate, state):
    deploy, patch = queue.module, queue.monkeypatch
    newout = queue.out.parent/'new-output'
    patch.setattr(sys, 'argv', ['deploy','--out',str(newout),
                              '--recover-prepared',str(newout/'salvage-prepared.json')])
    if gate == 'reviews':
        patch.setattr(deploy, 'check_review_gate', lambda **kw:SimpleNamespace(ready=False,reasons=['P1']))
    elif gate == 'completion':
        patch.setattr(deploy, 'completion_ready', lambda *a:False)
    elif gate == 'owner':
        queue.ledger.write_text('| fix-t8-sft/8.1 | train | 2 | 40 | yes | x | x |')
    elif gate == 'gpu_lock':
        patch.setattr(deploy, 'gpu_lock_available', lambda:False)
    elif gate == 'ledger':
        queue.ledger.write_text('| other | task | malformed | 2 | no | x | x |')
    elif gate == 'smoke':
        queue.smoke.write_text('{"status":"failed"}')
    elif gate == 'capacity':
        patch.setattr(deploy, 'probe', lambda:dict(load_1m=16,memory_free_percent=1))
    patch.setattr(deploy, 'launch', lambda *a:pytest.fail('gated recovery launched'))
    clock = iter([0,1,999999])
    patch.setattr(deploy.time,'monotonic',lambda:next(clock))
    def poll(_):
        assert json.loads((newout/'deployment-status.json').read_text())['state'] == state
    patch.setattr(deploy.time,'sleep',poll)
    assert deploy.main() == 1
    assert not (newout/'STARTED').exists()


@pytest.fixture
def host(tmp_path, monkeypatch):
    import llm.exit.runtime
    launcher = module('recover_on_host')
    monkeypatch.setattr(launcher,'ROOT',tmp_path)
    monkeypatch.setattr(launcher,'DEFAULT_OUT',tmp_path/'deployment-d129')
    monkeypatch.setattr(launcher,'PARENT',tmp_path/'8.1/adapter')
    monkeypatch.setattr(launcher,'COMPLETION',tmp_path/'8.1/DONE.json')
    out = launcher.DEFAULT_OUT
    out.mkdir()
    prepared = out/'salvage-prepared.json'
    prepared.write_text('{"epoch_reverified":true}')
    monkeypatch.setattr(llm.exit.runtime,'preflight_mlx_device',lambda:dict(metal_available=True))
    calls = []
    def popen(command, **kw):
        calls.append((command,kw))
        return SimpleNamespace(pid=200+len(calls))
    monkeypatch.setattr(launcher.subprocess,'Popen',popen)
    return SimpleNamespace(module=launcher, out=out, prepared=prepared,
                           monkeypatch=monkeypatch, calls=calls)


def test_host_recovery_keeps_new_output_and_gated_controller(host):
    result = host.module.launch()
    assert len(host.calls) == 2
    controller, kwargs = host.calls[0]
    assert controller[controller.index('--out')+1] == str(host.out)
    assert controller[controller.index('--recover-prepared')+1] == str(host.prepared)
    assert controller[controller.index('--parent')+1] == str(host.module.PARENT)
    assert controller[controller.index('--completion-marker')+1] == str(host.module.COMPLETION)
    assert kwargs['env']['STOWLM_MLX_CACHE_LIMIT_GB'] == '2'
    assert all(kwargs['env'][name]=='1' for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
        'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'))
    watcher = host.calls[1][0]
    assert watcher[-2:] == ['--out', str(host.out)]
    assert result['pid'] == 201 and result['watcher_pid'] == 202
    assert not (host.out/'STARTED').exists()
    assert json.loads((host.out/'controller-launch.json').read_text())['prepared'] == str(host.prepared)


def test_host_preflight_failure_starts_no_queue_and_mutates_no_output(host):
    import llm.exit.runtime
    before = sorted(p.name for p in host.out.iterdir())
    def no_metal():
        raise RuntimeError('Metal unavailable')
    host.monkeypatch.setattr(llm.exit.runtime,'preflight_mlx_device',no_metal)
    with pytest.raises(RuntimeError,match='Metal unavailable'):
        host.module.launch()
    assert not host.calls
    assert sorted(p.name for p in host.out.iterdir()) == before


@pytest.mark.parametrize('lockname',['.deployment.lock','.runner.lock'])
def test_host_never_overlaps_controller_or_runner_in_new_output(host, lockname):
    with (host.out/lockname).open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='existing recovery process holds'):
            host.module.launch()
    assert not host.calls


def test_host_does_not_cancel_or_clear_terminal_state(host):
    cancel = host.out/'deployment.cancel'
    cancel.write_text('leader hold')
    with pytest.raises(RuntimeError,match='cancelled/terminal'):
        host.module.launch()
    assert cancel.read_text() == 'leader hold'
    assert not host.calls

import fcntl
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def launcher(tmp_path,monkeypatch):
    source=Path(__file__).resolve().parents[3]/'experiments/mvp/exit/launch_on_host.py'
    spec=importlib.util.spec_from_file_location('exit_host_launcher_test',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    from llm.exit import runtime
    monkeypatch.setattr(runtime,'preflight_mlx_device',lambda:dict(metal_available=True))
    calls=[]
    def child(command,**kwargs):
        calls.append((command,kwargs));return SimpleNamespace(pid=100+len(calls))
    monkeypatch.setattr(module.subprocess,'Popen',child)
    return module,tmp_path/'experiments/mvp/exit/deployment',calls


def test_no_metal_refuses_before_queue_mutation_or_process_launch(launcher,monkeypatch):
    module,out,calls=launcher
    out.mkdir(parents=True);cancel=out/'deployment.cancel';cancel.write_text('held')
    from llm.exit import runtime
    def missing():raise RuntimeError('No Metal device available')
    monkeypatch.setattr(runtime,'preflight_mlx_device',missing)
    with pytest.raises(RuntimeError,match='No Metal'):module.launch()
    assert cancel.read_text()=='held' and not calls


def test_host_handoff_retains_failed_key_and_queues_all_existing_gates(launcher):
    module,out,calls=launcher
    out.mkdir(parents=True);(out/'deployment.cancel').write_text('held')
    marker=out.parent/'smoke-attempts'/'failed.json';marker.parent.mkdir()
    marker.write_text('{"status":"failed"}')
    record=module.launch(['--maps','12'])
    command,kwargs=calls[0]
    assert command[command.index('--parent')+1]=='experiments/mvp/sft/stowlm-v1/adapter'
    assert command[command.index('--completion-marker')+1].endswith('stowlm-v1/DONE.json')
    assert command[-2:]==['--maps','12'] and '--gpu-smoke' not in command
    assert kwargs['env']['STOWLM_MLX_CACHE_LIMIT_GB']=='2'
    assert kwargs['start_new_session'] and len(calls)==2
    assert record['watcher_pid']==102 and not (out/'STARTED').exists()
    assert not (out/'deployment.cancel').exists()
    assert json.loads(marker.read_text())=={'status':'failed'}


def test_existing_controller_lock_refuses_duplicate_host_queue(launcher):
    module,out,calls=launcher;out.mkdir(parents=True)
    with (out/'.deployment.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='existing controller still active'):module.launch()
    assert not calls

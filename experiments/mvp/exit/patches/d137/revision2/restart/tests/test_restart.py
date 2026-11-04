"""CPU-only restart-gate tests: no Metal, subprocess or deployment is started."""
from datetime import datetime,timedelta,timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

FOLDER = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(FOLDER))  # parent_audit imports its sibling state module


@pytest.fixture
def state(monkeypatch):
    spec=importlib.util.spec_from_file_location('_d137_restart_state',FOLDER/'state.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules,'state',module)
    return module


@pytest.fixture
def controller(state):
    spec=importlib.util.spec_from_file_location('_d137_restart_controller',FOLDER/'controller.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture
def host(state):
    spec=importlib.util.spec_from_file_location('_d137_restart_host',FOLDER/'restart_on_host.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def manifest(tmp_path,state):
    files={}
    for name in ('runner','data','control','selection','dpo'):
        relative=f'src/llm/exit/{name}.py';path=tmp_path/relative
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
        files[relative]=state.sha(path)
    value=dict(schema='exit-d137-installed-v1',targets=files)
    path=tmp_path/'installed.json';path.write_text(json.dumps(value))
    return path,value


def old_schedule(tmp_path,state,name='old',offset=0):
    path=tmp_path/name/'schedule.json';path.parent.mkdir(parents=True,exist_ok=True)
    value=dict(version=2,used=[f'{offset:064x}'],used_seeds=[offset],used_ids=[f'old-{offset}'],
        generations={'0':[dict(split='train',sha256=f'{offset+1:064x}',seed=offset+1,
                               instance_id=f'old-{offset+1}')]},
        validation=[dict(split='val',sha256='f'*64,seed=999,instance_id='held-out')])
    path.write_text(json.dumps(value));return path


def test_installation_requires_actual_target_bytes(tmp_path,state):
    path,record=manifest(tmp_path,state)
    result=state.validate_installed_manifest(path,root=tmp_path)
    assert result['targets']==5
    (tmp_path/'src/llm/exit/selection.py').write_text('wrong candidate pool')
    with pytest.raises(ValueError,match='does not match'):
        state.validate_installed_manifest(path,root=tmp_path)


@pytest.mark.parametrize('change',['old_schema','empty','missing_dpo','escape','bad_sha'])
def test_incomplete_or_unsafe_install_receipt_refused(tmp_path,state,change):
    path,record=manifest(tmp_path,state)
    if change=='old_schema':record['schema']='old'
    elif change=='empty':record['targets']={}
    elif change=='missing_dpo':record['targets'].pop('src/llm/exit/dpo.py')
    elif change=='escape':record['targets']['../escape.py']='a'*64
    else:record['targets']['src/llm/exit/dpo.py']='x'*64
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):state.validate_installed_manifest(path,root=tmp_path)


def test_union_previous_generations_never_imports_val_or_generation_numbers(tmp_path,state):
    first=old_schedule(tmp_path,state)
    second=old_schedule(tmp_path,state,'other',1)
    merged,sources=state.union_prior_reservations([second,first])
    assert merged['used']==[f'{i:064x}' for i in range(3)]
    assert merged['used_seeds']==[0,1,2] and merged['used_ids']==['old-0','old-1','old-2']
    assert merged['generations']=={} and merged['validation'] is None
    assert len(sources)==2 and all(len(row['sha256'])==64 for row in sources)
    assert 'f'*64 not in merged['used'] and 999 not in merged['used_seeds']


def test_schedule_seed_initialization_then_resume_preserves_live_reservations(tmp_path,state):
    old=old_schedule(tmp_path,state);out=tmp_path/'fresh'
    receipt=state.initialize_schedule(out,[old]);path=out/'schedule.json'
    value=json.loads(path.read_text());value['generations']['0']=[dict(split='train')]
    value['used'].append('e'*64);value['used_seeds'].append(22)
    path.write_text(json.dumps(value))
    assert state.initialize_schedule(out,[old])==receipt
    assert json.loads(path.read_text())==value
    value['used'].remove('0'*64);path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError,match='lost inherited'):
        state.initialize_schedule(out,[old])


def test_changed_prior_state_refuses_instead_of_reusing_fresh_maps(tmp_path,state):
    old=old_schedule(tmp_path,state);out=tmp_path/'fresh'
    state.initialize_schedule(out,[old]);old.write_text(old.read_text()+'\n')
    with pytest.raises(RuntimeError,match='inventory changed'):
        state.initialize_schedule(out,[old])


def test_existing_unproven_output_is_never_overwritten(tmp_path,state):
    out=tmp_path/'fresh';out.mkdir();path=out/'schedule.json';path.write_text('{}')
    with pytest.raises(RuntimeError,match='no D137 prior-reservation receipt'):
        state.initialize_schedule(out,[])
    assert path.read_text()=='{}'


def test_bad_prior_state_fails_closed(tmp_path,state):
    old=old_schedule(tmp_path,state);value=json.loads(old.read_text())
    value['generations']['0'][0]['split']='test';old.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='train only'):state.union_prior_reservations([old])


def test_clock_is_future_relative_to_first_admission_and_never_reset(tmp_path,state):
    first=datetime(2026,10,30,7,15,tzinfo=timezone(timedelta(hours=-4)))
    due=state.frozen_launch_clock(tmp_path,now=first)
    assert datetime.fromisoformat(due)-first==timedelta(minutes=330)  # D142 F4
    assert datetime.fromisoformat(due).hour==12
    assert state.frozen_launch_clock(tmp_path,now=first+timedelta(hours=5))==due
    with pytest.raises(RuntimeError,match='clock receipt changed'):
        state.frozen_launch_clock(tmp_path,minutes=151)


def test_clock_missing_timezone_refused(tmp_path,state):
    with pytest.raises(ValueError,match='timezone'):
        state.frozen_launch_clock(tmp_path,now=datetime(2026,10,30,6))


def test_owner_completion_is_same_parent_and_legacy_81_marker_only(controller,tmp_path):
    parent=tmp_path/'v2/adapter';parent.mkdir(parents=True)
    (parent/'adapters.safetensors').write_bytes(b'8.2')
    marker=parent.parent/'DONE.json'
    marker.write_text(json.dumps(dict(marker='8.1 SFT complete',adapter=str(parent))))
    assert controller.completion_ready(parent,marker)
    marker.write_text(json.dumps(dict(marker='8.1 SFT complete',adapter=str(tmp_path/'v1/adapter'))))
    assert not controller.completion_ready(parent,marker)


def test_load_gate_sizes_cpu_workers_without_vetoing_gpu_idle(controller):
    ledger='| other/work | task | 6 | 8 | no | 120 | now |\n'
    live=dict(load_1m=16.2,memory_free_percent=80.)
    assert controller.available_workers(ledger,live,maximum=4)==2
    live['load_1m']=10.
    assert controller.available_workers(ledger,live,maximum=4)==4
    assert controller.smoke_capacity(6,8,False,dict(load_1m=17.9,memory_free_percent=80.))
    assert not controller.smoke_capacity(6,8,True,dict(load_1m=1.,memory_free_percent=80.))


def test_controller_guard_refuses_missing_manifest_before_subprocess(controller,monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(controller,'_INSTALLED_MANIFEST',tmp_path/'missing.json')
    monkeypatch.setattr(controller.subprocess,'Popen',lambda *a,**k:calls.append((a,k)))
    with pytest.raises(FileNotFoundError):controller.launch(['forbidden'],tmp_path/'log',tmp_path/'pid')
    assert calls==[]


CHOICE=dict(schema='exit-d142-parent-choice-v1',parent_label='8.2',
    parent='/repo/experiments/mvp/sft/stowlm-v2/adapter',comparison_adapter='/repo/experiments/mvp/sft/stowlm-v1/adapter')


def test_host_default_plan_targets_82_and_new_output_without_old_clock(host):
    args=host.parser().parse_args([]);command=host.controller_command(args,CHOICE)
    assert 'stowlm-v2/adapter' in command[command.index('--parent')+1]
    assert command[command.index('--baseline-label')+1]=='8.2'
    assert 'stowlm-v1/adapter' in command[command.index('--comparison-adapter')+1]
    assert command[command.index('--gens')+1]=='6'
    assert command[command.index('--completion-marker')+1].endswith('stowlm-v2/DONE.json')
    assert command[command.index('--maps')+1]=='12'
    assert command[command.index('--k')+1]=='6'
    assert command[command.index('--steps')+1]=='100'
    assert command[command.index('--population')+1]=='2'
    assert command[command.index('--max-workers')+1]=='4'
    assert '--not-before' not in command and '--audit-clock' not in command


def test_host_missing_installed_receipt_cannot_queue_or_touch_metal(host,monkeypatch,tmp_path):
    out=tmp_path/'deployment-d137'
    monkeypatch.setattr(host,'DEFAULT_OUT',out)
    args=host.parser().parse_args(['--out',str(out),'--installed-manifest',str(tmp_path/'missing.json')])
    with pytest.raises(FileNotFoundError):host.launch(args)
    assert not out.exists()


def test_host_check_only_is_read_only_and_does_not_import_mlx(host,state,tmp_path,monkeypatch):
    path,record=manifest(tmp_path,state);out=tmp_path/'deployment-d137'
    monkeypatch.setattr(host,'DEFAULT_OUT',out)
    original=host.validate_installed_manifest
    monkeypatch.setattr(host,'validate_installed_manifest',lambda p:state.validate_installed_manifest(p,root=tmp_path))
    monkeypatch.setattr(host,'prior_schedule_paths',lambda **kwargs:[])
    choice=tmp_path/'choice.json';choice.write_text(json.dumps(CHOICE))
    args=host.parser().parse_args(['--out',str(out),'--installed-manifest',str(path),'--check-only',
                                   '--parent-choice',str(choice)])
    result=host.launch(args)
    assert result['gpu_launched'] is False and result['baseline_label']=='8.2'
    assert not out.exists()


def test_controller_source_preserves_all_existing_admission_gates():
    source=(FOLDER/'controller.py').read_text()
    for term in ('check_review_gate(root=ROOT)','completion_ready(parent,completion)',
                 'pilot_reserved(ledger)','gpu_lock_available()', 'smoke_capacity(',
                 'ledger_totals(',".get('status')!='done'",'available_workers(',
                 'attempt_path(parent,pins)', 'validate_installed_manifest(_INSTALLED_MANIFEST)',
                 "'--baseline-label',args.baseline_label", "'--teacher-planning-s','120'", "'--ballast-s','120'"):
        assert term in source


def test_host_refuses_to_start_without_the_audited_parent_choice(host,tmp_path):
    with pytest.raises(FileNotFoundError):host.load_choice(tmp_path/'missing.json')
    bad=tmp_path/'bad.json';bad.write_text(json.dumps(dict(schema='x',parent_label='8.2')))
    with pytest.raises(ValueError,match='parent choice'):host.load_choice(bad)


def test_thread_environment_scrubs_an_inherited_v4_flag(host,monkeypatch):
    monkeypatch.setenv('STOWAGE_BALLAST_V4','1')  # it would void every teacher row (D142 item 8)
    assert 'STOWAGE_BALLAST_V4' not in host.thread_environment()


def _rows(passes):
    return [dict(instance_id=i,passed=True,weighted_moves=m) for i,m in passes.items()]


def test_parent_rule_passes_then_mutual_moves_then_82():
    import parent_audit as p
    assert p.choose(_rows(dict(a=9,b=9,c=9)),_rows(dict(a=30,b=30)))['parent_label']=='8.1'  # more passes win
    assert p.choose(_rows(dict(a=30,b=30)),_rows(dict(a=20,b=20,c=20)))['parent_label']=='8.2'
    equal=p.choose(_rows(dict(a=20,b=20)),_rows(dict(a=30,b=30)))
    assert equal['parent_label']=='8.1' and equal['reason']=='equal passes, fewer mutual moves'
    assert p.choose(_rows(dict(a=30)),_rows(dict(a=20)))['parent_label']=='8.2'
    tied=p.choose(_rows(dict(a=20)),_rows(dict(a=20)))
    assert tied['parent_label']=='8.2' and 'tie' in tied['reason']


def test_parent_rule_compares_moves_only_on_mutual_maps():
    import parent_audit as p
    # 8.1 looks cheaper on its own passes, but on the one shared map 8.2 is better
    result=p.choose(_rows(dict(a=30,b=5)),_rows(dict(a=20,c=40)))
    assert result['mutual_maps']==1 and result['parent_label']=='8.2'


def test_completion_markers_are_looked_up_beside_the_adapter_folder():
    import parent_audit as p
    assert all('/sft/DONE.json' not in path for path in p.missing_inputs())
    assert not any(path.endswith('stowlm-v1/DONE.json') for path in p.missing_inputs())  # 8.1 finished long ago


def test_parent_audit_passes_the_current_epoch_marker_and_needs_it(monkeypatch,tmp_path):
    import parent_audit as p
    calls=[]
    monkeypatch.setattr(p.subprocess,'run',lambda command,**kwargs:calls.append(command))
    target=tmp_path/'parent-audit-8.1'
    with pytest.raises(FileNotFoundError):p.audit('8.1',tmp_path)
    assert calls[0][calls[0].index('--code-ready')+1]==str(p.CODE_READY)
    monkeypatch.setattr(p,'CODE_READY',tmp_path/'absent')
    assert str(tmp_path/'absent') in p.missing_inputs()

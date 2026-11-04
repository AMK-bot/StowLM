"""Bundle deployment cannot change active-study sources or overwrite drift."""
import importlib.util
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.sealed.common import HERE

spec=importlib.util.spec_from_file_location('sealed_install_d275',HERE/'fixtures/d275/install.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def plan(tmp_path):
    here=tmp_path/'sealed'
    stage=here/'fixtures/d275'
    stage.mkdir(parents=True)
    entries=[]
    for name in ('driver.py','worker.py'):
        (here/name).write_text('old '+name)
        (stage/name).write_text('new '+name)
        entries.append(dict(source='fixtures/d275/'+name,target=name,
            original_sha256=installer.digest(here/name),sha256=installer.digest(stage/name)))
    study=here/'measurements/study'
    study.mkdir(parents=True)
    (study/'FINAL.json').write_text('{}')
    return dict(here=str(here),files=entries,study=str(study),study_launcher_pid=123)


def test_bundle_waits_for_launcher_and_recorded_groups_before_replacing_imports(tmp_path):
    cfg=plan(tmp_path)
    study=Path(cfg['study'])
    job=study/'run/jobs/job'
    job.mkdir(parents=True)
    (job/'pid.json').write_text(json.dumps(dict(start_new_session=True,pgid=456)))
    state=dict(launcher=True,group=True)
    waits=[]
    def probe(pid,*,group=False):
        assert pid==(456 if group else 123)
        return state['group' if group else 'launcher']
    def sleep(_):
        assert (Path(cfg['here'])/'driver.py').read_text()=='old driver.py'
        waits.append(1)
        if state['launcher']: state['launcher']=False
        else: state['group']=False
    installed=installer.wait_and_install(cfg,probe=probe,sleep=sleep)
    assert len(waits)==2 and set(installed)=={'driver.py','worker.py'}
    assert (Path(cfg['here'])/'driver.py').read_text()=='new driver.py'


@pytest.mark.parametrize('changed',['source','target'])
def test_every_hash_is_checked_before_first_canonical_write(tmp_path,changed):
    cfg=plan(tmp_path)
    here=Path(cfg['here'])
    path=here/('fixtures/d275/worker.py' if changed=='source' else 'worker.py')
    path.write_text('drift')
    with pytest.raises(RuntimeError,match='changed'):
        installer.commit(cfg)
    assert (here/'driver.py').read_text()=='old driver.py'


def test_missing_terminal_receipt_never_installs(tmp_path):
    cfg=plan(tmp_path)
    (Path(cfg['study'])/'FINAL.json').unlink()
    with pytest.raises(RuntimeError,match='without a terminal receipt'):
        installer.wait_and_install(cfg,probe=lambda *_a,**_k:False)
    assert (Path(cfg['here'])/'worker.py').read_text()=='old worker.py'


def test_leader_stop_receipt_allows_install_only_after_owned_processes_exit(tmp_path):
    cfg=plan(tmp_path)
    study=Path(cfg['study'])
    (study/'FINAL.json').unlink()
    (study/'STUDY-STOPPED.json').write_text(json.dumps(dict(decision='D276',leader_requested=True)))
    calls=[]
    def probe(pid,*,group=False):
        calls.append(pid)
        return len(calls)==1
    installer.wait_and_install(cfg,probe=probe,sleep=lambda _:None)
    assert calls==[123,123]
    assert (Path(cfg['here'])/'worker.py').read_text()=='new worker.py'

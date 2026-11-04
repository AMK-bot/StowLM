"""Drain and attestation checks without sealed data, GPU or fleet mutation."""
from pathlib import Path

import pytest

from . import preflight as p
from .common import HarnessError, admission, atomic_json, read_json


def lattice(tmp_path, active):
    (tmp_path/'STOP').touch()
    atomic_json(tmp_path/'progress.json', dict(active=active))
    return tmp_path


def test_drain_waits_for_stop_and_running_rows_and_prints_why(tmp_path):
    messages=[]; polls=[]
    def sleep(seconds):
        polls.append(seconds)
        if len(polls)==1:
            lattice(tmp_path, ['running'])
        else:
            atomic_json(tmp_path/'progress.json', dict(active=[]))
    p.wait_for_drain(tmp_path, sleep=sleep, emit=lambda value,**kw: messages.append(value))
    assert len(polls)==2
    assert 'STOP is absent' in messages[0]
    assert '1 active rows' in messages[1]


@pytest.mark.parametrize('active',[False,-1,'0',None])
def test_malformed_progress_cannot_authorize_release(tmp_path, active):
    assert p.drain_status(lattice(tmp_path,active)) is not None


def test_idle_evidence_needs_named_attestation_and_drain(tmp_path):
    core=tmp_path/'core.json'
    atomic_json(core,dict(mask=(1<<18)-1,load1=2,t=100))
    directory=lattice(tmp_path,0)
    assert p.idle_snapshot(attested=False,directory=directory,core_state=core) is None
    row=p.idle_snapshot(attested=True,directory=directory,core_state=core)
    assert row['other_fleet_work']==[] and row['idle_confirmation_source']=='leader_run_sealed'
    assert row['t']==100  # Stale shared telemetry must never be made fresh.
    atomic_json(core,dict(mask=(1<<18)-1,load1=2,t=100,other_fleet_work=['reader']))
    assert p.idle_snapshot(attested=True,directory=directory,core_state=core) is None
    (directory/'STOP').unlink()
    assert p.idle_snapshot(attested=True,directory=directory,core_state=core) is None


def test_bridge_does_not_mutate_shared_telemetry_and_invalidates_after_exit(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'HERE',tmp_path)
    directory=tmp_path/'lattice'
    directory.mkdir()
    lattice(directory,0)
    core=tmp_path/'core.json';atomic_json(core,dict(mask=(1<<18)-1,load1=2,t=100))
    original=core.read_bytes();owned=tmp_path/'measurements/admission.json'
    with p.admission_bridge(owned,attested=True,directory=directory,core_state=core):
        assert admission(owned,now=101,workers=4,min_cores=18) is not None
        assert core.read_bytes()==original
    assert admission(owned,now=101,workers=4,min_cores=18) is None
    with pytest.raises(HarnessError,match='attest'):
        with p.admission_bridge(owned,attested=False):
            pytest.fail('unconfirmed bridge entered')


def test_manifest_independent_preflight_reports_all_static_blockers(monkeypatch):
    from . import budget,calibration,driver,prepare_launch_config
    monkeypatch.setattr(prepare_launch_config,'configuration',lambda: (_ for _ in ()).throw(HarnessError('reader drift')))
    monkeypatch.setattr(budget,'measured_budget',lambda config: (_ for _ in ()).throw(HarnessError('bad timing')))
    monkeypatch.setattr(calibration,'verify_calibration',lambda path: (_ for _ in ()).throw(HarnessError('7 of32')))
    monkeypatch.setattr(driver,'GLOBAL_CLAIM',Path('/nonexistent/synthetic-claim'))
    blockers=p.check({},operational=False)
    assert any('reader drift' in x for x in blockers)
    assert any('bad timing' in x for x in blockers)
    assert any('7 of32' in x for x in blockers)
